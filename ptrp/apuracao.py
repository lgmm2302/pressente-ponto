"""A apuração da jornada — cálculo puro, sem banco e sem rede.

Entra: as marcações (as originais do REP-P e as tratadas), a escala de cada
dia, as regras vigentes em cada dia, feriados e abonos. Sai: cada dia com
horas trabalhadas, noturnas, extras, débitos, intervalo, interjornada, banco
de horas, ausências e os alertas que a empresa precisa ver antes que virem
reclamação trabalhista.

Todos os horários aqui são LOCAIS do estabelecimento, sem fuso (quem chama
converte). Minutos são inteiros; o arredondamento é feito uma vez por dia.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Callable, Union

from .regras import Regras

DSR = "DSR"      # descanso semanal remunerado
FOLGA = "FOLGA"  # dia sem trabalho que não é o DSR (ex.: sábado compensado)

# Tipos de ausência/compensação do AEJ (registro 07).
AUS_DSR, AUS_FALTA, AUS_BANCO, AUS_FOLGA_COMPENSATORIA = 1, 2, 3, 4
BH_CREDITO, BH_DEBITO = 1, 2


def _hm(s: str) -> time:
    return time(int(s[:2]), int(s[2:]))


def _min(td: timedelta) -> float:
    return td.total_seconds() / 60


@dataclass(frozen=True)
class Horario:
    """Horário contratual (AEJ registro 04). `periodos` = pares (entrada, saída)
    em hhmm; uma saída menor que a entrada passa da meia-noite."""
    codigo: str
    periodos: tuple[tuple[str, str], ...]
    intervalo_pre_assinalado: bool = False

    def __post_init__(self) -> None:
        if not self.codigo or len(self.codigo) > 30 or "|" in self.codigo:
            raise ValueError("código do horário: 1 a 30 caracteres, sem «|»")
        if not self.periodos:
            raise ValueError("horário sem períodos")
        for e, s in self.periodos:
            _hm(e), _hm(s)
            if e == s:
                raise ValueError("período sem duração")
        if self.intervalo_pre_assinalado and len(self.periodos) < 2:
            raise ValueError("intervalo pré-assinalado pede ao menos dois períodos")

    def momentos(self, dia: date) -> list[datetime]:
        """Entradas e saídas do horário num dia, em ordem, cruzando a meia-noite."""
        out: list[datetime] = []
        base = datetime.combine(dia, time())
        for e, s in self.periodos:
            for h in (e, s):
                t = datetime.combine(base.date(), _hm(h))
                while out and t <= out[-1]:
                    t += timedelta(days=1)
                out.append(t)
        return out

    def duracao(self, regras: Regras, dia: date = date(2000, 1, 3)) -> int:
        """durJornada do AEJ: minutos da jornada contratual, com a hora noturna
        reduzida aplicada."""
        m = self.momentos(dia)
        pares = list(zip(m[0::2], m[1::2]))
        real, noturno = _trabalhado_e_noturno(pares, regras)
        return round(_computado(real, noturno, regras))


DiaDaEscala = Union[Horario, str, None]  # Horario | DSR | FOLGA | None (sem escala)


@dataclass(frozen=True)
class Marca:
    momento: datetime                 # local, sem fuso
    fonte: str = "O"                  # O original · I incluída · P pré-assinalada
    registro_id: int | None = None    # NSR/registro do REP-P (fonte O)
    tratamento_id: int | None = None  # tratamento que incluiu ou desconsiderou
    desconsiderada: bool = False
    motivo: str = ""
    contestada: bool = False          # o empregado recusou o tratamento
    alertas: tuple[str, ...] = ()     # da prova da batida (fora da cerca, sem foto...)


@dataclass
class MarcaNoDia:
    marca: Marca
    tipo: str      # E · S · D
    seq: int       # par de entrada/saída (1, 2, ...)


@dataclass
class Dia:
    data: date
    situacao: str                     # trabalho · DSR · FOLGA · feriado · sem_escala
    horario: Horario | None
    marcas: list[MarcaNoDia] = field(default_factory=list)
    trabalhado: int = 0               # minutos reais
    noturno: int = 0                  # minutos reais entre 22h e 5h (e prorrogação)
    computado: int = 0                # com a hora noturna reduzida
    esperado: int = 0
    extra: int = 0                    # adicional de hora extra (50% ou o da convenção)
    extra_descanso_feriado: int = 0   # 100% (ou o da convenção)
    debito: int = 0
    intervalo: int = 0
    intervalo_suprimido: int = 0
    interjornada_faltante: int = 0
    banco_credito: int = 0
    banco_debito: int = 0
    falta: bool = False
    abono: str = ""
    tolerado: bool = False
    exato: bool = False               # todas as marcações iguais ao horário
    alertas: list[str] = field(default_factory=list)
    ausencias: list[tuple[int, int, int]] = field(default_factory=list)  # (tipo, minutos, mov)


@dataclass
class Apuracao:
    dias: list[Dia]

    def total(self, campo: str) -> int:
        return sum(getattr(d, campo) for d in self.dias)

    @property
    def faltas(self) -> int:
        return sum(1 for d in self.dias if d.falta)


# ─────────────────────────────────────────────────────────────────────────────
# Noturno
# ─────────────────────────────────────────────────────────────────────────────

def _janelas_noturnas(a: datetime, b: datetime, regras: Regras) -> list[tuple[datetime, datetime]]:
    ini, fim = _hm(regras.inicio_noturno), _hm(regras.fim_noturno)
    out = []
    d = a.date() - timedelta(days=1)
    while d <= b.date():
        ws = datetime.combine(d, ini)
        we = datetime.combine(d + timedelta(days=1) if fim <= ini else d, fim)
        out.append((ws, we))
        d += timedelta(days=1)
    return out


def _sobreposicao(a: datetime, b: datetime, c: datetime, d: datetime) -> float:
    return max(0.0, _min(min(b, d) - max(a, c)))


def _trabalhado_e_noturno(pares: list[tuple[datetime, datetime]], regras: Regras,
                          aplica_prorrogacao: bool = True) -> tuple[float, float]:
    real = sum(_min(s - e) for e, s in pares)
    if not pares:
        return 0.0, 0.0
    noturno = 0.0
    janelas = _janelas_noturnas(pares[0][0], pares[-1][1], regras)
    for e, s in pares:
        for ws, we in janelas:
            noturno += _sobreposicao(e, s, ws, we)
    if aplica_prorrogacao and regras.prorrogacao_noturna:
        for ws, we in janelas:
            dentro = sum(_sobreposicao(e, s, ws, we) for e, s in pares)
            # «Cumprida a jornada no período noturno» (Súmula 60, II; OJ 388):
            # pelo menos metade da janela trabalhada e trabalho seguindo depois
            # das 5h no mesmo turno.
            if dentro >= _min(we - ws) / 2 and any(e < we < s or e == we for e, s in pares):
                noturno += sum(_sobreposicao(e, s, we, datetime.max) for e, s in pares)
    return real, noturno


def _computado(real: float, noturno: float, regras: Regras) -> float:
    if not regras.hora_noturna_reduzida:
        return real
    return real - noturno + noturno * 60 / 52.5


# ─────────────────────────────────────────────────────────────────────────────
# A apuração
# ─────────────────────────────────────────────────────────────────────────────

# Uma entrada sem saída espera a saída por até 20 h; depois disso a próxima
# marcação abre outra jornada (e a anterior fica com número ímpar).
ESPERA_MAXIMA_PELA_SAIDA = 1200


def _jornadas(marcas: list[Marca], separador: int) -> list[list[Marca]]:
    """Agrupa as marcações em jornadas: depois de uma SAÍDA, um intervalo maior
    que o separador abre jornada nova; depois de uma ENTRADA, a marcação
    seguinte é a saída dela (até 20 h)."""
    out: list[list[Marca]] = []
    for m in marcas:
        if out:
            atual = out[-1]
            vao = _min(m.momento - atual[-1].momento)
            aberta = len(atual) % 2 == 1
            if (aberta and vao < ESPERA_MAXIMA_PELA_SAIDA) or (not aberta and vao < separador):
                atual.append(m)
                continue
        out.append([m])
    return out


def _pre_assinaladas(jornada: list[Marca], horario: Horario, dia: date) -> list[Marca]:
    """Intervalo pré-assinalado (CLT art. 74, § 2º): a jornada com só a entrada
    e a saída ganha as marcações do intervalo do horário, com fonte P."""
    if not horario.intervalo_pre_assinalado or len(jornada) != 2:
        return jornada
    m = horario.momentos(dia)
    s1, e2 = m[1], m[2]
    if not (jornada[0].momento < s1 < e2 < jornada[1].momento):
        return jornada
    return [jornada[0], Marca(s1, "P", motivo="intervalo pré-assinalado"),
            Marca(e2, "P", motivo="intervalo pré-assinalado"), jornada[1]]


def apurar(*, inicio: date, fim: date, marcas: list[Marca], escala: Callable[[date], DiaDaEscala],
           regras: Callable[[date], Regras], feriados: set[date] = frozenset(),
           abonos: dict[date, str] | None = None, banco_anterior: int = 0) -> Apuracao:
    abonos = abonos or {}
    validas = sorted((m for m in marcas if not m.desconsiderada), key=lambda m: m.momento)
    descartadas = sorted((m for m in marcas if m.desconsiderada), key=lambda m: m.momento)
    sep = regras(inicio).separador_de_jornadas
    jornadas = _jornadas(validas, sep)

    por_dia: dict[date, list[list[Marca]]] = {}
    for j in jornadas:
        por_dia.setdefault(j[0].momento.date(), []).append(j)

    dias: list[Dia] = []
    fim_anterior: datetime | None = None
    for j in jornadas:  # a última jornada antes do período alimenta a interjornada
        if j[0].momento.date() < inicio:
            fim_anterior = j[-1].momento
    d = inicio
    while d <= fim:
        r = regras(d)
        esc = escala(d)
        horario = esc if isinstance(esc, Horario) else None
        if d in feriados:
            situacao = "feriado"
        elif esc == DSR:
            situacao = "DSR"
        elif esc == FOLGA:
            situacao = "FOLGA"
        elif horario is not None:
            situacao = "trabalho"
        else:
            situacao = "sem_escala"
        dia = Dia(d, situacao, horario)
        dia.esperado = horario.duracao(r, d) if situacao == "trabalho" else 0
        js = por_dia.get(d, [])
        real_total = noturno_total = 0.0
        pares_todos: list[tuple[datetime, datetime]] = []
        for jornada in js:
            if horario is not None:
                jornada = _pre_assinaladas(jornada, horario, d)
            if fim_anterior is not None:
                folga = _min(jornada[0].momento - fim_anterior)
                if folga < r.interjornada_minima:
                    dia.interjornada_faltante += round(r.interjornada_minima - folga)
            fim_anterior = jornada[-1].momento
            pares = []
            for i, m in enumerate(jornada):
                dia.marcas.append(MarcaNoDia(m, "E" if i % 2 == 0 else "S", i // 2 + 1))
                if i % 2 == 1:
                    pares.append((jornada[i - 1].momento, m.momento))
            if len(jornada) % 2:
                dia.alertas.append("número ímpar de marcações: falta uma entrada ou saída")
            real, noturno = _trabalhado_e_noturno(pares, r)
            real_total += real
            noturno_total += noturno
            pares_todos.extend(pares)
            # Intervalo intrajornada desta jornada.
            gozado = sum(_min(pares[i + 1][0] - pares[i][1]) for i in range(len(pares) - 1))
            minimo = (r.intervalo_minimo_acima_de_6h if real > 360
                      else r.intervalo_minimo_de_4_a_6h if real > 240 else 0)
            dia.intervalo += round(gozado)
            if minimo - gozado > r.tolerancia_intervalo:
                dia.intervalo_suprimido += round(minimo - gozado)
                dia.alertas.append(f"intervalo de {round(gozado)} min, abaixo do mínimo de {minimo} min (CLT art. 71)")
            if gozado > r.intervalo_maximo and len(pares) > 1:
                dia.alertas.append(f"intervalo de {round(gozado)} min, acima de {r.intervalo_maximo} min (CLT art. 71)")
        for m in descartadas:
            if m.momento.date() == d:
                dia.marcas.append(MarcaNoDia(m, "D", 0))
        dia.marcas.sort(key=lambda x: x.marca.momento)
        for x in dia.marcas:
            for a in x.marca.alertas:
                dia.alertas.append(f"{x.marca.momento:%H:%M}: {a}")
        if dia.interjornada_faltante:
            dia.alertas.append(f"interjornada {dia.interjornada_faltante} min menor que "
                               f"{r.interjornada_minima // 60} h (CLT art. 66)")

        dia.trabalhado = round(real_total)
        dia.noturno = round(noturno_total)
        computado = _computado(real_total, noturno_total, r)
        dia.computado = round(computado)

        # Tolerância (CLT art. 58, § 1º).
        saldo = dia.computado - dia.esperado
        if situacao == "trabalho" and horario is not None and len(js) == 1:
            previstas = horario.momentos(d)
            feitas = [x.marca.momento for x in dia.marcas if x.tipo != "D"]
            if len(feitas) == len(previstas):
                desvios = [abs(_min(a - b)) for a, b in zip(feitas, previstas)]
                dia.exato = all(v == 0 for v in desvios)
                if all(v <= r.tolerancia_por_marcacao for v in desvios) and sum(desvios) <= r.tolerancia_diaria:
                    if saldo != 0:
                        dia.tolerado = True
                    saldo = 0

        if d in abonos:
            dia.abono = abonos[d]
        if situacao in ("DSR", "feriado"):
            dia.extra_descanso_feriado = dia.computado
            if situacao == "DSR" and not js:
                dia.ausencias.append((AUS_DSR, 0, 0))
        elif situacao in ("FOLGA", "sem_escala"):
            dia.extra = dia.computado
        elif not js:
            if dia.abono:
                pass
            else:
                dia.falta = True
                dia.debito = dia.esperado
                dia.ausencias.append((AUS_FALTA, 0, 0))
        elif saldo > 0:
            dia.extra = saldo
        elif saldo < 0 and not dia.abono:
            dia.debito = -saldo
        # Limite de 2 h extras (CLT art. 59): no dia de trabalho, além da jornada;
        # em descanso, folga ou feriado, além de uma jornada normal (8 h).
        excesso = dia.extra if situacao == "trabalho" else dia.computado - 480
        if excesso > r.limite_extra_diario:
            dia.alertas.append(f"{excesso} min além do limite diário de {r.limite_extra_diario} min "
                               "de hora extra (CLT art. 59)")

        # Banco de horas: extras viram crédito e atrasos viram débito (faltas não).
        if r.banco_de_horas_meses:
            credito = dia.extra + (dia.extra_descanso_feriado if r.banco_inclui_descanso_feriado else 0)
            if credito:
                dia.banco_credito = credito
                dia.ausencias.append((AUS_BANCO, credito, BH_CREDITO))
                dia.extra = 0
                if r.banco_inclui_descanso_feriado:
                    dia.extra_descanso_feriado = 0
            if dia.debito and not dia.falta:
                dia.banco_debito = dia.debito
                dia.ausencias.append((AUS_BANCO, dia.debito, BH_DEBITO))
                dia.debito = 0
        dias.append(dia)
        d += timedelta(days=1)

    _alerta_britanico(dias, regras)
    return Apuracao(dias)


def _alerta_britanico(dias: list[Dia], regras: Callable[[date], Regras]) -> None:
    seguidos: list[Dia] = []
    for dia in dias + [None]:  # type: ignore[list-item]
        if dia is not None and dia.situacao in ("DSR", "FOLGA", "feriado") and not dia.marcas:
            continue  # descanso não interrompe a sequência
        if dia is not None and dia.exato:
            seguidos.append(dia)
            continue
        if seguidos and len(seguidos) >= regras(seguidos[0].data).alerta_britanico_dias:
            for x in seguidos:
                x.alertas.append(f"marcações idênticas ao horário contratual por {len(seguidos)} dias seguidos "
                                 "(ponto britânico — Súmula 338, III, do TST)")
        seguidos = []


def saldo_do_banco(apuracao: Apuracao, anterior: int = 0) -> int:
    return anterior + apuracao.total("banco_credito") - apuracao.total("banco_debito")
