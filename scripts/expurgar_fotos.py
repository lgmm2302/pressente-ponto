"""Apaga as fotos de batida cujo prazo de guarda venceu (LGPD). A batida, a
prova e o hash da foto continuam. Uso: python -m scripts.expurgar_fotos"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reppp import prova  # noqa: E402


async def main() -> None:
    conn = await asyncpg.connect(os.environ["PONTO_BANCO"])
    try:
        print(f"fotos expurgadas: {await prova.expurgar_fotos_vencidas(conn)}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
