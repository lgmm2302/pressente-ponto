"""O formato dos arquivos oficiais (Portaria MTP 671/2021, Anexo V — AFD).

Tudo o que é texto de posição fixa mora aqui, sem banco e sem rede, para que as
baterias confiram cada campo contra o leiaute sem montar nada em volta.

Regras gerais do Anexo V seguidas aqui:
  · ISO 8859-1; cada registro numa linha terminada em CR LF (bytes 13 e 10);
  · registros em ordem de NSR, sem linha em branco;
  · campo alfanumérico (A): alinhado à esquerda, completado com espaços;
  · data (D): AAAA-MM-dd; data e hora (DH): AAAA-MM-ddThh:mm:00ZZZZZ
    (os segundos saem sempre 00; ZZZZZ = sinal, horas e minutos do fuso);
  · registros 1 a 5 terminam com o CRC-16/KERMIT (CCITT-TRUE) em hexadecimal;
  · o registro 7 (marcação do REP-P) leva o hash SHA-256 encadeado.

PONTOS A CONFIRMAR NO TEXTO OFICIAL (marcados `CONFIRMAR` abaixo):
  · campo numérico (N): aqui alinhado à direita com zeros à esquerda, como o
    NSR "000000000" do cabeçalho sugere;
  · o hash do registro 7: aqui é o SHA-256 das posições 001–073 da linha,
    concatenadas com o hash do registro 7 anterior do mesmo estabelecimento,
    em hexadecimal minúsculo;
  · o CRC em hexadecimal maiúsculo, 4 dígitos.
Os três ficam em constantes, para trocar num lugar só.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

CODIFICACAO = "iso-8859-1"
FIM_DE_LINHA = "\r\n"
VERSAO_DO_LEIAUTE = "004"
LINHA_DA_ASSINATURA = "ASSINATURA_DIGITAL_EM_ARQUIVO_P7S".ljust(100)

HASH_EM_MAIUSCULAS = False  # CONFIRMAR
CRC_EM_MAIUSCULAS = True  # CONFIRMAR

# Identificador do coletor (registro 7, posições 071–072).
COLETOR_APP_MOBILE = 1
COLETOR_NAVEGADOR = 2
COLETOR_APP_DESKTOP = 3
COLETOR_DISPOSITIVO = 4
COLETOR_OUTRO = 5
COLETORES = {COLETOR_APP_MOBILE, COLETOR_NAVEGADOR, COLETOR_APP_DESKTOP, COLETOR_DISPOSITIVO, COLETOR_OUTRO}

# Eventos sensíveis do registro 6 que valem para o REP-P.
EVENTO_RETORNO_DE_ENERGIA = 2
EVENTO_DISPONIBILIDADE = 7
EVENTO_INDISPONIBILIDADE = 8
EVENTOS_DO_REP_P = {EVENTO_RETORNO_DE_ENERGIA, EVENTO_DISPONIBILIDADE, EVENTO_INDISPONIBILIDADE}

# Tipo do identificador do empregador / desenvolvedor.
ID_CNPJ = 1
ID_CPF = 2


class ErroDeFormato(ValueError):
    """Um valor que não cabe no leiaute. Nunca é cortado em silêncio."""


# ─────────────────────────────────────────────────────────────────────────────
# Campos
# ─────────────────────────────────────────────────────────────────────────────

def _latin1(texto: str, nome: str) -> str:
    try:
        texto.encode(CODIFICACAO)
    except UnicodeEncodeError as e:
        raise ErroDeFormato(f"{nome}: caractere fora do ISO 8859-1") from e
    if "\r" in texto or "\n" in texto:
        raise ErroDeFormato(f"{nome}: quebra de linha não cabe num campo")
    return texto


# Pontuação tipográfica comum em nomes e endereços colados de outro lugar, que
# o ISO 8859-1 não tem: vira a equivalente simples. O resto fora do 8859-1 é
# recusado no cadastro (nunca trocado em silêncio por «?»).
_TROCAS_TIPOGRAFICAS = str.maketrans({
    "\u2014": "-", "\u2013": "-", "\u2012": "-", "\u2010": "-", "\u2011": "-",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2018": "'", "\u2019": "'", "\u201a": "'",
    "\u2026": "...", "\u00a0": " ", "\u2009": " ", "\u202f": " ",
})


def texto_do_cadastro(valor: str, nome: str) -> str:
    """Nome, razão social ou endereço como vão ficar no AFD: pontuação
    tipográfica simplificada, espaços aparados; ErroDeFormato se sobrar
    caractere fora do ISO 8859-1."""
    v = " ".join((valor or "").translate(_TROCAS_TIPOGRAFICAS).split())
    return _latin1(v, nome)


def campo_a(valor: str, tamanho: int, nome: str, cortar: bool = False) -> str:
    """Alfanumérico: à esquerda, espaços à direita. Só corta quando `cortar`
    (nome e razão social longos), nunca um identificador."""
    v = _latin1(valor or "", nome)
    if len(v) > tamanho:
        if not cortar:
            raise ErroDeFormato(f"{nome}: {len(v)} caracteres, o campo tem {tamanho}")
        v = v[:tamanho]
    return v.ljust(tamanho)


def campo_n(valor: int | str | None, tamanho: int, nome: str) -> str:
    """Numérico: à direita, zeros à esquerda (CONFIRMAR). Vazio = só espaços."""
    if valor is None or valor == "":
        return " " * tamanho
    s = str(valor)
    if not s.isdigit():
        raise ErroDeFormato(f"{nome}: só dígitos, veio {s!r}")
    if len(s) > tamanho:
        raise ErroDeFormato(f"{nome}: {len(s)} dígitos, o campo tem {tamanho}")
    return s.rjust(tamanho, "0")


def campo_d(dia: date) -> str:
    return dia.strftime("%Y-%m-%d")


def campo_dh(momento: datetime, fuso: str) -> str:
    """AAAA-MM-ddThh:mm:00ZZZZZ no fuso do estabelecimento. `momento` precisa
    ter fuso (hora do servidor em UTC, por exemplo)."""
    if momento.tzinfo is None:
        raise ErroDeFormato("data e hora sem fuso")
    local = momento.astimezone(ZoneInfo(fuso))
    return local.strftime("%Y-%m-%dT%H:%M:00") + local.strftime("%z")


# ─────────────────────────────────────────────────────────────────────────────
# Documentos (CPF e CNPJ, inclusive o CNPJ alfanumérico de julho de 2026)
# ─────────────────────────────────────────────────────────────────────────────

def so_digitos(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def cpf_valido(cpf: str) -> bool:
    c = so_digitos(cpf)
    if len(c) != 11 or c == c[0] * 11:
        return False
    for n in (9, 10):
        soma = sum(int(c[i]) * (n + 1 - i) for i in range(n))
        dv = (soma * 10) % 11 % 10
        if dv != int(c[n]):
            return False
    return True


def normalizar_cnpj(cnpj: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", cnpj or "").upper()


def cnpj_valido(cnpj: str) -> bool:
    """Numérico ou alfanumérico (IN RFB 2.229/2024): letras valem código ASCII − 48;
    os dois dígitos verificadores continuam numéricos."""
    c = normalizar_cnpj(cnpj)
    if len(c) != 14 or not re.fullmatch(r"[0-9A-Z]{12}[0-9]{2}", c) or c == c[0] * 14:
        return False
    valores = [ord(ch) - 48 for ch in c]
    for n in (12, 13):
        pesos = list(range(n - 7, 1, -1)) + list(range(9, 1, -1))
        soma = sum(v * p for v, p in zip(valores[:n], pesos))
        resto = soma % 11
        dv = 0 if resto < 2 else 11 - resto
        if dv != valores[n]:
            return False
    return True


def identificador_valido(tipo: int, numero: str) -> bool:
    if tipo == ID_CNPJ:
        return cnpj_valido(numero)
    if tipo == ID_CPF:
        return cpf_valido(numero)
    return False


def identificador_normalizado(tipo: int, numero: str) -> str:
    return normalizar_cnpj(numero) if tipo == ID_CNPJ else so_digitos(numero)


# ─────────────────────────────────────────────────────────────────────────────
# CRC-16/KERMIT e hash
# ─────────────────────────────────────────────────────────────────────────────

def crc16_kermit(dados: bytes) -> int:
    """CRC-16/KERMIT (CCITT-TRUE): polinômio 0x1021 refletido (0x8408), início 0,
    entrada e saída refletidas, sem XOR final. «123456789» → 0x2189."""
    crc = 0
    for b in dados:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def _crc_texto(corpo: str) -> str:
    h = f"{crc16_kermit(corpo.encode(CODIFICACAO)):04x}"
    return h.upper() if CRC_EM_MAIUSCULAS else h


def com_crc(corpo: str) -> str:
    return corpo + _crc_texto(corpo)


def crc_confere(linha: str) -> bool:
    return len(linha) > 4 and _crc_texto(linha[:-4]) == linha[-4:]


def hash_da_marcacao(inicio_da_linha: str, hash_anterior: str | None) -> str:
    """SHA-256 das posições 001–073 do registro 7 + o hash do registro 7 anterior
    do mesmo estabelecimento (CONFIRMAR a composição no texto oficial)."""
    if len(inicio_da_linha) != 73:
        raise ErroDeFormato("o hash do registro 7 cobre exatamente as posições 001 a 073")
    h = hashlib.sha256((inicio_da_linha + (hash_anterior or "")).encode(CODIFICACAO)).hexdigest()
    return h.upper() if HASH_EM_MAIUSCULAS else h


# ─────────────────────────────────────────────────────────────────────────────
# Os registros
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Empregador:
    tipo_identificador: int
    identificador: str
    razao_social: str
    cno_caepf: str | None = None
    local_prestacao: str = ""


def registro_1(*, empregador: Empregador, numero_inpi: str, inicio: date, fim: date,
               gerado_em: datetime, fuso: str, tipo_id_desenvolvedor: int,
               id_desenvolvedor: str) -> str:
    corpo = (
        "000000000"
        + "1"
        + campo_n(empregador.tipo_identificador, 1, "tipo do identificador do empregador")
        + campo_a(empregador.identificador, 14, "CNPJ/CPF do empregador")
        + campo_n(empregador.cno_caepf, 14, "CNO/CAEPF")
        + campo_a(empregador.razao_social, 150, "razão social", cortar=True)
        + campo_n(numero_inpi, 17, "número do INPI")
        + campo_d(inicio)
        + campo_d(fim)
        + campo_dh(gerado_em, fuso)
        + VERSAO_DO_LEIAUTE
        + campo_n(tipo_id_desenvolvedor, 1, "tipo do identificador do desenvolvedor")
        + campo_a(id_desenvolvedor, 14, "CNPJ/CPF do desenvolvedor")
        + campo_a("", 30, "modelo")  # só REP-C
    )
    linha = com_crc(corpo)
    assert len(linha) == 302, len(linha)
    return linha


def registro_2(*, nsr: int, gravado_em: datetime, fuso: str, cpf_responsavel: str,
               empregador: Empregador) -> str:
    corpo = (
        campo_n(nsr, 9, "NSR")
        + "2"
        + campo_dh(gravado_em, fuso)
        + campo_n(so_digitos(cpf_responsavel), 14, "CPF do responsável")
        + campo_n(empregador.tipo_identificador, 1, "tipo do identificador do empregador")
        + campo_a(empregador.identificador, 14, "CNPJ/CPF do empregador")
        + campo_n(empregador.cno_caepf, 14, "CNO/CAEPF")
        + campo_a(empregador.razao_social, 150, "razão social", cortar=True)
        + campo_a(empregador.local_prestacao, 100, "local de prestação de serviços", cortar=True)
    )
    linha = com_crc(corpo)
    assert len(linha) == 331, len(linha)
    return linha


def registro_5(*, nsr: int, gravado_em: datetime, fuso: str, operacao: str, cpf: str,
               nome: str, cpf_responsavel: str, demais_dados: str = "") -> str:
    if operacao not in ("I", "A", "E"):
        raise ErroDeFormato("operação do registro 5: I, A ou E")
    corpo = (
        campo_n(nsr, 9, "NSR")
        + "5"
        + campo_dh(gravado_em, fuso)
        + operacao
        + campo_n(so_digitos(cpf), 12, "CPF do empregado")
        + campo_a(nome, 52, "nome do empregado", cortar=True)
        + campo_a(demais_dados, 4, "demais dados")
        + campo_n(so_digitos(cpf_responsavel), 11, "CPF do responsável")
    )
    linha = com_crc(corpo)
    assert len(linha) == 118, len(linha)
    return linha


def registro_6(*, nsr: int, gravado_em: datetime, fuso: str, evento: int) -> str:
    if evento not in EVENTOS_DO_REP_P:
        raise ErroDeFormato(f"evento {evento:02d} não é de REP-P")
    linha = campo_n(nsr, 9, "NSR") + "6" + campo_dh(gravado_em, fuso) + campo_n(evento, 2, "evento")
    assert len(linha) == 36, len(linha)
    return linha


def registro_7(*, nsr: int, marcado_em: datetime, gravado_em: datetime, fuso: str, cpf: str,
               coletor: int, offline: bool, hash_anterior: str | None) -> tuple[str, str]:
    """Devolve (linha, hash)."""
    if coletor not in COLETORES:
        raise ErroDeFormato("coletor: 01 a 05")
    inicio = (
        campo_n(nsr, 9, "NSR")
        + "7"
        + campo_dh(marcado_em, fuso)
        + campo_n(so_digitos(cpf), 12, "CPF do empregado")
        + campo_dh(gravado_em, fuso)
        + campo_n(coletor, 2, "coletor")
        + ("1" if offline else "0")
    )
    h = hash_da_marcacao(inicio, hash_anterior)
    linha = inicio + h
    assert len(linha) == 137, len(linha)
    return linha, h


def registro_9(contagem: dict[int, int]) -> str:
    linha = "999999999" + "".join(campo_n(contagem.get(t, 0), 9, f"total do tipo {t}") for t in (2, 3, 4, 5, 6, 7)) + "9"
    assert len(linha) == 64, len(linha)
    return linha


def nome_do_afd(numero_inpi: str, identificador_empregador: str) -> str:
    return f"AFD{so_digitos(numero_inpi)}{identificador_empregador}REP_P.txt"


TAMANHO_POR_TIPO = {1: 302, 2: 331, 5: 118, 6: 36, 7: 137, 9: 64}
