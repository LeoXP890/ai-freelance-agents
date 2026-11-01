#!/usr/bin/env python3
"""Pesquisa de mercado real no Mercado Livre (somente GET) e gravação como oportunidades da Luna.

Usa o cofre somente leitura do titular (scripts/ml_oauth_readonly.py); o token não é impresso nem passado por argv.
Sem --save apenas mostra o resultado. Com --save grava no banco de AIC_DATABASE_URL (padrão: stack DRY_RUN local).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/api/src'))
from aicommerce.ai import ml_market_research as mr  # noqa: E402
from aicommerce.providers.ml_onboarding import ReadOnlyOnboarding  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', type=Path, default=Path.home() / '.local/share/aicommerce/ml-readonly')
    parser.add_argument('--category', action='append', help='MLBxxxx=Nome (repetível); padrão: casa, pet, esportes')
    parser.add_argument('--per-category', type=int, default=8)
    parser.add_argument('--min-ticket', type=float, default=mr.MIN_TICKET)
    parser.add_argument('--save', action='store_true')
    parser.add_argument('--json', type=Path, help='também grava o resultado completo neste arquivo')
    args = parser.parse_args()
    categories = dict(c.split('=', 1) for c in args.category) if args.category else mr.DEFAULT_CATEGORIES
    reader = ReadOnlyOnboarding(args.vault).research_reader(authorized=True, refresh_if_needed=True)

    def get(path):
        r = reader.get(path)
        return (r.http or 0, r.data) if r.status == 'OK' else (r.http or 0, None)

    me = reader.get('/users/me')
    seller_id = str(me.data['id']) if me.status == 'OK' and isinstance(me.data, dict) else None
    items = mr.research(get, categories, per_category=args.per_category, min_ticket=args.min_ticket,
                        seller_id=seller_id)
    for o in items:
        m = o['sources'][0]['market']
        med = f"R$ {m['price_median']:.2f}" if m['price_median'] else 's/ preço'
        ship = f"frete R$ {m['shipping']['seller_cost']:.2f}" if m.get('shipping') else 'frete ?'
        left = f"sobra R$ {m['max_supplier_and_tax']:.2f}" if m.get('max_supplier_and_tax') is not None else ''
        print(f"{o['score']:>3}  {med:>11}  {m['sellers']:>2} vend.  {ship:>15}  {left:>16}  {o['title'][:55]}")
        for c in o['contraindications'][:-1]:
            print(f"          ⚠ {c}")
    if args.json:
        args.json.write_text(json.dumps(items, ensure_ascii=False, indent=1))
    if args.save:
        from aicommerce.db import session_scope

        with session_scope() as db:
            print(json.dumps(mr.save(db, items, categories=categories), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
