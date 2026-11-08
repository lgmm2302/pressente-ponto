"""A prova da batida (fase 7): local, cerca, dispositivo e foto — sem nunca
impedir a marcação."""
import base64
import os
from datetime import date, datetime, timedelta, timezone

import asyncpg
import pytest
from fastapi.testclient import TestClient

from ptrp import dados
from reppp import api, prova as pv, registro
from conftest import CPF_A, CPF_B, CPF_RESP

SINOP = (-11.8642, -55.5093)
FOTO = b"\xff\xd8\xff\xe0" + b"JFIF-teste" * 20
CHAVE = base64.b64encode(b"k" * 32).decode()


# ── sem banco ────────────────────────────────────────────────────────────────

def test_distancia_e_cerca():
    assert abs(pv.distancia_m(0, 0, 1, 0) - 111_195) < 10  # 1 grau de latitude
    c = [{"nome": "Sede", "latitude": SINOP[0], "longitude": SINOP[1], "raio_m": 100}]
    perto = pv.Local(SINOP[0] + 0.0005, SINOP[1])  # ~55 m
    longe = pv.Local(SINOP[0] + 0.0030, SINOP[1])  # ~334 m
    assert pv.avaliar_cercas(perto, c)["resultado"] == pv.DENTRO
    r = pv.avaliar_cercas(longe, c)
    assert r["resultado"] == pv.FORA and r["cerca"] == "Sede" and 320 < r["distancia_m"] < 345
    # a precisão do GPS dá folga, mas só até 100 m
    a_178m = SINOP[0] + 0.0016
    assert pv.avaliar_cercas(pv.Local(a_178m, SINOP[1]), c)["resultado"] == pv.FORA
    assert pv.avaliar_cercas(pv.Local(a_178m, SINOP[1], 250), c)["resultado"] == pv.DENTRO
    assert pv.avaliar_cercas(pv.Local(SINOP[0] + 0.005, SINOP[1], 5000), c)["resultado"] == pv.FORA
    assert pv.avaliar_cercas(None, c)["resultado"] == pv.SEM_LOCAL
    assert pv.avaliar_cercas(perto, [])["resultado"] == pv.SEM_CERCA


def test_configuracao_exige_base_legal_para_foto():
    with pytest.raises(pv.ProvaInvalida, match="base legal"):
        pv.Configuracao(coletar_foto=True)
    with pytest.raises(pv.ProvaInvalida):
        pv.Configuracao(exigir_foto=True)
    with pytest.raises(pv.ProvaInvalida):
        pv.Configuracao(exigir_local=True)
    pv.Configuracao(coletar_foto=True, base_legal_foto="prevenção à fraude, art. 11, II, g")


def test_montar_descarta_o_que_vem_errado_sem_derrubar():
    p, avisos = pv.montar(latitude=95, longitude=0, precisao_m=None, dispositivo="x", ip="1.2.3.4",
                          foto_base64="não é base64")
    assert p.local is None and p.foto is None and len(avisos) == 2
    p, avisos = pv.montar(latitude=1, longitude=2, precisao_m=10, dispositivo="x", ip="1.2.3.4",
                          foto_base64=base64.b64encode(b"GIF89a....").decode())
    assert p.local is not None and p.foto is None and "JPEG ou PNG" in avisos[0]


# ── com banco ────────────────────────────────────────────────────────────────



async def _config(conn, eid, **kw):
    await pv.configurar(conn, eid, pv.Configuracao(**kw), CPF_RESP)


@pytest.mark.asyncio
async def test_cerca_alerta_mas_nunca_bloqueia(conn, estab):
    eid, _ = estab
    await _config(conn, eid, coletar_local=True, exigir_local=True)
    await pv.criar_cerca(conn, eid, nome="Sede", latitude=SINOP[0], longitude=SINOP[1], raio_m=100, autor_cpf=CPF_RESP)
    dentro = await registro.marcar(conn, eid, cpf=CPF_A, coletor=1, prova=pv.Prova(pv.Local(SINOP[0], SINOP[1], 10)))
    fora = await registro.marcar(conn, eid, cpf=CPF_A, coletor=1, prova=pv.Prova(pv.Local(SINOP[0] + 0.01, SINOP[1], 10)))
    sem = await registro.marcar(conn, eid, cpf=CPF_A, coletor=1)
    assert dentro.alertas == []
    assert any("fora da cerca «Sede»" in a for a in fora.alertas)
    assert sem.alertas == ["batida sem localização"]
    assert (dentro.nsr, fora.nsr, sem.nsr) == (4, 5, 6)  # as três entraram
    assert await registro.conferir(conn, eid) == []


