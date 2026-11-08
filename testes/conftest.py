from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

from reppp import assinatura, registro  # noqa: E402
import certificado_de_teste  # noqa: E402

BANCO_ADMIN = os.environ.get("PONTO_BANCO_TESTE", "postgresql://postgres@127.0.0.1:5432/postgres")

# CPFs e CNPJs fictícios com dígitos verificadores válidos (gerados para teste).
CNPJ = "11222333000181"
CPF_RESP = "52998224725"
CPF_A = "39053344705"
CPF_B = "11144477735"


def _url(nome: str) -> str:
    base, _, _ = BANCO_ADMIN.rpartition("/")
    return f"{base}/{nome}"


@pytest_asyncio.fixture
async def banco():
    nome = "ponto_t_" + uuid.uuid4().hex[:10]
    admin = await asyncpg.connect(BANCO_ADMIN)
    await admin.execute(f'CREATE DATABASE "{nome}"')
    await admin.close()
    url = _url(nome)
    conn = await asyncpg.connect(url)
    await registro.criar_esquema(conn)
    await conn.close()
    yield url
    admin = await asyncpg.connect(BANCO_ADMIN)
    await admin.execute(f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)')
    await admin.close()


@pytest_asyncio.fixture
async def conn(banco):
    c = await asyncpg.connect(banco)
    yield c
    await c.close()


@pytest.fixture(scope="session")
def certificado(tmp_path_factory):
    return certificado_de_teste.gerar(tmp_path_factory.mktemp("cert"))


@pytest.fixture(scope="session")
def assinador(certificado):
    chave, cert = certificado
    return assinatura.carregar(chave_pem=str(chave), cert_pem=str(cert), de_teste=True)


@pytest_asyncio.fixture
async def estab(conn):
    eid, chave = await registro.cadastrar_estabelecimento(
        conn, tipo_identificador=1, identificador="11.222.333/0001-81", razao_social="Colégio Aurora Ltda",
        cpf_responsavel=CPF_RESP, local_prestacao="Rua das Acácias, 100 — Sinop/MT", fuso="America/Cuiaba")
    await registro.incluir_empregado(conn, eid, cpf=CPF_A, nome="Ana Conceição", cpf_responsavel=CPF_RESP)
    await registro.incluir_empregado(conn, eid, cpf=CPF_B, nome="Bruno Souza", cpf_responsavel=CPF_RESP)
    return eid, chave
