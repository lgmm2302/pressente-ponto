"""A apuração da jornada, caso a caso, contra a CLT e a jurisprudência."""
from datetime import date, datetime, timedelta

import pytest

from ptrp.apuracao import (AUS_BANCO, AUS_DSR, AUS_FALTA, BH_CREDITO, BH_DEBITO, DSR, FOLGA, Horario, Marca,
                           apurar, saldo_do_banco)
from ptrp.regras import Regras

COMERCIAL = Horario("COM", (("0800", "1200"), ("1300", "1700")))
NOITE = Horario("NOT", (("2200", "0200"), ("0300", "0600")))
SEG = date(2026, 11, 2)  # segunda-feira


def esc_semana(d: date):
    return DSR if d.weekday() == 6 else FOLGA if d.weekday() == 5 else COMERCIAL


def m(d: date, *horas: str, fonte="O") -> list[Marca]:
    out = []
    ultimo = None
    for h in horas:
        t = datetime.combine(d, datetime.strptime(h, "%H:%M").time())
        while ultimo and t <= ultimo:
            t += timedelta(days=1)
        out.append(Marca(t, fonte))
        ultimo = t
    return out


def um_dia(marcas, dia=SEG, escala=esc_semana, regras=None, **kw):
    r = regras or Regras()
    return apurar(inicio=dia, fim=dia, marcas=marcas, escala=escala, regras=lambda d: r, **kw).dias[0]


def test_dia_exato():
    d = um_dia(m(SEG, "08:00", "12:00", "13:00", "17:00"))
    assert (d.trabalhado, d.esperado, d.extra, d.debito, d.intervalo) == (480, 480, 0, 0, 60)
    assert d.exato and not d.alertas
    assert [(x.tipo, x.seq) for x in d.marcas] == [("E", 1), ("S", 1), ("E", 2), ("S", 2)]


def test_tolerancia_de_5_por_marcacao_e_10_no_dia():
    d = um_dia(m(SEG, "07:57", "12:02", "13:01", "17:03"))
    assert d.tolerado and d.extra == 0 and d.debito == 0
    # Uma marcação passou de 5 min: conta TUDO (Súmula 366).
    d = um_dia(m(SEG, "07:54", "12:02", "13:01", "17:03"))
    assert not d.tolerado and d.extra == 10
    # Cada uma dentro de 5, mas 12 no dia: conta tudo.
    d = um_dia(m(SEG, "07:57", "12:03", "12:57", "17:03"))
    assert d.extra == 12 and not d.tolerado


def test_hora_extra_e_limite_de_2h():
    d = um_dia(m(SEG, "08:00", "12:00", "13:00", "19:00"))
    assert d.extra == 120 and not any("CLT art. 59" in a for a in d.alertas)
    d = um_dia(m(SEG, "08:00", "12:00", "13:00", "20:00"))
    assert d.extra == 180 and any("CLT art. 59" in a for a in d.alertas)


def test_atraso_vira_debito():
    d = um_dia(m(SEG, "09:00", "12:00", "13:00", "17:00"))
    assert d.debito == 60 and d.extra == 0


def test_falta_e_abono():
    d = um_dia([])
    assert d.falta and d.debito == 480 and (AUS_FALTA, 0, 0) in d.ausencias
    d = um_dia([], abonos={SEG: "atestado médico"})
    assert not d.falta and d.debito == 0 and d.abono == "atestado médico"


def test_dsr_folga_e_feriado_trabalhados():
    dom, sab = SEG + timedelta(days=6), SEG + timedelta(days=5)
    assert um_dia(m(dom, "08:00", "12:00"), dia=dom).extra_descanso_feriado == 240
    assert um_dia(m(sab, "08:00", "12:00"), dia=sab).extra == 240
    d = um_dia(m(SEG, "08:00", "12:00"), feriados={SEG})
    assert d.situacao == "feriado" and d.extra_descanso_feriado == 240 and d.debito == 0
    d = um_dia([], dia=dom)
    assert (AUS_DSR, 0, 0) in d.ausencias and not d.falta


def test_noturno_com_hora_reduzida_e_prorrogacao():
    esc = lambda d: NOITE
    d = um_dia(m(SEG, "22:00", "02:00", "03:00", "06:00"), escala=esc)
    assert d.trabalhado == 420
    assert d.noturno == 420  # 22–02, 03–05 e a prorrogação 05–06
    assert d.computado == 480  # 420 × 60 / 52,5
    assert d.esperado == 480 and d.extra == 0 and d.debito == 0
    sem = Regras(prorrogacao_noturna=False)
    d2 = um_dia(m(SEG, "22:00", "02:00", "03:00", "06:00"), escala=esc, regras=sem)
    assert d2.noturno == 360 and d2.computado == 471


def test_jornada_que_passa_da_meia_noite_fica_no_dia_em_que_comecou():
    r = apurar(inicio=SEG, fim=SEG + timedelta(days=1), marcas=m(SEG, "22:00", "02:00", "03:00", "06:00"),
               escala=lambda d: NOITE, regras=lambda d: Regras())
    assert r.dias[0].trabalhado == 420 and r.dias[1].trabalhado == 0 and r.dias[1].falta