@pytest.mark.asyncio
async def test_local_so_e_guardado_se_a_empresa_coleta(conn, estab):
    eid, _ = estab
    m = await registro.marcar(conn, eid, cpf=CPF_A, coletor=1, prova=pv.Prova(pv.Local(1, 2), "aparelho-1", "10.0.0.1"))
    import json
    origem = json.loads(await conn.fetchval("SELECT origem FROM registros WHERE id=$1", m.registro_id))
    assert origem["prova"]["local"] is None and origem["prova"]["dispositivo"] == "aparelho-1"


@pytest.mark.asyncio
async def test_foto_cifrada_aberta_com_trilha(conn, estab, monkeypatch):
    eid, _ = estab
    monkeypatch.setenv(pv.VAR_CHAVE_FOTOS, CHAVE)
    await _config(conn, eid, coletar_foto=True, exigir_foto=True, base_legal_foto="prevenção à fraude, art. 11, II, g")
    m = await registro.marcar(conn, eid, cpf=CPF_A, coletor=1, prova=pv.Prova(foto=FOTO))
    assert m.alertas == []
    cifrada = bytes(await conn.fetchval("SELECT cifrada FROM fotos WHERE registro_id=$1", m.registro_id))
    assert FOTO not in cifrada and b"JFIF" not in cifrada
    with pytest.raises(pv.ProvaInvalida, match="motivo"):
        await pv.ver_foto(conn, eid, m.registro_id, quem_cpf=CPF_RESP, motivo="")
    foto, tipo = await pv.ver_foto(conn, eid, m.registro_id, quem_cpf=CPF_RESP, motivo="contestação do empregado")
    assert foto == FOTO and tipo == "image/jpeg"
    assert await conn.fetchval("SELECT count(*) FROM acessos_a_fotos WHERE registro_id=$1", m.registro_id) == 1
    sem = await registro.marcar(conn, eid, cpf=CPF_A, coletor=1)
    assert sem.alertas == ["batida sem foto"]
    with pytest.raises(asyncpg.RaiseError, match="imutável"):
        await conn.execute("UPDATE fotos SET tipo='x'")
    with pytest.raises(asyncpg.RaiseError, match="imutável"):
        await conn.execute("DELETE FROM fotos")


@pytest.mark.asyncio
async def test_sem_chave_a_batida_entra_e_avisa(conn, estab, monkeypatch):
    eid, _ = estab
    monkeypatch.delenv(pv.VAR_CHAVE_FOTOS, raising=False)
    await _config(conn, eid, coletar_foto=True, base_legal_foto="prevenção à fraude, art. 11, II, g")
    m = await registro.marcar(conn, eid, cpf=CPF_A, coletor=1, prova=pv.Prova(foto=FOTO))
    assert m.nsr == 4 and any("foto não guardada" in a for a in m.alertas)
    assert await conn.fetchval("SELECT count(*) FROM fotos") == 0


@pytest.mark.asyncio
async def test_foto_nao_coletada_quando_a_empresa_nao_pede(conn, estab, monkeypatch):
    eid, _ = estab
    monkeypatch.setenv(pv.VAR_CHAVE_FOTOS, CHAVE)
    await registro.marcar(conn, eid, cpf=CPF_A, coletor=1, prova=pv.Prova(foto=FOTO))
    assert await conn.fetchval("SELECT count(*) FROM fotos") == 0


@pytest.mark.asyncio
async def test_expurgo_so_depois_do_prazo(conn, estab, monkeypatch):
    eid, _ = estab
    monkeypatch.setenv(pv.VAR_CHAVE_FOTOS, CHAVE)
    await _config(conn, eid, coletar_foto=True, base_legal_foto="prevenção à fraude, art. 11, II, g",
                  retencao_foto_dias=30)
    agora = datetime.now(timezone.utc)
    monkeypatch.setattr(registro, "agora", lambda: agora - timedelta(days=40))
    velha = await registro.marcar(conn, eid, cpf=CPF_A, coletor=1, prova=pv.Prova(foto=FOTO))
    monkeypatch.setattr(registro, "agora", lambda: agora)
    nova = await registro.marcar(conn, eid, cpf=CPF_A, coletor=1, prova=pv.Prova(foto=FOTO))
    assert await pv.expurgar_fotos_vencidas(conn) == 1
    restantes = [r["registro_id"] for r in await conn.fetch("SELECT registro_id FROM fotos")]
    assert restantes == [nova.registro_id]
    assert await conn.fetchval("SELECT count(*) FROM registros WHERE id=$1", velha.registro_id) == 1
    assert await registro.conferir(conn, eid) == []  # o hash da foto continua na prova


