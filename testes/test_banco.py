"""O banco de horas: vencimento, ordem de abatimento e sugestão de compensação sem descobrir a loja."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from ppgestao.banco import Lancamento as L, apurar_saldo, custo, dur, somar_meses, sugerir_compensacoes
from ppgestao.escala import FOLGA, Escala, Turno, cobertura, demanda_pelo_movimento

A = Turno("A", "0900", "1800", 60)
F = Turno("F", "1300", "2200", 60)


def test_somar_meses_e_dur():
    assert somar_meses(date(2026, 8, 31), 6) == date(2027, 2, 28) and somar_meses(date(2026, 1, 15), 12) == date(2027, 1, 15)
    assert dur(90) == "1h30" and dur(180) == "3h" and dur(45) == "45 min"


def test_credito_vence_e_vira_hora_extra():
    s = apurar_saldo([L(date(2026, 5, 10), 120), L(date(2026, 9, 1), 60)], hoje=date(2026, 11, 15), validade_meses=6)
    assert s.vencido == 120 and s.saldo == 60 and [l.vence for l in s.lotes] == [date(2027, 3, 1)]


def test_debito_abate_o_mais_antigo_primeiro():
    s = apurar_saldo([L(date(2026, 6, 1), 120), L(date(2026, 9, 1), 120), L(date(2026, 10, 1), -150)],
                     hoje=date(2026, 11, 1), validade_meses=6)
    assert s.saldo == 90 and [(l.origem, l.restante) for l in s.lotes] == [(date(2026, 9, 1), 90)]
    assert s.vence_ate(date(2026, 12, 1)) == 0 and s.vence_ate(date(2027, 3, 1)) == 90


def test_debito_sem_credito_fica_em_aberto_e_o_credito_seguinte_paga():
    s = apurar_saldo([L(date(2026, 10, 1), -60), L(date(2026, 10, 5), 90)], hoje=date(2026, 11, 1), validade_meses=6)
    assert s.debito == 0 and s.saldo == 30
    s2 = apurar_saldo([L(date(2026, 10, 1), -60)], hoje=date(2026, 11, 1), validade_meses=1)
    assert s2.saldo == -60 and s2.debito == 60


def test_validade_so_as_da_lei():
    with pytest.raises(ValueError):
        apurar_saldo([], hoje=date(2026, 1, 1), validade_meses=3)


def test_custo():
    assert custo(360, Decimal("12.50")) == Decimal("112.50") and custo(60, Decimal("10"), 100) == Decimal("20.00")


def _escala():
    seg = date(2026, 11, 16)
    dias = {"eliza": {}, "ana": {}, "bia": {}}
    for i in range(7):
        d = seg + timedelta(days=i)
        dias["eliza"][d] = "A" if i < 5 else FOLGA
        dias["ana"][d] = "A" if i < 5 else FOLGA
        dias["bia"][d] = "F" if i < 5 else FOLGA
    return seg, Escala(seg, seg + timedelta(days=6), dias)


def test_sugestao_so_onde_sobra_gente():
    seg, e = _escala()
    # manhã fraca (1 pessoa basta), tarde e noite pedem 2 até 18h e 1 depois
    mov = {(d, h): (300.0 if h < 13 else 700.0 if h < 18 else 300.0) for d in range(7) for h in range(9, 22)}
    dem = demanda_pelo_movimento(mov, por_pessoa_hora=400, minimo=1)
    saldo = apurar_saldo([L(date(2026, 6, 1), 360)], hoje=seg, validade_meses=6)  # 6 h vencem em 01/12
    sug = sugerir_compensacoes(saldos={"eliza": saldo}, nomes={"eliza": "Eliza"}, escala=e, turnos=[A, F], demanda=dem,
                               ate=date(2026, 12, 1), bloqueadas=[seg + timedelta(days=1)])
    assert sum(s.minutos for s in sug) == 360
    assert all(s.como == "entra depois" for s in sug)  # só a manhã sobra: entra mais tarde
    assert all(s.data != seg + timedelta(days=1) for s in sug)  # dia bloqueado
    # e a loja continua coberta: aplicando as sugestões, nenhuma hora fica abaixo do necessário
    for s in sug:
        assert s.minutos <= 4 * 60
    cob = cobertura(e, [A, F], dem)
    for s in sug:
        for h in range(9, 9 + s.minutos // 60):
            tem, prec = cob[s.data][h]
            assert tem - 1 >= prec


def test_sem_sobra_nao_sugere():
    seg, e = _escala()
    dem = {(d, h): 3 for d in range(7) for h in range(9, 22)}
    saldo = apurar_saldo([L(date(2026, 6, 1), 360)], hoje=seg, validade_meses=6)
    assert sugerir_compensacoes(saldos={"eliza": saldo}, nomes={}, escala=e, turnos=[A, F], demanda=dem,
                                ate=date(2026, 12, 1)) == []


def test_folga_inteira_quando_o_dia_todo_sobra():
    seg, e = _escala()
    dem = {(d, h): 1 for d in range(7) for h in range(9, 22)}
    saldo = apurar_saldo([L(date(2026, 6, 1), 480)], hoje=seg, validade_meses=6)
    sug = sugerir_compensacoes(saldos={"eliza": saldo}, nomes={"eliza": "Eliza"}, escala=e, turnos=[A, F], demanda=dem,
                               ate=date(2026, 12, 1))
    assert [(s.como, s.minutos) for s in sug] == [("folga", 480)]
