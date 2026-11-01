"""Pontos de falha para testes de recuperação (auditoria A07): derrubam o processo de verdade (`os._exit`, como um
kill -9) numa fronteira escolhida, para provar que a retomada não duplica efeito nem perde custo.

Inertes fora de teste: só agem com AIC_FAULT_POINT definido E ambiente test/local E modo DRY_RUN.
"""

from __future__ import annotations

import os

EXIT_CODE = 137


def point(name: str) -> None:
    if os.environ.get("AIC_FAULT_POINT") != name:
        return
    from aicommerce.config import Environment, get_settings

    s = get_settings()
    if s.is_dry_run and s.environment in (Environment.TEST, Environment.LOCAL):
        os._exit(EXIT_CODE)
