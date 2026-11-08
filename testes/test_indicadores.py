"""Indicadores, ranking e alertas: as definições do painel, sem número inventado."""
from datetime import date, timedelta

from ppgestao.banco import Lancamento, apurar_saldo
from ppgestao.escala import FOLGA, Escala
from ppgestao.indicadores import Cadastro, DiaDaPessoa as D, alertas, ferias_vencendo, indicadores, ranking

INI = date(2026, 11, 2)


def mes(n=20, atrasos=(), faltas=(), abonos=(), saidas=()):
    out = []
    for i in range(n):
        d = INI + timedelta(days=i)
        if i in faltas or i in abonos:
            out.append(D(d, 480, 0, abonado=i in abonos))
        else:
            out.append(D(d, 480, 480 - (30 if i in saidas else 0), atraso=15 if i in atrasos else 0,
                         saida_antes=30 if i in saidas else 0))
    out.append(D(INI + timedelta(days=n), 0))  # folga não conta
    return out


def test_indicadores():
    i = indicadores(mes(atrasos=(3, 7), faltas=(10,), abonos=(11,), saidas=(15,)))
    assert i.dias_previstos == 20 and i.faltas == 1 and i.atrasos == 2
    assert i.pontualidade == round(100 * 16 / 18, 1)       # 18 trabalhados, 2 com atraso
    assert i.assiduidade == 95.0                            # a falta abonada não conta contra
    assert i.cumprimento == round(100 * 17 / 18, 1)        # a saída antes não cumpre (o atraso compensado, sim)
    assert i.absenteismo == round(100 * (480 + 30) / (20 * 480), 1)
    assert i.nota == round(0.4 * i.pontualidade + 0.4 * i.assiduidade + 0.2 * i.cumprimento, 1)
    assert i.sem_atraso_seguidos == 10                      # dias trabalhados sem atraso desde o último atraso


def test_sem_dia_previsto_nao_ha_indicador():
    i = indicadores([D(INI, 0)])
    assert (i.pontualidade, i.assiduidade, i.nota) == (None, None, None)


def test_ranking():
    a = {"ana": indicadores(mes()), "bia": indicadores(mes(atrasos=(1, 2, 3))), "nova": indicadores(mes(n=3))}
    ant = {"bia": indicadores(mes(atrasos=(1,)))}
    r = ranking(a, {"ana": "Ana", "bia": "Bia", "nova": "Nova"}, ant)
    assert [(p.posicao, p.chave) for p in r] == [(1, "ana"), (2, "bia")]   # quem tem 3 dias não entra
    assert r[0].variacao is None and r[1].variacao < 0


def test_ferias():
    c = Cadastro("eliza", "Eliza", admissao=date(2024, 12, 13))
    f = ferias_vencendo(c, date(2026, 11, 8))
    assert f and f.prazo == date(2026, 12, 12) and f.nivel == "atencao"
    assert ferias_vencendo(Cadastro("x", "X", date(2024, 12, 13), ferias_gozadas=(date(2024, 12, 13),)), date(2026, 11, 8)) is None
    v = ferias_vencendo(c, date(2026, 12, 20))
    assert v.nivel == "urgente" and "dobro" in v.detalhe


def test_alertas():
    hoje = date(2026, 11, 8)
    cads = [Cadastro("carla", "Carla", date(2026, 1, 5), atestados_pendentes=((date(2026, 11, 7), date(2026, 11, 8)),)),
            Cadastro("nelma", "Nelma", date(2025, 3, 1), licencas=((date(2026, 7, 1), date(2026, 11, 30), "licença-maternidade"),)),
            Cadastro("ana", "Ana", date(2026, 2, 1)), Cadastro("davi", "Davi", date(2026, 2, 1))]
    saldo = apurar_saldo([Lancamento(date(2026, 5, 20), 360)], hoje=hoje, validade_meses=6)
    dias = {"ana": {hoje - timedelta(days=i): "A" for i in range(6)}}
    dias["ana"][hoje + timedelta(days=1)] = "A"
    esc = Escala(hoje - timedelta(days=5), hoje + timedelta(days=1), dias)
    al = alertas(hoje=hoje, cadastros=cads, escala=esc, saldos={"ana": saldo},
                 faltas_sem_justificativa={"davi": [date(2026, 11, 3), date(2026, 11, 5)]})
    tipos = [(a.tipo, a.pessoa) for a in al]
    assert ("atestado", "carla") in tipos and ("licenca", "nelma") in tipos and ("falta", "davi") in tipos
    assert ("banco", "ana") in tipos and ("dias_seguidos", "ana") in tipos
    assert al[0].nivel == "urgente" and al[-1].nivel == "info"
    dias["ana"][hoje + timedelta(days=1)] = FOLGA
    assert not [a for a in alertas(hoje=hoje, cadastros=cads, escala=Escala(esc.inicio, esc.fim, dias)) if a.tipo == "dias_seguidos"]
