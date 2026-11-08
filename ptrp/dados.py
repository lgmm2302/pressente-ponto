"""O PTRP e o banco: cadastros (horários, escalas, regras, feriados),
tratamentos com ciência do empregado, e a carga do que a apuração precisa.

Lê as marcações do REP-P (`registros`, tipo 7) sem nunca escrever nelas.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import asyncpg

from reppp import formato as f
from reppp.registro import Recusado
from reppp import registro as _registro

from .apuracao import DSR, FOLGA, Apuracao, Horario, Marca, apurar
from .regras import Regras

ESQUEMA = (Path(__file__).parent / "esquema.sql").read_text(encoding="utf-8")
DIAS = ("0", "1", "2", "3", "4", "5", "6")


def agora() -> datetime:
    return _registro.agora()


async def criar_esquema(conn: asyncpg.Connection) -> None:
    await _registro.criar_esquema(conn)
    await conn.execute(ESQUEMA)


def _cpf(c: str, quem: str) -> str:
    d = f.so_digitos(c)
    if not f.cpf_valido(d):
        raise Recusado(f"CPF {quem} inválido")
    return d


def _motivo(m: str) -> str:
    m = " ".join((m or "").split())
    if len(m) < 3:
        raise Recusado("o motivo é obrigatório (no mínimo 3 caracteres)")
    if len(m) > 150:
        raise Recusado("o motivo cabe em 150 caracteres (AEJ)")
    try:
        return f.texto_do_cadastro(m, "motivo")
    except f.ErroDeFormato as e:
        raise Recusado(str(e)) from e


# ─────────────────────────────────────────────────────────────────────────────
# Cadastros
# ─────────────────────────────────────────────────────────────────────────────

async def cadastrar_horario(conn, eid: int, *, codigo: str, periodos: list[list[str]], autor_cpf: str,
                            intervalo_pre_assinalado: bool = False) -> int:
    try:
        h = Horario(codigo, tuple((str(e), str(s)) for e, s in periodos), intervalo_pre_assinalado)
    except (ValueError, TypeError) as e:
        raise Recusado(f"horário inválido: {e}") from e
    try:
        return await conn.fetchval(
            """INSERT INTO ptrp_horarios (estabelecimento_id, codigo, periodos, intervalo_pre_assinalado, autor_cpf)
               VALUES ($1,$2,$3::jsonb,$4,$5) RETURNING id""",
            eid, h.codigo, json.dumps(h.periodos), h.intervalo_pre_assinalado, _cpf(autor_cpf, "do autor"))
    except asyncpg.UniqueViolationError as e:
        raise Recusado("já existe horário com esse código: horário não se edita, crie outro código") from e


async def cadastrar_regras(conn, eid: int, *, vigencia: date, regras: dict, autor_cpf: str) -> int:
    try:
        r = Regras.de_dict(regras)
    except (ValueError, TypeError) as e:
        raise Recusado(f"regras inválidas: {e}") from e
    try:
        return await conn.fetchval(
            "INSERT INTO ptrp_regras (estabelecimento_id, vigencia, regras, autor_cpf) VALUES ($1,$2,$3::jsonb,$4) RETURNING id",
            eid, vigencia, json.dumps(r.como_dict()), _cpf(autor_cpf, "do autor"))
    except asyncpg.UniqueViolationError as e:
        raise Recusado("já há regras com essa vigência") from e


async def cadastrar_escala(conn, eid: int, *, cpf: str, vigencia: date, semana: dict[str, str], autor_cpf: str) -> int:
    c = _cpf(cpf, "do empregado")
    if set(semana) != set(DIAS):
        raise Recusado("a escala precisa dos 7 dias da semana (0 = segunda … 6 = domingo)")
    codigos = {v for v in semana.values() if v not in (DSR, FOLGA)}
    if codigos:
        achados = {r["codigo"] for r in await conn.fetch(
            "SELECT codigo FROM ptrp_horarios WHERE estabelecimento_id=$1 AND codigo = ANY($2::text[])", eid, list(codigos))}
        if codigos - achados:
            raise Recusado(f"horário inexistente: {', '.join(sorted(codigos - achados))}")
    if DSR not in semana.values():
        raise Recusado("a escala precisa de um dia de descanso semanal remunerado (DSR)")
    try:
        return await conn.fetchval(
            """INSERT INTO ptrp_escalas (estabelecimento_id, cpf, vigencia, semana, autor_cpf)
               VALUES ($1,$2,$3,$4::jsonb,$5) RETURNING id""",
            eid, c, vigencia, json.dumps(semana), _cpf(autor_cpf, "do autor"))
    except asyncpg.UniqueViolationError as e:
        raise Recusado("já há escala desse empregado com essa vigência") from e


async def cadastrar_feriado(conn, eid: int, *, data: date, descricao: str, autor_cpf: str, ativo: bool = True) -> int:
    return await conn.fetchval(
        "INSERT INTO ptrp_feriados (estabelecimento_id, data, descricao, ativo, autor_cpf) VALUES ($1,$2,$3,$4,$5) RETURNING id",
        eid, data, _motivo(descricao), ativo, _cpf(autor_cpf, "do autor"))


async def cadastrar_matricula(conn, eid: int, *, cpf: str, matricula_esocial: str) -> None:
    m = (matricula_esocial or "").strip()
    if not m or len(m) > 30 or "|" in m:
        raise Recusado("matrícula do eSocial: 1 a 30 caracteres")
    try:
        await conn.execute("INSERT INTO ptrp_vinculos (estabelecimento_id, cpf, matricula_esocial) VALUES ($1,$2,$3)",
                           eid, _cpf(cpf, "do empregado"), m)
    except asyncpg.UniqueViolationError as e:
        raise Recusado("matrícula já cadastrada para esse vínculo") from e


# ─────────────────────────────────────────────────────────────────────────────
# Tratamentos (CLT art. 74; Portaria 671, art. 82): a original fica, o ajuste é
# um registro novo, com motivo, autor e a ciência do empregado.
# ─────────────────────────────────────────────────────────────────────────────

async def _fuso(conn, eid: int) -> str:
    fz = await conn.fetchval("SELECT fuso FROM estabelecimentos WHERE id=$1", eid)
    if fz is None:
        raise Recusado("estabelecimento desconhecido")
    return fz


async def incluir_marcacao(conn, eid: int, *, cpf: str, momento: datetime, motivo: str, autor_cpf: str) -> int:
    """Marcação esquecida (fonte «I» no AEJ). Não pode estar no futuro."""
    c = _cpf(cpf, "do empregado")
    if momento.tzinfo is None:
        raise Recusado("a marcação incluída precisa de data, hora e fuso")
    if momento > agora():
        raise Recusado("não se inclui marcação no futuro")
    await _fuso(conn, eid)
    return await conn.fetchval(
        """INSERT INTO ptrp_tratamentos (estabelecimento_id, cpf, tipo, momento, motivo, autor_cpf)
           VALUES ($1,$2,'incluir',$3,$4,$5) RETURNING id""", eid, c, momento, _motivo(motivo), _cpf(autor_cpf, "do autor"))


async def desconsiderar_marcacao(conn, eid: int, *, registro_id: int, motivo: str, autor_cpf: str) -> int:
    """Batida indevida (duplicidade, engano) deixa de contar, mas continua no
    espelho e no AEJ como «D», com o motivo."""
    r = await conn.fetchrow("SELECT cpf FROM registros WHERE id=$1 AND estabelecimento_id=$2 AND tipo=7", registro_id, eid)
    if r is None:
        raise Recusado("marcação inexistente neste estabelecimento")
    ja = await conn.fetchval(
        """SELECT t.id FROM ptrp_tratamentos t
            WHERE t.registro_id=$1 AND t.tipo='desconsiderar'
              AND NOT EXISTS (SELECT 1 FROM ptrp_tratamentos v WHERE v.tipo='revogar' AND v.alvo_id=t.id)""", registro_id)
    if ja:
        raise Recusado("essa marcação já está desconsiderada")
    return await conn.fetchval(
        """INSERT INTO ptrp_tratamentos (estabelecimento_id, cpf, tipo, registro_id, motivo, autor_cpf)
           VALUES ($1,$2,'desconsiderar',$3,$4,$5) RETURNING id""",
        eid, r["cpf"], registro_id, _motivo(motivo), _cpf(autor_cpf, "do autor"))


async def abonar(conn, eid: int, *, cpf: str, data: date, motivo: str, autor_cpf: str) -> int:
    await _fuso(conn, eid)
    return await conn.fetchval(
        """INSERT INTO ptrp_tratamentos (estabelecimento_id, cpf, tipo, data, motivo, autor_cpf)
           VALUES ($1,$2,'abonar',$3,$4,$5) RETURNING id""",
        eid, _cpf(cpf, "do empregado"), data, _motivo(motivo), _cpf(autor_cpf, "do autor"))


async def revogar(conn, eid: int, *, tratamento_id: int, motivo: str, autor_cpf: str) -> int:
    alvo = await conn.fetchrow("SELECT cpf, tipo FROM ptrp_tratamentos WHERE id=$1 AND estabelecimento_id=$2",
                               tratamento_id, eid)
    if alvo is None or alvo["tipo"] == "revogar":
        raise Recusado("tratamento inexistente (ou é uma revogação)")
    if await conn.fetchval("SELECT 1 FROM ptrp_tratamentos WHERE tipo='revogar' AND alvo_id=$1", tratamento_id):
        raise Recusado("esse tratamento já foi revogado")
    return await conn.fetchval(
        """INSERT INTO ptrp_tratamentos (estabelecimento_id, cpf, tipo, alvo_id, motivo, autor_cpf)
           VALUES ($1,$2,'revogar',$3,$4,$5) RETURNING id""",
        eid, alvo["cpf"], tratamento_id, _motivo(motivo), _cpf(autor_cpf, "do autor"))


async def dar_ciencia(conn, eid: int, *, tratamento_id: int, cpf: str, decisao: str, comentario: str = "",
                      origem: dict | None = None) -> int:
    """O empregado (e só ele) diz se está ciente ou recusa o tratamento. A
    decisão é uma só e não muda; a recusa vai para o espelho."""
    if decisao not in ("ciente", "recusado"):
        raise Recusado("decisão: ciente ou recusado")
    t = await conn.fetchrow("SELECT cpf, tipo FROM ptrp_tratamentos WHERE id=$1 AND estabelecimento_id=$2",
                            tratamento_id, eid)
    if t is None or t["cpf"] != f.so_digitos(cpf):
        raise Recusado("tratamento inexistente para esse empregado")
    if decisao == "recusado" and len((comentario or "").strip()) < 3:
        raise Recusado("a recusa precisa dizer o porquê")
    try:
        return await conn.fetchval(
            "INSERT INTO ptrp_ciencias (tratamento_id, decisao, comentario, origem) VALUES ($1,$2,$3,$4::jsonb) RETURNING id",
            tratamento_id, decisao, " ".join((comentario or "").split())[:500], json.dumps(origem or {}))
    except asyncpg.UniqueViolationError as e:
        raise Recusado("o empregado já se manifestou sobre esse tratamento") from e


async def pendentes_de_ciencia(conn, eid: int, cpf: str) -> list[asyncpg.Record]:
    return await conn.fetch(
        """SELECT t.id, t.tipo, t.momento, t.registro_id, t.data, t.alvo_id, t.motivo, t.criado_em
             FROM ptrp_tratamentos t LEFT JOIN ptrp_ciencias c ON c.tratamento_id = t.id
            WHERE t.estabelecimento_id=$1 AND t.cpf=$2 AND c.id IS NULL ORDER BY t.id""", eid, f.so_digitos(cpf))


# ─────────────────────────────────────────────────────────────────────────────
# A carga para a apuração
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Entrada:
    fuso: str
    marcas: list[Marca]
    escalas: list[asyncpg.Record]
    horarios: dict[str, Horario]
    regras: list[tuple[date, Regras]]
    feriados: set[date]
    abonos: dict[date, str]

    def escala(self, d: date):
        vig = [e for e in self.escalas if e["vigencia"] <= d]
        if not vig:
            return None
        v = json.loads(vig[-1]["semana"])[str(d.weekday())]
        return v if v in (DSR, FOLGA) else self.horarios.get(v)

    def regras_do_dia(self, d: date) -> Regras:
        vig = [r for v, r in self.regras if v <= d]
        return vig[-1] if vig else Regras()

    def apurar(self, inicio: date, fim: date, banco_anterior: int = 0) -> Apuracao:
        return apurar(inicio=inicio, fim=fim, marcas=self.marcas, escala=self.escala, regras=self.regras_do_dia,
                      feriados=self.feriados, abonos=self.abonos, banco_anterior=banco_anterior)


async def carregar(conn, eid: int, cpf: str, inicio: date, fim: date) -> Entrada:
    c = f.so_digitos(cpf)
    fuso = await _fuso(conn, eid)
    zona = ZoneInfo(fuso)
    de = datetime.combine(inicio - timedelta(days=2), time(), zona)
    ate = datetime.combine(fim + timedelta(days=2), time(), zona)
    trat = await conn.fetch(
        """SELECT t.*, c.decisao, c.comentario,
                  EXISTS (SELECT 1 FROM ptrp_tratamentos v WHERE v.tipo='revogar' AND v.alvo_id=t.id) AS revogado
             FROM ptrp_tratamentos t LEFT JOIN ptrp_ciencias c ON c.tratamento_id=t.id
            WHERE t.estabelecimento_id=$1 AND t.cpf=$2 AND t.tipo <> 'revogar'""", eid, c)
    validos = [t for t in trat if not t["revogado"]]
    desc = {t["registro_id"]: t for t in validos if t["tipo"] == "desconsiderar"}
    local = lambda x: x.astimezone(zona).replace(tzinfo=None)  # noqa: E731
    marcas: list[Marca] = []
    for r in await conn.fetch(
            """SELECT id, marcado_em FROM registros WHERE estabelecimento_id=$1 AND cpf=$2 AND tipo=7
                  AND marcado_em >= $3 AND marcado_em < $4 ORDER BY nsr""", eid, c, de, ate):
        t = desc.get(r["id"])
        marcas.append(Marca(local(r["marcado_em"]), "O", registro_id=r["id"],
                            tratamento_id=t["id"] if t else None, desconsiderada=t is not None,
                            motivo=t["motivo"] if t else "", contestada=bool(t and t["decisao"] == "recusado")))
    for t in validos:
        if t["tipo"] == "incluir" and de <= t["momento"] < ate:
            marcas.append(Marca(local(t["momento"]), "I", tratamento_id=t["id"], motivo=t["motivo"],
                                contestada=t["decisao"] == "recusado"))
    abonos = {t["data"]: t["motivo"] for t in validos if t["tipo"] == "abonar"}
    escalas = await conn.fetch("SELECT vigencia, semana FROM ptrp_escalas WHERE estabelecimento_id=$1 AND cpf=$2 "
                               "ORDER BY vigencia", eid, c)
    horarios = {h["codigo"]: Horario(h["codigo"], tuple(tuple(p) for p in json.loads(h["periodos"])),
                                     h["intervalo_pre_assinalado"])
                for h in await conn.fetch("SELECT * FROM ptrp_horarios WHERE estabelecimento_id=$1", eid)}
    regras = [(r["vigencia"], Regras.de_dict(json.loads(r["regras"])))
              for r in await conn.fetch("SELECT vigencia, regras FROM ptrp_regras WHERE estabelecimento_id=$1 "
                                        "ORDER BY vigencia", eid)]
    feriados: dict[date, bool] = {}
    for r in await conn.fetch("SELECT data, ativo FROM ptrp_feriados WHERE estabelecimento_id=$1 ORDER BY id", eid):
        feriados[r["data"]] = r["ativo"]
    return Entrada(fuso, marcas, escalas, horarios, regras, {d for d, a in feriados.items() if a}, abonos)


async def saldo_anterior_do_banco(conn, eid: int, cpf: str, competencia: str) -> int:
    """O saldo final do banco no último espelho antes desta competência."""
    r = await conn.fetchval(
        """SELECT resumo->>'banco_saldo' FROM ptrp_espelhos
            WHERE estabelecimento_id=$1 AND cpf=$2 AND competencia < $3
            ORDER BY competencia DESC, versao DESC LIMIT 1""", eid, f.so_digitos(cpf), competencia)
    return int(r) if r is not None else 0
