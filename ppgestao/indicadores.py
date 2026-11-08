"""Indicadores, rankings e alertas do painel — cálculo puro.

Entra: o dia de cada pessoa (o que a apuração do `ptrp` ou o ponto do sistema
integrado já calculou: previsto, trabalhado, atraso, falta, abono), os
cadastros (admissão, férias, licenças, atestados) e a escala. Sai: os números
do painel, o ranking e os alertas, cada um com o prazo.

Definições (as mesmas em todo lugar — o painel, a tela da pessoa e a exportação):
  · pontualidade  = dias trabalhados sem atraso ÷ dias trabalhados;
  · assiduidade   = dias previstos com presença (ou ausência abonada) ÷ dias previstos;
  · cumprimento   = dias trabalhados com a jornada cumprida (horas e sem sair antes) ÷ dias trabalhados;
  · absenteísmo   = minutos previstos não trabalhados sem abono ÷ minutos previstos;
  · nota          = 40% pontualidade + 40% assiduidade + 20% cumprimento.
Sem dia previsto não há indicador (None), nunca um zero inventado.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Optional

from .banco import Saldo, dur, somar_meses
from .escala import FOLGA, Escala


@dataclass(frozen=True)
class DiaDaPessoa:
    data: date
    previsto: int            # minutos previstos (0 = folga)
    trabalhado: int = 0
    atraso: int = 0          # minutos além da tolerância
    saida_antes: int = 0
    abonado: bool = False    # atestado, abono, férias, licença
    extra: int = 0


@dataclass
class Indicadores:
    pontualidade: Optional[float]
    assiduidade: Optional[float]
    cumprimento: Optional[float]
    absenteismo: Optional[float]
    nota: Optional[float]
    dias_previstos: int
    faltas: int
    atrasos: int
    extra: int
    sem_atraso_seguidos: int


def _pct(a: int, b: int) -> Optional[float]:
    return round(100 * a / b, 1) if b else None


def indicadores(dias: Iterable[DiaDaPessoa]) -> Indicadores:
    dias = sorted(dias, key=lambda d: d.data)
    previstos = [d for d in dias if d.previsto > 0]
    trabalhados = [d for d in previstos if d.trabalhado > 0]
    faltas = [d for d in previstos if d.trabalhado == 0 and not d.abonado]
    sem_atraso = [d for d in trabalhados if d.atraso == 0]
    cumpridos = [d for d in trabalhados if d.saida_antes == 0 and d.trabalhado >= d.previsto - 10]
    perdido = sum(max(0, d.previsto - d.trabalhado) for d in previstos if not d.abonado)
    p, a, c = _pct(len(sem_atraso), len(trabalhados)), _pct(len(previstos) - len(faltas), len(previstos)), \
        _pct(len(cumpridos), len(trabalhados))
    nota = round(0.4 * p + 0.4 * a + 0.2 * c, 1) if None not in (p, a, c) else None
    seguidos = 0
    for d in reversed(trabalhados):
        if d.atraso:
            break
        seguidos += 1
    return Indicadores(p, a, c, _pct(perdido, sum(d.previsto for d in previstos)), nota, len(previstos), len(faltas),
                       len(trabalhados) - len(sem_atraso), sum(d.extra for d in dias), seguidos)


@dataclass(frozen=True)
class Posicao:
    posicao: int
    chave: str
    nome: str
    nota: float
    variacao: Optional[float]


def ranking(atual: dict[str, Indicadores], nomes: dict[str, str],
            anterior: Optional[dict[str, Indicadores]] = None, minimo_de_dias: int = 10) -> list[Posicao]:
    """Do maior para o menor. Quem tem poucos dias previstos (férias, admissão recente) não entra:
    um mês de 3 dias perfeitos não deve passar na frente de quem trabalhou o mês inteiro."""
    validos = [(k, i) for k, i in atual.items() if i.nota is not None and i.dias_previstos >= minimo_de_dias]
    validos.sort(key=lambda x: (-x[1].nota, -(x[1].pontualidade or 0), nomes.get(x[0], x[0])))
    out = []
    for n, (k, i) in enumerate(validos, start=1):
        antes = (anterior or {}).get(k)
        var = round(i.nota - antes.nota, 1) if antes and antes.nota is not None else None
        out.append(Posicao(n, k, nomes.get(k, k), i.nota, var))
    return out


# ════════════════════════════════════════════════════════════════════════════
# Alertas
# ════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Alerta:
    tipo: str          # ferias · atestado · licenca · falta · banco · dias_seguidos · rodizio
    pessoa: str
    titulo: str
    detalhe: str
    prazo: Optional[date]
    nivel: str = "atencao"   # «urgente» · «atencao» · «info»


@dataclass(frozen=True)
class Cadastro:
    id: str
    nome: str
    admissao: date
    #: inícios de período aquisitivo já com férias gozadas
    ferias_gozadas: tuple[date, ...] = ()
    #: licenças: (início, fim, motivo)
    licencas: tuple[tuple[date, date, str], ...] = ()
    #: atestados recebidos e ainda não lançados no ponto: (de, até)
    atestados_pendentes: tuple[tuple[date, date], ...] = ()


def ferias_vencendo(c: Cadastro, hoje: date, antecedencia: int = 60) -> Optional[Alerta]:
    """O período concessivo (12 meses depois do aquisitivo, CLT art. 134) que termina primeiro sem férias.
    Depois dele, as férias são pagas em dobro (art. 137)."""
    ini = c.admissao
    while somar_meses(ini, 12) <= hoje:
        if ini not in c.ferias_gozadas:
            limite = somar_meses(ini, 24) - timedelta(days=1)
            if limite < hoje:
                return Alerta("ferias", c.id, f"Férias vencidas — {c.nome}", f"o período concessivo terminou em "
                              f"{limite:%d/%m/%Y}: as férias passam a ser em dobro", limite, "urgente")
            if (limite - hoje).days <= antecedencia:
                return Alerta("ferias", c.id, f"Férias vencendo — {c.nome}", f"período concessivo termina em "
                              f"{limite:%d/%m/%Y}", limite, "urgente" if (limite - hoje).days <= 30 else "atencao")
        ini = somar_meses(ini, 12)
    return None


def alertas(*, hoje: date, cadastros: list[Cadastro], escala: Optional[Escala] = None,
            saldos: Optional[dict[str, Saldo]] = None, dias: Optional[dict[str, list[DiaDaPessoa]]] = None,
            faltas_sem_justificativa: Optional[dict[str, list[date]]] = None, max_dias_seguidos: int = 6,
            banco_antecedencia: int = 30) -> list[Alerta]:
    nomes = {c.id: c.nome for c in cadastros}
    out: list[Alerta] = []
    for c in cadastros:
        f = ferias_vencendo(c, hoje)
        if f:
            out.append(f)
        for a, b in c.atestados_pendentes:
            out.append(Alerta("atestado", c.id, f"Atestado recebido — {c.nome}",
                              f"{(b - a).days + 1} dia(s), de {a:%d/%m} a {b:%d/%m}: lançar no ponto para abonar", hoje, "urgente"))
        for a, b, motivo in c.licencas:
            if a <= hoje <= b and (b - hoje).days <= 30:
                out.append(Alerta("licenca", c.id, f"Retorno de {motivo} — {c.nome}", f"volta em {b + timedelta(days=1):%d/%m}: "
                                  "incluir na escala", b + timedelta(days=1), "info"))
    for p, datas in (faltas_sem_justificativa or {}).items():
        if datas:
            out.append(Alerta("falta", p, f"Falta sem justificativa — {nomes.get(p, p)}",
                              f"{len(datas)} no mês ({', '.join(f'{d:%d/%m}' for d in sorted(datas))}): a loja ainda não justificou",
                              max(datas), "urgente" if len(datas) > 1 else "atencao"))
    for p, s in (saldos or {}).items():
        v = s.vence_ate(hoje + timedelta(days=banco_antecedencia))
        if v:
            primeiro = min(l.vence for l in s.lotes if l.vence <= hoje + timedelta(days=banco_antecedencia))
            out.append(Alerta("banco", p, f"Banco de horas a vencer — {nomes.get(p, p)}",
                              f"{dur(v)} vencem até {primeiro:%d/%m}: compensar ou vai para a folha", primeiro))
        if s.vencido:
            out.append(Alerta("banco", p, f"Banco vencido — {nomes.get(p, p)}", f"{dur(s.vencido)} a pagar como hora extra",
                              hoje, "urgente"))
    if escala:
        for p, ds in escala.dias.items():
            seguidos = 0
            # só olha para a frente: amanhã precisa ser folga?
            for d in sorted(x for x in ds if x <= hoje):
                seguidos = 0 if ds[d] == FOLGA else seguidos + 1
            amanha = hoje + timedelta(days=1)
            if seguidos >= max_dias_seguidos and ds.get(amanha, FOLGA) != FOLGA:
                out.append(Alerta("dias_seguidos", p, f"{seguidos} dias seguidos — {nomes.get(p, p)}",
                                  "amanhã precisa ser folga (descanso semanal)", amanha, "urgente"))
    ordem = {"urgente": 0, "atencao": 1, "info": 2}
    return sorted(out, key=lambda a: (ordem[a.nivel], a.prazo or date.max, a.pessoa))
