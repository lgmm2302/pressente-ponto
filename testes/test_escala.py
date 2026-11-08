"""A escala 5x2: o planejador propõe dentro da regra e o validador pega o que sai dela."""
from datetime import date, timedelta

import pytest

from ppgestao.escala import (FOLGA, Cobertura, Escala, Pessoa, Premissas, RegrasDaEscala, SemFolga, Turno, cobertura,
                             demanda_pelo_movimento, planejar, validar)

SEG = date(2026, 11, 16)  # segunda-feira
FIM = SEG + timedelta(days=27)  # quatro semanas
TURNOS = [Turno("A", "0900", "1800", 60, "Abertura"), Turno("F", "1300", "2200", 60, "Fechamento"),
          Turno("M", "1100", "2000", 60, "Intermediário")]
#: o grupo «q» folga um domingo a cada dois; o «padrao», um a cada três
REGRAS = RegrasDaEscala(domingo=(("padrao", 2), ("q", 1)))
PREMISSAS = Premissas(coberturas=[Cobertura(2, "2000", "2200", texto="2 no fechamento")],
                      sem_folga=[SemFolga(dias=(5,), texto="sábado ninguém folga")])


def movimento():
    """Vendas médias por hora: manhã fraca, pico no fim da tarde, sábado forte."""
    m = {}
    for d in range(7):
        for h in range(9, 22):
            base = 300 if h < 12 else 600 if h < 17 else 1100
            m[(d, h)] = base * (1.6 if d == 5 else 0.7 if d in (1, 2) else 1.0)
    return m


DEMANDA = demanda_pelo_movimento(movimento(), por_pessoa_hora=450, minimo=1)
PESSOAS = [Pessoa("ana", "Ana", "q"), Pessoa("bia", "Bia", "q"), Pessoa("carla", "Carla", "q", sair_ate=((1, "1800"),)),
           Pessoa("davi", "Davi"), Pessoa("eliza", "Eliza", "q"), Pessoa("fer", "Fernanda", "q"),
           Pessoa("gabi", "Gabriel"), Pessoa("helena", "Helena", "q")]


def plano():
    return planejar(inicio=SEG, fim=FIM, pessoas=PESSOAS, turnos=TURNOS, demanda=DEMANDA, premissas=PREMISSAS, regras=REGRAS)


def bloqueios(e):
    return [p for p in validar(e, pessoas=PESSOAS, turnos=TURNOS, demanda=DEMANDA, premissas=PREMISSAS, regras=REGRAS)
            if p.nivel == "bloqueia"]


def test_demanda_pelo_movimento():
    d = demanda_pelo_movimento({(5, 19): 1760.0, (1, 9): 100.0}, por_pessoa_hora=450, minimo=1, abertura=[9, 19])
    assert d[(5, 19)] == 4 and d[(1, 9)] == 1 and d[(0, 9)] == 1  # arredonda para cima; sem movimento, o mínimo
    with pytest.raises(ValueError):
        demanda_pelo_movimento({}, por_pessoa_hora=0)


def test_turno():
    assert TURNOS[0].duracao == 480 and TURNOS[1].horas_cobertas() == set(range(13, 22))
    noite = Turno("N", "2200", "0500", 60)
    assert noite.duracao == 360 and noite.horas_cobertas() == {22, 23, 0, 1, 2, 3, 4}
    with pytest.raises(ValueError):
        Turno("X", "0900", "0930", 60)


def test_a_proposta_cumpre_a_regra():
    e = plano()
    assert bloqueios(e) == []
    for p in PESSOAS:
        for s in range(4):
            semana = [SEG + timedelta(days=7 * s + i) for i in range(7)]
            assert sum(1 for d in semana if e.dias[p.id][d] == FOLGA) == 2, (p.nome, s)  # 5x2
            assert e.dias[p.id][semana[5]] != FOLGA  # sábado ninguém folga


def test_rodizio_de_domingo_por_grupo():
    e = plano()
    domingos = [SEG + timedelta(days=6 + 7 * s) for s in range(4)]
    for p in PESSOAS:
        seguidos = maior = 0
        for d in domingos:
            seguidos = 0 if e.dias[p.id][d] == FOLGA else seguidos + 1
            maior = max(maior, seguidos)
        assert maior <= REGRAS.domingos_maximos(p.grupo_domingo), p.nome