def test_duracao_do_horario():
    assert COMERCIAL.duracao(Regras()) == 480
    assert NOITE.duracao(Regras()) == 480
    assert NOITE.duracao(Regras(hora_noturna_reduzida=False)) == 420


def test_variacao_infima_do_intervalo_nao_gera_supressao():
    # TST, IRR Tema 14: até 5 min no total.
    d = um_dia(m(SEG, "08:00", "12:03", "13:00", "17:00"))
    assert d.intervalo == 57 and d.intervalo_suprimido == 0 and not any("art. 71" in a for a in d.alertas)
    d = um_dia(m(SEG, "08:00", "12:06", "13:00", "17:00"))
    assert d.intervalo_suprimido == 6


def test_limite_de_extra_em_descanso_conta_alem_de_8h():
    dom = SEG + timedelta(days=6)
    assert not any("CLT art. 59" in a for a in um_dia(m(dom, "08:00", "12:00"), dia=dom).alertas)
    d = um_dia(m(dom, "08:00", "12:00", "13:00", "19:30"), dia=dom)
    assert any("CLT art. 59" in a for a in d.alertas)


def test_intervalo_suprimido_e_longo():
    d = um_dia(m(SEG, "08:00", "17:00"))
    assert d.intervalo_suprimido == 60 and any("CLT art. 71" in a for a in d.alertas)
    d = um_dia(m(SEG, "08:00", "15:00"))  # 7 h, sem pausa: mínimo de 1 h (passou de 6 h)
    assert d.intervalo_suprimido == 60
    d = um_dia(m(SEG, "08:00", "13:00"))  # 5 h: mínimo de 15 min
    assert d.intervalo_suprimido == 15
    d = um_dia(m(SEG, "08:00", "10:00", "13:00", "17:00"))
    assert any("acima de 120" in a for a in d.alertas)


def test_interjornada():
    ter = SEG + timedelta(days=1)
    marcas = m(SEG, "08:00", "12:00", "13:00", "23:00") + m(ter, "08:00", "12:00", "13:00", "17:00")
    r = apurar(inicio=SEG, fim=ter, marcas=marcas, escala=esc_semana, regras=lambda d: Regras())
    assert r.dias[1].interjornada_faltante == 120
    assert any("CLT art. 66" in a for a in r.dias[1].alertas)


def test_marcacao_impar():
    d = um_dia(m(SEG, "08:00", "12:00", "13:00"))
    assert any("ímpar" in a for a in d.alertas) and d.trabalhado == 240


def test_ponto_britanico():
    marcas = []
    for i in range(5):
        marcas += m(SEG + timedelta(days=i), "08:00", "12:00", "13:00", "17:00")
    r = apurar(inicio=SEG, fim=SEG + timedelta(days=4), marcas=marcas, escala=esc_semana, regras=lambda d: Regras())
    assert all(any("Súmula 338" in a for a in d.alertas) for d in r.dias)
    r = apurar(inicio=SEG, fim=SEG + timedelta(days=3), marcas=marcas[:16], escala=esc_semana,
               regras=lambda d: Regras())
    assert not any(any("Súmula 338" in a for a in d.alertas) for d in r.dias)


def test_banco_de_horas():
    rb = Regras(banco_de_horas_meses=6)
    ter = SEG + timedelta(days=1)
    marcas = m(SEG, "08:00", "12:00", "13:00", "18:00") + m(ter, "08:30", "12:00", "13:00", "17:00")
    r = apurar(inicio=SEG, fim=ter, marcas=marcas, escala=esc_semana, regras=lambda d: rb)
    assert r.dias[0].extra == 0 and r.dias[0].banco_credito == 60 and (AUS_BANCO, 60, BH_CREDITO) in r.dias[0].ausencias
    assert r.dias[1].debito == 0 and r.dias[1].banco_debito == 30 and (AUS_BANCO, 30, BH_DEBITO) in r.dias[1].ausencias
    assert saldo_do_banco(r, anterior=15) == 45


def test_intervalo_pre_assinalado():
    h = Horario("PRE", (("0800", "1200"), ("1300", "1700")), intervalo_pre_assinalado=True)
    d = um_dia(m(SEG, "08:00", "17:00"), escala=lambda x: h)
    assert [x.marca.fonte for x in d.marcas] == ["O", "P", "P", "O"]
    assert d.trabalhado == 480 and d.intervalo == 60 and d.intervalo_suprimido == 0


def test_marcacao_desconsiderada_nao_conta_mas_aparece():
    marcas = m(SEG, "08:00", "12:00", "13:00", "17:00")
    marcas.insert(1, Marca(datetime(2026, 11, 2, 8, 1), desconsiderada=True, motivo="batida em duplicidade"))
    d = um_dia(marcas)
    assert d.trabalhado == 480 and [x.tipo for x in d.marcas] == ["E", "D", "S", "E", "S"]


def test_regras_que_ferem_a_lei_sao_recusadas():
    with pytest.raises(ValueError):
        Regras(adicional_extra=40)
    with pytest.raises(ValueError):
        Regras(adicional_noturno=10)
    with pytest.raises(ValueError):
        Regras.de_dict({"inventada": 1})
    with pytest.raises(ValueError):
        Horario("X", ())