@pytest.mark.asyncio
async def test_prova_adulterada_e_detectada(conn, estab):
    eid, _ = estab
    await _config(conn, eid, coletar_local=True)
    m = await registro.marcar(conn, eid, cpf=CPF_A, coletor=1, prova=pv.Prova(pv.Local(-11.86, -55.50, 5)))
    await conn.execute("ALTER TABLE registros DISABLE TRIGGER registros_imutaveis")
    await conn.execute("""UPDATE registros SET origem = jsonb_set(origem, '{prova,local,latitude}', '-11.9')
                          WHERE id=$1""", m.registro_id)
    await conn.execute("ALTER TABLE registros ENABLE TRIGGER registros_imutaveis")
    assert any("prova da batida" in p for p in await registro.conferir(conn, eid))


@pytest.mark.asyncio
async def test_alerta_da_prova_chega_ao_espelho(conn, estab):
    eid, _ = estab
    await dados.criar_esquema(conn)
    await _config(conn, eid, coletar_local=True, exigir_local=True)
    await registro.marcar(conn, eid, cpf=CPF_A, coletor=1)
    hoje = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-4))).date()
    ap = (await dados.carregar(conn, eid, CPF_A, hoje, hoje)).apurar(hoje, hoje)
    assert any("batida sem localização" in a for a in ap.dias[0].alertas)


def test_api_da_prova(banco, assinador, monkeypatch):
    monkeypatch.setenv("PONTO_BANCO", banco)
    monkeypatch.setenv("PONTO_CHAVE_ADMINISTRACAO", "adm")
    monkeypatch.setenv(pv.VAR_CHAVE_FOTOS, CHAVE)
    with TestClient(api.criar_app(api.Config(), assinador)) as c:
        def estab(ident, tipo=1):
            r = c.post("/v1/estabelecimentos", headers={"X-Chave-Administracao": "adm"}, json={
                "tipo_identificador": tipo, "identificador": ident, "razao_social": "Empresa", "cpf_responsavel": CPF_RESP})
            return {"X-Chave-Estabelecimento": r.json()["chave"]}
        h = estab("11222333000181")
        c.post("/v1/empregados", headers=h, json={"cpf": CPF_A, "nome": "Ana", "cpf_responsavel": CPF_RESP})
        r = c.put("/v1/provas/config", headers=h, json={"coletar_foto": True, "autor_cpf": CPF_RESP})
        assert r.status_code == 422 and "base legal" in r.text
        assert c.put("/v1/provas/config", headers=h, json={
            "coletar_local": True, "coletar_foto": True, "base_legal_foto": "prevenção à fraude, art. 11, II, g",
            "autor_cpf": CPF_RESP}).status_code == 200
        assert c.post("/v1/cercas", headers=h, json={"nome": "Sede", "latitude": SINOP[0], "longitude": SINOP[1],
                                                     "raio_m": 150, "autor_cpf": CPF_RESP}).status_code == 201
        assert c.post("/v1/cercas", headers=h, json={"nome": "X", "latitude": 0, "longitude": 0, "raio_m": 5,
                                                     "autor_cpf": CPF_RESP}).status_code == 422
        r = c.post("/v1/marcacoes", headers=h, json={"cpf": CPF_A, "coletor": 1, "latitude": SINOP[0] + 0.02,
                                                     "longitude": SINOP[1], "precisao_m": 8, "dispositivo": "app-1",
                                                     "foto_base64": base64.b64encode(FOTO).decode()})
        assert r.status_code == 201 and any("fora da cerca" in a for a in r.json()["alertas"])
        rid = r.json()["registro_id"]
        assert c.get(f"/v1/marcacoes/{rid}/foto", headers=h, params={"quem_cpf": "123", "motivo": "x"}).status_code == 422
        f = c.get(f"/v1/marcacoes/{rid}/foto", headers=h, params={"quem_cpf": CPF_RESP, "motivo": "auditoria"})
        assert f.status_code == 200 and f.content == FOTO and f.headers["cache-control"] == "no-store"
        h2 = estab(CPF_B, tipo=2)
        assert c.get(f"/v1/marcacoes/{rid}/foto", headers=h2,
                     params={"quem_cpf": CPF_RESP, "motivo": "auditoria"}).status_code == 404
        # batida com foto inválida entra do mesmo jeito
        r = c.post("/v1/marcacoes", headers=h, json={"cpf": CPF_A, "coletor": 1, "foto_base64": "@@@"})
        assert r.status_code == 201 and any("foto descartada" in a for a in r.json()["alertas"])