def test_dois_no_fechamento_e_interjornada():
    e = plano()
    cob = cobertura(e, TURNOS, DEMANDA, PREMISSAS)
    for d, horas in cob.items():
        assert horas[20][0] >= 2 and horas[21][0] >= 2, d
    # ninguém fecha às 22h e abre às 9h (11 h exatas passam; menos que isso, não)
    assert not [p for p in validar(e, pessoas=PESSOAS, turnos=TURNOS, demanda=DEMANDA, premissas=PREMISSAS, regras=REGRAS)
                if p.tipo == "interjornada"]


def test_restricao_da_pessoa():
    e = plano()
    for d, c in e.dias["carla"].items():
        if d.weekday() == 1:
            assert c in (FOLGA, "A"), d  # na terça sai até 18h


def test_e_deterministica():
    assert plano().dias == plano().dias


def test_validador_pega_o_que_foi_mexido_a_mao():
    e = plano()
    dias = {k: dict(v) for k, v in e.dias.items()}
    sab = SEG + timedelta(days=5)
    semana = [SEG + timedelta(days=i) for i in range(7)]
    # a Ana perde as folgas da 1ª semana: vira 7 dias, sem 5x2, acima de 42 h
    for d in semana:
        dias["ana"][d] = "A"
    # o Davi fecha na sexta e abre no sábado: 11 h — passa; com um turno de 7h, não
    dias["davi"][sab - timedelta(days=1)] = "F"
    cedo = Turno("C", "0700", "1600", 60)
    dias["davi"][sab] = "C"
    # a Bia folga no sábado (premissa) e a Carla fecha na terça (precisa sair às 18h)
    dias["bia"][sab] = FOLGA
    dias["carla"][SEG + timedelta(days=1)] = "F"
    m = Escala(e.inicio, e.fim, dias)
    probs = validar(m, pessoas=PESSOAS, turnos=TURNOS + [cedo], demanda=DEMANDA, premissas=PREMISSAS, regras=REGRAS)
    tipos = {(p.tipo, p.pessoa) for p in probs}
    assert ("folgas_da_semana", "ana") in tipos and ("horas_da_semana", "ana") in tipos
    assert ("dias_seguidos", "ana") in tipos or ("folgas_da_semana", "ana") in tipos
    assert ("interjornada", "davi") in tipos
    assert ("restricao_da_pessoa", "carla") in tipos
    assert any(p.tipo == "dia_sem_folga" and p.data == sab for p in probs)


def test_validador_rodizio_de_domingo():
    e = plano()
    dias = {k: dict(v) for k, v in e.dias.items()}
    for s in range(4):
        dias["ana"][SEG + timedelta(days=6 + 7 * s)] = "A"
    probs = validar(Escala(e.inicio, e.fim, dias), pessoas=PESSOAS, turnos=TURNOS, demanda=DEMANDA,
                    premissas=PREMISSAS, regras=REGRAS)
    assert any(p.tipo == "rodizio_de_domingo" and p.pessoa == "ana" for p in probs)
    # o texto para o gestor fala em rodízio, sem regra de lei nem de convenção
    assert all("386" not in p.texto and "CCT" not in p.texto and "convenção" not in p.texto for p in probs)


def test_reforco_numa_data_aparece_na_cobertura():
    natal = date(2026, 12, 9)
    prem = Premissas(coberturas=PREMISSAS.coberturas + [Cobertura(6, "1800", "2200", datas=(natal,), texto="reforço")],
                     sem_folga=PREMISSAS.sem_folga)
    e = planejar(inicio=SEG, fim=FIM, pessoas=PESSOAS, turnos=TURNOS, demanda=DEMANDA, premissas=prem, regras=REGRAS)
    cob = cobertura(e, TURNOS, DEMANDA, prem)
    assert cob[natal][19][1] == 6
    probs = validar(e, pessoas=PESSOAS, turnos=TURNOS, demanda=DEMANDA, premissas=prem, regras=REGRAS)
    if cob[natal][19][0] < 6:
        assert any(p.tipo == "cobertura" and p.data == natal for p in probs)


