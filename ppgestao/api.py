"""A API da gestão — o que o painel de qualquer sistema integrado (FPM Gestão, Exxen, Redalis) chama.

Sem estado: cada pedido traz os dados e recebe a resposta. Quem guarda escala,
banco e cadastros é o sistema integrado (ou, no produto avulso, o tratamento).
Assim o mesmo motor serve a todos e a licença do Pressente Ponto vale sozinha.

    POST /v1/gestao/escala/planejar     propõe a escala do período
    POST /v1/gestao/escala/validar      confere uma escala (a proposta ou a mexida à mão)
    POST /v1/gestao/banco/saldo         saldo com vencimento, por pessoa
    POST /v1/gestao/banco/sugestoes     onde compensar sem descobrir a loja
    POST /v1/gestao/indicadores         indicadores e ranking
    POST /v1/gestao/alertas             férias, atestados, licenças, faltas, banco, dias seguidos

A chave é a mesma do estabelecimento (cabeçalho X-Chave-Estabelecimento), conferida
pelo `verificar_chave` que quem monta o app passar.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Awaitable, Callable, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from . import NOME_DO_PROGRAMA, VERSAO
from . import banco as B
from . import escala as E
from . import indicadores as I


class TurnoIn(BaseModel):
    codigo: str
    inicio: str
    fim: str
    intervalo: int = 60
    nome: str = ""


class PessoaIn(BaseModel):
    id: str
    nome: str
    grupo_domingo: str = "padrao"
    turnos: list[str] = []
    nao_trabalha_dias: list[int] = []
    nao_trabalha_datas: list[date] = []
    sair_ate: dict[int, str] = {}
    domingos_seguidos: int = 0
    dias_seguidos: int = 0
    fim_na_vespera: Optional[int] = None


class CoberturaIn(BaseModel):
    minimo: int = Field(ge=0)
    das: str
    ate: str
    dias: list[int] = []
    datas: list[date] = []
    texto: str = ""


class SemFolgaIn(BaseModel):
    dias: list[int] = []
    datas: list[date] = []
    texto: str = ""


class RegrasIn(BaseModel):
    folgas_por_semana: int = Field(2, ge=1, le=7)
    max_semana: int = Field(42 * 60, ge=60)
    max_dia: int = Field(8 * 60, ge=60)
    interjornada: int = Field(11 * 60, ge=0)
    max_dias_seguidos: int = Field(6, ge=1)
    domingo: dict[str, int] = {"padrao": 2}


class DemandaIn(BaseModel):
    """Ou a necessidade pronta (`necessidade`: «dia-hora» → pessoas), ou o movimento e quanto uma pessoa atende."""
    necessidade: dict[str, int] = {}
    movimento: dict[str, float] = {}
    por_pessoa_hora: float = 0
    minimo: int = 1
    abre: int = 9
    fecha: int = 22


class EscalaBase(BaseModel):
    inicio: date
    fim: date
    pessoas: list[PessoaIn]
    turnos: list[TurnoIn]
    demanda: DemandaIn = DemandaIn()
    coberturas: list[CoberturaIn] = []
    sem_folga: list[SemFolgaIn] = []
    regras: RegrasIn = RegrasIn()


class ValidarIn(EscalaBase):
    escala: dict[str, dict[date, str]]


class LancamentoIn(BaseModel):
    data: date
    minutos: int
    origem: str = ""


class SaldoIn(BaseModel):
    hoje: date
    validade_meses: int
    lancamentos: dict[str, list[LancamentoIn]]
    valor_hora: dict[str, Decimal] = {}


class SugestoesIn(EscalaBase):
    escala: dict[str, dict[date, str]]
    hoje: date
    ate: date
    validade_meses: int
    lancamentos: dict[str, list[LancamentoIn]]
    bloqueadas: list[date] = []


class DiaIn(BaseModel):
    data: date
    previsto: int
    trabalhado: int = 0
    atraso: int = 0
    saida_antes: int = 0
    abonado: bool = False
    extra: int = 0


class IndicadoresIn(BaseModel):
    nomes: dict[str, str] = {}
    atual: dict[str, list[DiaIn]]
    anterior: dict[str, list[DiaIn]] = {}
    minimo_de_dias: int = 10


class CadastroIn(BaseModel):
    id: str
    nome: str
    admissao: date
    ferias_gozadas: list[date] = []
    licencas: list[tuple[date, date, str]] = []
    atestados_pendentes: list[tuple[date, date]] = []


class AlertasIn(BaseModel):
    hoje: date
    cadastros: list[CadastroIn]
    escala: dict[str, dict[date, str]] = {}
    validade_meses: int = 6
    lancamentos: dict[str, list[LancamentoIn]] = {}
    faltas_sem_justificativa: dict[str, list[date]] = {}


# ════════════════════════════════════════════════════════════════════════════
def _demanda(d: DemandaIn) -> dict[tuple[int, int], int]:
    def chave(k: str) -> tuple[int, int]:
        try:
            a, b = k.split("-")
            dia, hora = int(a), int(b)
        except ValueError:
            raise HTTPException(422, f"chave de demanda inválida: «{k}» (use «dia-hora», ex.: «5-19»)")
        if not (0 <= dia <= 6 and 0 <= hora <= 23):
            raise HTTPException(422, f"chave de demanda fora do intervalo: «{k}»")
        return dia, hora
    if d.necessidade:
        return {chave(k): int(v) for k, v in d.necessidade.items()}
    if d.movimento:
        if d.por_pessoa_hora <= 0:
            raise HTTPException(422, "com o movimento, informe quanto uma pessoa atende por hora (por_pessoa_hora)")
        return E.demanda_pelo_movimento({chave(k): v for k, v in d.movimento.items()}, por_pessoa_hora=d.por_pessoa_hora,
                                        minimo=d.minimo, abertura=range(d.abre, d.fecha))
    return {}


def _montar(b: EscalaBase):
    try:
        turnos = [E.Turno(**t.model_dump()) for t in b.turnos]
        pessoas = [E.Pessoa(id=p.id, nome=p.nome, grupo_domingo=p.grupo_domingo, turnos=tuple(p.turnos),
                            nao_trabalha_dias=tuple(p.nao_trabalha_dias), nao_trabalha_datas=tuple(p.nao_trabalha_datas),
                            sair_ate=tuple(sorted(p.sair_ate.items())), domingos_seguidos=p.domingos_seguidos,
                            dias_seguidos=p.dias_seguidos, fim_na_vespera=p.fim_na_vespera) for p in b.pessoas]
        prem = E.Premissas([E.Cobertura(c.minimo, c.das, c.ate, tuple(c.dias), tuple(c.datas), c.texto) for c in b.coberturas],
                           [E.SemFolga(tuple(s.dias), tuple(s.datas), s.texto) for s in b.sem_folga])
        r = b.regras
        regras = E.RegrasDaEscala(r.folgas_por_semana, r.max_semana, r.max_dia, r.interjornada, r.max_dias_seguidos,
                                  tuple(sorted(r.domingo.items())))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return pessoas, turnos, _demanda(b.demanda), prem, regras


def _saida_escala(esc: E.Escala, probs: list[E.Problema], cob) -> dict:
    return {"escala": {p: {d.isoformat(): c for d, c in dias.items()} for p, dias in esc.dias.items()},
            "explicacoes": esc.explicacoes,
            "problemas": [{"tipo": p.tipo, "texto": p.texto, "pessoa": p.pessoa,
                           "data": p.data.isoformat() if p.data else None, "nivel": p.nivel} for p in probs],
            "pode_publicar": not any(p.nivel == "bloqueia" for p in probs),
            "cobertura": {d.isoformat(): {str(h): {"escalados": t, "necessarios": n} for h, (t, n) in horas.items()}
                          for d, horas in cob.items()}}


def _saldos(lanc: dict[str, list[LancamentoIn]], hoje: date, meses: int) -> dict[str, B.Saldo]:
    try:
        return {p: B.apurar_saldo([B.Lancamento(x.data, x.minutos, x.origem) for x in ls], hoje=hoje, validade_meses=meses)
                for p, ls in lanc.items()}
    except ValueError as e:
        raise HTTPException(422, str(e))


def roteador(verificar_chave: Optional[Callable[[str], Awaitable[bool]]] = None) -> APIRouter:
    """O roteador da gestão. `verificar_chave(chave)` diz se a chave do estabelecimento vale."""
    async def chave(x_chave_estabelecimento: str = Header(...)) -> None:
        if verificar_chave is not None and not await verificar_chave(x_chave_estabelecimento):
            raise HTTPException(401, "chave do estabelecimento inválida")

    r = APIRouter(prefix="/v1/gestao", dependencies=[Depends(chave)])

    @r.get("/versao")
    async def versao():
        return {"programa": NOME_DO_PROGRAMA, "versao": VERSAO}

    @r.post("/escala/planejar")
    async def planejar(b: EscalaBase):
        pessoas, turnos, dem, prem, regras = _montar(b)
        try:
            esc = E.planejar(inicio=b.inicio, fim=b.fim, pessoas=pessoas, turnos=turnos, demanda=dem, premissas=prem, regras=regras)
        except ValueError as e:
            raise HTTPException(422, str(e))
        probs = E.validar(esc, pessoas=pessoas, turnos=turnos, demanda=dem, premissas=prem, regras=regras)
        return _saida_escala(esc, probs, E.cobertura(esc, turnos, dem, prem))

    @r.post("/escala/validar")
    async def validar(b: ValidarIn):
        pessoas, turnos, dem, prem, regras = _montar(b)
        esc = E.Escala(b.inicio, b.fim, b.escala)
        probs = E.validar(esc, pessoas=pessoas, turnos=turnos, demanda=dem, premissas=prem, regras=regras)
        return _saida_escala(esc, probs, E.cobertura(esc, turnos, dem, prem))

    @r.post("/banco/saldo")
    async def saldo(b: SaldoIn):
        out = {}
        for p, s in _saldos(b.lancamentos, b.hoje, b.validade_meses).items():
            vh = b.valor_hora.get(p)
            out[p] = {"saldo": s.saldo, "vencido": s.vencido, "debito": s.debito,
                      "vence_30_dias": s.vence_ate(date.fromordinal(b.hoje.toordinal() + 30)),
                      "lotes": [{"origem": l.origem.isoformat(), "restante": l.restante, "vence": l.vence.isoformat()} for l in s.lotes],
                      "custo_se_pagar_hoje": str(B.custo(max(0, s.saldo), vh)) if vh is not None else None,
                      "custo_do_vencido": str(B.custo(s.vencido, vh)) if vh is not None else None}
        return out

    @r.post("/banco/sugestoes")
    async def sugestoes(b: SugestoesIn):
        pessoas, turnos, dem, prem, _ = _montar(b)
        saldos = _saldos(b.lancamentos, b.hoje, b.validade_meses)
        sug = B.sugerir_compensacoes(saldos=saldos, nomes={p.id: p.nome for p in pessoas}, escala=E.Escala(b.inicio, b.fim, b.escala),
                                     turnos=turnos, demanda=dem, premissas=prem, ate=b.ate, bloqueadas=b.bloqueadas)
        return [{"pessoa": s.pessoa, "data": s.data.isoformat(), "minutos": s.minutos, "como": s.como, "texto": s.texto} for s in sug]

    @r.post("/indicadores")
    async def indicadores(b: IndicadoresIn):
        conv = lambda ds: [I.DiaDaPessoa(**d.model_dump()) for d in ds]  # noqa: E731
        atual = {p: I.indicadores(conv(ds)) for p, ds in b.atual.items()}
        ant = {p: I.indicadores(conv(ds)) for p, ds in b.anterior.items()}
        geral = I.indicadores([x for ds in b.atual.values() for x in conv(ds)])
        return {"geral": geral.__dict__, "pessoas": {p: i.__dict__ for p, i in atual.items()},
                "ranking": [p.__dict__ for p in I.ranking(atual, b.nomes, ant, b.minimo_de_dias)]}

    @r.post("/alertas")
    async def alertas(b: AlertasIn):
        cads = [I.Cadastro(c.id, c.nome, c.admissao, tuple(c.ferias_gozadas), tuple(tuple(x) for x in c.licencas),
                           tuple(tuple(x) for x in c.atestados_pendentes)) for c in b.cadastros]
        esc = None
        if b.escala:
            todas = [d for ds in b.escala.values() for d in ds]
            esc = E.Escala(min(todas), max(todas), b.escala) if todas else None
        al = I.alertas(hoje=b.hoje, cadastros=cads, escala=esc, saldos=_saldos(b.lancamentos, b.hoje, b.validade_meses),
                       faltas_sem_justificativa=b.faltas_sem_justificativa)
        return [{"tipo": a.tipo, "pessoa": a.pessoa, "titulo": a.titulo, "detalhe": a.detalhe,
                 "prazo": a.prazo.isoformat() if a.prazo else None, "nivel": a.nivel} for a in al]

    return r
