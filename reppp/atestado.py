"""O Atestado Técnico e Termo de Responsabilidade (Portaria 671, art. 89 e Anexo VII).

O desenvolvedor entrega a CADA empresa usuária um atestado de que o programa
atende à Portaria; sem ele, a empresa não pode usar o REP-P. É um PDF assinado
eletronicamente, com certificado ICP-Brasil de PESSOA FÍSICA, pelo
representante legal e pelo responsável técnico do desenvolvedor.

Este módulo monta o PDF já preenchido com os dados da empresa usuária (a que
pediu) e do desenvolvedor. As duas assinaturas são feitas depois, por cada uma
das pessoas, com o próprio e-CPF — o servidor nunca tem esses certificados.

CONFERIR com o advogado o texto contra o modelo oficial do Anexo VII antes do
primeiro uso: os campos aqui seguem o que a Portaria e as respostas oficiais do
MTE pedem (empresa e CNPJ obrigatórios, versão «a partir de», registro no INPI).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from xml.sax.saxutils import escape as e
from datetime import datetime
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import VERSAO
from . import formato as f

TITULO = "Atestado Técnico e Termo de Responsabilidade"


@dataclass(frozen=True)
class Desenvolvedor:
    razao_social: str
    tipo_identificador: int
    identificador: str
    representante_legal: str
    responsavel_tecnico: str


def _doc(tipo: int, ident: str) -> str:
    if tipo == f.ID_CNPJ and len(ident) == 14 and ident.isdigit():
        return f"CNPJ {ident[:2]}.{ident[2:5]}.{ident[5:8]}/{ident[8:12]}-{ident[12:]}"
    return f"{'CNPJ' if tipo == f.ID_CNPJ else 'CPF'} {ident}"


def montar(*, empresa: f.Empregador, desenvolvedor: Desenvolvedor, numero_inpi: str, nome_do_programa: str,
           certificado_titular: str, certificado_sha256: str, emitido_em: datetime) -> bytes:
    faltando = [n for n, v in (("razão social do desenvolvedor", desenvolvedor.razao_social),
                               ("CNPJ/CPF do desenvolvedor", desenvolvedor.identificador),
                               ("representante legal", desenvolvedor.representante_legal),
                               ("responsável técnico", desenvolvedor.responsavel_tecnico)) if not (v or "").strip()]
    if faltando:
        raise ValueError("faltam dados para o atestado: " + ", ".join(faltando))
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                            bottomMargin=18 * mm, title=TITULO, invariant=1, pageCompression=0)
    st = ParagraphStyle("t", fontName="Helvetica", fontSize=10, leading=13.5)
    st_t = ParagraphStyle("tt", parent=st, fontName="Helvetica-Bold", fontSize=14, leading=18)
    st_s = ParagraphStyle("s", parent=st, fontName="Helvetica-Bold", fontSize=10.5, leading=14, spaceBefore=6)
    st_p = ParagraphStyle("p", parent=st, fontSize=8.5, leading=11, textColor="#444444")

    def tabela(linhas):
        t = Table([[Paragraph(f"<b>{e(a)}</b>", st), Paragraph(b, st)] for a, b in linhas], colWidths=[52 * mm, 118 * mm])
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, "#B8B6CC"), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        return t

    els = [Paragraph(TITULO, st_t),
           Paragraph("Portaria MTP nº 671/2021, art. 89 — Registrador Eletrônico de Ponto via Programa (REP-P)", st_p),
           Spacer(0, 5 * mm),
           Paragraph("1. Desenvolvedor", st_s),
           tabela([("Razão social", e(desenvolvedor.razao_social)),
                   ("Identificação", e(_doc(desenvolvedor.tipo_identificador, desenvolvedor.identificador))),
                   ("Representante legal", e(desenvolvedor.representante_legal)),
                   ("Responsável técnico", e(desenvolvedor.responsavel_tecnico))]),
           Paragraph("2. Programa", st_s),
           tabela([("Nome", e(nome_do_programa)),
                   ("Tipo", "REP-P — Registrador Eletrônico de Ponto via Programa"),
                   ("Versão", f"a partir de {VERSAO}"),
                   ("Registro no INPI", e(numero_inpi) or "PENDENTE — o atestado só vale com o número do registro"),
                   ("Código hash da marcação", "SHA-256"),
                   ("Assinatura dos arquivos", "CAdES destacada (AFD e AEJ) e PAdES (comprovante), certificado ICP-Brasil"),
                   ("Certificado do desenvolvedor", f"{e(certificado_titular)}<br/>SHA-256: {e(certificado_sha256)}")]),
           Paragraph("3. Empresa usuária", st_s),
           tabela([("Razão social / nome", e(empresa.razao_social)),
                   ("Identificação", e(_doc(empresa.tipo_identificador, empresa.identificador)))]),
           Paragraph("4. Declaração", st_s),
           Paragraph(
               "Atestamos, sob as penas da lei, que o programa identificado acima atende integralmente aos requisitos "
               "da Portaria MTP nº 671/2021 aplicáveis ao REP-P, em especial os arts. 74 e 78 a 81 e os Anexos V e IX; "
               "que não possui funcionalidade que permita restringir horários de marcação, marcar automaticamente, "
               "exigir autorização prévia para marcação de horas extras, nem alterar ou eliminar os dados registrados; "
               "e que os arquivos fiscais são gerados nos leiautes oficiais e assinados com certificado ICP-Brasil. "
               "Assumimos a responsabilidade técnica pelas informações deste documento.", st),
           Spacer(0, 6 * mm),
           Paragraph(f"Emitido em {emitido_em:%d/%m/%Y}.", st),
           Spacer(0, 10 * mm),
           Paragraph(f"{e(desenvolvedor.representante_legal)} — representante legal", st),
           Paragraph(f"{e(desenvolvedor.responsavel_tecnico)} — responsável técnico", st),
           Spacer(0, 4 * mm),
           Paragraph("Documento a ser assinado eletronicamente, com assinatura qualificada (certificado ICP-Brasil de "
                     "pessoa física), pelo representante legal e pelo responsável técnico (Lei nº 14.063/2020, "
                     "art. 4º, III). Sem as duas assinaturas, este documento não tem validade.", st_p)]
    doc.build(els)
    return buf.getvalue()


def impressao_do_certificado(cert_der: bytes) -> str:
    h = hashlib.sha256(cert_der).hexdigest().upper()
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2))
