"""Fase 9: o pacote do INPI é reproduzível e a versão registrada não muda escondida."""
import hashlib
import io
import os
import zipfile
from datetime import datetime, timezone

import pytest

import pacote_inpi as P
from reppp import atestado, formato as f


@pytest.mark.parametrize("chave", list(P.PROGRAMAS))
def test_pacote_e_deterministico_e_completo(chave):
    prog = P.PROGRAMAS[chave]
    a, man_a = P.montar(prog)
    b, man_b = P.montar(prog)
    assert a == b and man_a == man_b  # mesmo arquivo, byte a byte
    nomes = zipfile.ZipFile(io.BytesIO(a)).namelist()
    assert nomes == [p.relative_to(P.RAIZ).as_posix() for p in prog.arquivos()]
    assert all(not n.endswith(".pyc") and "__pycache__" not in n for n in nomes)
    assert any(n.startswith("inpi/DOCUMENTACAO-TECNICA") for n in nomes) and "LICENCA.md" in nomes
    for linha in man_a.splitlines():
        h, nome = linha.split("  ", 1)
        dados = zipfile.ZipFile(io.BytesIO(a)).read(nome)
        assert hashlib.sha256(dados).hexdigest() == h and b"\r\n" not in dados
    assert len(P.sha512(a)) == 128 and P.sha512(a) == P.sha512(a).upper()


def test_rep_p_so_leva_o_rep_p():
    nomes = [p.relative_to(P.RAIZ).as_posix() for p in P.PROGRAMAS["rep-p"].arquivos()]
    assert all(n.startswith(("reppp/", "inpi/", "LICENCA")) for n in nomes)
    assert "reppp/esquema.sql" in nomes and "reppp/registro.py" in nomes


@pytest.mark.parametrize("chave", list(P.PROGRAMAS))
def test_versao_registrada_nao_muda_escondida(chave):
    """Se o pacote desta versão já foi gerado (e talvez registrado), o código
    precisa continuar gerando o MESMO hash. Mudou o código? Suba a versão."""
    if os.environ.get("PONTO_SEM_TRAVA_DE_VERSAO") == "1":
        pytest.skip("rodada de mutações: a trava da versão não entra")
    prog = P.PROGRAMAS[chave]
    gravado = P.pasta(prog) / "SHA512.txt"
    if not gravado.exists():
        pytest.skip(f"a versão {prog.versao} de {chave} ainda não foi empacotada para o INPI")
    esperado = [l.split(":", 1)[1].strip() for l in gravado.read_text(encoding="utf-8").splitlines()
                if l.startswith("Resumo digital")][0]
    atual = P.sha512(P.montar(prog)[0])
    assert atual == esperado, (
        f"O código de {chave} mudou depois do pacote do INPI da versão {prog.versao}. "
        f"Suba a VERSAO em {prog.pacote_python}/__init__.py e gere o pacote novo (python -m scripts.pacote_inpi {chave}).")


def test_atestado_preenchido_e_recusa_sem_quem_assina():
    emp = f.Empregador(1, "11222333000181", "Colégio Aurora & Cia Ltda")
    dev = atestado.Desenvolvedor("FPM Tech Solutions Ltda", 1, "11222333000181", "Fulano Representante", "Beltrano Técnico")
    pdf = atestado.montar(empresa=emp, desenvolvedor=dev, numero_inpi="BR512026001234-5", nome_do_programa="Pressente Ponto - REP-P",
                          certificado_titular="CN=Teste", certificado_sha256="AB:CD",
                          emitido_em=datetime(2026, 11, 8, tzinfo=timezone.utc))
    for trecho in (b"Atestado T", b"11.222.333/0001-81", b"BR512026001234-5", b"Fulano Representante",
                   b"Beltrano T", b"SHA-256", b"a partir de 1.0.0", b"Aurora &amp; Cia".replace(b"&amp;", b"&")):
        assert trecho in pdf, trecho
    with pytest.raises(ValueError, match="representante legal"):
        atestado.montar(empresa=emp, desenvolvedor=atestado.Desenvolvedor("X", 1, "1", "", "Y"), numero_inpi="",
                        nome_do_programa="P", certificado_titular="", certificado_sha256="",
                        emitido_em=datetime(2026, 11, 8, tzinfo=timezone.utc))
    sem_inpi = atestado.montar(empresa=emp, desenvolvedor=dev, numero_inpi="", nome_do_programa="P",
                               certificado_titular="", certificado_sha256="", emitido_em=datetime(2026, 11, 8))
    assert b"PENDENTE" in sem_inpi
