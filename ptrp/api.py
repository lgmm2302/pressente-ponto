"""A API do programa de tratamento (PTRP) — processo separado do REP-P.

Lê as marcações do REP-P e grava só nas tabelas `ptrp_*`. Em produção, o
usuário do banco deste processo deve ter apenas SELECT nas tabelas do REP-P
(`scripts/permissoes.sql`).

Autenticação igual à do REP-P: `X-Chave-Estabelecimento`.
Variáveis além das do REP-P: PONTO_DESENVOLVEDOR_RAZAO, PONTO_DESENVOLVEDOR_EMAIL.
"""
from __future__ import annotations

import io
import os
import zipfile
from contextlib import asynccontextmanager
from datetime import date, datetime

import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

from reppp import assinatura, registro
from reppp.api import Config as ConfigDoRep

from . import aej, dados, espelho


class Config(ConfigDoRep):
    def __init__(self) -> None:
        super().__init__()
        self.desenvolvedor_aej = aej.Desenvolvedor(
            self.desenvolvedor.tipo_identificador, self.desenvolvedor.identificador,
            os.environ.get("PONTO_DESENVOLVEDOR_RAZAO", ""), os.environ.get("PONTO_DESENVOLVEDOR_EMAIL", ""))


class NovoHorario(BaseModel):
    codigo: str
    periodos: list[list[str]]
    intervalo_pre_assinalado: bool = False
    autor_cpf: str


class NovasRegras(BaseModel):
    vigencia: date
    regras: dict = Field(default_factory=dict)
    autor_cpf: str


class NovaEscala(BaseModel):
    cpf: str
    vigencia: date
    semana: dict[str, str]
    autor_cpf: str


class NovoFeriado(BaseModel):
    data: date
    descricao: str
    ativo: bool = True
    autor_cpf: str


class Matricula(BaseModel):
    cpf: str
    matricula_esocial: str


class Inclusao(BaseModel):
    cpf: str
    momento: datetime
    motivo: str
    autor_cpf: str


class Desconsideracao(BaseModel):
    registro_id: int
    motivo: str
    autor_cpf: str


class Abono(BaseModel):
    cpf: str
    data: date
    motivo: str
    autor_cpf: str


class Revogacao(BaseModel):
    tratamento_id: int
    motivo: str
    autor_cpf: str


class Ciencia(BaseModel):
    cpf: str
    decisao: str
    comentario: str = ""
    origem: dict = Field(default_factory=dict)


class PedidoDeEspelho(BaseModel):
    cpf: str
    competencia: str


class Aceite(BaseModel):
    cpf: str
    decisao: str
    comentario: str = ""
    origem: dict = Field(default_factory=dict)


