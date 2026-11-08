"""A API do REP-P — a porta pela qual Pressente, Exxen, Redalis e o app próprio
batem o ponto e leem o resultado. Nenhum sistema escreve no banco do REP-P.

Autenticação:
  · `X-Chave-Estabelecimento`: a chave do estabelecimento, entregue uma vez no
    cadastro (o banco guarda só o hash). Serve para bater o ponto, baixar
    comprovantes e gerar o AFD daquele estabelecimento, e nada além.
  · `X-Chave-Administracao`: cadastro de estabelecimentos (variável
    `PONTO_CHAVE_ADMINISTRACAO`). Sem a variável, a rota fica fechada.

Configuração (variáveis de ambiente):
  PONTO_BANCO                 URL do PostgreSQL do REP-P (só dele)
  PONTO_NUMERO_INPI           número do registro no INPI (vazio até sair)
  PONTO_DESENVOLVEDOR_TIPO    1 = CNPJ, 2 = CPF
  PONTO_DESENVOLVEDOR_ID      CNPJ/CPF do desenvolvedor
  + as do certificado (ver `assinatura.py`)
"""
from __future__ import annotations

import hmac
import io
import os
import zipfile
from contextlib import asynccontextmanager
from datetime import date, datetime

import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

from . import afd, assinatura, comprovante, formato as f, registro


# Os corpos dos pedidos (no módulo: o FastAPI resolve as anotações por nome).

class NovoEstabelecimento(BaseModel):
    tipo_identificador: int = Field(ge=1, le=2)
    identificador: str
    razao_social: str
    cpf_responsavel: str
    cno_caepf: str | None = None
    local_prestacao: str = ""
    fuso: str = "America/Sao_Paulo"


class Empregado(BaseModel):
    cpf: str
    nome: str = ""
    cpf_responsavel: str


class Batida(BaseModel):
    cpf: str
    coletor: int = Field(ge=1, le=5)
    offline: bool = False
    marcado_em_no_coletor: datetime | None = None
    origem: dict = Field(default_factory=dict)


class Config:
    def __init__(self) -> None:
        self.banco = os.environ.get("PONTO_BANCO", "")
        self.numero_inpi = os.environ.get("PONTO_NUMERO_INPI", "")
        self.desenvolvedor = afd.Desenvolvedor(int(os.environ.get("PONTO_DESENVOLVEDOR_TIPO", "1")),
                                               os.environ.get("PONTO_DESENVOLVEDOR_ID", ""))
        self.chave_admin = os.environ.get("PONTO_CHAVE_ADMINISTRACAO", "")


