"""Proteção de dados pessoais (LGPD; SDD §5, §10, §15 "PII criptografada/ACL").

* Cifra AES-256-GCM com nonce aleatório; texto cifrado = base64(nonce ‖ ciphertext).
* `pseudonym` = HMAC-SHA256 estável para relacionar pedidos do mesmo cliente sem expor o dado.
* A chave vem de AIC_PII_KEY (base64, 32 bytes). Em DRY_RUN há uma chave de simulação; LIVE exige chave real.
* Troca de chave: a nova vai em AIC_PII_KEY e a antiga em AIC_PII_PREVIOUS_KEYS (lidas só para decifrar);
  `python -m aicommerce.manage rotate-pii-key` regrava tudo com a nova e depois a antiga pode ser removida.
* Segredos curtos (TOTP do MFA) usam `seal`/`unseal` com prefixo de versão; valores antigos em claro continuam
  legíveis e são cifrados na próxima gravação ou na rotação.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from aicommerce.config import get_settings

AAD = b"aicommerce-pii-v1"
SEAL_PREFIX = "enc1:"


def _decode(b64: str, name: str) -> bytes:
    key = base64.b64decode(b64)
    if len(key) != 32:
        raise ValueError(f"{name} deve ter 32 bytes em base64")
    return key


def _key() -> bytes:
    return _decode(get_settings().pii_key, "AIC_PII_KEY")


def _keys() -> list[bytes]:
    """Chave atual primeiro; anteriores só para decifrar dados ainda não regravados."""
    prev = [k.strip() for k in get_settings().pii_previous_keys.split(",") if k.strip()]
    return [_key(), *(_decode(k, "AIC_PII_PREVIOUS_KEYS") for k in prev)]


def encrypt(data: dict[str, Any]) -> str:
    nonce = os.urandom(12)
    ct = AESGCM(_key()).encrypt(nonce, json.dumps(data, ensure_ascii=False).encode(), AAD)
    return base64.b64encode(nonce + ct).decode()


def decrypt(token: str) -> dict[str, Any]:
    raw = base64.b64decode(token)
    last: Exception | None = None
    for key in _keys():
        try:
            return json.loads(AESGCM(key).decrypt(raw[:12], raw[12:], AAD))
        except InvalidTag as e:
            last = e
    raise ValueError("dado cifrado com uma chave desconhecida (AIC_PII_KEY/AIC_PII_PREVIOUS_KEYS)") from last


def uses_current_key(token: str) -> bool:
    raw = base64.b64decode(token)
    try:
        AESGCM(_key()).decrypt(raw[:12], raw[12:], AAD)
        return True
    except InvalidTag:
        return False


def seal(secret: str | None) -> str | None:
    return None if secret is None else SEAL_PREFIX + encrypt({"v": secret})


def unseal(stored: str | None) -> str | None:
    if stored is None or not stored.startswith(SEAL_PREFIX):
        return stored  # legado em claro (antes da cifragem dos segredos de MFA)
    return decrypt(stored.removeprefix(SEAL_PREFIX))["v"]


def pseudonym(value: str) -> str:
    return hmac.new(_key(), value.strip().lower().encode(), hashlib.sha256).hexdigest()[:32]


def mask(data: dict[str, Any]) -> dict[str, Any]:
    """Visão minimizada para quem não tem orders:pii."""
    name = str(data.get("name", ""))
    return {"name": (name[:1] + "***") if name else None, "uf": data.get("uf"), "city": data.get("city")}
