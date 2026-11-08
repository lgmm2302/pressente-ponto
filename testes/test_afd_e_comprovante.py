"""AFD (Anexo V) e comprovante (arts. 79–80), com as assinaturas conferidas."""
import hashlib
from datetime import date, datetime, timedelta, timezone

import asyncpg
import pytest
from asn1crypto import cms
from asn1crypto import x509 as ax509
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import Encoding
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.validation import async_validate_pdf_signature
from pyhanko_certvalidator import ValidationContext
from io import BytesIO

from reppp import afd, assinatura, comprovante, formato as f, registro
from conftest import CPF_A, CPF_B

pytestmark = pytest.mark.asyncio
DEV = afd.Desenvolvedor(1, "11222333000181")
INPI = "512026001234"


def _verifica_cades(conteudo: bytes, p7s: bytes, cert_pem: bytes) -> None:
    ci = cms.ContentInfo.load(p7s)
    sd = ci["content"]
    assert sd["encap_content_info"]["content"].native is None, "assinatura precisa ser destacada"
    si = sd["signer_infos"][0]
    attrs = {a["type"].native: a["values"][0] for a in si["signed_attrs"]}
    assert attrs["message_digest"].native == hashlib.sha256(conteudo).digest()
    assert "signing_certificate_v2" in attrs, "CAdES-BES pede o atributo signing-certificate-v2"
    assinado = si["signed_attrs"].dump()
    assinado = b"\x31" + assinado[1:]
    cert = x509.load_pem_x509_certificate(cert_pem)
    cert.public_key().verify(si["signature"].native, assinado, padding.PKCS1v15(), hashes.SHA256())


async def test_afd_completo(conn, estab, assinador, certificado):
    eid, _ = estab
    for cpf in (CPF_A, CPF_B, CPF_A):
        await registro.marcar(conn, eid, cpf=cpf, coletor=1)
    hoje = registro.agora().astimezone().date()
    arq = await afd.gerar(conn, eid, inicio=hoje - timedelta(days=1), fim=hoje + timedelta(days=1),
                          numero_inpi=INPI, desenvolvedor=DEV, assinador=assinador)
    assert arq.nome == "AFD51202600123411222333000181REP_P.txt"
    assert arq.nome_da_assinatura == arq.nome + ".p7s"
    assert b"\r\n" in arq.conteudo and arq.conteudo.endswith(b"\r\n")
    assert b"\n\n" not in arq.conteudo and b"\r\n\r\n" not in arq.conteudo
    linhas = afd.ler(arq.conteudo)
    assert [l[9] for l in linhas[:-2]] == ["1", "2", "5", "5", "7", "7", "7"]
    assert linhas[0][189:206] == "00000512026001234"
    assert linhas[-2] == f.registro_9({2: 1, 5: 2, 7: 3})
    assert linhas[-1] == f.LINHA_DA_ASSINATURA
    nsrs = [int(l[:9]) for l in linhas[1:-2]]
    assert nsrs == sorted(nsrs) == list(range(1, 7))
    for l in linhas[:-2]:
        assert len(l) == f.TAMANHO_POR_TIPO[int(l[9])]
        if l[9] in "125":
            assert f.crc_confere(l)
    assert "Colégio Aurora".encode("iso-8859-1") in arq.conteudo  # ISO 8859-1, não UTF-8
    _verifica_cades(arq.conteudo, arq.assinatura, certificado[1].read_bytes())


async def test_afd_por_intervalo(conn, estab, assinador, monkeypatch):
    eid, _ = estab
    base = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)
    for d in range(4):
        monkeypatch.setattr(registro, "agora", lambda d=d: base + timedelta(days=d))
        await registro.marcar(conn, eid, cpf=CPF_A, coletor=1)
    arq = await afd.gerar(conn, eid, inicio=date(2026, 11, 4), fim=date(2026, 11, 5), numero_inpi=INPI,
                          desenvolvedor=DEV, assinador=assinador)
    marcas = [l for l in afd.ler(arq.conteudo) if len(l) > 9 and l[9] == "7" and not l.startswith("9999")]
    assert [l[10:20] for l in marcas] == ["2026-11-04", "2026-11-05"]
    with pytest.raises(registro.Recusado):
        await afd.gerar(conn, eid, inicio=date(2026, 11, 5), fim=date(2026, 11, 4), numero_inpi=INPI,
                        desenvolvedor=DEV, assinador=assinador)


async def test_afd_alterado_nao_bate_com_a_assinatura(conn, estab, assinador, certificado):
    eid, _ = estab
    await registro.marcar(conn, eid, cpf=CPF_A, coletor=1)
    hoje = registro.agora().astimezone().date()
    arq = await afd.gerar(conn, eid, inicio=hoje - timedelta(days=1), fim=hoje + timedelta(days=1),
                          numero_inpi=INPI, desenvolvedor=DEV, assinador=assinador)
    adulterado = arq.conteudo.replace(b"T", b"X", 1)
    with pytest.raises(AssertionError):
        _verifica_cades(adulterado, arq.assinatura, certificado[1].read_bytes())


async def test_comprovante_campos_e_pades(conn, estab, assinador, certificado):
    eid, _ = estab
    m = await registro.marcar(conn, eid, cpf=CPF_A, coletor=1)
    pdf = await comprovante.obter(conn, m.registro_id, assinador, INPI)
    for trecho in (comprovante.CABECALHO, f"{m.nsr:09d}", "390.533.447-05", "11.222.333/0001-81", INPI,
                   m.hash[:32], m.hash[32:], "CERTIFICADO DE TESTE"):
        assert trecho.encode("latin-1") in pdf, trecho
    r = PdfFileReader(BytesIO(pdf))
    assert len(r.embedded_signatures) == 1
    raiz = x509.load_pem_x509_certificate(certificado[1].read_bytes())
    vc = ValidationContext(trust_roots=[ax509.Certificate.load(raiz.public_bytes(Encoding.DER))])
    st = await async_validate_pdf_signature(r.embedded_signatures[0], signer_validation_context=vc)
    assert st.intact and st.valid
    assert "/ETSI.CAdES.detached" in str(r.embedded_signatures[0].sig_object.get("/SubFilter"))


async def test_comprovante_e_gravado_uma_vez_e_imutavel(conn, estab, assinador):
    eid, _ = estab
    m = await registro.marcar(conn, eid, cpf=CPF_A, coletor=1)
    a = await comprovante.obter(conn, m.registro_id, assinador, INPI)
    b = await comprovante.obter(conn, m.registro_id, assinador, INPI)
    assert a == b
    with pytest.raises(asyncpg.RaiseError, match="imutável"):
        await conn.execute("UPDATE comprovantes SET pdf='x'::bytea")
    with pytest.raises(asyncpg.RaiseError, match="imutável"):
        await conn.execute("DELETE FROM comprovantes")


async def test_comprovantes_das_ultimas_48_horas(conn, estab, monkeypatch):
    eid, _ = estab
    agora = datetime.now(timezone.utc)
    for horas in (60, 47, 1):
        monkeypatch.setattr(registro, "agora", lambda h=horas: agora - timedelta(hours=h))
        await registro.marcar(conn, eid, cpf=CPF_A, coletor=1)
    lista = await comprovante.ultimas_48_horas(conn, eid, CPF_A)
    assert len(lista) == 2


async def test_assinatura_cades_tem_os_atributos(assinador):
    p7s = await assinador.cades_destacada(b"conteudo")
    assert assinatura.e_destacada(p7s)
    assert {"content_type", "message_digest", "signing_certificate_v2"} <= assinatura.atributos_assinados(p7s)
