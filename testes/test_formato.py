"""O leiaute do Anexo V, campo a campo, sem banco."""
from datetime import date, datetime, timezone

import pytest

from reppp import formato as f

UTC = timezone.utc
EMP = f.Empregador(1, "11222333000181", "Colégio Aurora Ltda", None, "Rua das Acácias, 100")


def test_crc16_kermit_valor_de_conferencia():
    # Valor de conferência do CRC-16/KERMIT (CCITT-TRUE) citado no Anexo V.
    assert f.crc16_kermit(b"123456789") == 0x2189


def test_crc_confere_e_pega_alteracao():
    linha = f.com_crc("000000002" + "5" + "X" * 20)
    assert f.crc_confere(linha)
    adulterada = linha[:12] + ("Y" if linha[12] != "Y" else "Z") + linha[13:]
    assert not f.crc_confere(adulterada)


@pytest.mark.parametrize("cpf,ok", [("529.982.247-25", True), ("39053344705", True), ("11111111111", False),
                                    ("52998224724", False), ("123", False)])
def test_cpf(cpf, ok):
    assert f.cpf_valido(cpf) is ok


@pytest.mark.parametrize("cnpj,ok", [("11.222.333/0001-81", True), ("11222333000180", False),
                                     ("12.ABC.345/01DE-35", True), ("12ABC34501DE36", False),
                                     ("00000000000000", False)])
def test_cnpj_numerico_e_alfanumerico(cnpj, ok):
    assert f.cnpj_valido(cnpj) is ok


def test_data_e_hora_no_fuso_com_segundos_zerados():
    m = datetime(2026, 11, 7, 23, 43, 59, tzinfo=UTC)
    assert f.campo_dh(m, "America/Cuiaba") == "2026-11-07T19:43:00-0400"
    assert f.campo_dh(m, "America/Sao_Paulo") == "2026-11-07T20:43:00-0300"


def test_campo_alfanumerico_nao_corta_identificador():
    with pytest.raises(f.ErroDeFormato):
        f.campo_a("123456789012345", 14, "CNPJ")
    assert f.campo_a("Muito longo", 5, "nome", cortar=True) == "Muito"


def test_campo_numerico_com_zeros_e_recusa_letras():
    assert f.campo_n(7, 9, "NSR") == "000000007"
    with pytest.raises(f.ErroDeFormato):
        f.campo_n("12a", 9, "NSR")
    with pytest.raises(f.ErroDeFormato):
        f.campo_n(1234567890, 9, "NSR")


def test_fora_do_latin1_e_recusado():
    with pytest.raises(f.ErroDeFormato):
        f.campo_a("Ação 🙂", 20, "nome")


def test_registro_7_posicoes():
    m = datetime(2026, 11, 7, 11, 2, 30, tzinfo=UTC)
    g = datetime(2026, 11, 7, 11, 2, 31, tzinfo=UTC)
    linha, h = f.registro_7(nsr=42, marcado_em=m, gravado_em=g, fuso="America/Cuiaba", cpf="390.533.447-05",
                            coletor=1, offline=False, hash_anterior=None)
    assert len(linha) == 137
    assert linha[0:9] == "000000042"
    assert linha[9] == "7"
    assert linha[10:34] == "2026-11-07T07:02:00-0400"
    assert linha[34:46] == "039053344705"
    assert linha[46:70] == "2026-11-07T07:02:00-0400"
    assert linha[70:72] == "01"
    assert linha[72] == "0"
    assert linha[73:] == h and len(h) == 64


def test_hash_encadeado():
    m = datetime(2026, 11, 7, 11, 0, tzinfo=UTC)
    l1, h1 = f.registro_7(nsr=3, marcado_em=m, gravado_em=m, fuso="UTC", cpf="39053344705", coletor=2,
                          offline=False, hash_anterior=None)
    l2a, h2a = f.registro_7(nsr=4, marcado_em=m, gravado_em=m, fuso="UTC", cpf="39053344705", coletor=2,
                            offline=False, hash_anterior=h1)
    l2b, h2b = f.registro_7(nsr=4, marcado_em=m, gravado_em=m, fuso="UTC", cpf="39053344705", coletor=2,
                            offline=False, hash_anterior="0" * 64)
    assert h2a != h2b  # o hash depende do anterior
    assert f.hash_da_marcacao(l2a[:73], h1) == h2a


