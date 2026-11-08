"""O núcleo do REP-P: cada operação vira um registro numerado, imutável, com a
linha do AFD já pronta no momento da gravação.

Quem chama passa uma conexão asyncpg. Cada função abre a sua transação, trava a
linha do estabelecimento (o NSR sai em fila, sem buraco) e grava UM registro.
A hora é sempre a do servidor (`agora()`), nunca a que o coletor manda — exceto
na marcação off-line, em que a hora da batida vem do coletor e fica marcada
como off-line no próprio registro.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import asyncpg

from . import formato as f

ESQUEMA = (Path(__file__).parent / "esquema.sql").read_text(encoding="utf-8")

# Uma batida off-line não pode dizer que aconteceu no futuro. A tolerância cobre
# o desvio de relógio que o Anexo IX admite (30 s).
TOLERANCIA_DO_RELOGIO = timedelta(seconds=30)


def _agora_real() -> datetime:
    return datetime.now(timezone.utc)


agora: Callable[[], datetime] = _agora_real


class Recusado(ValueError):
    """Pedido que não vira registro (dado inválido, empregado desconhecido...).
    Nada foi gravado e nenhum NSR foi gasto."""


@dataclass(frozen=True)
class Marcacao:
    registro_id: int
    estabelecimento_id: int
    nsr: int
    cpf: str
    marcado_em: datetime
    gravado_em: datetime
    coletor: int
    offline: bool
    hash: str
    linha: str


async def criar_esquema(conn: asyncpg.Connection) -> None:
    await conn.execute(ESQUEMA)


def hash_da_chave(chave: str) -> str:
    return hashlib.sha256(chave.encode()).hexdigest()


def _empregador(linha: asyncpg.Record) -> f.Empregador:
    return f.Empregador(
        tipo_identificador=linha["tipo_identificador"],
        identificador=linha["identificador"],
        razao_social=linha["razao_social"],
        cno_caepf=linha["cno_caepf"],
        local_prestacao=linha["local_prestacao"],
    )


async def _travar(conn: asyncpg.Connection, estabelecimento_id: int) -> asyncpg.Record:
    est = await conn.fetchrow("SELECT * FROM estabelecimentos WHERE id = $1 FOR UPDATE", estabelecimento_id)
    if est is None:
        raise Recusado("estabelecimento desconhecido")
    return est


async def _gravar(conn: asyncpg.Connection, est: asyncpg.Record, *, nsr: int, tipo: int,
                  gravado_em: datetime, linha: str, marcado_em: datetime | None = None,
                  cpf: str | None = None, coletor: int | None = None, offline: bool | None = None,
                  hash_: str | None = None, hash_anterior: str | None = None,
                  origem: dict | None = None) -> int:
    rid = await conn.fetchval(
        """INSERT INTO registros (estabelecimento_id, nsr, tipo, gravado_em, marcado_em, cpf,
               coletor, offline, hash, hash_anterior, origem, linha)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12) RETURNING id""",
        est["id"], nsr, tipo, gravado_em, marcado_em, cpf, coletor, offline, hash_, hash_anterior,
        json.dumps(origem or {}), linha,
    )
    await conn.execute(
        "UPDATE estabelecimentos SET ultimo_nsr = $2, ultimo_hash = COALESCE($3, ultimo_hash) WHERE id = $1",
        est["id"], nsr, hash_,
    )
    return rid


def _texto(valor: str, nome: str) -> str:
    try:
        return f.texto_do_cadastro(valor, nome)
    except f.ErroDeFormato as e:
        raise Recusado(str(e)) from e


def _validar_empregador(tipo: int, identificador: str, razao_social: str, fuso: str) -> str:
    ident = f.identificador_normalizado(tipo, identificador)
    if not f.identificador_valido(tipo, ident):
        raise Recusado("CNPJ/CPF do empregador inválido")
    if not (razao_social or "").strip():
        raise Recusado("razão social ou nome do empregador é obrigatório")
    try:
        ZoneInfo(fuso)
    except Exception as e:  # noqa: BLE001
        raise Recusado(f"fuso desconhecido: {fuso}") from e
    return ident


def _validar_cpf(cpf: str, quem: str) -> str:
    c = f.so_digitos(cpf)
    if not f.cpf_valido(c):
        raise Recusado(f"CPF {quem} inválido")
    return c


# ─────────────────────────────────────────────────────────────────────────────
# Empregador (registro 2)
# ─────────────────────────────────────────────────────────────────────────────

async def cadastrar_estabelecimento(conn: asyncpg.Connection, *, tipo_identificador: int,
                                    identificador: str, razao_social: str, cpf_responsavel: str,
                                    cno_caepf: str | None = None, local_prestacao: str = "",
                                    fuso: str = "America/Sao_Paulo") -> tuple[int, str]:
    """Cria o estabelecimento e grava o registro 2 (NSR 1). Devolve (id, chave da
    API) — a chave aparece só aqui; o banco guarda apenas o hash dela."""
    ident = _validar_empregador(tipo_identificador, identificador, razao_social, fuso)
    resp = _validar_cpf(cpf_responsavel, "do responsável")
    razao_social = _texto(razao_social, "razão social")
    local_prestacao = _texto(local_prestacao, "local de prestação de serviços")
    chave = secrets.token_urlsafe(32)
    async with conn.transaction():
        eid = await conn.fetchval(
            """INSERT INTO estabelecimentos (tipo_identificador, identificador, cno_caepf, razao_social,
                   local_prestacao, fuso, chave_api_hash) VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id""",
            tipo_identificador, ident, f.so_digitos(cno_caepf or "") or None, razao_social.strip(),
            local_prestacao.strip(), fuso, hash_da_chave(chave),
        )
        est = await _travar(conn, eid)
        await _registro_2(conn, est, resp)
    return eid, chave


async def alterar_estabelecimento(conn: asyncpg.Connection, estabelecimento_id: int, *,
                                  cpf_responsavel: str, razao_social: str | None = None,
                                  cno_caepf: str | None = None, local_prestacao: str | None = None) -> int:
    """Muda nome, CNO/CAEPF ou local e grava um novo registro 2. O CNPJ/CPF do
    estabelecimento não muda: outro documento é outro estabelecimento."""
    resp = _validar_cpf(cpf_responsavel, "do responsável")
    if razao_social is not None:
        razao_social = _texto(razao_social, "razão social")
    if local_prestacao is not None:
        local_prestacao = _texto(local_prestacao, "local de prestação de serviços")
    async with conn.transaction():
        est = await _travar(conn, estabelecimento_id)
        novo_nome = (razao_social if razao_social is not None else est["razao_social"]).strip()
        if not novo_nome:
            raise Recusado("razão social ou nome do empregador é obrigatório")
        await conn.execute(
            "UPDATE estabelecimentos SET razao_social=$2, cno_caepf=$3, local_prestacao=$4 WHERE id=$1",
            est["id"], novo_nome,
            (f.so_digitos(cno_caepf) or None) if cno_caepf is not None else est["cno_caepf"],
            local_prestacao.strip() if local_prestacao is not None else est["local_prestacao"],
        )
        est = await conn.fetchrow("SELECT * FROM estabelecimentos WHERE id = $1", est["id"])
        return await _registro_2(conn, est, resp)


async def _registro_2(conn: asyncpg.Connection, est: asyncpg.Record, cpf_responsavel: str) -> int:
    nsr = est["ultimo_nsr"] + 1
    quando = agora()
    linha = f.registro_2(nsr=nsr, gravado_em=quando, fuso=est["fuso"], cpf_responsavel=cpf_responsavel,
                         empregador=_empregador(est))
    return await _gravar(conn, est, nsr=nsr, tipo=2, gravado_em=quando, linha=linha,
                         cpf=cpf_responsavel)


# ─────────────────────────────────────────────────────────────────────────────
# Empregados (registro 5)
# ─────────────────────────────────────────────────────────────────────────────

async def incluir_empregado(conn: asyncpg.Connection, estabelecimento_id: int, *, cpf: str, nome: str,
                            cpf_responsavel: str) -> int:
    c = _validar_cpf(cpf, "do empregado")
    resp = _validar_cpf(cpf_responsavel, "do responsável")
    nome = _texto(nome, "nome do empregado")
    if not nome:
        raise Recusado("nome do empregado é obrigatório")
    async with conn.transaction():
        est = await _travar(conn, estabelecimento_id)
        atual = await conn.fetchrow("SELECT * FROM empregados WHERE estabelecimento_id=$1 AND cpf=$2", est["id"], c)
        if atual is not None and atual["ativo"]:
            raise Recusado("empregado já incluído neste estabelecimento")
        if atual is None:
            await conn.execute("INSERT INTO empregados (estabelecimento_id, cpf, nome) VALUES ($1,$2,$3)",
                               est["id"], c, nome.strip())
        else:
            await conn.execute("UPDATE empregados SET nome=$3, ativo=true WHERE estabelecimento_id=$1 AND cpf=$2",
                               est["id"], c, nome.strip())
        return await _registro_5(conn, est, "I", c, nome.strip(), resp)


async def alterar_empregado(conn: asyncpg.Connection, estabelecimento_id: int, *, cpf: str, nome: str,
                            cpf_responsavel: str) -> int:
    c = _validar_cpf(cpf, "do empregado")
    resp = _validar_cpf(cpf_responsavel, "do responsável")
    nome = _texto(nome, "nome do empregado")
    if not nome:
        raise Recusado("nome do empregado é obrigatório")
    async with conn.transaction():
        est = await _travar(conn, estabelecimento_id)
        n = await conn.execute("UPDATE empregados SET nome=$3 WHERE estabelecimento_id=$1 AND cpf=$2 AND ativo",
                               est["id"], c, nome.strip())
        if n.endswith(" 0"):
            raise Recusado("empregado não está ativo neste estabelecimento")
        return await _registro_5(conn, est, "A", c, nome.strip(), resp)


async def excluir_empregado(conn: asyncpg.Connection, estabelecimento_id: int, *, cpf: str,
                            cpf_responsavel: str) -> int:
    """Tira o empregado do REP-P (registro 5 «E»). As batidas dele continuam."""
    c = _validar_cpf(cpf, "do empregado")
    resp = _validar_cpf(cpf_responsavel, "do responsável")
    async with conn.transaction():
        est = await _travar(conn, estabelecimento_id)
        atual = await conn.fetchrow(
            "SELECT * FROM empregados WHERE estabelecimento_id=$1 AND cpf=$2 AND ativo", est["id"], c)
        if atual is None:
            raise Recusado("empregado não está ativo neste estabelecimento")
        await conn.execute("UPDATE empregados SET ativo=false WHERE id=$1", atual["id"])
        return await _registro_5(conn, est, "E", c, atual["nome"], resp)


async def _registro_5(conn, est, operacao: str, cpf: str, nome: str, resp: str) -> int:
    nsr = est["ultimo_nsr"] + 1
    quando = agora()
    linha = f.registro_5(nsr=nsr, gravado_em=quando, fuso=est["fuso"], operacao=operacao, cpf=cpf,
                         nome=nome, cpf_responsavel=resp)
    return await _gravar(conn, est, nsr=nsr, tipo=5, gravado_em=quando, linha=linha, cpf=cpf)


# ─────────────────────────────────────────────────────────────────────────────
# Eventos sensíveis (registro 6)
# ─────────────────────────────────────────────────────────────────────────────

async def registrar_evento(conn: asyncpg.Connection, estabelecimento_id: int, evento: int) -> int:
    if evento not in f.EVENTOS_DO_REP_P:
        raise Recusado(f"evento {evento} não existe para o REP-P")
    async with conn.transaction():
        est = await _travar(conn, estabelecimento_id)
        nsr = est["ultimo_nsr"] + 1
        quando = agora()
        linha = f.registro_6(nsr=nsr, gravado_em=quando, fuso=est["fuso"], evento=evento)
        return await _gravar(conn, est, nsr=nsr, tipo=6, gravado_em=quando, linha=linha)


# ─────────────────────────────────────────────────────────────────────────────
# A marcação (registro 7)
# ─────────────────────────────────────────────────────────────────────────────

async def marcar(conn: asyncpg.Connection, estabelecimento_id: int, *, cpf: str, coletor: int,
                 offline: bool = False, marcado_em_no_coletor: datetime | None = None,
                 origem: dict | None = None) -> Marcacao:
    """Grava a batida. Não há horário proibido, autorização prévia nem batida
    automática (art. 74): qualquer empregado ativo bate a qualquer hora.

    On-line: a hora da batida é a hora do servidor.
    Off-line: a hora da batida vem do coletor (`marcado_em_no_coletor`, com
    fuso) e a hora de gravação é a do servidor; o registro diz que foi off-line.
    """
    c = _validar_cpf(cpf, "do empregado")
    if coletor not in f.COLETORES:
        raise Recusado("coletor: 1 a 5")
    async with conn.transaction():
        est = await _travar(conn, estabelecimento_id)
        emp = await conn.fetchrow(
            "SELECT 1 FROM empregados WHERE estabelecimento_id=$1 AND cpf=$2 AND ativo", est["id"], c)
        if emp is None:
            raise Recusado("empregado não está ativo neste estabelecimento")
        gravado_em = agora()
        if offline:
            if marcado_em_no_coletor is None or marcado_em_no_coletor.tzinfo is None:
                raise Recusado("marcação off-line precisa da hora do coletor, com fuso")
            if marcado_em_no_coletor > gravado_em + TOLERANCIA_DO_RELOGIO:
                raise Recusado("marcação off-line com hora no futuro")
            marcado_em = marcado_em_no_coletor
        else:
            marcado_em = gravado_em
        nsr = est["ultimo_nsr"] + 1
        anterior = est["ultimo_hash"]
        linha, h = f.registro_7(nsr=nsr, marcado_em=marcado_em, gravado_em=gravado_em, fuso=est["fuso"],
                                cpf=c, coletor=coletor, offline=offline, hash_anterior=anterior)
        rid = await _gravar(conn, est, nsr=nsr, tipo=7, gravado_em=gravado_em, linha=linha,
                            marcado_em=marcado_em, cpf=c, coletor=coletor, offline=offline,
                            hash_=h, hash_anterior=anterior, origem=origem)
    return Marcacao(rid, est["id"], nsr, c, marcado_em, gravado_em, coletor, offline, h, linha)


# ─────────────────────────────────────────────────────────────────────────────
# A conferência: o que está gravado ainda é o que foi gravado?
# ─────────────────────────────────────────────────────────────────────────────

async def conferir(conn: asyncpg.Connection, estabelecimento_id: int) -> list[str]:
    """Refaz a corrente inteira do estabelecimento e devolve os problemas
    achados (lista vazia = íntegro): NSR sem buraco a partir de 1, CRC dos
    registros 2 e 5, hash de cada registro 7 recalculado e encadeado."""
    problemas: list[str] = []
    linhas = await conn.fetch(
        "SELECT nsr, tipo, linha, hash, hash_anterior FROM registros WHERE estabelecimento_id=$1 ORDER BY nsr",
        estabelecimento_id)
    esperado_nsr = 1
    anterior: str | None = None
    for r in linhas:
        if r["nsr"] != esperado_nsr:
            problemas.append(f"NSR {esperado_nsr} faltando (veio {r['nsr']})")
            esperado_nsr = r["nsr"]
        if r["linha"][:9] != f"{r['nsr']:09d}" or r["linha"][9] != str(r["tipo"]):
            problemas.append(f"NSR {r['nsr']}: linha não corresponde ao registro")
        if len(r["linha"]) != f.TAMANHO_POR_TIPO.get(r["tipo"], -1):
            problemas.append(f"NSR {r['nsr']}: tamanho da linha errado")
        if r["tipo"] in (2, 4, 5) and not f.crc_confere(r["linha"]):
            problemas.append(f"NSR {r['nsr']}: CRC não confere")
        if r["tipo"] == 7:
            recalculado = f.hash_da_marcacao(r["linha"][:73], anterior)
            if r["hash_anterior"] != anterior:
                problemas.append(f"NSR {r['nsr']}: corrente quebrada (hash anterior diferente)")
            if recalculado != r["hash"] or r["linha"][73:] != r["hash"]:
                problemas.append(f"NSR {r['nsr']}: hash da marcação não confere")
            anterior = r["hash"]
        esperado_nsr += 1
    est = await conn.fetchrow("SELECT ultimo_nsr, ultimo_hash FROM estabelecimentos WHERE id=$1", estabelecimento_id)
    if est is not None and linhas and est["ultimo_nsr"] != linhas[-1]["nsr"]:
        problemas.append("último NSR do estabelecimento difere do último registro")
    if est is not None and est["ultimo_hash"] != anterior:
        problemas.append("último hash do estabelecimento difere do último registro 7")
    return problemas
