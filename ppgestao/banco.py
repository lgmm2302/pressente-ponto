"""O banco de horas — saldo com vencimento e sugestão de quando compensar.

Cálculo puro. Entra: os lançamentos de cada pessoa (o crédito e o débito de cada
dia, que saem da apuração do `ptrp`), a validade do acordo (CLT art. 59: 6 meses
no acordo individual, 12 na convenção, o mês no acordo tácito) e, para sugerir,
a escala aprovada e a necessidade de gente por hora.

Regras:
  · o débito abate o crédito MAIS ANTIGO primeiro (o que vence antes);
  · crédito que venceu sem compensar vira hora extra a pagar (com o adicional);
  · a sugestão só tira a pessoa de uma hora em que a loja continua coberta,
    e nunca nos dias que o gestor bloqueou (ex.: Natal).

Nada aqui lança compensação: a sugestão vai para o gestor aprovar.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Optional

from .escala import FOLGA, Escala, Premissas, Turno, cobertura, hm


def dur(m: int) -> str:
    """Duração para gente ler: «3h», «1h30», «45 min»."""
    h, r = divmod(abs(int(m)), 60)
    return (f"{h}h{r:02d}" if r else f"{h}h") if h else f"{r} min"


def somar_meses(d: date, meses: int) -> date:
    m = d.month - 1 + meses
    a, m = d.year + m // 12, m % 12 + 1
    return date(a, m, min(d.day, calendar.monthrange(a, m)[1]))


@dataclass(frozen=True)
class Lancamento:
    data: date
    minutos: int          # + crédito · − débito
    origem: str = ""      # «extra», «compensação», «atraso»…


@dataclass
class Lote:
    """Um crédito ainda não compensado, com a data em que vence."""
    origem: date
    restante: int
    vence: date


@dataclass
class Saldo:
    saldo: int                       # minutos: créditos vivos − débitos em aberto
    lotes: list[Lote] = field(default_factory=list)
    vencido: int = 0                 # crédito que venceu sem compensar: vai para a folha
    debito: int = 0                  # débito sem crédito para abater

    def vence_ate(self, d: date) -> int:
        return sum(l.restante for l in self.lotes if l.vence <= d)


def apurar_saldo(lancamentos: Iterable[Lancamento], *, hoje: date, validade_meses: int) -> Saldo:
    if validade_meses not in (1, 6, 12):
        raise ValueError("validade do banco: 1, 6 ou 12 meses (CLT art. 59)")
    lotes: list[Lote] = []
    vencido = debito = 0
    for l in sorted(lancamentos, key=lambda x: (x.data, -x.minutos)):
        # vence o que passou do prazo antes deste lançamento
        for lo in [lo for lo in lotes if lo.vence <= l.data]:
            vencido += lo.restante
            lotes.remove(lo)
        if l.minutos > 0:
            credito = l.minutos
            if debito:  # débito antigo é abatido pelo crédito novo
                abate = min(debito, credito)
                debito -= abate
                credito -= abate
            if credito:
                lotes.append(Lote(l.data, credito, somar_meses(l.data, validade_meses)))
        elif l.minutos < 0:
            falta = -l.minutos
            for lo in sorted(lotes, key=lambda x: x.vence):
                usa = min(lo.restante, falta)
                lo.restante -= usa
                falta -= usa
                if not falta:
                    break
            lotes = [lo for lo in lotes if lo.restante > 0]
            debito += falta
    for lo in [lo for lo in lotes if lo.vence <= hoje]:
        vencido += lo.restante
        lotes.remove(lo)
    return Saldo(sum(lo.restante for lo in lotes) - debito, sorted(lotes, key=lambda x: x.vence), vencido, debito)


def custo(minutos: int, valor_hora: Decimal, adicional: int = 50) -> Decimal:
    """Quanto custa pagar `minutos` como hora extra (valor-hora × (1 + adicional))."""
    v = Decimal(minutos) / Decimal(60) * valor_hora * (Decimal(100 + adicional) / Decimal(100))
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Sugestao:
    pessoa: str
    data: date
    minutos: int
    como: str          # «folga» · «entra depois» · «sai antes»
    texto: str


def sugerir_compensacoes(*, saldos: dict[str, Saldo], nomes: dict[str, str], escala: Escala, turnos: list[Turno],
                         demanda: dict[tuple[int, int], int], premissas: Optional[Premissas] = None,
                         ate: date, bloqueadas: Iterable[date] = (), minimo_por_vez: int = 60) -> list[Sugestao]:
    """Onde cada pessoa pode compensar o saldo que vence até `ate`, sem descobrir a loja.

    Uma hora só é tirada de alguém se, sem ela, a loja continua com a gente necessária.
    Primeiro o saldo que vence antes; dentro da pessoa, o dia de mais sobra."""
    premissas = premissas or Premissas()
    tmap = {t.codigo: t for t in turnos}
    bloq = set(bloqueadas)
    cob = {d: {h: list(v) for h, v in horas.items()} for d, horas in cobertura(escala, turnos, demanda, premissas).items()}
    out: list[Sugestao] = []
    fila = sorted(((min((l.vence for l in s.lotes if l.vence <= ate), default=None), p) for p, s in saldos.items()),
                  key=lambda x: (x[0] or date.max, x[1]))
    for vence, p in fila:
        if vence is None:
            continue
        resta = saldos[p].vence_ate(ate)
        dias = sorted((d for d, c in escala.dias.get(p, {}).items()
                       if c in tmap and escala.inicio <= d <= min(ate, escala.fim) and d not in bloq),
                      key=lambda d: (-sum(v[0] - v[1] for v in cob[d].values()), d))
        for d in dias:
            if resta < minimo_por_vez:
                break
            t = tmap[escala.dias[p][d]]
            horas = sorted(t.horas_cobertas(), key=lambda h: (h - t.ini // 60) % 24)
            if all(cob[d].get(h, [0, 0])[0] - 1 >= cob[d].get(h, [0, 0])[1] for h in horas) and resta >= t.duracao:
                for h in horas:
                    cob[d][h][0] -= 1
                resta -= t.duracao
                out.append(Sugestao(p, d, t.duracao, "folga", f"{nomes.get(p, p)}: folga compensatória em {d:%d/%m} "
                                    f"({dur(t.duracao)}) — a loja continua coberta o dia todo."))
                continue
            for como, ordem in (("entra depois", horas), ("sai antes", list(reversed(horas)))):
                livres = []
                for h in ordem:
                    if cob[d].get(h, [0, 0])[0] - 1 < cob[d].get(h, [0, 0])[1]:
                        break
                    livres.append(h)
                minutos = min(len(livres) * 60, resta - resta % 60, t.duracao - 4 * 60)
                if minutos >= minimo_por_vez:
                    for h in livres[:minutos // 60]:
                        cob[d][h][0] -= 1
                    resta -= minutos
                    quando = (f"entra às {hm(t.ini + minutos)}" if como == "entra depois"
                              else f"sai às {hm(t.fim_min - minutos)}")
                    out.append(Sugestao(p, d, minutos, como, f"{nomes.get(p, p)}: em {d:%d/%m} {quando} e compensa "
                                        f"{dur(minutos)} — sobra gente nesse horário."))
                    break
    return out