def test_offline_marca_1():
    m = datetime(2026, 11, 7, 11, 0, tzinfo=UTC)
    linha, _ = f.registro_7(nsr=1, marcado_em=m, gravado_em=m, fuso="UTC", cpf="39053344705", coletor=1,
                            offline=True, hash_anterior=None)
    assert linha[72] == "1"


def test_coletor_invalido():
    m = datetime(2026, 11, 7, 11, 0, tzinfo=UTC)
    with pytest.raises(f.ErroDeFormato):
        f.registro_7(nsr=1, marcado_em=m, gravado_em=m, fuso="UTC", cpf="39053344705", coletor=6,
                     offline=False, hash_anterior=None)


def test_registro_1_posicoes():
    g = datetime(2026, 11, 7, 23, 0, tzinfo=UTC)
    l = f.registro_1(empregador=EMP, numero_inpi="512026001234", inicio=date(2026, 11, 1),
                     fim=date(2026, 11, 7), gerado_em=g, fuso="America/Cuiaba", tipo_id_desenvolvedor=1,
                     id_desenvolvedor="11222333000181")
    assert len(l) == 302
    assert l[0:9] == "000000000" and l[9] == "1" and l[10] == "1"
    assert l[11:25] == "11222333000181"
    assert l[25:39] == " " * 14  # sem CNO/CAEPF
    assert l[39:189].rstrip() == "Colégio Aurora Ltda"
    assert l[189:206] == "00000512026001234"
    assert l[206:216] == "2026-11-01" and l[216:226] == "2026-11-07"
    assert l[226:250] == "2026-11-07T19:00:00-0400"
    assert l[250:253] == "004"
    assert l[253] == "1" and l[254:268] == "11222333000181"
    assert l[268:298] == " " * 30
    assert f.crc_confere(l)


def test_registro_2_5_6_9_tamanhos():
    g = datetime(2026, 11, 7, 23, 0, tzinfo=UTC)
    r2 = f.registro_2(nsr=1, gravado_em=g, fuso="UTC", cpf_responsavel="52998224725", empregador=EMP)
    r5 = f.registro_5(nsr=2, gravado_em=g, fuso="UTC", operacao="I", cpf="39053344705", nome="Ana Conceição",
                      cpf_responsavel="52998224725")
    r6 = f.registro_6(nsr=3, gravado_em=g, fuso="UTC", evento=7)
    r9 = f.registro_9({2: 1, 5: 1, 6: 1, 7: 12})
    assert (len(r2), len(r5), len(r6), len(r9)) == (331, 118, 36, 64)
    assert r2[34:48] == "00052998224725" and r2[48] == "1" and r2[227:327].rstrip() == "Rua das Acácias, 100"
    assert r5[34] == "I" and r5[35:47] == "039053344705" and r5[47:99].rstrip() == "Ana Conceição"
    assert r5[103:114] == "52998224725"
    assert f.crc_confere(r2) and f.crc_confere(r5)
    assert r6[34:36] == "07"
    assert r9 == "999999999" + "000000001" + "000000000" + "000000000" + "000000001" + "000000001" + "000000012" + "9"


def test_evento_de_rep_c_nao_entra():
    g = datetime(2026, 11, 7, 23, 0, tzinfo=UTC)
    with pytest.raises(f.ErroDeFormato):
        f.registro_6(nsr=1, gravado_em=g, fuso="UTC", evento=1)


def test_linha_da_assinatura_e_nome():
    assert len(f.LINHA_DA_ASSINATURA) == 100 and f.LINHA_DA_ASSINATURA.startswith("ASSINATURA_DIGITAL_EM_ARQUIVO_P7S ")
    assert f.nome_do_afd("51 2026 001234", "11222333000181") == "AFD51202600123411222333000181REP_P.txt"
