"""A API, ponta a ponta, como Pressente/Exxen/Redalis vão usar."""
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from reppp import api
from conftest import CPF_A, CPF_RESP

ADMIN = "chave-admin-de-teste"


@pytest.fixture
def cliente(banco, assinador, monkeypatch):
    monkeypatch.setenv("PONTO_BANCO", banco)
    monkeypatch.setenv("PONTO_NUMERO_INPI", "512026001234")
    monkeypatch.setenv("PONTO_DESENVOLVEDOR_ID", "11222333000181")
    monkeypatch.setenv("PONTO_CHAVE_ADMINISTRACAO", ADMIN)
    with TestClient(api.criar_app(api.Config(), assinador)) as c:
        yield c


def _estabelecimento(c):
    r = c.post("/v1/estabelecimentos", headers={"X-Chave-Administracao": ADMIN}, json={
        "tipo_identificador": 1, "identificador": "11.222.333/0001-81", "razao_social": "Colégio Aurora Ltda",
        "cpf_responsavel": CPF_RESP, "fuso": "America/Cuiaba"})
    assert r.status_code == 201, r.text
    return {"X-Chave-Estabelecimento": r.json()["chave"]}


def test_fluxo_completo(cliente):
    h = _estabelecimento(cliente)
    r = cliente.post("/v1/empregados", headers=h, json={"cpf": CPF_A, "nome": "Ana Conceição", "cpf_responsavel": CPF_RESP})
    assert r.status_code == 201, r.text
    r = cliente.post("/v1/marcacoes", headers=h, json={"cpf": CPF_A, "coletor": 1})
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["nsr"] == 3 and len(m["hash"]) == 64
    pdf = cliente.get(m["comprovante"], headers=h)
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    lista = cliente.get("/v1/comprovantes", headers=h, params={"cpf": CPF_A}).json()
    assert [x["nsr"] for x in lista] == [3]
    hoje = m["marcado_em"][:10]
    z = cliente.get("/v1/afd", headers=h, params={"inicio": "2000-01-01", "fim": "2099-12-31"})
    assert z.status_code == 200, z.text
    nomes = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert nomes == ["AFD51202600123411222333000181REP_P.txt", "AFD51202600123411222333000181REP_P.txt.p7s"]
    assert cliente.get("/v1/conferencia", headers=h).json() == {"integro": True, "problemas": []}
    assert hoje


def test_sem_chave_ou_chave_errada(cliente):
    h = _estabelecimento(cliente)
    assert cliente.post("/v1/marcacoes", json={"cpf": CPF_A, "coletor": 1}).status_code == 422
    assert cliente.post("/v1/marcacoes", headers={"X-Chave-Estabelecimento": "x"},
                        json={"cpf": CPF_A, "coletor": 1}).status_code == 401
    assert cliente.post("/v1/estabelecimentos", headers={"X-Chave-Administracao": "errada"}, json={}).status_code == 401
    assert h


def test_um_estabelecimento_nao_le_o_outro(cliente):
    h1 = _estabelecimento(cliente)
    r = cliente.post("/v1/estabelecimentos", headers={"X-Chave-Administracao": ADMIN}, json={
        "tipo_identificador": 2, "identificador": CPF_RESP, "razao_social": "Outro Empregador",
        "cpf_responsavel": CPF_RESP})
    h2 = {"X-Chave-Estabelecimento": r.json()["chave"]}
    cliente.post("/v1/empregados", headers=h1, json={"cpf": CPF_A, "nome": "Ana", "cpf_responsavel": CPF_RESP})
    m = cliente.post("/v1/marcacoes", headers=h1, json={"cpf": CPF_A, "coletor": 1}).json()
    assert cliente.get(m["comprovante"], headers=h2).status_code == 404
    assert cliente.post("/v1/marcacoes", headers=h2, json={"cpf": CPF_A, "coletor": 1}).status_code == 422


def test_estabelecimento_repetido(cliente):
    _estabelecimento(cliente)
    r = cliente.post("/v1/estabelecimentos", headers={"X-Chave-Administracao": ADMIN}, json={
        "tipo_identificador": 1, "identificador": "11222333000181", "razao_social": "X", "cpf_responsavel": CPF_RESP})
    assert r.status_code == 409


def test_hora_do_servidor(cliente):
    assert "agora" in cliente.get("/v1/hora").json()