def test_continuidade_com_o_periodo_anterior():
    # o Davi vem de 6 dias seguidos e de 2 domingos trabalhados: folga no 1º dia e no 1º domingo
    davi = Pessoa("davi", "Davi", dias_seguidos=6, domingos_seguidos=2)
    pessoas = [davi if p.id == "davi" else p for p in PESSOAS]
    e = planejar(inicio=SEG, fim=FIM, pessoas=pessoas, turnos=TURNOS, demanda=DEMANDA, premissas=PREMISSAS, regras=REGRAS)
    assert e.dias["davi"][SEG] == FOLGA and e.dias["davi"][SEG + timedelta(days=6)] == FOLGA


def test_pede_o_basico():
    with pytest.raises(ValueError):
        planejar(inicio=SEG, fim=FIM, pessoas=[], turnos=TURNOS, demanda=DEMANDA)
    with pytest.raises(ValueError):
        planejar(inicio=FIM, fim=SEG, pessoas=PESSOAS, turnos=TURNOS, demanda=DEMANDA)


def test_dia_sem_folga_vale_mesmo_com_pouco_movimento():
    plano_fraco = demanda_pelo_movimento({}, por_pessoa_hora=450, minimo=1)
    e = planejar(inicio=SEG, fim=FIM, pessoas=PESSOAS, turnos=TURNOS, demanda=plano_fraco, premissas=PREMISSAS, regras=REGRAS)
    assert all(e.dias[p.id][d] != FOLGA for p in PESSOAS for d in e.dias[p.id] if d.weekday() == 5)


def test_rodizio_vence_o_domingo_cheio():
    # domingo com movimento para 7 das 8 pessoas: o rodízio ainda assim é cumprido, e o planejador avisa
    cheio = dict(DEMANDA)
    for h in range(9, 22):
        cheio[(6, h)] = 7
    e = planejar(inicio=SEG, fim=FIM, pessoas=PESSOAS, turnos=TURNOS, demanda=cheio, premissas=PREMISSAS, regras=REGRAS)
    domingos = [SEG + timedelta(days=6 + 7 * s) for s in range(4)]
    for p in PESSOAS:
        seguidos = 0
        for d in domingos:
            seguidos = 0 if e.dias[p.id][d] == FOLGA else seguidos + 1
            assert seguidos <= REGRAS.domingos_maximos(p.grupo_domingo), (p.nome, d)
    assert any("rodízio pede" in x for x in e.explicacoes)


def test_planejador_respeita_a_interjornada():
    cedo = Turno("C", "0700", "1600", 60)
    turnos = TURNOS + [cedo]
    dem = dict(DEMANDA)
    for d in range(7):
        dem[(d, 7)] = dem[(d, 8)] = 3
    e = planejar(inicio=SEG, fim=FIM, pessoas=PESSOAS, turnos=turnos, demanda=dem, premissas=PREMISSAS, regras=REGRAS)
    assert not [p for p in validar(e, pessoas=PESSOAS, turnos=turnos, demanda=dem, premissas=PREMISSAS, regras=REGRAS)
                if p.tipo == "interjornada"]
    assert any(c == "C" for dias in e.dias.values() for c in dias.values())


def test_validador_sete_dias_seguidos_mesmo_com_duas_folgas_por_semana():
    dias = {}
    for p in PESSOAS:
        dias[p.id] = {SEG + timedelta(days=i): "A" for i in range(28)}
    folgas_ana = [0, 1, 9, 10, 15, 16, 22, 23]  # sete seguidos: do dia 2 ao dia 8
    for i in folgas_ana:
        dias["ana"][SEG + timedelta(days=i)] = FOLGA
    probs = validar(Escala(SEG, FIM, dias), pessoas=PESSOAS, turnos=TURNOS, demanda=DEMANDA, regras=REGRAS)
    assert not any(p.tipo == "folgas_da_semana" and p.pessoa == "ana" for p in probs)
    assert any(p.tipo == "dias_seguidos" and p.pessoa == "ana" for p in probs)
