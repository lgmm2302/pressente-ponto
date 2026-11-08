"""A bateria do Pressente Ponto: os testes passam, e cada regra quebrada de
propósito é pega.

Roda numa CÓPIA do repositório (pasta temporária): nenhum arquivo real é
alterado, nem se a execução for interrompida.

  python verificar.py            # testes + mutações
  python verificar.py --rapido   # só os testes
  python verificar.py --parte 1/2  # metade das mutações (máquina lenta)
  python verificar.py --so "Tema 14" # só as mutações com esse trecho no nome

Precisa de um PostgreSQL de teste em PONTO_BANCO_TESTE
(padrão postgresql://postgres@127.0.0.1:5432/postgres).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent

# (arquivo, trecho original, trecho estragado, o que deveria ser pego)
MUTACOES = [
    ("reppp/formato.py", "crc = (crc >> 1) ^ 0x8408", "crc = (crc >> 1) ^ 0xA001", "CRC-16 com outro polinômio"),
    ("reppp/formato.py", '(inicio_da_linha + (hash_anterior or ""))', "(inicio_da_linha)", "hash sem encadear o anterior"),
    ("reppp/formato.py", '("1" if offline else "0")', '("0" if offline else "1")', "off-line invertido"),
    ("reppp/formato.py", '"%Y-%m-%dT%H:%M:00"', '"%Y-%m-%dT%H:%M:%S"', "segundos no lugar do :00"),
    ("reppp/formato.py", 'CODIFICACAO = "iso-8859-1"', 'CODIFICACAO = "utf-8"', "AFD em UTF-8"),
    ("reppp/formato.py", 'return s.rjust(tamanho, "0")', 'return s.ljust(tamanho, " ")', "numérico sem zeros à esquerda"),
    ("reppp/formato.py", "if dv != valores[n]:", "if False:", "CNPJ sem dígito verificador"),
    ("reppp/esquema.sql", "CREATE TRIGGER registros_imutaveis BEFORE UPDATE OR DELETE ON registros",
     "CREATE TRIGGER registros_imutaveis BEFORE UPDATE ON registros", "registro apagável"),
    ("reppp/esquema.sql", "CREATE TRIGGER comprovantes_imutaveis BEFORE UPDATE OR DELETE ON comprovantes",
     "CREATE TRIGGER comprovantes_imutaveis BEFORE DELETE ON comprovantes", "comprovante alterável"),
    ("reppp/registro.py", 'SELECT * FROM estabelecimentos WHERE id = $1 FOR UPDATE"',
     'SELECT * FROM estabelecimentos WHERE id = $1"', "NSR sem trava (concorrência)"),
    ("reppp/registro.py", "            marcado_em = gravado_em\n",
     "            marcado_em = marcado_em_no_coletor or gravado_em\n", "on-line aceita a hora do coletor"),
    ("reppp/registro.py", "if marcado_em_no_coletor > gravado_em + TOLERANCIA_DO_RELOGIO:", "if False:",
     "off-line no futuro"),
    ("reppp/registro.py", '"SELECT 1 FROM empregados WHERE estabelecimento_id=$1 AND cpf=$2 AND ativo"',
     '"SELECT 1 FROM empregados WHERE estabelecimento_id=$1 AND cpf=$2"', "empregado excluído ainda bate"),
    ("reppp/registro.py", 'problemas.append(f"NSR {r[\'nsr\']}: hash da marcação não confere")', "pass",
     "conferência cega para adulteração"),
    ("reppp/afd.py", "f.registro_9(dict(contagem)), f.LINHA_DA_ASSINATURA]", "f.registro_9(dict(contagem))]",
     "AFD sem a linha da assinatura"),
    ("reppp/afd.py", "WHERE estabelecimento_id=$1 AND gravado_em >= $2 AND gravado_em < $3",
     "WHERE estabelecimento_id=$1", "AFD ignora o intervalo"),
    ("reppp/assinatura.py", '"sha256", detached=True)', '"sha256", detached=False)', "p7s não destacado"),
    ("reppp/comprovante.py", "    c.drawString(x, y, hash_[32:])\n", "", "comprovante sem metade do hash"),
    ("reppp/comprovante.py", 'linha("Registro do REP-P no INPI", numero_inpi or "—")', "pass",
     "comprovante sem o número do INPI"),
    ("reppp/api.py", "if dono != eid:", "if dono is None:", "um estabelecimento lê o comprovante do outro"),
    # ── programa de tratamento ──
    ("ptrp/apuracao.py", " and sum(desvios) <= r.tolerancia_diaria", "", "tolerância sem o teto de 10 min no dia"),
    ("ptrp/apuracao.py", "return real - noturno + noturno * 60 / 52.5", "return real", "sem a hora noturna reduzida"),
    ("ptrp/apuracao.py", "noturno += sum(_sobreposicao(e, s, we, datetime.max) for e, s in pares)", "pass",
     "sem a prorrogação do noturno (Súmula 60)"),
    ("ptrp/apuracao.py", "minimo = (r.intervalo_minimo_acima_de_6h if real > 360",
     "minimo = (r.intervalo_minimo_acima_de_6h if real > 480", "intervalo de 1 h só depois de 8 h"),
    ("ptrp/apuracao.py", "if folga < r.interjornada_minima:", "if False:", "interjornada ignorada"),
    ("ptrp/apuracao.py", "dia.falta = True", "dia.falta = False", "falta não registrada"),
    ("ptrp/apuracao.py", "dia.extra_descanso_feriado = dia.computado", "dia.extra = dia.computado",
     "DSR e feriado pagos a 50%"),
    ("ptrp/apuracao.py", "if dia is not None and dia.exato:", "if False:", "sem alerta de ponto britânico"),
    ("ptrp/apuracao.py", "                dia.extra = 0\n", "", "banco de horas paga e credita a mesma hora"),
    ("ptrp/apuracao.py", "if (aberta and vao < ESPERA_MAXIMA_PELA_SAIDA) or (not aberta and vao < separador):",
     "if vao < separador:", "jornada longa partida em duas"),
    ("ptrp/regras.py", "if self.adicional_extra < 50:", "if False:", "adicional de extra abaixo de 50% aceito"),
    ("ptrp/apuracao.py", "if minimo - gozado > r.tolerancia_intervalo:", "if minimo - gozado > 0:",
     "variação ínfima do intervalo vira supressão (Tema 14)"),
    ("ptrp/apuracao.py", 'excesso = dia.extra if situacao == "trabalho" else dia.computado - 480',
     "excesso = dia.extra + dia.extra_descanso_feriado", "limite de 2 h extras ignora o descanso"),
    ("ptrp/dados.py", 'validos = [t for t in trat if not t["revogado"]]', "validos = list(trat)",
     "tratamento revogado ainda vale"),
    ("ptrp/dados.py", 'if t is None or t["cpf"] != f.so_digitos(cpf):', "if t is None:",
     "outro empregado dá ciência por ele"),
    ("ptrp/dados.py", "if momento > agora():", "if False:", "marcação incluída no futuro"),
    ("ptrp/esquema.sql", "BEFORE UPDATE OR DELETE ON %I FOR EACH ROW", "BEFORE UPDATE ON %I FOR EACH ROW",
     "tratamento apagável"),
    ("ptrp/esquema.sql", "espelho_id          BIGINT NOT NULL UNIQUE REFERENCES",
     "espelho_id          BIGINT NOT NULL REFERENCES", "aceite repetido no mesmo espelho"),
    ("ptrp/aej.py", 'ID_DO_REP if m.fonte == "O" else ""', "ID_DO_REP", "marcação incluída atribuída ao REP"),
    ("ptrp/aej.py", '(x.tipo == "E" and x.seq == 1 and dia.horario)', '(x.tipo == "E" and dia.horario)',
     "código do horário em toda entrada"),
    ("ptrp/aej.py", 'if (x.tipo == "D" or m.fonte == "I") else ""', 'if x.tipo == "D" else ""',
     "inclusão sem motivo no AEJ"),
    ("ptrp/aej.py", 'VERSAO_DO_LEIAUTE = "002"', 'VERSAO_DO_LEIAUTE = "001"', "versão antiga do leiaute"),
]
PLACEBO = ("reppp/formato.py", "Um valor que não cabe no leiaute.", "Um valor que não cabe no layout.",
           "PLACEBO — só um comentário")


def pytest_em(pasta: Path) -> bool:
    r = subprocess.run([sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider"], cwd=pasta,
                       capture_output=True, text=True)
    return r.returncode == 0


def main() -> int:
    rapido = "--rapido" in sys.argv
    with tempfile.TemporaryDirectory(prefix="ponto-bateria-") as tmp:
        copia = Path(tmp) / "repo"
        shutil.copytree(RAIZ, copia, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=copia, text=True)
        if r.returncode != 0:
            print("\n✗ VERMELHA — os testes não passam.")
            return 1
        if rapido:
            print("\n✓ testes verdes (sem mutações).")
            return 0
        falhas = []
        lista = MUTACOES + [PLACEBO]
        if "--so" in sys.argv:
            trecho = sys.argv[sys.argv.index("--so") + 1]
            lista = [m for m in lista if trecho in m[3]]
        if "--parte" in sys.argv:
            k, n = map(int, sys.argv[sys.argv.index("--parte") + 1].split("/"))
            lista = [m for i, m in enumerate(lista) if i % n == k - 1]
        for arquivo, de, para, o_que in lista:
            alvo = copia / arquivo
            original = alvo.read_text(encoding="utf-8")
            if original.count(de) != 1:
                falhas.append(f"âncora some ou repete ({original.count(de)}x): {o_que}")
                continue
            alvo.write_text(original.replace(de, para), encoding="utf-8")
            try:
                passou = pytest_em(copia)
            finally:
                alvo.write_text(original, encoding="utf-8")
            placebo = o_que.startswith("PLACEBO")
            if placebo and not passou:
                falhas.append("o placebo derrubou os testes (teste frágil)")
            elif not placebo and passou:
                falhas.append(f"NÃO pegou: {o_que}")
            print(("  ✓ " if (passou == placebo) else "  ✗ ") + o_que)
        if falhas:
            print("\n✗ VERMELHA —", *falhas, sep="\n  ")
            return 1
        pegos = sum(1 for m in lista if not m[3].startswith("PLACEBO"))
        print(f"\n✓ VERDE — testes passam e {pegos} estragos foram pegos" + ("; placebo verde." if PLACEBO in lista else "."))
        return 0


if __name__ == "__main__":
    sys.exit(main())
