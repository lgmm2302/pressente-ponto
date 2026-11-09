# -*- coding: utf-8 -*-
"""`implantacao/ponto.sh` — a ação «voltar» e a memória da «anterior» (08/11/2026).

Defeito visto na 1ª instalação (bastão de 08/11): a verificação mostrou
`[anterior] /opt/pressente-ponto/atual` — o «anterior» apontando para o
próprio link «atual». Causa: `readlink -f "$BASE/atual"` devolve o caminho
mesmo quando o link ainda não existe, e `publicar` gravou isso como anterior.
Um «voltar» nesse estado faria `atual` apontar para si mesmo.

Aqui as funções do script rodam sozinhas, numa pasta temporária, com `sudo`,
`systemctl` e a checagem de saúde substituídos por dublês. Roda com pytest
(`python -m pytest testes/test_implantacao.py`) ou direto
(`python testes/test_implantacao.py`), sem banco e sem a VM.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PONTO_SH = RAIZ / "implantacao" / "ponto.sh"

#: Dublês: `sudo -n X args` vira `X args`; serviços e saúde obedecem a SAUDE_OK.
DUBLES = r"""
sudo() { [ "$1" = "-n" ] && shift; "$@"; }
systemctl() { return 0; }
journalctl() { return 0; }
precisa_sudo() { return 0; }
saudavel() { [ "${SAUDE_OK:-1}" = "1" ]; }
"""


def _funcoes_do_script() -> str:
    """O script sem o despachante do fim (`case "$ACAO"`), para dar `source`."""
    texto = PONTO_SH.read_text(encoding="utf-8")
    i = texto.find('case "$ACAO" in')
    assert i > 0, "o despachante do ponto.sh mudou de cara"
    return texto[:i]


def rodar(base: Path, comandos: str, saude_ok: bool = True) -> subprocess.CompletedProcess:
    # O script define BASE, precisa_sudo e saudavel: os dublês e a pasta de teste entram DEPOIS dele.
    script = "set -uo pipefail\n" + _funcoes_do_script() + "\n" + f'BASE="{base}"\n' + DUBLES + "\n" + comandos
    env = {**os.environ, "SAUDE_OK": "1" if saude_ok else "0", "HOME": str(base)}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=60)


def preparar(base: Path, releases=("r1", "r2"), atual: str | None = "r2", anterior: str | None = None) -> None:
    (base / "releases").mkdir(parents=True)
    for r in releases:
        (base / "releases" / r).mkdir()
    if atual:
        (base / "atual").symlink_to(base / "releases" / atual)
    if anterior is not None:
        (base / "anterior").write_text(anterior + "\n", encoding="utf-8")


def _tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="ponto-sh-"))


# ── a memória da anterior ────────────────────────────────────────────────────

def test_sem_atual_a_versao_no_ar_e_vazia():
    """1ª instalação: não há `atual`; a função não pode inventar o caminho do link."""
    base = _tmp()
    try:
        preparar(base, atual=None)
        r = rodar(base, 'printf "<%s>" "$(versao_no_ar)"')
        assert r.returncode == 0, r.stderr
        assert r.stdout == "<>", f"sem link «atual» a versão no ar tem de ser vazia, veio {r.stdout!r}"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_com_atual_a_versao_no_ar_e_a_release():
    base = _tmp()
    try:
        preparar(base)
        r = rodar(base, 'printf "%s" "$(versao_no_ar)"')
        assert r.stdout == str((base / "releases" / "r2").resolve())
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_anterior_que_aponta_para_o_proprio_atual_nao_vale():
    """O estado deixado pela 1ª instalação: anterior = $BASE/atual."""
    base = _tmp()
    try:
        preparar(base, anterior=str(base / "atual"))
        r = rodar(base, 'printf "<%s>" "$(anterior_valida || true)"')
        assert r.stdout == "<>", f"«anterior = atual» foi aceita: {r.stdout!r}"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_anterior_igual_a_versao_no_ar_nao_vale():
    base = _tmp()
    try:
        preparar(base, anterior=str(base / "releases" / "r2"))
        r = rodar(base, 'printf "<%s>" "$(anterior_valida || true)"')
        assert r.stdout == "<>"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_anterior_fora_de_releases_ou_inexistente_nao_vale():
    base = _tmp()
    try:
        preparar(base, anterior="/tmp")
        assert rodar(base, 'printf "<%s>" "$(anterior_valida || true)"').stdout == "<>"
        (base / "anterior").write_text(str(base / "releases" / "sumiu") + "\n")
        assert rodar(base, 'printf "<%s>" "$(anterior_valida || true)"').stdout == "<>"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_anterior_de_verdade_vale():
    base = _tmp()
    try:
        preparar(base, anterior=str(base / "releases" / "r1"))
        r = rodar(base, 'printf "%s" "$(anterior_valida)"')
        assert r.stdout == str(base / "releases" / "r1")
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ── voltar ───────────────────────────────────────────────────────────────────

def test_voltar_recusa_quando_a_anterior_e_o_proprio_atual():
    base = _tmp()
    try:
        preparar(base, anterior=str(base / "atual"))
        r = rodar(base, "voltar")
        assert r.returncode != 0, "voltar tinha de recusar"
        assert "Nada mudou" in r.stdout, r.stdout
        assert os.readlink(base / "atual") == str(base / "releases" / "r2"), "«atual» foi mexido"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_voltar_troca_e_guarda_a_que_saiu():
    base = _tmp()
    try:
        preparar(base, anterior=str(base / "releases" / "r1"))
        r = rodar(base, "voltar")
        assert r.returncode == 0, r.stdout + r.stderr
        assert os.readlink(base / "atual") == str(base / "releases" / "r1")
        assert (base / "anterior").read_text().strip() == str((base / "releases" / "r2").resolve()), \
            "a versão que saiu do ar tem de virar a nova «anterior» (para poder voltar de novo)"
        assert "[ok] no ar: r1" in r.stdout
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_voltar_sem_saude_volta_para_a_atual():
    base = _tmp()
    try:
        preparar(base, anterior=str(base / "releases" / "r1"))
        r = rodar(base, "voltar", saude_ok=False)
        assert r.returncode != 0
        assert "mantive a atual" in r.stdout, r.stdout
        assert os.readlink(base / "atual") == str(base / "releases" / "r2")
        assert (base / "anterior").read_text().strip() == str(base / "releases" / "r1"), "a anterior não pode ser perdida"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_verificar_mostra_nenhuma_quando_a_anterior_nao_vale():
    base = _tmp()
    try:
        preparar(base, anterior=str(base / "atual"))
        r = rodar(base, 'curl() { return 1; }; HOST_PUBLICO=""; verificar')
        assert "[anterior] nenhuma" in r.stdout, r.stdout
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_publicar_usa_a_versao_no_ar_e_nao_o_readlink_f():
    """Leitura do script: o ponto exato do defeito."""
    texto = PONTO_SH.read_text(encoding="utf-8")
    assert 'anterior="$(readlink -f "$BASE/atual"' not in texto, \
        "publicar() voltou a usar readlink -f no link «atual» — é isso que grava «anterior = atual» na 1ª instalação"
    assert 'anterior="$(versao_no_ar)"' in texto


if __name__ == "__main__":
    import inspect
    falhas = 0
    for nome, f in sorted(globals().items()):
        if nome.startswith("test_") and inspect.isfunction(f):
            try:
                f()
                print("  ✓", nome)
            except AssertionError as e:
                falhas += 1
                print("  ✗", nome, "—", (str(e).splitlines() or ["(sem mensagem)"])[0][:160])
    print("VERDE" if not falhas else f"VERMELHO — {falhas} falha(s)")
    sys.exit(1 if falhas else 0)
