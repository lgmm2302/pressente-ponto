"""As duas APIs juntas: o REP-P registra, o tratamento apura e entrega."""
import io
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from ptrp import api as api_ptrp
from reppp import api as api_rep
from conftest import CPF_A, CPF_RESP

ADMIN = "chave-admin-de-teste"


@pytest.fixture
def clientes(banco, assinador, monkeypatch):
    for k, v in {"PONTO_BANCO": banco, "PONTO_NUMERO_INPI": "512026001234", "PONTO_DESENVOLVEDOR_ID": "11222333000181",
                 "PONTO_CHAVE_ADMINISTRACAO": ADMIN, "PONTO_DESENVOLVEDOR_RAZAO": "FPM Tech Solutions Ltda",
                 "PONTO_DESENVOLVEDOR_EMAIL": "contato@exemplo.com.br"}.items():
        monkeypatch.setenv(k, v)
    with TestClient(api_rep.criar_app(api_rep.Config(), assinador)) as rep, \
            TestClient(api_ptrp.criar_app(api_ptrp.Config(), assinador)) as trat:
        yield rep, trat


def test_ponta_a_ponta(clientes):
    rep, trat = clientes
    r = rep.post("/v1/estabelecimentos", headers={"X-Chave-Administracao": ADMIN}, json={
        "tipo_identificador": 1, "identificador": "11222333000181", "razao_social": "Colégio Aurora Ltda",
        "cpf_responsavel": CPF_RESP, "fuso": "America/Cuiaba"})
    h = {"X-Chave-Estabelecimento": r.json()["chave"]}
    assert rep.post("/v1/empregados", headers=h, json={"cpf": CPF_A, "nome": "Ana", "cpf_responsavel": CPF_RESP}).status_code == 201
    assert trat.post("/v1/horarios", headers=h, json={"codigo": "COM", "periodos": [["0800", "1200"], ["1300", "1700"]],
                                                      "autor_cpf": CPF_RESP}).status_code == 201
    semana = {"0": "COM", "1": "COM", "2": "COM", "3": "COM", "4": "COM", "5": "FOLGA", "6": "DSR"}
    r = trat.post("/v1/escalas", headers=h, json={"cpf": CPF_A, "vigencia": "2026-01-01", "semana": semana,
                                                  "autor_cpf": CPF_RESP})
    assert r.status_code == 201, r.text
    m = rep.post("/v1/marcacoes", headers=h, json={"cpf": CPF_A, "coletor": 2}).json()
    hoje = datetime.fromisoformat(m["marcado_em"]).astimezone(ZoneInfo("America/Cuiaba")).date().isoformat()
    r = trat.get("/v1/apuracao", headers=h, params={"cpf": CPF_A, "inicio": hoje, "fim": hoje})
    assert r.status_code == 200, r.text
    dia = r.json()["dias"][0]
    assert dia["marcas"][0]["registro_id"] == m["registro_id"] and dia["marcas"][0]["fonte"] == "O"
    t = trat.post("/v1/tratamentos/desconsiderar", headers=h, json={
        "registro_id": m["registro_id"], "motivo": "teste de tratamento", "autor_cpf": CPF_RESP}).json()["id"]
    assert [p["id"] for p in trat.get("/v1/tratamentos/pendentes", headers=h, params={"cpf": CPF_A}).json()] == [t]
    assert trat.post(f"/v1/tratamentos/{t}/ciencia", headers=h, json={"cpf": CPF_A, "decisao": "ciente"}).status_code == 201
    e = trat.post("/v1/espelhos", headers=h, json={"cpf": CPF_A, "competencia": hoje[:7]})
    assert e.status_code == 201, e.text
    pdf = trat.get(e.json()["pdf"], headers=h)
    assert pdf.content.startswith(b"%PDF")
    assert trat.post(f"/v1/espelhos/{e.json()['id']}/aceite", headers=h,
                     json={"cpf": CPF_A, "decisao": "aceito"}).status_code == 201
    z = trat.get("/v1/aej", headers=h, params={"inicio": hoje, "fim": hoje})
    assert z.status_code == 200, z.text
    nomes = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert len(nomes) == 2 and nomes[1] == nomes[0] + ".p7s"
    assert trat.post("/v1/regras", headers=h, json={"vigencia": "2027-01-01", "regras": {"adicional_extra": 10},
                                                    "autor_cpf": CPF_RESP}).status_code == 422
    assert trat.get("/v1/apuracao", headers={"X-Chave-Estabelecimento": "x"},
                    params={"cpf": CPF_A, "inicio": hoje, "fim": hoje}).status_code == 401
