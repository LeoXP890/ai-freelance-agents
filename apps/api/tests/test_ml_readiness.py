import datetime as dt

import pytest
from sqlalchemy import select

from aicommerce.commerce import channels
from aicommerce.db import session_scope
from aicommerce.models import ChannelAccount
from aicommerce.providers import marketplaces as mp
from aicommerce.providers.contracts import ErrorKind, ResultStatus
from aicommerce.providers.ml_readonly import MLReadOnly
from tests.test_ml_live import FakeML, connect_real, item, live, live_app, ml  # noqa: F401


@pytest.mark.parametrize("status", [401, 403, 404, 429, 503])
def test_item_lookup_error_never_publishes_duplicate(status):
    api = FakeML()
    ref = ml(api).execute(live("publish_listing", item())).provider_ref
    original = api.__call__

    def partial(method, url, *args, **kw):
        if method == "GET" and url.endswith("/items/" + ref):
            return status, {"message": "read failed"}
        return original(method, url, *args, **kw)

    before = len([x for x in api.calls if x[0] == "POST"])
    assert ml(partial).execute(live("publish_listing", item())).status is ResultStatus.FAILED
    assert len([x for x in api.calls if x[0] == "POST"]) == before


def test_duplicate_sku_is_rejected_instead_of_selecting_arbitrary_item():
    api = FakeML()
    ref = ml(api).execute(live("publish_listing", item())).provider_ref
    api.items["MLB9999"] = {**api.items[ref], "id": "MLB9999"}
    result = ml(api).execute(live("update_listing", item(price=10)))
    assert result.error_kind is ErrorKind.REJECTED
    assert not any(c[0] == "PUT" for c in api.calls)


def test_reconciliation_does_not_ignore_images_or_attributes():
    api = FakeML()
    ml(api).execute(live("publish_listing", item()))
    assert ml(api).lookup("listing:x:update_listing_material", item(pictures=[{"source": "https://new/image"}])) is None
    assert ml(api).lookup("listing:x:update_listing_material", item(attributes=[])) is None


def test_write_500_after_effect_is_unknown_and_reconciles_without_second_post():
    api = FakeML()

    def lost(method, *args, **kw):
        result = api(method, *args, **kw)
        return (500, {"error": "upstream"}) if method == "POST" else result

    provider = ml(lost)
    assert provider.execute(live("publish_listing", item())).status is ResultStatus.UNKNOWN
    assert provider.lookup("listing:x:publish_listing", item()).status is ResultStatus.SUCCEEDED
    assert len(api.items) == 1


@pytest.mark.usefixtures("live_app")
def test_live_token_commits_reauth_even_when_error_propagates(monkeypatch):
    ep = connect_real(monkeypatch)
    with session_scope() as db:
        acc = db.execute(select(ChannelAccount).where(ChannelAccount.is_simulated.is_(False))).scalar_one()
        acc.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    ep.reply = (400, {"error": "invalid_grant"})
    with pytest.raises(channels.TokenUnavailable):
        channels.live_token("MERCADO_LIVRE")
    with session_scope() as db:
        acc = db.execute(select(ChannelAccount).where(ChannelAccount.is_simulated.is_(False))).scalar_one()
        assert acc.status == "REAUTH_REQUIRED"


@pytest.mark.parametrize(
    "resource",
    ["//evil.example/orders/1", "/orders/../users/me", "/orders/1?access_token=x", "/users/1", "/orders/%31"],
)
def test_notifications_bind_resource_to_topic(resource):
    note = {"application_id": "123", "topic": "orders_v2", "user_id": 1, "resource": resource}
    assert mp.validate_ml_notification(note, "123")


def test_notification_non_object_is_rejected(client):
    assert client.post("/v1/webhooks/mercadolivre", json=[]).status_code == 400


def test_readonly_retries_bounded_respects_rate_limit_and_redacts_receipt():
    calls, sleeps = [], []
    replies = [
        (429, {}, {"Retry-After": "3"}),
        (503, {}, {}),
        (200, {"id": 123456, "site_id": "MLB", "email": "private@example.invalid"}, {}),
    ]

    def send(path, token):
        calls.append(path)
        return replies.pop(0)

    receipt = MLReadOnly("test-token", send=send, sleep=sleeps.append).receipt()
    assert receipt["state"] == "READ_ONLY_VERIFICADO" and receipt["account_id"] == "***456"
    assert receipt["commerce_effects"] == 0 and "email" not in str(receipt) and "test-token" not in str(receipt)
    assert calls == ["/users/me"] * 3 and 3 in sleeps and 4 in sleeps


@pytest.mark.parametrize(
    "status,expected", [(401, "REAUTH_REQUIRED"), (403, "PERMISSION_DENIED"), (429, "DEFERRED_RATE_LIMIT")]
)
def test_readonly_does_not_retry_auth_or_long_rate_limit(status, expected):
    calls = []

    def send(*args):
        calls.append(args)
        return status, {}, {"Retry-After": "120"}

    assert MLReadOnly("token", send=send, sleep=lambda _: None).receipt()["state"] == expected
    assert len(calls) == 1


@pytest.mark.parametrize(
    "path", ["https://evil.example/", "//evil.example/users/me", "/oauth/token", "/orders/1?x=2", "/users/me#x"]
)
def test_readonly_allowlist_rejects_arbitrary_destinations(path):
    def never(*args):
        pytest.fail("network call forbidden")

    with pytest.raises(ValueError):
        MLReadOnly("token", send=never).get(path)
