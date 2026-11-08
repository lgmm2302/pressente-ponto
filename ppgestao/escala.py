"""A escala — planejar e conferir, por data, a jornada de cada pessoa.

Cálculo puro (sem banco e sem rede). Entra: as pessoas, os turnos da loja, o
período, a necessidade de gente por hora (do movimento do PDV e das premissas
do gestor) e as regras. Sai: a escala dia a dia, a cobertura de cada hora, o
que não fecha e por quê.

O PLANEJADOR propõe; o VALIDADOR confere qualquer escala, inclusive a que o
gestor mexeu na mão. Nada aqui publica escala: quem aprova é uma pessoa.

As regras (`RegrasDaEscala`) vêm do cadastro que a direção mantém — inclusive
a regra de domingo de folga de cada grupo, que a tela do gestor mostra só como
«rodízio». Os padrões abaixo são os da CLT com a EC 141/2026 (5x2, 42 h).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Optional

FOLGA = "FOLGA"
DIAS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")


def _min(hhmm: str) -> int:
    if len(hhmm) != 4 or not hhmm.isdigit() or int(hhmm[:2]) > 24 or int(hhmm[2:]) > 59:
        raise ValueError(f"horário inválido: {hhmm}")
    return int(hhmm[:2]) * 60 + int(hhmm[2:])


def hm(m: int) -> str:
    m %= 1440
    return f"{m // 60:02d}h{m % 60:02d}" if m % 60 else f"{m // 60:02d}h"


@dataclass(frozen=True)
class Turno:
    """Um turno da loja. `fim` menor que `inicio` passa da meia-noite."""
    codigo: str
    inicio: str
    fim: str
    intervalo: int = 60
    nome: str = ""

    def __post_init__(self) -> None:
        if not self.codigo:
            raise ValueError("turno sem código")
        if self.duracao <= 0:
            raise ValueError(f"turno {self.codigo}: o intervalo é maior que o turno")

    @property
    def ini(self) -> int:
        return _min(self.inicio)

    @property
    def fim_min(self) -> int:
        f = _min(self.fim)
        return f if f > self.ini else f + 1440

    @property
    def duracao(self) -> int:
        """Minutos trabalhados (sem o intervalo)."""
        return self.fim_min - self.ini - self.intervalo

    def horas_cobertas(self) -> set[int]:
        """As horas cheias em que o turno tem gente (sem contar o intervalo, que é móvel)."""
        return {h % 24 for h in range(self.ini // 60, (self.fim_min + 59) // 60) if h * 60 < self.fim_min}


@dataclass(frozen=True)
class Pessoa:
    id: str
    nome: str
    #: chave da regra de domingo (RegrasDaEscala.domingo); vazio = regra «padrao»
    grupo_domingo: str = "padrao"
    #: turnos que a pessoa pode fazer (vazio = todos)
    turnos: tuple[str, ...] = ()
    #: dias da semana (0 = segunda) ou datas em que a pessoa NÃO trabalha
    nao_trabalha_dias: tuple[int, ...] = ()
    nao_trabalha_datas: tuple[date, ...] = ()
    #: {dia da semana: «hhmm»} — nesse dia precisa sair até essa hora
    sair_ate: tuple[tuple[int, str], ...] = ()
    #: domingos seguidos já trabalhados antes do período (o rodízio continua)
    domingos_seguidos: int = 0
    #: dias seguidos já trabalhados até a véspera do período
    dias_seguidos: int = 0
    #: fim do último turno antes do período (minutos depois da meia-noite da véspera; None = folgou)
    fim_na_vespera: Optional[int] = None


@dataclass(frozen=True)
class RegrasDaEscala:
    folgas_por_semana: int = 2            # EC 141/2026: 5x2
    max_semana: int = 42 * 60             # EC 141/2026 (40 h depois da transição)
    max_dia: int = 8 * 60                 # sem hora extra planejada
    interjornada: int = 11 * 60           # CLT art. 66
    max_dias_seguidos: int = 6            # o 7º dia é o DSR
    #: grupo → no máximo quantos domingos seguidos de trabalho (1 = folga a cada 2 domingos)
    domingo: tuple[tuple[str, int], ...] = (("padrao", 2),)

    def domingos_maximos(self, grupo: str) -> int:
        d = dict(self.domingo)
        return d.get(grupo or "padrao", d.get("padrao", 2))


@dataclass(frozen=True)
class Cobertura:
    """«Preciso de N pessoas das HH às HH» — em certos dias da semana, em certas datas, ou sempre."""
    minimo: int
    das: str
    ate: str
    dias: tuple[int, ...] = ()
    datas: tuple[date, ...] = ()
    texto: str = ""

    def vale(self, d: date) -> bool:
        if self.datas:
            return d in self.datas
        return not self.dias or d.weekday() in self.dias

    def horas(self) -> list[int]:
        a, b = _min(self.das), _min(self.ate)
        b = b if b > a else b + 1440
        return [h % 24 for h in range(a // 60, (b + 59) // 60) if h * 60 < b]


@dataclass(frozen=True)
class SemFolga:
    """«Nesses dias ninguém folga.»"""
    dias: tuple[int, ...] = ()
    datas: tuple[date, ...] = ()
    texto: str = ""

    def vale(self, d: date) -> bool:
        return d in self.datas or d.weekday() in self.dias


@dataclass
class Premissas:
    coberturas: list[Cobertura] = field(default_factory=list)
    sem_folga: list[SemFolga] = field(default_factory=list)


@dataclass(frozen=True)
class Problema:
    tipo: str
    texto: str
    pessoa: Optional[str] = None
    data: Optional[date] = None
    #: «bloqueia»: não publica assim · «atencao»: dá para publicar, mas alguém precisa saber
    nivel: str = "bloqueia"


@dataclass
class Escala:
    inicio: date
    fim: date
    #: pessoa → data → código do turno ou FOLGA
    dias: dict[str, dict[date, str]]
    explicacoes: list[str] = field(default_factory=list)


# ════════════════════════════════════════════════════════════════════════════
# A necessidade de gente
# ════════════════════════════════════════════════════════════════════════════
def demanda_pelo_movimento(movimento: dict[tuple[int, int], float], *, por_pessoa_hora: float,
                           minimo: int = 1, abertura: Iterable[int] = range(9, 22)) -> dict[tuple[int, int], int]:
    """Pessoas necessárias por (dia da semana, hora), a partir do movimento médio do PDV
    (vendas, tíquetes ou fluxo por hora) e de quanto uma pessoa atende numa hora.

    Hora sem movimento conhecido, mas com a loja aberta, pede o `minimo`."""
    if por_pessoa_hora <= 0:
        raise ValueError("por_pessoa_hora precisa ser maior que zero")
    out: dict[tuple[int, int], int] = {}
    for dia in range(7):
        for h in abertura:
            v = movimento.get((dia, h), 0.0)
            out[(dia, h)] = max(minimo, -int(-v // por_pessoa_hora))  # arredonda para cima
    return out


def necessidade(d: date, demanda: dict[tuple[int, int], int], premissas: Premissas) -> dict[int, int]:
    """Hora → pessoas necessárias neste dia: o maior entre o movimento e as premissas."""
    out = {h: n for (dia, h), n in demanda.items() if dia == d.weekday()}
    for c in premissas.coberturas:
        if c.vale(d):
            for h in c.horas():
                out[h] = max(out.get(h, 0), c.minimo)
    return out


# ════════════════════════════════════════════════════════════════════════════
# O planejador
# ════════════════════════════════════════════════════════════════════════════
def _semanas(inicio: date, fim: date) -> list[list[date]]:
    seg = inicio - timedelta(days=inicio.weekday())
    out = []
    while seg <= fim:
        out.append([seg + timedelta(days=i) for i in range(7) if inicio <= seg + timedelta(days=i) <= fim])
        seg += timedelta(days=7)
    return out


def minimo_de_gente(precisa: dict[int, int], turnos: list[Turno], max_dia: int = 8 * 60) -> int:
    """Quantas pessoas, no mínimo, cobrem a necessidade do dia com estes turnos (estimativa gulosa)."""
    falta = {h: n for h, n in precisa.items() if n > 0}
    cabem = [t for t in turnos if t.duracao <= max_dia]
    n = 0
    while falta and cabem and n < 200:
        t = max(cabem, key=lambda t: (sum(1 for h in t.horas_cobertas() if h in falta), -t.ini))
        if not any(h in falta for h in t.horas_cobertas()):
            break
        for h in t.horas_cobertas():
            if h in falta:
                falta[h] -= 1
                if falta[h] <= 0:
                    del falta[h]
        n += 1
    return n


def _sem_trabalho(p: Pessoa, d: date) -> bool:
    return d.weekday() in p.nao_trabalha_dias or d in p.nao_trabalha_datas


def planejar(*, inicio: date, fim: date, pessoas: list[Pessoa], turnos: list[Turno], demanda: dict[tuple[int, int], int],
             premissas: Optional[Premissas] = None, regras: Optional[RegrasDaEscala] = None) -> Escala:
    """Propõe a escala do período. Determinístico: os mesmos dados dão a mesma escala."""
    premissas = premissas or Premissas()
    regras = regras or RegrasDaEscala()
    if not pessoas or not turnos:
        raise ValueError("faltam pessoas ou turnos")
    if fim < inicio:
        raise ValueError("o período termina antes de começar")
    tmap = {t.codigo: t for t in turnos}
    dias = [inicio + timedelta(days=i) for i in range((fim - inicio).days + 1)]
    precisa = {d: necessidade(d, demanda, premissas) for d in dias}
    # «pico» = quantas pessoas o dia pede, no mínimo, para cobrir todas as horas com os turnos que existem
    pico = {d: minimo_de_gente(precisa[d], turnos, regras.max_dia) for d in dias}
    folga: dict[str, set[date]] = {p.id: {d for d in dias if _sem_trabalho(p, d)} for p in pessoas}
    exp: list[str] = []

    domingos_seguidos = {p.id: p.domingos_seguidos for p in pessoas}
    for semana in _semanas(inicio, fim):
        cheia = len(semana) == 7
        cota = regras.folgas_por_semana if cheia else round(regras.folgas_por_semana * len(semana) / 7)
        # 1) O domingo: quem chegou ao limite do rodízio folga; depois, quem está há mais tempo sem domingo.
        dom = [d for d in semana if d.weekday() == 6]
        if dom:
            d = dom[0]
            vagas = max(0, len(pessoas) - pico[d])
            obrigados = [p for p in pessoas if domingos_seguidos[p.id] >= regras.domingos_maximos(p.grupo_domingo)
                         or d in folga[p.id]]
            fila = sorted((p for p in pessoas if p not in obrigados),
                          key=lambda p: (-domingos_seguidos[p.id], p.id))
            escolhidos = obrigados + fila[:max(0, vagas - len(obrigados))]
            for p in escolhidos:
                folga[p.id].add(d)
            for p in pessoas:
                domingos_seguidos[p.id] = 0 if d in folga[p.id] else domingos_seguidos[p.id] + 1
            if len(obrigados) > vagas:
                exp.append(f"Domingo {d:%d/%m}: o rodízio pede {len(obrigados)} folga(s) e o movimento só comporta {vagas}.")
        # 2) As outras folgas da semana nos dias de mais sobra, fora dos dias sem folga.
        livres = [d for d in semana if d.weekday() != 6 and not any(s.vale(d) for s in premissas.sem_folga)]
        disponiveis = {d: sum(1 for p in pessoas if d not in folga[p.id]) for d in semana}
        ordem = sorted(pessoas, key=lambda p: (len([d for d in livres if d not in folga[p.id]]), p.id))
        for p in ordem:
            faltam = cota - len([d for d in semana if d in folga[p.id]])
            for _ in range(max(0, faltam)):
                opcoes = [d for d in livres if d not in folga[p.id]]
                if not opcoes:
                    break
                d = max(opcoes, key=lambda x: (disponiveis[x] - pico[x], -abs(x.weekday() - 2), -x.toordinal()))
                folga[p.id].add(d)
                disponiveis[d] -= 1

    # 3) Ninguém passa do limite de dias seguidos. Primeiro tenta ANTECIPAR uma folga da mesma semana (a
    #    pessoa continua com 2 folgas); só se não der, acrescenta uma.
    sem_folga = lambda d: any(s.vale(d) for s in premissas.sem_folga)  # noqa: E731
    for p in pessoas:
        seguidos = p.dias_seguidos
        for i, d in enumerate(dias):
            if d in folga[p.id]:
                seguidos = 0
                continue
            seguidos += 1
            if seguidos > regras.max_dias_seguidos:
                # o dia mais tarde possível da sequência que aceita folga
                alvo = next((x for x in (dias[j] for j in range(i, max(-1, i - regras.max_dias_seguidos - 1), -1))
                             if not sem_folga(x) and x.weekday() != 6), d)
                seg = alvo - timedelta(days=alvo.weekday())
                depois = sorted(x for x in folga[p.id] if seg <= x < seg + timedelta(days=7) and x > d
                                and x.weekday() != 6 and not _sem_trabalho(p, x))
                if depois:
                    folga[p.id].discard(depois[0])
                    exp.append(f"{p.nome}: folga de {depois[0]:%d/%m} antecipada para {alvo:%d/%m}, para não passar de "
                               f"{regras.max_dias_seguidos} dias seguidos.")
                else:
                    exp.append(f"{p.nome}: folga a mais em {alvo:%d/%m}, para não passar de {regras.max_dias_seguidos} dias seguidos.")
                folga[p.id].add(alvo)
                seguidos = (d - alvo).days

    # 4) Os turnos de cada dia: quem é mais restrito escolhe primeiro o turno que mais cobre o que falta.
    escala: dict[str, dict[date, str]] = {p.id: {} for p in pessoas}
    fim_anterior: dict[str, Optional[int]] = {p.id: p.fim_na_vespera for p in pessoas}
    for d in dias:
        falta = dict(precisa[d])
        trabalham = [p for p in pessoas if d not in folga[p.id]]

        def possiveis(p: Pessoa) -> list[Turno]:
            limite = dict(p.sair_ate).get(d.weekday())
            out = []
            for t in turnos:
                if p.turnos and t.codigo not in p.turnos:
                    continue
                if t.duracao > regras.max_dia:
                    continue
                if limite and t.fim_min > _min(limite):
                    continue
                f = fim_anterior[p.id]
                if f is not None and t.ini + 1440 - f < regras.interjornada:
                    continue
                out.append(t)
            return out

        # Quem escolhe primeiro gira a cada dia: assim abertura e fechamento se revezam entre as pessoas.
        posicao = {p.id: i for i, p in enumerate(pessoas)}
        for p in sorted(trabalham, key=lambda p: (len(possiveis(p)), (posicao[p.id] + d.toordinal()) % len(pessoas))):
            ops = possiveis(p)
            if not ops:
                folga[p.id].add(d)
                escala[p.id][d] = FOLGA
                exp.append(f"{p.nome}: nenhum turno cabe em {d:%d/%m} (interjornada ou restrição) — ficou de folga.")
                fim_anterior[p.id] = None
                continue
            t = max(ops, key=lambda t: (sum(1 for h in t.horas_cobertas() if falta.get(h, 0) > 0),
                                        sum(max(0, falta.get(h, 0)) for h in t.horas_cobertas()), -t.ini))
            escala[p.id][d] = t.codigo
            for h in t.horas_cobertas():
                if h in falta:
                    falta[h] -= 1
            fim_anterior[p.id] = t.fim_min
        for p in pessoas:
            if d in folga[p.id]:
                escala[p.id][d] = FOLGA
                fim_anterior[p.id] = None
    return Escala(inicio, fim, escala, exp)


# ════════════════════════════════════════════════════════════════════════════
# O validador — confere qualquer escala, a proposta ou a mexida à mão
# ════════════════════════════════════════════════════════════════════════════
def cobertura(escala: Escala, turnos: list[Turno], demanda: dict[tuple[int, int], int],
              premissas: Optional[Premissas] = None) -> dict[date, dict[int, tuple[int, int]]]:
    """data → hora → (escalados, necessários)."""
    premissas = premissas or Premissas()
    tmap = {t.codigo: t for t in turnos}
    out: dict[date, dict[int, tuple[int, int]]] = {}
    d = escala.inicio
    while d <= escala.fim:
        prec = necessidade(d, demanda, premissas)
        tem: dict[int, int] = {}
        for dias in escala.dias.values():
            t = tmap.get(dias.get(d, FOLGA))
            if t:
                for h in t.horas_cobertas():
                    tem[h] = tem.get(h, 0) + 1
        out[d] = {h: (tem.get(h, 0), prec.get(h, 0)) for h in sorted(set(prec) | set(tem))}
        d += timedelta(days=1)
    return out


def validar(escala: Escala, *, pessoas: list[Pessoa], turnos: list[Turno], demanda: dict[tuple[int, int], int],
            premissas: Optional[Premissas] = None, regras: Optional[RegrasDaEscala] = None) -> list[Problema]:
    premissas = premissas or Premissas()
    regras = regras or RegrasDaEscala()
    tmap = {t.codigo: t for t in turnos}
    probs: list[Problema] = []
    dias = [escala.inicio + timedelta(days=i) for i in range((escala.fim - escala.inicio).days + 1)]
    for p in pessoas:
        e = escala.dias.get(p.id, {})
        for d in dias:
            c = e.get(d)
            if c is None:
                probs.append(Problema("sem_escala", f"{p.nome} está sem escala em {d:%d/%m}.", p.id, d))
            elif c != FOLGA and c not in tmap:
                probs.append(Problema("turno_desconhecido", f"{p.nome}: turno «{c}» não existe ({d:%d/%m}).", p.id, d))
        # 5x2 e horas por semana
        for semana in _semanas(escala.inicio, escala.fim):
            if len(semana) < 7:
                continue
            folgas = sum(1 for d in semana if e.get(d) == FOLGA)
            if folgas < regras.folgas_por_semana:
                probs.append(Problema("folgas_da_semana", f"{p.nome}: {folgas} folga(s) na semana de {semana[0]:%d/%m} "
                                      f"(a regra pede {regras.folgas_por_semana}).", p.id, semana[0]))
            minutos = sum(tmap[e[d]].duracao for d in semana if e.get(d) in tmap)
            if minutos > regras.max_semana:
                probs.append(Problema("horas_da_semana", f"{p.nome}: {minutos // 60}h{minutos % 60:02d} na semana de "
                                      f"{semana[0]:%d/%m} (limite {regras.max_semana // 60} h).", p.id, semana[0]))
        # o dia, a interjornada e os dias seguidos
        fim_ant = p.fim_na_vespera
        seguidos = p.dias_seguidos
        dom_seg = p.domingos_seguidos
        for d in dias:
            t = tmap.get(e.get(d, ""))
            if t is None:
                fim_ant, seguidos = None, 0
                if d.weekday() == 6:
                    dom_seg = 0
                continue
            if t.duracao > regras.max_dia:
                probs.append(Problema("horas_do_dia", f"{p.nome}: {hm(t.duracao)} em {d:%d/%m} (limite {hm(regras.max_dia)}).", p.id, d))
            if fim_ant is not None and t.ini + 1440 - fim_ant < regras.interjornada:
                folga_min = t.ini + 1440 - fim_ant
                probs.append(Problema("interjornada", f"{p.nome}: só {hm(folga_min)} entre o fim de um turno e o início do "
                                      f"outro em {d:%d/%m} (mínimo {regras.interjornada // 60} h).", p.id, d))
            limite = dict(p.sair_ate).get(d.weekday())
            if limite and t.fim_min > _min(limite):
                probs.append(Problema("restricao_da_pessoa", f"{p.nome} precisa sair até {hm(_min(limite))} em {d:%d/%m}.", p.id, d))
            if _sem_trabalho(p, d):
                probs.append(Problema("restricao_da_pessoa", f"{p.nome} não trabalha em {d:%d/%m}.", p.id, d))
            if p.turnos and t.codigo not in p.turnos:
                probs.append(Problema("turno_da_pessoa", f"{p.nome} não faz o turno {t.codigo}.", p.id, d, "atencao"))
            seguidos += 1
            if seguidos == regras.max_dias_seguidos + 1:
                probs.append(Problema("dias_seguidos", f"{p.nome}: {seguidos} dias seguidos de trabalho até {d:%d/%m}.", p.id, d))
            if d.weekday() == 6:
                dom_seg += 1
                if dom_seg == regras.domingos_maximos(p.grupo_domingo) + 1:
                    probs.append(Problema("rodizio_de_domingo", f"{p.nome}: o rodízio pede folga no domingo {d:%d/%m}.", p.id, d))
            fim_ant = t.fim_min
    # premissas
    for s in premissas.sem_folga:
        for d in dias:
            if s.vale(d):
                quem = [p.nome for p in pessoas if escala.dias.get(p.id, {}).get(d) == FOLGA and not _sem_trabalho(p, d)]
                if quem:
                    probs.append(Problema("dia_sem_folga", f"{d:%d/%m} é dia sem folga ({s.texto or 'premissa'}), mas folga(m): "
                                          + ", ".join(quem) + ".", None, d, "atencao"))
    for d, horas in cobertura(escala, turnos, demanda, premissas).items():
        buracos = [h for h, (tem, prec) in horas.items() if tem < prec]
        if buracos:
            maior = max(prec - tem for h, (tem, prec) in horas.items() if h in buracos)
            probs.append(Problema("cobertura", f"{DIAS[d.weekday()]} {d:%d/%m}: falta gente em {len(buracos)} hora(s) "
                                  f"({hm(min(buracos) * 60)}–{hm((max(buracos) + 1) * 60)}), até {maior} pessoa(s).",
                                  None, d, "atencao"))
    return probs
