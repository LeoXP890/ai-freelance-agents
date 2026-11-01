#!/usr/bin/env python3
"""Gera packages/contracts/*.json a partir dos modelos Pydantic da API (fonte de verdade).

Uso (a partir de apps/api): uv run python ../../scripts/export_contracts.py
"""
import json
import pathlib

from aicommerce.office.contract import json_schemas

OUT = pathlib.Path(__file__).resolve().parents[1] / "packages" / "contracts"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, schema in json_schemas().items():
        (OUT / name).write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf8")
        print("escrito", OUT / name)


if __name__ == "__main__":
    main()
