"""O núcleo com o banco de verdade: NSR, corrente de hash, imutabilidade."""
import asyncio
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from reppp import formato as f
from reppp import registro
from conftest import CPF_A, CPF_B, CPF_RESP

pytestmark = pytest.mark.asyncio


async def test_cadastro_grava_registro_2_e_5(conn, estab):
    eid, chave = estab
    linhas = await conn.fetch("SELECT nsr, tipo, linha FROM registros WHERE estabelecimento_id=$1 ORDER BY nsr", eid)
    assert [(r["nsr"], r["tipo"]) for r in linhas] == [(1, 2), (2, 5), (3, 5)]
    assert all(f.crc_confere(r["linha"]) for r in linhas)
    assert len(chave) > 30
    guardado = await conn.fetchval("SELECT chave_api_hash FROM estabelecimentos WHERE id=$1", eid)
    assert guardado == registro.hash_da_chave(chave) and chave not in guardado


async def test_marcacao_encadeia_e_confere(conn, estab):
    eid, _ = estab
    m1 = await registro.marcar(conn, eid, cpf=CPF_A, coletor=1)
    m2 = await registro.marcar(conn, eid, cpf=CPF_B, coletor=2)
    assert (m1.nsr, m2.nsr) == (4, 5)
    anterior = await conn.fetchval("SELECT hash_anterior FROM registros WHERE id=$1", m2.registro_id)
    assert anterior == m1.hash
    assert f.hash_da_marcacao(m2.linha[:73], m1.hash) == m2.hash
    assert await registro.conferir(conn, eid) == []


async def test_nsr_sem_buraco_com_batidas_simultaneas(banco, estab):
    eid, _ = estab
    pool = await asyncpg.create_pool(banco, min_size=8, max_size=8)

    async def bate(i):
        async with pool.acquire() as c:
            await registro.marcar(c, eid, cpf=CPF_A if i % 2 else CPF_B, coletor=1)

    await asyncio.gather(*(bate(i) for i in range(40)))
    async with pool.acquire() as c:
        nsrs = [r["nsr"] for r in await c.fetch("SELECT nsr FROM registros WHERE estabelecimento_id=$1 ORDER BY nsr", eid)]
        assert nsrs == list(range(1, 44))
        assert await registro.conferir(c, eid) == []
    await pool.close()


@pytest.mark.parametrize("sql", [
    "UPDATE registros SET cpf='00000000000' WHERE nsr=4",
    "DELETE FROM registros WHERE nsr=4",
    "TRUNCATE registros CASCADE",
])
async def test_registro_e_imutavel_no_banco(conn, estab, sql):
    eid, _ = estab
    await registro.marcar(conn, eid, cpf=CPF_A, coletor=1)
    with pytest.raises(asyncpg.RaiseError, match="imutável"):
        await conn.execute(sql)
    assert await conn.fetchval("SELECT count(*) FROM registros WHERE estabelecimento_id=$1", eid) == 4


async def test_adulteracao_por_fora_e_detectada(conn, estab):
    """Mesmo quem desliga a trava no banco não altera uma batida sem a
    conferência perceber: o hash não fecha mais."""
    eid, _ = estab
    m = await registro.marcar(conn, eid, cpf=CPF_A, coletor=1)
    await registro.marcar(conn, eid, cpf=CPF_A, coletor=1)
    hora = "06" if m.linha[21:23] != "06" else "07"
    nova = m.linha[:21] + hora + m.linha[23:]  # a batida «passa» a ter sido em outra hora
    await conn.execute("ALTER TABLE registros DISABLE TRIGGER registros_imutaveis")
    await conn.execute("UPDATE registros SET linha=$2 WHERE id=$1", m.registro_id, nova)
    await conn.execute("ALTER TABLE registros ENABLE TRIGGER registros_imutaveis")
    problemas = await registro.conferir(conn, eid)
    assert any("hash da marcação não confere" in p for p in problemas)


async def test_apagar_por_fora_deixa_buraco_visivel(conn, estab):
    eid, _ = estab
    m = await registro.marcar(conn, eid, cpf=CPF_A, coletor=1)
    await registro.marcar(conn, eid, cpf=CPF_A, coletor=1)
    await conn.execute("ALTER TABLE registros DISABLE TRIGGER registros_imutaveis")
    await conn.execute("DELETE FROM registros WHERE id=$1", m.registro_id)
    await conn.execute("ALTER TABLE registros ENABLE TRIGGER registros_imutaveis")
    problemas = await registro.conferir(conn, eid)
    assert any("NSR 4 faltando" in p for p in problemas)
    assert any("corrente quebrada" in p for p in problemas)


async def test_recusa_nao_gasta_nsr(conn, estab):
    eid, _ = estab
    with pytest.raises(registro.Recusado):
        await registro.marcar(conn, eid, cpf="52998224724", coletor=1)  # CPF inválido
    with pytest.raises(registro.Recusado):
        await registro.marcar(conn, eid, cpf=CPF_RESP, coletor=1)  # não é empregado
    m = await registro.marcar(conn, eid, cpf=CPF_A, coletor=1)
    assert m.nsr == 4