def criar_app(config: Config | None = None, assinador: assinatura.Assinador | None = None) -> FastAPI:
    cfg = config or Config()
    estado: dict = {}

    @asynccontextmanager
    async def ciclo(app: FastAPI):
        estado["pool"] = await asyncpg.create_pool(cfg.banco, min_size=1, max_size=10)
        async with estado["pool"].acquire() as c:
            await dados.criar_esquema(c)
        estado["assinador"] = assinador or assinatura.do_ambiente()
        yield
        await estado["pool"].close()

    app = FastAPI(title="Pressente Ponto — Programa de Tratamento", lifespan=ciclo)

    async def conexao():
        async with estado["pool"].acquire() as c:
            yield c

    async def estabelecimento(x_chave_estabelecimento: str = Header(...), conn=Depends(conexao)) -> int:
        eid = await conn.fetchval("SELECT id FROM estabelecimentos WHERE chave_api_hash=$1",
                                  registro.hash_da_chave(x_chave_estabelecimento))
        if eid is None:
            raise HTTPException(401, "chave do estabelecimento inválida")
        return eid

    async def faz(coro):
        try:
            return await coro
        except registro.Recusado as e:
            raise HTTPException(422, str(e))

    @app.post("/v1/horarios", status_code=201)
    async def horario(d: NovoHorario, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        return {"id": await faz(dados.cadastrar_horario(conn, eid, **d.model_dump()))}

    @app.post("/v1/regras", status_code=201)
    async def regras(d: NovasRegras, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        return {"id": await faz(dados.cadastrar_regras(conn, eid, **d.model_dump()))}

    @app.post("/v1/escalas", status_code=201)
    async def escala(d: NovaEscala, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        return {"id": await faz(dados.cadastrar_escala(conn, eid, **d.model_dump()))}

    @app.post("/v1/feriados", status_code=201)
    async def feriado(d: NovoFeriado, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        return {"id": await faz(dados.cadastrar_feriado(conn, eid, **d.model_dump()))}

    @app.post("/v1/matriculas", status_code=201)
    async def matricula(d: Matricula, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        await faz(dados.cadastrar_matricula(conn, eid, **d.model_dump()))
        return {"ok": True}

    @app.post("/v1/tratamentos/incluir", status_code=201)
    async def incluir(d: Inclusao, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        return {"id": await faz(dados.incluir_marcacao(conn, eid, **d.model_dump()))}

    @app.post("/v1/tratamentos/desconsiderar", status_code=201)
    async def desconsiderar(d: Desconsideracao, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        return {"id": await faz(dados.desconsiderar_marcacao(conn, eid, **d.model_dump()))}

    @app.post("/v1/tratamentos/abonar", status_code=201)
    async def abonar(d: Abono, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        return {"id": await faz(dados.abonar(conn, eid, **d.model_dump()))}

    @app.post("/v1/tratamentos/revogar", status_code=201)
    async def revogar(d: Revogacao, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        return {"id": await faz(dados.revogar(conn, eid, **d.model_dump()))}

    @app.get("/v1/tratamentos/pendentes")
    async def pendentes(cpf: str, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        return [dict(r) for r in await dados.pendentes_de_ciencia(conn, eid, cpf)]

    @app.post("/v1/tratamentos/{tratamento_id}/ciencia", status_code=201)
    async def ciencia(tratamento_id: int, d: Ciencia, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        return {"id": await faz(dados.dar_ciencia(conn, eid, tratamento_id=tratamento_id, **d.model_dump()))}

    @app.get("/v1/apuracao")
    async def apuracao(cpf: str, inicio: date, fim: date, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        if (fim - inicio).days > 62 or fim < inicio:
            raise HTTPException(422, "período de até 62 dias, com fim depois do início")
        entrada = await faz(dados.carregar(conn, eid, cpf, inicio, fim))
        ap = entrada.apurar(inicio, fim)
        return {"dias": [{
            "data": d.data.isoformat(), "situacao": d.situacao, "horario": d.horario.codigo if d.horario else None,
            "marcas": [{"momento": x.marca.momento.isoformat(), "tipo": x.tipo, "seq": x.seq, "fonte": x.marca.fonte,
                        "registro_id": x.marca.registro_id, "tratamento_id": x.marca.tratamento_id,
                        "motivo": x.marca.motivo, "contestada": x.marca.contestada} for x in d.marcas],
            "trabalhado": d.trabalhado, "noturno": d.noturno, "computado": d.computado, "esperado": d.esperado,
            "extra": d.extra, "extra_descanso_feriado": d.extra_descanso_feriado, "debito": d.debito,
            "intervalo": d.intervalo, "intervalo_suprimido": d.intervalo_suprimido,
            "interjornada_faltante": d.interjornada_faltante, "banco_credito": d.banco_credito,
            "banco_debito": d.banco_debito, "falta": d.falta, "abono": d.abono, "tolerado": d.tolerado,
            "alertas": d.alertas} for d in ap.dias]}

    @app.post("/v1/espelhos", status_code=201)
    async def novo_espelho(d: PedidoDeEspelho, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        eid_esp, versao, _ = await faz(espelho.gerar(conn, eid, cpf=d.cpf, competencia=d.competencia,
                                                     assinador=estado["assinador"]))
        return {"id": eid_esp, "versao": versao, "pdf": f"/v1/espelhos/{eid_esp}"}

    @app.get("/v1/espelhos/{espelho_id}")
    async def baixar_espelho(espelho_id: int, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        pdf = await conn.fetchval("SELECT pdf FROM ptrp_espelhos WHERE id=$1 AND estabelecimento_id=$2", espelho_id, eid)
        if pdf is None:
            raise HTTPException(404, "espelho não encontrado")
        return Response(bytes(pdf), media_type="application/pdf")

    @app.post("/v1/espelhos/{espelho_id}/aceite", status_code=201)
    async def aceite(espelho_id: int, d: Aceite, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        return {"id": await faz(espelho.registrar_aceite(conn, eid, espelho_id=espelho_id, **d.model_dump()))}

    @app.get("/v1/aej")
    async def gerar_aej(inicio: date, fim: date, eid: int = Depends(estabelecimento), conn=Depends(conexao)):
        arq = await faz(aej.gerar(conn, eid, inicio=inicio, fim=fim, numero_inpi=cfg.numero_inpi,
                                  desenvolvedor=cfg.desenvolvedor_aej, assinador=estado["assinador"]))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(arq.nome, arq.conteudo)
            z.writestr(arq.nome_da_assinatura, arq.assinatura)
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{arq.nome[:-4]}.zip"'})

    return app
