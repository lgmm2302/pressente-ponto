"""O Arquivo Fonte de Dados (Portaria 671, art. 81 e Anexo V).

Gerado na hora, para qualquer intervalo de datas, a partir das linhas que foram
gravadas no momento de cada registro (o AFD não recalcula nada: ele junta o que
o REP-P já escreveu). Monta-se assim:

  registro 1 (cabeçalho) · os registros do intervalo em ordem de NSR ·
  registro 9 (totais) · a linha «ASSINATURA_DIGITAL_EM_ARQUIVO_P7S»

e sai acompanhado do `.p7s` (CAdES destacada) com o mesmo nome + «.p7s».
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import asyncpg

from . import formato as f
from . import registro
from .assinatura import Assinador


@dataclass(frozen=True)
class Desenvolvedor:
    tipo_identificador: int
    identificador: str


@dataclass(frozen=True)
class ArquivoAFD:
    nome: str
    conteudo: bytes
    assinatura: bytes

    @property
    def nome_da_assinatura(self) -> str:
        return self.nome + ".p7s"


async def gerar(conn: asyncpg.Connection, estabelecimento_id: int, *, inicio: date, fim: date,
                numero_inpi: str, desenvolvedor: Desenvolvedor, assinador: Assinador) -> ArquivoAFD:
    if fim < inicio:
        raise registro.Recusado("data final antes da inicial")
    est = await conn.fetchrow("SELECT * FROM estabelecimentos WHERE id=$1", estabelecimento_id)
    if est is None:
        raise registro.Recusado("estabelecimento desconhecido")
    zona = ZoneInfo(est["fuso"])
    de = datetime.combine(inicio, time.min, zona)
    ate = datetime.combine(fim + timedelta(days=1), time.min, zona)
    linhas = await conn.fetch(
        """SELECT tipo, linha FROM registros
            WHERE estabelecimento_id=$1 AND gravado_em >= $2 AND gravado_em < $3
            ORDER BY nsr""", estabelecimento_id, de, ate)
    emp = f.Empregador(est["tipo_identificador"], est["identificador"], est["razao_social"],
                       est["cno_caepf"], est["local_prestacao"])
    cabecalho = f.registro_1(
        empregador=emp, numero_inpi=f.so_digitos(numero_inpi), inicio=inicio, fim=fim,
        gerado_em=registro.agora(), fuso=est["fuso"],
        tipo_id_desenvolvedor=desenvolvedor.tipo_identificador,
        id_desenvolvedor=f.identificador_normalizado(desenvolvedor.tipo_identificador, desenvolvedor.identificador),
    )
    contagem = Counter(r["tipo"] for r in linhas)
    todas = [cabecalho, *(r["linha"] for r in linhas), f.registro_9(dict(contagem)), f.LINHA_DA_ASSINATURA]
    conteudo = "".join(l + f.FIM_DE_LINHA for l in todas).encode(f.CODIFICACAO)
    p7s = await assinador.cades_destacada(conteudo)
    return ArquivoAFD(f.nome_do_afd(numero_inpi, est["identificador"]), conteudo, p7s)


def ler(conteudo: bytes) -> list[str]:
    """As linhas de um AFD, sem o CR LF (para conferência)."""
    texto = conteudo.decode(f.CODIFICACAO)
    if not texto.endswith(f.FIM_DE_LINHA):
        raise ValueError("AFD não termina em CR LF")
    partes = texto.split(f.FIM_DE_LINHA)
    return partes[:-1]
