"""O pacote do pedido de registro no INPI (fase 9).

O INPI não recebe o código: recebe o RESUMO DIGITAL (hash) da documentação
técnica — o código-fonte e a documentação, num único arquivo. Quem pede o
registro guarda esse arquivo, sem mudar um byte, por 50 anos: é ele que prova,
num processo, que o programa registrado é este.

Por isso o pacote é DETERMINÍSTICO: os mesmos arquivos geram sempre o mesmo
.zip, byte a byte (ordem fixa, data fixa, permissões fixas, fim de linha LF),
e o hash pode ser refeito a qualquer tempo a partir do repositório.

Dois programas, dois pedidos (o REP-P é o obrigatório — Portaria 671, art. 91;
o tratamento é opcional, para proteção):

  python -m scripts.pacote_inpi rep-p         → inpi/rep-p-<versão>/
  python -m scripts.pacote_inpi tratamento    → inpi/tratamento-<versão>/

Cada pasta recebe: o .zip, SHA512.txt (o que vai no formulário do INPI) e
MANIFESTO.txt (o SHA-256 de cada arquivo dentro do pacote).
"""
from __future__ import annotations

import hashlib
import io
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DATA_FIXA = (2026, 11, 7, 0, 0, 0)  # a data de criação do programa; não muda com o relógio de quem gera


@dataclass(frozen=True)
class Programa:
    chave: str
    titulo: str
    pacote_python: str
    extras: tuple[str, ...]

    @property
    def versao(self) -> str:
        texto = (RAIZ / self.pacote_python / "__init__.py").read_text(encoding="utf-8")
        for linha in texto.splitlines():
            if linha.startswith("VERSAO"):
                return linha.split("=", 1)[1].strip().strip('"')
        raise SystemExit(f"{self.pacote_python}/__init__.py sem VERSAO")

    def arquivos(self) -> list[Path]:
        base = sorted(p for p in (RAIZ / self.pacote_python).rglob("*")
                      if p.is_file() and "__pycache__" not in p.parts and p.suffix in (".py", ".sql"))
        return base + [RAIZ / e for e in self.extras]


PROGRAMAS = {
    "rep-p": Programa("rep-p", "Pressente Ponto - Registrador Eletrônico de Ponto via Programa (REP-P)", "reppp",
                      ("inpi/DOCUMENTACAO-TECNICA-REP-P.md", "LICENCA.md")),
    "tratamento": Programa("tratamento", "Pressente Ponto - Programa de Tratamento de Registro de Ponto", "ptrp",
                           ("inpi/DOCUMENTACAO-TECNICA-TRATAMENTO.md", "LICENCA.md")),
}


def montar(prog: Programa) -> tuple[bytes, str]:
    """Devolve (zip, manifesto). Os mesmos arquivos geram sempre os mesmos bytes."""
    buf = io.BytesIO()
    manifesto = []
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in prog.arquivos():
            if not p.exists():
                raise SystemExit(f"falta no pacote: {p.relative_to(RAIZ)}")
            nome = p.relative_to(RAIZ).as_posix()
            dados = p.read_bytes().replace(b"\r\n", b"\n")
            info = zipfile.ZipInfo(nome, date_time=DATA_FIXA)
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            z.writestr(info, dados, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
            manifesto.append(f"{hashlib.sha256(dados).hexdigest()}  {nome}")
    return buf.getvalue(), "\n".join(manifesto) + "\n"


def sha512(dados: bytes) -> str:
    return hashlib.sha512(dados).hexdigest().upper()


def pasta(prog: Programa) -> Path:
    return RAIZ / "inpi" / f"{prog.chave}-{prog.versao}"


def gerar(chave: str) -> Path:
    prog = PROGRAMAS[chave]
    dados, manifesto = montar(prog)
    destino = pasta(prog)
    destino.mkdir(parents=True, exist_ok=True)
    nome_zip = f"pressente-ponto-{prog.chave}-{prog.versao}.zip"
    (destino / nome_zip).write_bytes(dados)
    (destino / "MANIFESTO.txt").write_text(manifesto, encoding="utf-8", newline="\n")
    (destino / "SHA512.txt").write_text(
        f"Programa: {prog.titulo}\nVersão: {prog.versao}\nArquivo: {nome_zip}\nAlgoritmo: SHA-512\n"
        f"Resumo digital (hash): {sha512(dados)}\n", encoding="utf-8", newline="\n")
    return destino


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in PROGRAMAS:
        raise SystemExit(f"uso: python -m scripts.pacote_inpi {{{'|'.join(PROGRAMAS)}}}")
    d = gerar(sys.argv[1])
    print((d / "SHA512.txt").read_text(encoding="utf-8"))