async def test_empregado_excluido_nao_bate_mas_historico_fica(conn, estab):
    eid, _ = estab
    m = await registro.marcar(conn, eid, cpf=CPF_A, coletor=1)
    await registro.excluir_empregado(conn, eid, cpf=CPF_A, cpf_responsavel=CPF_RESP)
    with pytest.raises(registro.Recusado):
        await registro.marcar(conn, eid, cpf=CPF_A, coletor=1)
    assert await conn.fetchval("SELECT count(*) FROM registros WHERE id=$1", m.registro_id) == 1
    ultimo = await conn.fetchrow("SELECT tipo, linha FROM registros WHERE estabelecimento_id=$1 ORDER BY nsr DESC LIMIT 1", eid)
    assert ultimo["tipo"] == 5 and ultimo["linha"][34] == "E"


async def test_offline_usa_hora_do_coletor_e_recusa_futuro(conn, estab, monkeypatch):
    eid, _ = estab
    agora = datetime(2026, 11, 7, 23, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(registro, "agora", lambda: agora)
    antes = agora - timedelta(hours=2)
    m = await registro.marcar(conn, eid, cpf=CPF_A, coletor=1, offline=True, marcado_em_no_coletor=antes)
    assert m.offline and m.marcado_em == antes and m.gravado_em == agora
    assert m.linha[72] == "1" and m.linha[10:34] == "2026-11-07T17:00:00-0400"
    with pytest.raises(registro.Recusado, match="futuro"):
        await registro.marcar(conn, eid, cpf=CPF_A, coletor=1, offline=True,
                              marcado_em_no_coletor=agora + timedelta(minutes=5))
    with pytest.raises(registro.Recusado):
        await registro.marcar(conn, eid, cpf=CPF_A, coletor=1, offline=True)


async def test_online_ignora_hora_do_coletor(conn, estab, monkeypatch):
    eid, _ = estab
    agora = datetime(2026, 11, 7, 7, 3, tzinfo=timezone.utc)  # 03:03 em Cuiabá: sem horário proibido
    monkeypatch.setattr(registro, "agora", lambda: agora)
    m = await registro.marcar(conn, eid, cpf=CPF_A, coletor=1,
                              marcado_em_no_coletor=agora - timedelta(hours=5))
    assert m.marcado_em == agora and not m.offline


async def test_alterar_estabelecimento_grava_novo_registro_2(conn, estab):
    eid, _ = estab
    await registro.alterar_estabelecimento(conn, eid, cpf_responsavel=CPF_RESP, local_prestacao="Av. Nova, 9")
    r = await conn.fetchrow("SELECT tipo, linha FROM registros WHERE estabelecimento_id=$1 ORDER BY nsr DESC LIMIT 1", eid)
    assert r["tipo"] == 2 and r["linha"][227:327].rstrip() == "Av. Nova, 9"
    assert await registro.conferir(conn, eid) == []


async def test_nsr_e_por_estabelecimento(conn, estab):
    eid, _ = estab
    outro, _ = await registro.cadastrar_estabelecimento(
        conn, tipo_identificador=2, identificador=CPF_RESP, razao_social="Fulano Empregador Doméstico",
        cpf_responsavel=CPF_RESP)
    assert await conn.fetchval("SELECT nsr FROM registros WHERE estabelecimento_id=$1", outro) == 1


async def test_evento_de_disponibilidade(conn, estab):
    eid, _ = estab
    await registro.registrar_evento(conn, eid, f.EVENTO_DISPONIBILIDADE)
    with pytest.raises(registro.Recusado):
        await registro.registrar_evento(conn, eid, 1)
    assert await registro.conferir(conn, eid) == []


async def test_dados_invalidos_no_cadastro(conn):
    with pytest.raises(registro.Recusado):
        await registro.cadastrar_estabelecimento(conn, tipo_identificador=1, identificador="11222333000180",
                                                 razao_social="X", cpf_responsavel=CPF_RESP)
    with pytest.raises(registro.Recusado):
        await registro.cadastrar_estabelecimento(conn, tipo_identificador=1, identificador="11222333000181",
                                                 razao_social="X", cpf_responsavel=CPF_RESP, fuso="Lua/Base")


async def test_texto_tipografico_vira_latin1_e_emoji_e_recusado(conn, estab):
    eid, _ = estab
    local = await conn.fetchval("SELECT local_prestacao FROM estabelecimentos WHERE id=$1", eid)
    assert local == "Rua das Acácias, 100 - Sinop/MT"  # o travessão virou hífen
    with pytest.raises(registro.Recusado, match="ISO 8859-1"):
        await registro.incluir_empregado(conn, eid, cpf="52998224725", nome="Zé 🙂", cpf_responsavel=CPF_RESP)
