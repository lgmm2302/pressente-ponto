"""O programa de tratamento com o banco: tratamentos, ciência, espelho e AEJ."""
import hashlib
from datetime import date, datetime, timedelta, timezone
from io import BytesIO

import asyncpg
import pytest
import pytest_asyncio
from asn1crypto import cms
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from pyhanko.pdf_utils.reader import PdfFileReader

from ptrp import aej, dados, espelho
from reppp import formato as f, registro
from conftest import CPF_A, CPF_B, CPF_RESP

pytestmark = pytest.mark.asyncio
SEG = date(2026, 11, 2)
FUSO = timezone(timedelta(hours=-4))  # Cuiabá
DEV = aej.Desenvolvedor(1, "11222333000181", "FPM Tech Solutions Ltda", "contato@exemplo.com.br")
SEMANA = {"0": "COM", "1": "COM", "2": "COM", "3": "COM", "4": "COM", "5": "FOLGA", "6": "DSR"}


def local(d: date, hhmm: str) -> datetime:
    return datetime.combine(d, datetime.strptime(hhmm, "%H:%M").time(), FUSO)


async def bate(conn, eid, monkeypatch, cpf, d, *horas):
    ids = []
    for h in horas:
        monkeypatch.setattr(registro, "agora", lambda h=h: local(d, h).astimezone(timezone.utc))
        ids.append((await registro.marcar(conn, eid, cpf=cpf, coletor=1)).registro_id)
    monkeypatch.setattr(registro, "agora", lambda: datetime(2026, 12, 1, 12, tzinfo=timezone.utc))
    return ids


@pytest_asyncio.fixture
async def base(conn, estab):
    eid, _ = estab
    await dados.criar_esquema(conn)
    await dados.cadastrar_horario(conn, eid, codigo="COM", periodos=[["0800", "1200"], ["1300", "1700"]],
                                  autor_cpf=CPF_RESP)
    for cpf in (CPF_A, CPF_B):
        await dados.cadastrar_escala(conn, eid, cpf=cpf, vigencia=date(2026, 1, 1), semana=SEMANA, autor_cpf=CPF_RESP)
    return eid


async def test_semana_completa_com_tratamentos(conn, base, monkeypatch):
    eid = base
    ids = await bate(conn, eid, monkeypatch, CPF_A, SEG, "08:00", "12:00", "13:00", "17:00")
    await bate(conn, eid, monkeypatch, CPF_A, SEG + timedelta(days=1), "08:00", "12:00", "13:00")  # esqueceu a saída
    dup = await bate(conn, eid, monkeypatch, CPF_A, SEG + timedelta(days=2), "08:00", "08:01", "12:00", "13:00", "17:00")
    # quinta sem marcação (falta); sexta abonada
    await dados.incluir_marcacao(conn, eid, cpf=CPF_A, momento=local(SEG + timedelta(days=1), "17:00"),
                                 motivo="esqueceu de bater a saída", autor_cpf=CPF_RESP)
    t_dup = await dados.desconsiderar_marcacao(conn, eid, registro_id=dup[1], motivo="batida em duplicidade",
                                               autor_cpf=CPF_RESP)
    await dados.abonar(conn, eid, cpf=CPF_A, data=SEG + timedelta(days=4), motivo="atestado médico", autor_cpf=CPF_RESP)
    e = await dados.carregar(conn, eid, CPF_A, SEG, SEG + timedelta(days=6))
    ap = e.apurar(SEG, SEG + timedelta(days=6))
    seg, ter, qua, qui, sex, sab, dom = ap.dias
    assert seg.trabalhado == 480 and not seg.alertas
    assert ter.trabalhado == 480 and [x.marca.fonte for x in ter.marcas] == ["O", "O", "O", "I"]
    assert qua.trabalhado == 480 and [x.tipo for x in qua.marcas] == ["E", "D", "S", "E", "S"]
    assert qui.falta and qui.debito == 480
    assert not sex.falta and sex.abono == "atestado médico"
    assert sab.situacao == "FOLGA" and dom.situacao == "DSR"
    # A original continua intacta no REP-P.
    assert await conn.fetchval("SELECT count(*) FROM registros WHERE id=$1", dup[1]) == 1
    # Revogar o tratamento devolve a batida ao cálculo.
    await dados.revogar(conn, eid, tratamento_id=t_dup, motivo="era batida válida", autor_cpf=CPF_RESP)
    ap = (await dados.carregar(conn, eid, CPF_A, SEG, SEG + timedelta(days=6))).apurar(SEG, SEG + timedelta(days=6))
    assert "D" not in [x.tipo for x in ap.dias[2].marcas]


