"""A prova da batida (fase 7): o que sustenta a marcação em juízo além do que a
Portaria exige — onde, de que aparelho, de que rede e, se a empresa escolher,
com que rosto.

Regras que valem para tudo aqui:
  · NADA bloqueia a batida (Portaria 671, art. 74: sem restrição à marcação).
    Fora da cerca, sem local ou sem foto, a batida é gravada do mesmo jeito e
    sai marcada para o gestor ver.
  · O que foi apurado no instante da batida (distância, cerca, resultado)
    fica gravado com ela e não muda, mesmo se a cerca mudar depois.
  · Foto é dado biométrico (LGPD, art. 5º, II e art. 11): só com base legal
    registrada pela empresa, cifrada (AES-256-GCM), com prazo de guarda; cada
    vez que alguém a abre fica registrado quem, quando e por quê.
  · Localização só no instante da batida; nunca rastreamento.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

import asyncpg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VAR_CHAVE_FOTOS = "PONTO_CHAVE_FOTOS"
TAMANHO_MAXIMO_DA_FOTO = 400_000  # bytes
TIPOS_DE_FOTO = {b"\xff\xd8\xff": "image/jpeg", b"\x89PNG": "image/png"}
FOLGA_DE_PRECISAO_MAXIMA = 100  # m: a precisão do GPS ajuda até aqui, não mais

DENTRO, FORA, SEM_LOCAL, SEM_CERCA = "dentro", "fora", "sem_local", "sem_cerca"


class ProvaInvalida(ValueError):
    pass


@dataclass(frozen=True)
class Local:
    latitude: float
    longitude: float
    precisao_m: float | None = None

    def __post_init__(self) -> None:
        if not (-90 <= self.latitude <= 90 and -180 <= self.longitude <= 180):
            raise ProvaInvalida("coordenadas fora do globo")
        if self.precisao_m is not None and self.precisao_m < 0:
            raise ProvaInvalida("precisão negativa")


@dataclass(frozen=True)
class Prova:
    local: Local | None = None
    dispositivo: str = ""   # identificador do aparelho que o app gerou (não é IMEI)
    ip: str = ""
    foto: bytes | None = None

    def __post_init__(self) -> None:
        if len(self.dispositivo) > 120:
            raise ProvaInvalida("identificador do dispositivo longo demais")
        if self.foto is not None:
            if len(self.foto) > TAMANHO_MAXIMO_DA_FOTO:
                raise ProvaInvalida("foto acima de 400 KB")
            if tipo_da_foto(self.foto) is None:
                raise ProvaInvalida("foto precisa ser JPEG ou PNG")


def tipo_da_foto(dados: bytes) -> str | None:
    for assinatura, tipo in TIPOS_DE_FOTO.items():
        if dados.startswith(assinatura):
            return tipo
    return None


def distancia_m(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    """Distância de grande círculo (haversine), em metros."""
    r = 6_371_008.8
    f1, f2 = math.radians(a_lat), math.radians(b_lat)
    df, dl = math.radians(b_lat - a_lat), math.radians(b_lon - a_lon)
    h = math.sin(df / 2) ** 2 + math.cos(f1) * math.cos(f2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def avaliar_cercas(local: Local | None, cercas: list[asyncpg.Record] | list[dict]) -> dict:
    """Onde a batida aconteceu em relação às cercas ativas do estabelecimento.
    «dentro» se cair em qualquer cerca (com a folga da precisão do GPS, até
    100 m); senão «fora», com a cerca mais próxima e a distância."""
    if not cercas:
        return {"resultado": SEM_CERCA}
    if local is None:
        return {"resultado": SEM_LOCAL}
    folga = min(local.precisao_m or 0, FOLGA_DE_PRECISAO_MAXIMA)
    melhor = None
    for c in cercas:
        d = distancia_m(local.latitude, local.longitude, c["latitude"], c["longitude"])
        excesso = d - c["raio_m"] - folga
        if melhor is None or excesso < melhor[0]:
            melhor = (excesso, c, d)
    excesso, cerca, d = melhor
    return {"resultado": DENTRO if excesso <= 0 else FORA, "cerca": cerca["nome"], "distancia_m": round(d),
            "raio_m": cerca["raio_m"]}


# ─────────────────────────────────────────────────────────────────────────────
# Configuração por estabelecimento (com histórico)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Configuracao:
    coletar_local: bool = False
    exigir_local: bool = False      # sem local = alerta (a batida entra do mesmo jeito)
    coletar_foto: bool = False
    exigir_foto: bool = False       # sem foto = alerta (idem)
    base_legal_foto: str = ""       # LGPD art. 11: obrigatória para coletar foto
    retencao_foto_dias: int = 0     # 0 = até o fim do prazo de guarda do ponto

    def __post_init__(self) -> None:
        if self.coletar_foto and len(self.base_legal_foto.strip()) < 10:
            raise ProvaInvalida("para coletar foto, registre a base legal (LGPD, art. 11) — ex.: «prevenção à "
                                "fraude e segurança do titular, art. 11, II, g»")
        if self.exigir_foto and not self.coletar_foto:
            raise ProvaInvalida("exigir foto pede coletar foto")
        if self.exigir_local and not self.coletar_local:
            raise ProvaInvalida("exigir local pede coletar local")
        if self.retencao_foto_dias < 0:
            raise ProvaInvalida("prazo de guarda negativo")


async def configuracao(conn: asyncpg.Connection, eid: int) -> Configuracao:
    r = await conn.fetchval("SELECT config FROM provas_config WHERE estabelecimento_id=$1 ORDER BY id DESC LIMIT 1", eid)
    return Configuracao(**json.loads(r)) if r else Configuracao()


async def configurar(conn: asyncpg.Connection, eid: int, cfg: Configuracao, autor_cpf: str) -> int:
    return await conn.fetchval(
        "INSERT INTO provas_config (estabelecimento_id, config, autor_cpf) VALUES ($1,$2::jsonb,$3) RETURNING id",
        eid, json.dumps(asdict(cfg)), autor_cpf)


# ─────────────────────────────────────────────────────────────────────────────
# Foto cifrada
# ─────────────────────────────────────────────────────────────────────────────

def _chave() -> bytes:
    bruto = os.environ.get(VAR_CHAVE_FOTOS, "")
    try:
        chave = base64.b64decode(bruto, validate=True)
    except ValueError as e:
        raise ProvaInvalida("chave das fotos inválida") from e
    if len(chave) != 32:
        raise ProvaInvalida("chave das fotos ausente ou com tamanho errado (32 bytes em base64)")
    return chave


def cifrar(foto: bytes, associado: bytes) -> tuple[bytes, bytes]:
    nonce = os.urandom(12)
    return nonce, AESGCM(_chave()).encrypt(nonce, foto, associado)


def decifrar(nonce: bytes, cifrada: bytes, associado: bytes) -> bytes:
    return AESGCM(_chave()).decrypt(nonce, cifrada, associado)


def resumo_da_prova(prova: Prova, avaliacao: dict, foto_sha256: str | None, cfg: Configuracao) -> dict:
    """O que vai para `registros.origem` (imutável): sem a foto, só o hash dela."""
    alertas = []
    if cfg.exigir_local and prova.local is None:
        alertas.append("batida sem localização")
    if avaliacao.get("resultado") == FORA:
        alertas.append(f"batida fora da cerca «{avaliacao['cerca']}» ({avaliacao['distancia_m']} m do centro, "
                       f"raio de {avaliacao['raio_m']} m)")
    if cfg.exigir_foto and foto_sha256 is None:
        alertas.append("batida sem foto")
    return {
        "local": asdict(prova.local) if prova.local else None,  # quem chama já tirou se a empresa não coleta
        "cerca": avaliacao,
        "dispositivo": prova.dispositivo or None,
        "ip": prova.ip or None,
        "foto_sha256": foto_sha256,
        "alertas": alertas,
    }


def hash_da_prova(resumo: dict) -> str:
    return hashlib.sha256(json.dumps(resumo, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def expira_em(cfg: Configuracao, agora: datetime) -> datetime | None:
    return agora + timedelta(days=cfg.retencao_foto_dias) if cfg.retencao_foto_dias else None


# ─────────────────────────────────────────────────────────────────────────────
# Cercas e acesso às fotos
# ─────────────────────────────────────────────────────────────────────────────

async def criar_cerca(conn: asyncpg.Connection, eid: int, *, nome: str, latitude: float, longitude: float,
                      raio_m: int, autor_cpf: str) -> int:
    Local(latitude, longitude)
    nome = " ".join((nome or "").split())
    if not 2 <= len(nome) <= 60:
        raise ProvaInvalida("nome da cerca: 2 a 60 caracteres")
    if not 10 <= raio_m <= 5000:
        raise ProvaInvalida("raio da cerca: de 10 a 5000 m")
    try:
        return await conn.fetchval(
            """INSERT INTO cercas (estabelecimento_id, nome, latitude, longitude, raio_m, autor_cpf)
               VALUES ($1,$2,$3,$4,$5,$6) RETURNING id""", eid, nome, latitude, longitude, raio_m, autor_cpf)
    except asyncpg.UniqueViolationError as e:
        raise ProvaInvalida("já existe cerca ativa com esse nome") from e


async def desativar_cerca(conn: asyncpg.Connection, eid: int, cerca_id: int) -> None:
    n = await conn.execute("UPDATE cercas SET ativa=false, desativada_em=now() WHERE id=$1 AND estabelecimento_id=$2 "
                           "AND ativa", cerca_id, eid)
    if n.endswith(" 0"):
        raise ProvaInvalida("cerca inexistente ou já desativada")


async def ver_foto(conn: asyncpg.Connection, eid: int, registro_id: int, *, quem_cpf: str, motivo: str) -> tuple[bytes, str]:
    """Abre a foto de uma batida. Cada abertura fica registrada (quem, quando,
    por quê) — é a trilha que a LGPD pede para dado sensível."""
    r = await conn.fetchrow(
        """SELECT f.nonce, f.cifrada, f.tipo, f.sha256, r.nsr FROM fotos f JOIN registros r ON r.id = f.registro_id
            WHERE f.registro_id=$1 AND r.estabelecimento_id=$2""", registro_id, eid)
    if r is None:
        raise LookupError("foto inexistente (ou já expurgada pelo prazo de guarda)")
    motivo = " ".join((motivo or "").split())
    if len(motivo) < 3:
        raise ProvaInvalida("diga o motivo de abrir a foto")
    async with conn.transaction():
        await conn.execute("INSERT INTO acessos_a_fotos (registro_id, quem_cpf, motivo) VALUES ($1,$2,$3)",
                           registro_id, quem_cpf, motivo[:300])
    foto = decifrar(bytes(r["nonce"]), bytes(r["cifrada"]), f"{eid}:{r['nsr']}".encode())
    if hashlib.sha256(foto).hexdigest() != r["sha256"]:
        raise ProvaInvalida("a foto não confere com o hash gravado na batida")
    return foto, r["tipo"]


async def expurgar_fotos_vencidas(conn: asyncpg.Connection) -> int:
    n = await conn.execute("DELETE FROM fotos WHERE expira_em IS NOT NULL AND expira_em <= now()")
    return int(n.split()[-1])


def montar(*, latitude: float | None, longitude: float | None, precisao_m: float | None, dispositivo: str,
           ip: str, foto_base64: str | None) -> tuple[Prova, list[str]]:
    """Monta a prova vinda do coletor. O que vier errado é descartado com um
    aviso — nunca derruba a batida."""
    avisos: list[str] = []
    local = None
    if latitude is not None and longitude is not None:
        try:
            local = Local(float(latitude), float(longitude), None if precisao_m is None else float(precisao_m))
        except (ProvaInvalida, ValueError) as e:
            avisos.append(f"localização descartada: {e}")
    foto = None
    if foto_base64:
        try:
            foto = base64.b64decode(foto_base64, validate=True)
            Prova(foto=foto)
        except (ValueError, ProvaInvalida) as e:
            foto = None
            avisos.append(f"foto descartada: {e}")
    disp = (dispositivo or "")[:120]
    return Prova(local, disp, (ip or "")[:64], foto), avisos
