"""Gera um certificado AUTOASSINADO de teste (chave + certificado em PEM).

Serve só para desenvolvimento e para as baterias: o que for assinado com ele
não vale para a fiscalização (não é ICP-Brasil). O certificado de verdade é o
e-CNPJ do desenvolvedor, que o Luiz instala no servidor.

Uso: python scripts/certificado_de_teste.py <pasta>
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def gerar(pasta: Path) -> tuple[Path, Path]:
    pasta.mkdir(parents=True, exist_ok=True)
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "BR"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Pressente Ponto - TESTE"),
        x509.NameAttribute(NameOID.COMMON_NAME, "CERTIFICADO DE TESTE - SEM VALOR LEGAL"),
    ])
    agora = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nome).issuer_name(nome)
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora - timedelta(days=1))
        .not_valid_after(agora + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False,
                                     data_encipherment=False, key_agreement=False, key_cert_sign=True,
                                     crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .sign(chave, hashes.SHA256())
    )
    k = pasta / "teste-chave.pem"
    c = pasta / "teste-certificado.pem"
    k.write_bytes(chave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    c.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return k, c


if __name__ == "__main__":
    k, c = gerar(Path(sys.argv[1] if len(sys.argv) > 1 else "certificado-de-teste"))
    print(f"chave: {k}\ncertificado: {c}\nNÃO é ICP-Brasil: só para testes.")
