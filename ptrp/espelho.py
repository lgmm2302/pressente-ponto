"""O Espelho de Ponto Eletrônico (Portaria 671, art. 84) e o aceite do empregado.

Um PDF por empregado e competência (mês), assinado em PAdES, com:
  · empregador e empregado identificados; data de emissão e período;
  · os horários contratuais;
  · todas as marcações do mês, com a origem de cada uma (original, incluída,
    pré-assinalada, desconsiderada) e o motivo dos tratamentos;
  · a duração das jornadas, noturnas, extras, débitos, intervalos, banco de
    horas e os alertas do dia.

O espelho de uma competência nunca muda: gerar de novo cria a versão 2, 3...
O empregado aceita ou contesta cada versão uma única vez.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from io import BytesIO

import asyncpg
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from reppp import formato as f
from reppp.assinatura import Assinador
from reppp.registro import Recusado

from .apuracao import Apuracao, Dia
from .dados import agora, carregar, saldo_anterior_do_banco

DIAS_DA_SEMANA = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
SITUACOES = {"trabalho": "", "DSR": "DSR", "FOLGA": "folga", "feriado": "feriado", "sem_escala": "sem escala"}


def _hhmm(minutos: int) -> str:
    if not minutos:
        return ""
    sinal = "-" if minutos < 0 else ""
    m = abs(minutos)
    return f"{sinal}{m // 60:02d}:{m % 60:02d}"


def _cpf(c: str) -> str:
    return f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}"


def _marcas(dia: Dia) -> str:
    partes = []
    for x in dia.marcas:
        h = x.marca.momento.strftime("%H:%M")
        if x.marca.momento.date() != dia.data:
            h += "(+1)"
        if x.tipo == "D":
            h = f"<strike>{h}</strike>D"
        elif x.marca.fonte == "I":
            h += "i"
        elif x.marca.fonte == "P":
            h += "p"
        if x.marca.contestada:
            h += "!"
        partes.append(h)
    return " ".join(partes)


def competencia_em_datas(competencia: str) -> tuple[date, date]:
    try:
        a, m = int(competencia[:4]), int(competencia[5:7])
        inicio = date(a, m, 1)
    except (ValueError, IndexError) as e:
        raise Recusado("competência no formato AAAA-MM") from e
    fim = (date(a + (m == 12), m % 12 + 1, 1)) - timedelta(days=1)
    return inicio, fim


def montar_pdf(*, est: asyncpg.Record, nome: str, cpf: str, matricula: str | None, inicio: date, fim: date,
               ap: Apuracao, horarios: dict, tratamentos: list, banco_anterior: int, banco_final: int,
               versao: int, de_teste: bool) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=10 * mm, rightMargin=10 * mm,
                            topMargin=10 * mm, bottomMargin=10 * mm, title="Espelho de Ponto Eletrônico",
                            invariant=1, pageCompression=0)
    st = ParagraphStyle("t", fontName="Helvetica", fontSize=7.5, leading=9)
    st_b = ParagraphStyle("b", parent=st, fontName="Helvetica-Bold", fontSize=12, leading=14)
    st_p = ParagraphStyle("p", parent=st, fontSize=8, leading=10)
    els = [Paragraph("Espelho de Ponto Eletrônico", st_b), Spacer(0, 2 * mm)]
    ident = (f"<b>Empregador:</b> {est['razao_social']} · "
             + (f"CNPJ {est['identificador']}" if est["tipo_identificador"] == 1 else f"CPF {_cpf(est['identificador'])}")
             + (f" · CNO/CAEPF {est['cno_caepf']}" if est["cno_caepf"] else "")
             + f"<br/><b>Empregado:</b> {nome} · CPF {_cpf(cpf)}" + (f" · matrícula {matricula}" if matricula else "")
             + f"<br/><b>Período:</b> {inicio:%d/%m/%Y} a {fim:%d/%m/%Y} · <b>Emissão:</b> "
             + f"{agora().astimezone():%d/%m/%Y %H:%M} · versão {versao}")
    els.append(Paragraph(ident, st_p))
    if horarios:
        hs = "; ".join(f"{c}: " + ", ".join(f"{e[:2]}:{e[2:]}–{s[:2]}:{s[2:]}" for e, s in h.periodos)
                       + (" (intervalo pré-assinalado)" if h.intervalo_pre_assinalado else "")
                       for c, h in horarios.items())
        els.append(Paragraph(f"<b>Horário contratual:</b> {hs}", st_p))
    els.append(Spacer(0, 3 * mm))

    cab = ["Data", "Dia", "Situação", "Marcações", "Trab.", "Not.", "Extra", "DSR/fer.", "Débito",
           "Interv.", "Banco", "Observações"]
    linhas = [cab]
    for d in ap.dias:
        obs = []
        if d.falta:
            obs.append("falta")
        if d.abono:
            obs.append(f"abonado: {d.abono}")
        if d.tolerado:
            obs.append("dentro da tolerância")
        obs.extend(d.alertas)
        banco = d.banco_credito - d.banco_debito
        linhas.append([d.data.strftime("%d/%m"), DIAS_DA_SEMANA[d.data.weekday()], SITUACOES.get(d.situacao, d.situacao),
                       Paragraph(_marcas(d), st), _hhmm(d.computado), _hhmm(d.noturno), _hhmm(d.extra),
                       _hhmm(d.extra_descanso_feriado), _hhmm(d.debito), _hhmm(d.intervalo), _hhmm(banco),
                       Paragraph("; ".join(obs), st)])
    linhas.append(["Totais", "", "", "", _hhmm(ap.total("computado")), _hhmm(ap.total("noturno")),
                   _hhmm(ap.total("extra")), _hhmm(ap.total("extra_descanso_feriado")), _hhmm(ap.total("debito")),
                   "", _hhmm(ap.total("banco_credito") - ap.total("banco_debito")), f"faltas: {ap.faltas}"])
    t = Table(linhas, colWidths=[13 * mm, 9 * mm, 15 * mm, 62 * mm, 13 * mm, 12 * mm, 12 * mm, 17 * mm, 13 * mm,
                                 13 * mm, 13 * mm, 85 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 7.5),
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
        ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 7.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E4E3EE")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B8B6CC")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    els += [t, Spacer(0, 3 * mm)]
    els.append(Paragraph(
        f"<b>Banco de horas:</b> saldo anterior {_hhmm(banco_anterior) or '00:00'} · "
        f"saldo ao fim do período {_hhmm(banco_final) or '00:00'}. "
        "<b>Legenda:</b> i = incluída pelo empregador; p = intervalo pré-assinalado; riscada com D = desconsiderada; "
        "! = o empregado recusou o tratamento; (+1) = no dia seguinte. DSR/fer. = horas em descanso ou feriado "
        "(adicional próprio). Horas noturnas em minutos reais; "
        "o total trabalhado já aplica a hora noturna reduzida.", st_p))
    if tratamentos:
        els.append(Spacer(0, 2 * mm))
        els.append(Paragraph("<b>Tratamentos no período</b>", st_p))
        for tr in tratamentos:
            ciencia = {"ciente": "empregado ciente", "recusado": f"RECUSADO pelo empregado: {tr['comentario']}"}.get(
                tr["decisao"], "aguardando a ciência do empregado")
            els.append(Paragraph(f"#{tr['id']} {tr['tipo']} em {tr['criado_em'].astimezone():%d/%m/%Y %H:%M} — "
                                 f"{tr['motivo']} — {ciencia}", st))
    if de_teste:
        els.append(Spacer(0, 3 * mm))
        els.append(Paragraph('<font color="#C01A1A"><b>CERTIFICADO DE TESTE — SEM VALOR LEGAL</b></font>', st_p))
    doc.build(els)
    return buf.getvalue()


async def gerar(conn: asyncpg.Connection, eid: int, *, cpf: str, competencia: str, assinador: Assinador) -> tuple[int, int, bytes]:
    """Gera, assina e grava o espelho. Devolve (id, versão, pdf)."""
    c = f.so_digitos(cpf)
    inicio, fim = competencia_em_datas(competencia)
    est = await conn.fetchrow("SELECT * FROM estabelecimentos WHERE id=$1", eid)
    emp = await conn.fetchrow("SELECT nome FROM empregados WHERE estabelecimento_id=$1 AND cpf=$2", eid, c)
    if est is None or emp is None:
        raise Recusado("empregado inexistente neste estabelecimento")
    matricula = await conn.fetchval("SELECT matricula_esocial FROM ptrp_vinculos WHERE estabelecimento_id=$1 AND cpf=$2",
                                    eid, c)
    entrada = await carregar(conn, eid, c, inicio, fim)
    anterior = await saldo_anterior_do_banco(conn, eid, c, competencia)
    ap = entrada.apurar(inicio, fim)
    final = anterior + ap.total("banco_credito") - ap.total("banco_debito")
    horarios = {d.horario.codigo: d.horario for d in ap.dias if d.horario is not None}
    tratamentos = await conn.fetch(
        """WITH base AS (
               SELECT t.id FROM ptrp_tratamentos t
                WHERE t.estabelecimento_id=$1 AND t.cpf=$2
                  AND ((t.momento AT TIME ZONE $5)::date BETWEEN $3 AND $4
                       OR t.data BETWEEN $3 AND $4
                       OR t.registro_id IN (SELECT id FROM registros
                                             WHERE estabelecimento_id=$1
                                               AND (marcado_em AT TIME ZONE $5)::date BETWEEN $3 AND $4)))
           SELECT t.id, t.tipo, t.motivo, t.criado_em, c.decisao, c.comentario FROM ptrp_tratamentos t
             LEFT JOIN ptrp_ciencias c ON c.tratamento_id=t.id
            WHERE t.id IN (SELECT id FROM base) OR t.alvo_id IN (SELECT id FROM base)
            ORDER BY t.id""", eid, c, inicio, fim, est["fuso"])
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"espelho:{eid}:{c}:{competencia}")
        versao = 1 + (await conn.fetchval(
            "SELECT coalesce(max(versao), 0) FROM ptrp_espelhos WHERE estabelecimento_id=$1 AND cpf=$2 AND competencia=$3",
            eid, c, competencia))
        pdf = montar_pdf(est=est, nome=emp["nome"], cpf=c, matricula=matricula, inicio=inicio, fim=fim, ap=ap,
                         horarios=horarios, tratamentos=tratamentos, banco_anterior=anterior, banco_final=final,
                         versao=versao, de_teste=assinador.de_teste)
        assinado = await assinador.pades(pdf, motivo="Espelho de Ponto Eletrônico")
        resumo = {
            "trabalhado": ap.total("computado"), "noturno": ap.total("noturno"), "extra": ap.total("extra"),
            "extra_descanso_feriado": ap.total("extra_descanso_feriado"), "debito": ap.total("debito"),
            "faltas": ap.faltas, "intervalo_suprimido": ap.total("intervalo_suprimido"),
            "interjornada_faltante": ap.total("interjornada_faltante"), "banco_anterior": anterior,
            "banco_saldo": final, "alertas": sum(len(d.alertas) for d in ap.dias),
            "sha256": hashlib.sha256(assinado).hexdigest(),
        }
        eid_esp = await conn.fetchval(
            """INSERT INTO ptrp_espelhos (estabelecimento_id, cpf, competencia, versao, pdf, resumo)
               VALUES ($1,$2,$3,$4,$5,$6::jsonb) RETURNING id""", eid, c, competencia, versao, assinado,
            json.dumps(resumo))
    return eid_esp, versao, assinado


async def registrar_aceite(conn: asyncpg.Connection, eid: int, *, espelho_id: int, cpf: str, decisao: str,
                           comentario: str = "", origem: dict | None = None) -> int:
    if decisao not in ("aceito", "contestado"):
        raise Recusado("decisão: aceito ou contestado")
    e = await conn.fetchrow("SELECT cpf FROM ptrp_espelhos WHERE id=$1 AND estabelecimento_id=$2", espelho_id, eid)
    if e is None or e["cpf"] != f.so_digitos(cpf):
        raise Recusado("espelho inexistente para esse empregado")
    if decisao == "contestado" and len((comentario or "").strip()) < 3:
        raise Recusado("a contestação precisa dizer o que está errado")
    try:
        return await conn.fetchval(
            "INSERT INTO ptrp_aceites (espelho_id, decisao, comentario, origem) VALUES ($1,$2,$3,$4::jsonb) RETURNING id",
            espelho_id, decisao, " ".join((comentario or "").split())[:1000], json.dumps(origem or {}))
    except asyncpg.UniqueViolationError as e:
        raise Recusado("o empregado já se manifestou sobre esta versão do espelho") from e
