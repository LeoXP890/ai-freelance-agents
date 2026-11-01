"""Troca da chave de dados pessoais (AIC_PII_KEY) sem perder nada, e segredos de MFA cifrados no banco.

Com a chave nova em AIC_PII_KEY e a antiga em AIC_PII_PREVIOUS_KEYS, tudo continua legível; `rotate-pii-key` regrava
pedidos (e o pseudônimo do cliente), tokens de canal, PKCE e segredos de MFA com a nova — depois a antiga sai.
"""

from __future__ import annotations

import base64
import datetime as dt
import os
import uuid

import pyotp
import pytest
from sqlalchemy import select

from aicommerce.config import get_settings
from aicommerce.db import session_scope
from aicommerce.identity import mfa, pii
from aicommerce.manage import rotate_pii_key
from aicommerce.models import ChannelAccount, Order, User

NEW_KEY = base64.b64encode(os.urandom(32)).decode()


@pytest.fixture
def swap_key(monkeypatch):
    s = get_settings()
    old = s.pii_key

    def to(new: str, previous: str = "") -> None:
        monkeypatch.setattr(s, "pii_key", new)
        monkeypatch.setattr(s, "pii_previous_keys", previous)

    yield to, old
    # devolve o banco de teste à chave original para os outros testes
    to(old, NEW_KEY)
    rotate_pii_key()
    to(old, "")


def test_rotation_keeps_everything_readable_with_only_the_new_key(swap_key, make_user):
    to, old = swap_key
    ext = f"ROT-{uuid.uuid4().hex[:8]}"
    customer = {"name": "Maria Cliente", "email": "maria@exemplo.com", "uf": "SP"}
    secret = pyotp.random_base32()
    user = make_user("viewer", mfa=False)
    with session_scope() as db:
        db.add(
            Order(
                channel="WOOCOMMERCE",
                external_id=ext,
                status="NEW",
                items_total_minor=1000,
                total_minor=1000,
                customer_ciphertext=pii.encrypt(customer),
                customer_ref=pii.pseudonym(customer["email"]),
                placed_at=dt.datetime.now(dt.UTC),
                is_simulated=True,
            )
        )
        u = db.get(User, user["id"])
        u.totp_secret, u.mfa_enabled = secret, True  # legado: segredo de MFA em claro
        old_ref = pii.pseudonym(customer["email"])

    to(NEW_KEY, old)  # chave nova em uso; a antiga só para ler
    with session_scope() as db:
        o = db.execute(select(Order).where(Order.external_id == ext)).scalar_one()
        assert pii.decrypt(o.customer_ciphertext) == customer and not pii.uses_current_key(o.customer_ciphertext)

    counts = rotate_pii_key()
    assert counts["orders"] >= 1 and counts["mfa_secrets"] >= 1 and counts["channel_tokens"] >= 1
    assert rotate_pii_key() == {"orders": 0, "channel_tokens": 0, "oauth_states": 0, "mfa_secrets": 0}  # idempotente

    to(NEW_KEY, "")  # chave antiga removida
    with session_scope() as db:
        o = db.execute(select(Order).where(Order.external_id == ext)).scalar_one()
        assert pii.decrypt(o.customer_ciphertext) == customer
        assert o.customer_ref == pii.pseudonym(customer["email"]) != old_ref  # pseudônimo acompanha a chave
        for acc in db.execute(select(ChannelAccount)).scalars():
            assert pii.decrypt(acc.access_token_ciphertext)["v"]
        u = db.get(User, user["id"])
        assert u.totp_secret.startswith(pii.SEAL_PREFIX) and secret not in u.totp_secret  # agora cifrado
        mfa.verify_login_code(db, u, pyotp.TOTP(secret).now())  # e o login com MFA continua funcionando


def test_unknown_key_is_a_clear_error_not_garbage(swap_key):
    to, old = swap_key
    token = pii.encrypt({"v": "x"})
    to(NEW_KEY, "")
    with pytest.raises(ValueError, match="chave desconhecida"):
        pii.decrypt(token)
    to(old, "")


def test_new_mfa_enrollment_never_stores_the_secret_in_clear(client):
    from tests.test_mfa_enrollment import login, new_owner

    u = new_owner()
    tok = login(client, u).json()["enrollment_token"]
    s = client.post("/v1/auth/mfa/enroll/start", json={"enrollment_token": tok}).json()["secret"]
    with session_scope() as db:
        stored = db.get(User, uuid.UUID(u["user_id"])).totp_pending_secret
    assert s not in stored and pii.unseal(stored) == s
    ok = client.post("/v1/auth/mfa/enroll/confirm", json={"enrollment_token": tok, "code": pyotp.TOTP(s).now()})
    assert ok.status_code == 200
    with session_scope() as db:
        assert pii.unseal(db.get(User, uuid.UUID(u["user_id"])).totp_secret) == s