async def test_recusas_dos_tratamentos(conn, base, monkeypatch):
    eid = base
    ids = await bate(conn, eid, monkeypatch, CPF_A, SEG, "08:00")
    with pytest.raises(registro.Recusado, match="futuro"):
        await dados.incluir_marcacao(conn, eid, cpf=CPF_A, momento=datetime(2030, 1, 1, tzinfo=timezone.utc),
                                     motivo="teste", autor_cpf=CPF_RESP)
    with pytest.raises(registro.Recusado, match="motivo"):
        await dados.abonar(conn, eid, cpf=CPF_A, data=SEG, motivo="  ", autor_cpf=CPF_RESP)
    await dados.desconsiderar_marcacao(conn, eid, registro_id=ids[0], motivo="engano", autor_cpf=CPF_RESP)
    with pytest.raises(registro.Recusado, match="já está desconsiderada"):
        await dados.desconsiderar_marcacao(conn, eid, registro_id=ids[0], motivo="engano", autor_cpf=CPF_RESP)
    with pytest.raises(registro.Recusado, match="DSR"):
        await dados.cadastrar_escala(conn, eid, cpf=CPF_A, vigencia=date(2027, 1, 1),
                                     semana={k: "COM" for k in SEMANA}, autor_cpf=CPF_RESP)
    with pytest.raises(registro.Recusado, match="inexistente"):
        await dados.cadastrar_escala(conn, eid, cpf=CPF_A, vigencia=date(2027, 1, 1),
                                     semana={**SEMANA, "0": "XYZ"}, autor_cpf=CPF_RESP)
    with pytest.raises(registro.Recusado, match="não se edita"):
        await dados.cadastrar_horario(conn, eid, codigo="COM", periodos=[["0900", "1800"]], autor_cpf=CPF_RESP)
    with pytest.raises(registro.Recusado, match="50%"):
        await dados.cadastrar_regras(conn, eid, vigencia=date(2027, 1, 1), regras={"adicional_extra": 30},
                                     autor_cpf=CPF_RESP)


async def test_ciencia_do_empregado(conn, base, monkeypatch):
    eid = base
    t = await dados.abonar(conn, eid, cpf=CPF_A, data=SEG, motivo="atestado", autor_cpf=CPF_RESP)
    assert [r["id"] for r in await dados.pendentes_de_ciencia(conn, eid, CPF_A)] == [t]
    with pytest.raises(registro.Recusado):
        await dados.dar_ciencia(conn, eid, tratamento_id=t, cpf=CPF_B, decisao="ciente")
    with pytest.raises(registro.Recusado, match="porquê"):
        await dados.dar_ciencia(conn, eid, tratamento_id=t, cpf=CPF_A, decisao="recusado")
    await dados.dar_ciencia(conn, eid, tratamento_id=t, cpf=CPF_A, decisao="ciente")
    with pytest.raises(registro.Recusado, match="já se manifestou"):
        await dados.dar_ciencia(conn, eid, tratamento_id=t, cpf=CPF_A, decisao="recusado", comentario="mudei de ideia")
    assert await dados.pendentes_de_ciencia(conn, eid, CPF_A) == []


async def test_recusa_aparece_na_apuracao(conn, base, monkeypatch):
    eid = base
    await bate(conn, eid, monkeypatch, CPF_A, SEG, "08:00", "12:00", "13:00")
    t = await dados.incluir_marcacao(conn, eid, cpf=CPF_A, momento=local(SEG, "17:00"), motivo="saída esquecida",
                                     autor_cpf=CPF_RESP)
    await dados.dar_ciencia(conn, eid, tratamento_id=t, cpf=CPF_A, decisao="recusado", comentario="saí às 19h")
    ap = (await dados.carregar(conn, eid, CPF_A, SEG, SEG)).apurar(SEG, SEG)
    assert ap.dias[0].marcas[-1].marca.contestada


@pytest.mark.parametrize("sql", ["UPDATE ptrp_tratamentos SET motivo='outro'", "DELETE FROM ptrp_tratamentos",
                                 "DELETE FROM ptrp_horarios", "UPDATE ptrp_escalas SET semana='{}'"])
async def test_tabelas_do_tratamento_sao_imutaveis(conn, base, sql):
    eid = base
    await dados.abonar(conn, eid, cpf=CPF_A, data=SEG, motivo="atestado", autor_cpf=CPF_RESP)
    with pytest.raises(asyncpg.RaiseError, match="imutável"):
        await conn.execute(sql)


