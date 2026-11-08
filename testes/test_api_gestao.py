"""A API da gestão, montada no processo do tratamento, com a chave do estabelecimento."""
import pytest
from fastapi.testclient import TestClient

from ptrp import api as api_ptrp
from reppp import api as api_rep
from conftest import CPF_RESP

ADMIN = "chave-admin-de-teste"


@pytest.fixture
def clientes(banco, assinador, monkeypatch):
    for k, v in {"PONTO_BANCO": banco, "PONTO_NUMERO_INPI": "512026001234", "PONTO_DESENVOLVEDOR_ID": "11222333000181",
                 "PONTO_CHAVE_ADMINISTRACAO": ADMIN}.items():
        monkeypatch.setenv(k, v)
    with TestClient(api_rep.criar_app(api_rep.Config(), assinador)) as rep, \
            TestClient(api_ptrp.criar_app(api_ptrp.Config(), assinador)) as trat:
        yield rep, trat


def _chave(rep):
    r = rep.post("/v1/estabelecimentos", headers={"X-Chave-Administracao": ADMIN}, json={
        "tipo_identificador": 1, "identificador": "11222333000181", "razao_social": "Loja Teste Ltda",
        "cpf_responsavel": CPF_RESP, "fuso": "America/Cuiaba"})
    assert r.status_code == 201, r.text
    return {"X-Chave-Estabelecimento": r.json()["chave"]}


CORPO = {
    "inicio": "2026-11-16", "fim": "2026-11-29",
    "pessoas": [{"id": p, "nome": p.title(), "grupo_domingo": "q"} for p in ("ana", "bia", "carla", "davi", "eliza", "fer")],
    "turnos": [{"codigo": "A", "inicio": "0900", "fim": "1800"}, {"codigo": "F", "inicio": "1300", "fim": "2200"}],
    "demanda": {"movimento": {f"{d}-{h}": (300.0 if h < 17 else 800.0) for d in range(7) for h in range(9, 22)},
                "por_pessoa_hora": 450},
    "coberturas": [{"minimo": 2, "das": "2000", "ate": "2200", "texto": "2 no fechamento"}],
    "sem_folga": [{"dias": [5], "texto": "sábado ninguém folga"}],
    "regras": {"domingo": {"padrao": 2, "q": 1}},
}


def test_planejar_validar_e_chave(clientes):
    rep, trat = clientes
    h = _chave(rep)
    assert trat.post("/v1/gestao/escala/planejar", json=CORPO).status_code == 422          # sem chave
    assert trat.post("/v1/gestao/escala/planejar", json=CORPO, headers={"X-Chave-Estabelecimento": "x"}).status_code == 401
    r = trat.post("/v1/gestao/escala/planejar", json=CORPO, headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["pode_publicar"] and set(d["escala"]) == {"ana", "bia", "carla", "davi", "eliza", "fer"}
    assert all(d["cobertura"][dia]["20"]["escalados"] >= 2 for dia in d["cobertura"])
    mexida = d["escala"]
    mexida["ana"] = {k: "A" for k in mexida["ana"]}
    v = trat.post("/v1/gestao/escala/validar", json={**CORPO, "escala": mexida}, headers=h).json()
    assert not v["pode_publicar"] and any(p["tipo"] == "folgas_da_semana" and p["pessoa"] == "ana" for p in v["problemas"])
    ruim = {**CORPO, "demanda": {"necessidade": {"9-1": 2}}}
    assert trat.post("/v1/gestao/escala/planejar", json=ruim, headers=h).status_code == 422


def test_banco_indicadores_e_alertas(clientes):
    rep, trat = clientes
    h = _chave(rep)
    s = trat.post("/v1/gestao/banco/saldo", headers=h, json={
        "hoje": "2026-11-15", "validade_meses": 6, "valor_hora": {"ana": "12.50"},
        "lancamentos": {"ana": [{"data": "2026-05-10", "minutos": 120}, {"data": "2026-09-01", "minutos": 360}]}}).json()
    assert s["ana"]["vencido"] == 120 and s["ana"]["saldo"] == 360 and s["ana"]["custo_do_vencido"] == "37.50"
    i = trat.post("/v1/gestao/indicadores", headers=h, json={"nomes": {"ana": "Ana"}, "minimo_de_dias": 1, "atual": {"ana": [
        {"data": "2026-11-02", "previsto": 480, "trabalhado": 480}, {"data": "2026-11-03", "previsto": 480, "trabalhado": 465, "atraso": 15}]}}).json()
    assert i["pessoas"]["ana"]["pontualidade"] == 50.0 and i["ranking"][0]["chave"] == "ana"
    a = trat.post("/v1/gestao/alertas", headers=h, json={"hoje": "2026-11-08", "cadastros": [
        {"id": "eliza", "nome": "Eliza", "admissao": "2024-12-13"}]}).json()
    assert a and a[0]["tipo"] == "ferias" and a[0]["prazo"] == "2026-12-12"
