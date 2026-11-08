"""O Arquivo Eletrônico de Jornada (Portaria 671, art. 83 e Anexo VI).

Campos separados por «|» (sem «|» depois do último campo), cada registro numa
linha terminada em CR LF, ISO 8859-1. Ordem:

  01 cabeçalho · 02 o REP-P · 03 vínculos · 04 horários contratuais ·
  05 marcações · 06 matrículas do eSocial · 07 ausências e banco de horas ·
  08 o programa de tratamento · 99 totais · linha da assinatura

O AEJ é assinado pelo desenvolvedor do programa de tratamento (CAdES
destacada, `.p7s` com o mesmo nome).

CONFIRMAR no leiaute oficial: seqEntSaida com 3 dígitos (adotado: zeros à
esquerda) e o seqEntSaida de uma marcação desconsiderada (adotado: o do par em
que ela caiu).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import asyncpg

from reppp import formato as f
from reppp.assinatura import Assinador
from reppp.registro import Recusado

from . import NOME_DO_PROGRAMA, VERSAO
from .apuracao import Horario
from .dados import carregar, agora

VERSAO_DO_LEIAUTE = "002"
ID_DO_REP = 1
TIPO_REP_P = 3


@dataclass(frozen=True)
class Desenvolvedor:
    tipo_identificador: int
    identificador: str
    razao_social: str
    email: str


@dataclass(frozen=True)
class ArquivoAEJ:
    nome: str
    conteudo: bytes
    assinatura: bytes

    @property
    def nome_da_assinatura(self) -> str:
        return self.nome + ".p7s"


def _txt(v: str, tamanho: int) -> str:
    s = " ".join((v or "").replace("|", "/").split())[:tamanho]
    return f.texto_do_cadastro(s, "texto do AEJ")


def linha(*campos: object) -> str:
    return "|".join("" if c is None else str(c) for c in campos)


def _dh(local: datetime, fuso: str) -> str:
    return f.campo_dh(local.replace(tzinfo=ZoneInfo(fuso)), fuso)


async def gerar(conn: asyncpg.Connection, eid: int, *, inicio: date, fim: date, numero_inpi: str,
                desenvolvedor: Desenvolvedor, assinador: Assinador) -> ArquivoAEJ:
    if fim < inicio:
        raise Recusado("data final antes da inicial")
    est = await conn.fetchrow("SELECT * FROM estabelecimentos WHERE id=$1", eid)
    if est is None:
        raise Recusado("estabelecimento desconhecido")
    fuso = est["fuso"]
    cno_caepf = est["cno_caepf"] or ""
    caepf = cno_caepf if len(cno_caepf) == 14 else ""
    cno = cno_caepf if len(cno_caepf) == 12 else ""

    vinculos = await conn.fetch(
        """SELECT p.cpf, p.nome, v.matricula_esocial FROM empregados p
             LEFT JOIN ptrp_vinculos v ON v.estabelecimento_id = p.estabelecimento_id AND v.cpf = p.cpf
            WHERE p.estabelecimento_id=$1
              AND (p.ativo OR EXISTS (SELECT 1 FROM registros r WHERE r.estabelecimento_id=$1 AND r.cpf=p.cpf
                                       AND r.tipo=7 AND r.marcado_em::date BETWEEN $2 AND $3))
            ORDER BY p.nome, p.cpf""", eid, inicio, fim)

    l01 = linha("01", est["tipo_identificador"], est["identificador"], caepf, cno, _txt(est["razao_social"], 150),
                f.campo_d(inicio), f.campo_d(fim), f.campo_dh(agora(), fuso), VERSAO_DO_LEIAUTE)
    l02 = [linha("02", ID_DO_REP, TIPO_REP_P, f.so_digitos(numero_inpi))]
    l03, l05, l06, l07 = [], [], [], []
    horarios_usados: dict[str, Horario] = {}
    duracoes: dict[str, int] = {}
    for idx, v in enumerate(vinculos, start=1):
        l03.append(linha("03", idx, v["cpf"], _txt(v["nome"], 150)))
        if v["matricula_esocial"]:
            l06.append(linha("06", idx, _txt(v["matricula_esocial"], 30)))
        entrada = await carregar(conn, eid, v["cpf"], inicio, fim)
        ap = entrada.apurar(inicio, fim)
        for dia in ap.dias:
            if dia.horario is not None and dia.horario.codigo not in horarios_usados:
                horarios_usados[dia.horario.codigo] = dia.horario
                duracoes[dia.horario.codigo] = dia.horario.duracao(entrada.regras_do_dia(dia.data), dia.data)
            seq_atual = 1
            for x in dia.marcas:
                m = x.marca
                if x.tipo != "D":
                    seq_atual = x.seq
                cod = dia.horario.codigo if (x.tipo == "E" and x.seq == 1 and dia.horario) else ""
                motivo = _txt(m.motivo, 150) if (x.tipo == "D" or m.fonte == "I") else ""
                l05.append(linha("05", idx, _dh(m.momento, fuso), ID_DO_REP if m.fonte == "O" else "",
                                 x.tipo, f"{seq_atual:03d}", m.fonte, cod, motivo))
            for tipo, minutos, mov in dia.ausencias:
                l07.append(linha("07", idx, tipo, f.campo_d(dia.data), minutos or "", mov or ""))
    l04 = []
    for cod, h in horarios_usados.items():
        horas = [x for par in h.periodos for x in par]
        l04.append(linha("04", cod, duracoes[cod], *horas))
    l08 = linha("08", NOME_DO_PROGRAMA, VERSAO, desenvolvedor.tipo_identificador,
                f.identificador_normalizado(desenvolvedor.tipo_identificador, desenvolvedor.identificador),
                _txt(desenvolvedor.razao_social, 150), _txt(desenvolvedor.email, 50))
    corpo = [l01, *l02, *l03, *l04, *l05, *l06, *l07, l08]
    cont = Counter(x[:2] for x in corpo)
    l99 = linha("99", *(cont.get(f"{t:02d}", 0) for t in range(1, 9)))
    todas = corpo + [l99, f.LINHA_DA_ASSINATURA]
    conteudo = "".join(x + f.FIM_DE_LINHA for x in todas).encode(f.CODIFICACAO)
    p7s = await assinador.cades_destacada(conteudo)
    nome = f"AEJ_{est['identificador']}_{inicio:%Y%m%d}_{fim:%Y%m%d}.txt"
    return ArquivoAEJ(nome, conteudo, p7s)