def criar_app(config: Config | None = None, assinador: assinatura.Assinador | None = None) -> FastAPI:
    cfg = config or Config()
    estado: dict = {}

    @asynccontextmanager
    async def ciclo(app: FastAPI):
        estado["pool"] = await asyncpg.create_pool(cfg.banco, min_size=1, max_size=10)
        async with estado["pool"].acquire() as c:
            await registro.criar_esquema(c)
        estado["assinador"] = assinador or assinatura.do_ambiente()
        yield
        await estado["pool"].close()

    app = FastAPI(title="Pressente Ponto — REP-P", lifespan=ciclo)

    async def conexao():
        async with estado["pool"].acquire() as c:
            yield c

    async def estabelecimento(x_chave_estabelecimento: str = Header(...),
                              conn: asyncpg.Connection = Depends(conexao)) -> int:
        eid = await conn.fetchval("SELECT id FROM estabelecimentos WHERE chave_api_hash=$1",
                                  registro.hash_da_chave(x_chave_estabelecimento))
        if eid is None:
            raise HTTPException(401, "chave do estabelecimento inválida")
        return eid

    def administracao(x_chave_administracao: str = Header(...)) -> None:
        if not cfg.chave_admin or not hmac.compare_digest(x_chave_administracao, cfg.chave_admin):
            raise HTTPException(401, "chave de administração inválida")

    def recusar(e: Exception) -> HTTPException:
        return HTTPException(422, str(e))

    @app.post("/v1/estabelecimentos", dependencies=[Depends(administracao)], status_code=201)
    async def novo_estabelecimento(dados: NovoEstabelecimento, conn=Depends(conexao)):
        try:
            eid, chave = await registro.cadastrar_estabelecimento(conn, **dados.model_dump())
        except registro.Recusado as e:
            raise recusar(e)
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, "estabelecimento já cadastrado")
        return {"id": eid, "chave": chave, "aviso": "guarde a chave: ela não é mostrada de novo"}

    @app.post("/v1/empregados", status_code=201)
    async def incluir(dados: Empregado, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        try:
            rid = await registro.incluir_empregado(conn, eid, cpf=dados.cpf, nome=dados.nome,
                                                   cpf_responsavel=dados.cpf_responsavel)
        except registro.Recusado as e:
            raise recusar(e)
        return {"registro_id": rid}

    @app.put("/v1/empregados")
    async def alterar(dados: Empregado, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        try:
            rid = await registro.alterar_empregado(conn, eid, cpf=dados.cpf, nome=dados.nome,
                                                   cpf_responsavel=dados.cpf_responsavel)
        except registro.Recusado as e:
            raise recusar(e)
        return {"registro_id": rid}

    @app.post("/v1/empregados/excluir")
    async def excluir(dados: Empregado, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        try:
            rid = await registro.excluir_empregado(conn, eid, cpf=dados.cpf, cpf_responsavel=dados.cpf_responsavel)
        except registro.Recusado as e:
            raise recusar(e)
        return {"registro_id": rid}

    @app.post("/v1/marcacoes", status_code=201)
    async def marcar(dados: Batida, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        try:
            m = await registro.marcar(conn, eid, cpf=dados.cpf, coletor=dados.coletor, offline=dados.offline,
                                      marcado_em_no_coletor=dados.marcado_em_no_coletor, origem=dados.origem)
        except registro.Recusado as e:
            raise recusar(e)
        # O comprovante sai logo após a batida, sem o empregado pedir (art. 80).
        await comprovante.obter(conn, m.registro_id, estado["assinador"], cfg.numero_inpi)
        return {"registro_id": m.registro_id, "nsr": m.nsr, "marcado_em": m.marcado_em.isoformat(),
                "hash": m.hash, "comprovante": f"/v1/comprovantes/{m.registro_id}"}

    @app.get("/v1/comprovantes/{registro_id}")
    async def baixar_comprovante(registro_id: int, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        dono = await conn.fetchval("SELECT estabelecimento_id FROM registros WHERE id=$1 AND tipo=7", registro_id)
        if dono != eid:
            raise HTTPException(404, "comprovante não encontrado")
        pdf = await comprovante.obter(conn, registro_id, estado["assinador"], cfg.numero_inpi)
        return Response(pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="comprovante-{registro_id}.pdf"'})

    @app.get("/v1/comprovantes")
    async def recentes(cpf: str, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        linhas = await comprovante.ultimas_48_horas(conn, eid, cpf)
        return [{"registro_id": r["id"], "nsr": r["nsr"], "marcado_em": r["marcado_em"].isoformat(),
                 "comprovante": f"/v1/comprovantes/{r['id']}"} for r in linhas]

    @app.get("/v1/afd")
    async def gerar_afd(inicio: date, fim: date, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        try:
            arq = await afd.gerar(conn, eid, inicio=inicio, fim=fim, numero_inpi=cfg.numero_inpi,
                                  desenvolvedor=cfg.desenvolvedor, assinador=estado["assinador"])
        except registro.Recusado as e:
            raise recusar(e)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(arq.nome, arq.conteudo)
            z.writestr(arq.nome_da_assinatura, arq.assinatura)
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{arq.nome[:-4]}.zip"'})

    @app.get("/v1/conferencia")
    async def conferencia(eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        problemas = await registro.conferir(conn, eid)
        return {"integro": not problemas, "problemas": problemas}

    @app.get("/v1/hora")
    async def hora():
        """A hora do REP-P, para o coletor mostrar o relógio (horas, minutos e
        segundos) no momento da batida (Anexo IX)."""
        return {"agora": registro.agora().isoformat()}

    return app
