"""Cria ou atualiza o esquema do banco e aplica as permissões.

Roda com o usuário DONO do banco (PONTO_BANCO aponta para ele). Os dois
serviços rodam com usuários sem DDL: o REP-P grava só no que é dele e o
programa de tratamento só lê o REP-P (`scripts/permissoes.sql`).

Uso: python -m scripts.migrar
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from ptrp import dados  # noqa: E402


async def main() -> None:
    url = os.environ.get("PONTO_BANCO")
    if not url:
        raise SystemExit("PONTO_BANCO não definido")
    conn = await asyncpg.connect(url)
    try:
        async with conn.transaction():
            await dados.criar_esquema(conn)
            await conn.execute((RAIZ / "scripts" / "permissoes.sql").read_text(encoding="utf-8"))
        tabelas = await conn.fetchval(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
        print(f"esquema em dia ({tabelas} tabelas); permissões aplicadas")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
