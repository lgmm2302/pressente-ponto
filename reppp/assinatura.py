"""As assinaturas que a Portaria 671 exige do REP-P (arts. 87 e 88):

  · AFD e AEJ: CAdES destacada, gravada ao lado do arquivo em `<nome>.p7s`;
  · comprovante em PDF: PAdES.

Ambas com o certificado ICP-Brasil do desenvolvedor (e-CNPJ). Esse certificado
é do Luiz: o código só o lê do caminho e da senha que ele configurar nas
variáveis de ambiente — nunca fica no repositório, nunca vai para log.

Para desenvolvimento e baterias existe o certificado de TESTE
(`scripts/certificado_de_teste.py`), autoassinado. Arquivo assinado com ele
não vale para a fiscalização, e o comprovante diz isso na própria página.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from io import BytesIO

from asn1crypto import cms
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import fields, signers

VAR_PFX = "PONTO_CERTIFICADO_PFX"
VAR_SENHA = "PONTO_CERTIFICADO_SENHA"
VAR_CHAVE_PEM = "PONTO_CHAVE_PEM"
VAR_CERT_PEM = "PONTO_CERTIFICADO_PEM"


class SemCertificado(RuntimeError):
    pass


@dataclass
class Assinador:
    signer: signers.SimpleSigner
    de_teste: bool

    @property
    def titular(self) -> str:
        return self.signer.signing_cert.subject.human_friendly

    async def cades_destacada(self, dados: bytes) -> bytes:
        """CMS/CAdES-BES destacada (o conteúdo não vai dentro), SHA-256, com o
        atributo signing-certificate-v2. Devolve o DER do `.p7s`."""
        ci = await self.signer.async_sign_general_data(dados, "sha256", detached=True)
        return ci.dump()

    async def pades(self, pdf: bytes, *, motivo: str = "Comprovante de Registro de Ponto") -> bytes:
        w = IncrementalPdfFileWriter(BytesIO(pdf))
        meta = signers.PdfSignatureMetadata(
            field_name="AssinaturaREPP",
            md_algorithm="sha256",
            subfilter=fields.SigSeedSubFilter.PADES,
            reason=motivo,
        )
        saida = await signers.PdfSigner(meta, signer=self.signer).async_sign_pdf(w)
        return saida.getvalue()


def carregar(*, pfx: str | None = None, senha: str | None = None, chave_pem: str | None = None,
             cert_pem: str | None = None, de_teste: bool = False) -> Assinador:
    if pfx:
        s = signers.SimpleSigner.load_pkcs12(pfx, passphrase=(senha or "").encode() or None)
    elif chave_pem and cert_pem:
        s = signers.SimpleSigner.load(chave_pem, cert_pem, key_passphrase=(senha or "").encode() or None)
    else:
        raise SemCertificado("nenhum certificado configurado")
    if s is None:
        raise SemCertificado("o certificado não abriu (caminho ou senha)")
    return Assinador(s, de_teste)


def do_ambiente() -> Assinador:
    """O certificado de produção, pelas variáveis de ambiente."""
    return carregar(
        pfx=os.environ.get(VAR_PFX), senha=os.environ.get(VAR_SENHA),
        chave_pem=os.environ.get(VAR_CHAVE_PEM), cert_pem=os.environ.get(VAR_CERT_PEM),
        de_teste=os.environ.get("PONTO_CERTIFICADO_DE_TESTE") == "1",
    )


def atributos_assinados(p7s: bytes) -> set[str]:
    """Os nomes dos atributos assinados de um `.p7s` (para as baterias)."""
    ci = cms.ContentInfo.load(p7s)
    si = ci["content"]["signer_infos"][0]
    return {a["type"].native for a in si["signed_attrs"]}


def e_destacada(p7s: bytes) -> bool:
    ci = cms.ContentInfo.load(p7s)
    return ci["content"]["encap_content_info"]["content"].native is None
