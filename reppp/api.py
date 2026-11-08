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
import json
import os
import zipfile
from dataclasses import asdict
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import afd, assinatura, comprovante, formato as f, prova, registro


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
    # Prova da batida (fase 7) — tudo opcional; o que faltar vira alerta.
    latitude: float | None = None
    longitude: float | None = None
    precisao_m: float | None = None
    dispositivo: str = ""
    foto_base64: str | None = None


class ConfigDaProva(BaseModel):
    coletar_local: bool = False
    exigir_local: bool = False
    coletar_foto: bool = False
    exigir_foto: bool = False
    base_legal_foto: str = ""
    retencao_foto_dias: int = 0
    autor_cpf: str


class NovaCerca(BaseModel):
    nome: str
    latitude: float
    longitude: float
    raio_m: int
    autor_cpf: str


class Config:
    def __init__(self) -> None:
        self.banco = os.environ.get("PONTO_BANCO", "")
        self.numero_inpi = os.environ.get("PONTO_NUMERO_INPI", "")
        self.desenvolvedor = afd.Desenvolvedor(int(os.environ.get("PONTO_DESENVOLVEDOR_TIPO", "1")),
                                               os.environ.get("PONTO_DESENVOLVEDOR_ID", ""))
        self.chave_admin = os.environ.get("PONTO_CHAVE_ADMINISTRACAO", "")
        # Em produção o esquema é criado pela migração (usuário dono do banco);
        # os serviços rodam com usuários sem permissão de DDL.
        self.criar_esquema = os.environ.get("PONTO_CRIAR_ESQUEMA", "1") == "1"


def criar_app(config: Config | None = None, assinador: assinatura.Assinador | None = None) -> FastAPI:
    cfg = config or Config()
    estado: dict = {}

    @asynccontextmanager
    async def ciclo(app: FastAPI):
        estado["pool"] = await asyncpg.create_pool(cfg.banco, min_size=1, max_size=10)
        if cfg.criar_esquema:
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
    async def marcar(dados: Batida, request: Request, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        p, avisos = prova.montar(latitude=dados.latitude, longitude=dados.longitude, precisao_m=dados.precisao_m,
                                 dispositivo=dados.dispositivo, ip=request.client.host if request.client else "",
                                 foto_base64=dados.foto_base64)
        origem = {k: v for k, v in dados.origem.items() if k != "prova"}
        try:
            m = await registro.marcar(conn, eid, cpf=dados.cpf, coletor=dados.coletor, offline=dados.offline,
                                      marcado_em_no_coletor=dados.marcado_em_no_coletor, origem=origem,
                                      prova=p, avisos_da_prova=avisos)
        except registro.Recusado as e:
            raise recusar(e)
        # O comprovante sai logo após a batida, sem o empregado pedir (art. 80).
        await comprovante.obter(conn, m.registro_id, estado["assinador"], cfg.numero_inpi)
        return {"registro_id": m.registro_id, "nsr": m.nsr, "marcado_em": m.marcado_em.isoformat(),
                "hash": m.hash, "comprovante": f"/v1/comprovantes/{m.registro_id}", "alertas": m.alertas}

    @app.get("/v1/provas/config")
    async def ler_config_da_prova(eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        return asdict(await prova.configuracao(conn, eid))

    @app.put("/v1/provas/config")
    async def configurar_prova(d: ConfigDaProva, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        dados = d.model_dump()
        autor = f.so_digitos(dados.pop("autor_cpf"))
        if not f.cpf_valido(autor):
            raise HTTPException(422, "CPF do autor inválido")
        try:
            cfg = prova.Configuracao(**dados)
        except prova.ProvaInvalida as e:
            raise HTTPException(422, str(e))
        return {"id": await prova.configurar(conn, eid, cfg, autor)}

    @app.post("/v1/cercas", status_code=201)
    async def nova_cerca(d: NovaCerca, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        dados = d.model_dump()
        dados["autor_cpf"] = f.so_digitos(dados["autor_cpf"])
        if not f.cpf_valido(dados["autor_cpf"]):
            raise HTTPException(422, "CPF do autor inválido")
        try:
            return {"id": await prova.criar_cerca(conn, eid, **dados)}
        except prova.ProvaInvalida as e:
            raise HTTPException(422, str(e))

    @app.get("/v1/cercas")
    async def cercas(eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        return [dict(r) for r in await conn.fetch(
            "SELECT id, nome, latitude, longitude, raio_m, ativa, criado_em, desativada_em FROM cercas "
            "WHERE estabelecimento_id=$1 ORDER BY ativa DESC, nome", eid)]

    @app.post("/v1/cercas/{cerca_id}/desativar")
    async def desativar_cerca(cerca_id: int, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        try:
            await prova.desativar_cerca(conn, eid, cerca_id)
        except prova.ProvaInvalida as e:
            raise HTTPException(422, str(e))
        return {"ok": True}

    @app.get("/v1/marcacoes/{registro_id}/foto")
    async def foto(registro_id: int, quem_cpf: str, motivo: str, eid: int = Depends(estabelecimento),
                   conn=Depends(conexao)):
        quem = f.so_digitos(quem_cpf)
        if not f.cpf_valido(quem):
            raise HTTPException(422, "CPF de quem abre a foto é obrigatório")
        try:
            dados, tipo = await prova.ver_foto(conn, eid, registro_id, quem_cpf=quem, motivo=motivo)
        except LookupError as e:
            raise HTTPException(404, str(e))
        except prova.ProvaInvalida as e:
            raise HTTPException(422, str(e))
        return Response(dados, media_type=tipo, headers={"Cache-Control": "no-store"})

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

    @app.get("/v1/saude")
    async def saude(conn=Depends(conexao)):
        await conn.fetchval("SELECT 1")
        return {"ok": True}

    @app.get("/v1/versao")
    async def versao():
        return ler_versao()

    return app


def ler_versao() -> dict:
    """O commit publicado (gravado pelo botão «Publicar»), sem nada sensível."""
    arq = Path(__file__).resolve().parent.parent / "VERSAO_PUBLICADA.json"
    try:
        return json.loads(arq.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"commit": None}
