"""TrendSource (SDD §23.1). Somente mock em DRY_RUN: fixtures determinísticas, claramente SIMULADAS.

Nenhuma fonte real é consultada nem inventada: cada item tem url `sim://`, data e métrica fictícias.
"""

from __future__ import annotations

from typing import Any, Protocol

from aicommerce.ai.knowledge import tokens


class TrendSource(Protocol):
    name: str

    def search(self, topic: str, limit: int = 5) -> list[dict[str, Any]]: ...


FIXTURES: list[dict[str, Any]] = [
    {
        "title": "Busca por garrafas térmicas cresce no inverno (SIMULADO)",
        "category": "Casa e cozinha",
        "keywords": "garrafa térmica inox casa cozinha bebida inverno",
        "metric": "+38% buscas t/t",
        "date": "2026-09-30",
    },
    {
        "title": "Organizadores de cabos em alta com home office (SIMULADO)",
        "category": "Eletrônicos > Acessórios",
        "keywords": "organizador cabos home office mesa eletrônicos acessório",
        "metric": "+22% buscas t/t",
        "date": "2026-10-02",
    },
    {
        "title": "Comedouros lentos para pets: nicho com baixa concorrência (SIMULADO)",
        "category": "Pet",
        "keywords": "pet cachorro gato comedouro lento ansiedade",
        "metric": "+15% buscas t/t",
        "date": "2026-09-18",
    },
    {
        "title": "Garrafas squeeze esportivas: saturação alta em marketplaces (SIMULADO)",
        "category": "Esporte",
        "keywords": "garrafa squeeze esporte academia bebida",
        "metric": "concorrência alta",
        "date": "2026-10-05",
    },
    {
        "title": "Suportes de notebook ajustáveis mantêm demanda estável (SIMULADO)",
        "category": "Eletrônicos > Acessórios",
        "keywords": "suporte notebook ergonomia home office mesa",
        "metric": "estável",
        "date": "2026-09-25",
    },
    {
        "title": "Potes herméticos de vidro para marmitas (SIMULADO)",
        "category": "Casa e cozinha",
        "keywords": "pote vidro hermético marmita cozinha casa",
        "metric": "+12% buscas t/t",
        "date": "2026-10-08",
    },
]


class MockTrendSource:
    name = "mock-trends"

    def search(self, topic: str, limit: int = 5) -> list[dict[str, Any]]:
        q = set(tokens(topic))
        scored = []
        for i, f in enumerate(FIXTURES):
            overlap = len(q & set(tokens(f["keywords"] + " " + f["title"])))
            if overlap:
                scored.append((overlap, i, f))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [{**f, "url": f"sim://tendencias/{i}", "simulated": True} for _, i, f in scored[:limit]]
