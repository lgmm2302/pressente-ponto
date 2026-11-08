"""O Comprovante de Registro de Ponto do Trabalhador (Portaria 671, arts. 79 e 80).

Um PDF por batida, assinado em PAdES, com:
  · o cabeçalho «Comprovante de Registro de Ponto do Trabalhador»;
  · o NSR;
  · nome, CNPJ/CPF e CNO/CAEPF (quando houver) do empregador;
  · o local de prestação de serviços;
  · nome e CPF do trabalhador;
  · data e horário da marcação;
  · o número de registro do REP-P no INPI;
  · o código hash SHA-256 da marcação.

O PDF fica gravado no banco na primeira vez que é pedido e nunca mais muda
(tabela `comprovantes`, imutável). O empregado o recebe logo após a batida,
sem precisar pedir, e consegue baixar no mínimo os das últimas 48 horas.
"""
from __future__ import annotations

from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo

import asyncpg
from reportlab.lib.pagesizes import A6
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from . import formato as f
from .assinatura import Assinador

CABECALHO = "Comprovante de Registro de Ponto do Trabalhador"
NOMES_DOS_COLETORES = {1: "aplicativo mobile", 2: "navegador", 3: "aplicativo desktop",
                       4: "dispositivo eletrônico", 5: "outro dispositivo"}


def _cpf(c: str) -> str:
    return f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}" if len(c) == 11 else c


def _doc(tipo: int, ident: str) -> str:
    if tipo == f.ID_CNPJ and len(ident) == 14:
        return f"CNPJ {ident[:2]}.{ident[2:5]}.{ident[5:8]}/{ident[8:12]}-{ident[12:]}"
    return f"CPF {_cpf(ident)}"


def montar_pdf(*, nsr: int, empregador: f.Empregador, nome_empregado: str, cpf: str,
               marcado_em: datetime, fuso: str, numero_inpi: str, hash_: str, coletor: int,
               offline: bool, de_teste: bool) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A6, invariant=1, pageCompression=0)
    c.setTitle(CABECALHO)
    largura, altura = A6
    x = 8 * mm
    y = altura - 12 * mm

    def linha(rotulo: str, valor: str, *, tam: float = 8.5, espaco: float = 4.6 * mm) -> None:
        nonlocal y
        c.setFont("Helvetica", 6.5)
        c.drawString(x, y, rotulo.upper())
        y -= (3.2 if tam < 10 else 4.4) * mm  # valor maior pede mais folga sob o rótulo
        c.setFont("Helvetica", tam)
        c.drawString(x, y, valor)
        y -= espaco

    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(x, y, CABECALHO)
    y -= 8 * mm
    local = marcado_em.astimezone(ZoneInfo(fuso))
    linha("NSR", f"{nsr:09d}", tam=11)
    linha("Empregador", empregador.razao_social[:60])
    doc = _doc(empregador.tipo_identificador, empregador.identificador)
    if empregador.cno_caepf:
        doc += f" · CNO/CAEPF {empregador.cno_caepf}"
    linha("Identificação do empregador", doc)
    linha("Local de prestação de serviços", (empregador.local_prestacao or "—")[:60])
    linha("Trabalhador", nome_empregado[:60])
    linha("CPF do trabalhador", _cpf(cpf))
    linha("Data e horário da marcação",
          local.strftime("%d/%m/%Y %H:%M") + f" (UTC{local.strftime('%z')[:3]}:{local.strftime('%z')[3:]})"
          + (" · off-line" if offline else ""), tam=11)
    linha("Coletor", NOMES_DOS_COLETORES.get(coletor, str(coletor)))
    linha("Registro do REP-P no INPI", numero_inpi or "—")
    c.setFont("Helvetica", 6.5)
    c.drawString(x, y, "CÓDIGO HASH (SHA-256)")
    y -= 3.2 * mm
    c.setFont("Courier", 7.2)
    c.drawString(x, y, hash_[:32])
    y -= 3.4 * mm
    c.drawString(x, y, hash_[32:])
    y -= 6 * mm
    c.setFont("Helvetica", 6)
    c.drawString(x, y, "Documento assinado eletronicamente (PAdES).")
    if de_teste:
        c.setFont("Helvetica-Bold", 8)
        c.setFillColorRGB(0.75, 0.1, 0.1)
        c.drawString(x, 8 * mm, "CERTIFICADO DE TESTE — SEM VALOR LEGAL")
    c.showPage()
    c.save()
    return buf.getvalue()


async def obter(conn: asyncpg.Connection, registro_id: int, assinador: Assinador, numero_inpi: str) -> bytes:
    """O comprovante da batida: o gravado, ou gera, assina e grava agora."""
    pronto = await conn.fetchval("SELECT pdf FROM comprovantes WHERE registro_id=$1", registro_id)
    if pronto is not None:
        return bytes(pronto)
    r = await conn.fetchrow(
        """SELECT r.*, e.tipo_identificador, e.identificador, e.razao_social, e.cno_caepf,
                  e.local_prestacao, e.fuso, p.nome AS nome_empregado
             FROM registros r JOIN estabelecimentos e ON e.id = r.estabelecimento_id
             LEFT JOIN empregados p ON p.estabelecimento_id = r.estabelecimento_id AND p.cpf = r.cpf
            WHERE r.id = $1 AND r.tipo = 7""", registro_id)
    if r is None:
        raise LookupError("marcação inexistente")
    emp = f.Empregador(r["tipo_identificador"], r["identificador"], r["razao_social"], r["cno_caepf"],
                       r["local_prestacao"])
    pdf = montar_pdf(nsr=r["nsr"], empregador=emp, nome_empregado=r["nome_empregado"] or "", cpf=r["cpf"],
                     marcado_em=r["marcado_em"], fuso=r["fuso"], numero_inpi=numero_inpi, hash_=r["hash"],
                     coletor=r["coletor"], offline=r["offline"], de_teste=assinador.de_teste)
    assinado = await assinador.pades(pdf)
    await conn.execute(
        "INSERT INTO comprovantes (registro_id, pdf) VALUES ($1, $2) ON CONFLICT (registro_id) DO NOTHING",
        registro_id, assinado)
    return bytes(await conn.fetchval("SELECT pdf FROM comprovantes WHERE registro_id=$1", registro_id))


async def ultimas_48_horas(conn: asyncpg.Connection, estabelecimento_id: int, cpf: str) -> list[asyncpg.Record]:
    return await conn.fetch(
        """SELECT id, nsr, marcado_em FROM registros
            WHERE estabelecimento_id=$1 AND cpf=$2 AND tipo=7 AND marcado_em >= now() - interval '48 hours'
            ORDER BY nsr DESC""", estabelecimento_id, f.so_digitos(cpf))