async def test_espelho_versoes_aceite_e_banco(conn, base, monkeypatch, assinador):
    eid = base
    await dados.cadastrar_regras(conn, eid, vigencia=date(2026, 1, 1), regras={"banco_de_horas_meses": 6},
                                 autor_cpf=CPF_RESP)
    await bate(conn, eid, monkeypatch, CPF_A, date(2026, 10, 5), "08:00", "12:00", "13:00", "18:00")  # +60 no banco
    await bate(conn, eid, monkeypatch, CPF_A, SEG, "08:00", "12:00", "13:00", "17:30")  # +30
    out_id, v, _ = await espelho.gerar(conn, eid, cpf=CPF_A, competencia="2026-10", assinador=assinador)
    assert v == 1
    nov_id, v, pdf = await espelho.gerar(conn, eid, cpf=CPF_A, competencia="2026-11", assinador=assinador)
    resumo = await conn.fetchval("SELECT resumo->>'banco_saldo' FROM ptrp_espelhos WHERE id=$1", nov_id)
    assert int(resumo) == 90
    assert b"Espelho de Ponto Eletr" in pdf and b"Ana Concei" in pdf and b"CERTIFICADO DE TESTE" in pdf
    assert len(PdfFileReader(BytesIO(pdf)).embedded_signatures) == 1
    _, v2, _ = await espelho.gerar(conn, eid, cpf=CPF_A, competencia="2026-11", assinador=assinador)
    assert v2 == 2
    with pytest.raises(registro.Recusado, match="o que está errado"):
        await espelho.registrar_aceite(conn, eid, espelho_id=nov_id, cpf=CPF_A, decisao="contestado")
    await espelho.registrar_aceite(conn, eid, espelho_id=nov_id, cpf=CPF_A, decisao="aceito")
    with pytest.raises(registro.Recusado, match="já se manifestou"):
        await espelho.registrar_aceite(conn, eid, espelho_id=nov_id, cpf=CPF_A, decisao="aceito")
    with pytest.raises(registro.Recusado):
        await espelho.registrar_aceite(conn, eid, espelho_id=out_id, cpf=CPF_B, decisao="aceito")
    with pytest.raises(asyncpg.RaiseError, match="imutável"):
        await conn.execute("UPDATE ptrp_espelhos SET versao=9")


def _p7s_confere(conteudo: bytes, p7s: bytes, cert_pem: bytes) -> None:
    si = cms.ContentInfo.load(p7s)["content"]["signer_infos"][0]
    attrs = {a["type"].native: a["values"][0] for a in si["signed_attrs"]}
    assert attrs["message_digest"].native == hashlib.sha256(conteudo).digest()
    x509.load_pem_x509_certificate(cert_pem).public_key().verify(
        si["signature"].native, b"\x31" + si["signed_attrs"].dump()[1:], padding.PKCS1v15(), hashes.SHA256())


async def test_aej(conn, base, monkeypatch, assinador, certificado):
    eid = base
    await dados.cadastrar_matricula(conn, eid, cpf=CPF_A, matricula_esocial="MAT-001")
    dup = await bate(conn, eid, monkeypatch, CPF_A, SEG, "08:00", "08:01", "12:00", "13:00")
    await dados.incluir_marcacao(conn, eid, cpf=CPF_A, momento=local(SEG, "17:00"), motivo="saída esquecida",
                                 autor_cpf=CPF_RESP)
    await dados.desconsiderar_marcacao(conn, eid, registro_id=dup[1], motivo="batida em duplicidade", autor_cpf=CPF_RESP)
    arq = await aej.gerar(conn, eid, inicio=SEG, fim=SEG + timedelta(days=6), numero_inpi="512026001234",
                          desenvolvedor=DEV, assinador=assinador)
    assert arq.conteudo.endswith(b"\r\n") and b"\r\n\r\n" not in arq.conteudo
    linhas = arq.conteudo.decode("iso-8859-1").split("\r\n")[:-1]
    assert linhas[0].split("|") == ["01", "1", "11222333000181", "", "", "Colégio Aurora Ltda", "2026-11-02",
                                    "2026-11-08", linhas[0].split("|")[8], "002"]
    assert "02|1|3|512026001234" in linhas
    assert "03|1|39053344705|Ana Conceição" in linhas and "03|2|11144477735|Bruno Souza" in linhas
    assert "04|COM|480|0800|1200|1300|1700" in linhas
    l05 = [l for l in linhas if l.startswith("05|1|2026-11-02")]
    assert l05 == [
        "05|1|2026-11-02T08:00:00-0400|1|E|001|O|COM|",
        "05|1|2026-11-02T08:01:00-0400|1|D|001|O||batida em duplicidade",
        "05|1|2026-11-02T12:00:00-0400|1|S|001|O||",
        "05|1|2026-11-02T13:00:00-0400|1|E|002|O||",
        "05|1|2026-11-02T17:00:00-0400||S|002|I||saída esquecida",
    ]
    assert "06|1|MAT-001" in linhas
    assert "07|1|1|2026-11-08||" in linhas          # DSR
    assert "07|2|2|2026-11-02||" in linhas          # falta do Bruno
    assert linhas[-3].startswith("08|Pressente Ponto - Tratamento|0.1.0|1|11222333000181|FPM Tech Solutions Ltda|")
    contagem = [sum(1 for l in linhas if l.startswith(f"{t:02d}|")) for t in range(1, 9)]
    assert linhas[-2] == "99|" + "|".join(map(str, contagem))
    assert linhas[-1] == f.LINHA_DA_ASSINATURA
    for l in linhas[:-1]:
        assert not l.endswith("|") or l.startswith(("05|", "07|"))  # vazio no fim só nos campos opcionais
    _p7s_confere(arq.conteudo, arq.assinatura, certificado[1].read_bytes())
