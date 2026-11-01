import pytest

from aicommerce.governance.policy import (
    POLICY_V1_RULES,
    POLICY_V1_VERSION,
    Decision,
    PolicyContext,
    PolicyInput,
    evaluate,
)

CTX = PolicyContext(emergency_stop=False, golive_decision=None)


def run(action, *, risk=10, evidence=("ev:1",), money=0, currency="BRL", mode="DRY_RUN", ctx=CTX):
    return evaluate(
        PolicyInput(action, risk, list(evidence), money, currency, mode), ctx, POLICY_V1_RULES, POLICY_V1_VERSION
    )


def test_unknown_action_is_denied_closed_world():
    assert run("TRANSFER_ALL_MONEY").decision is Decision.DENY


@pytest.mark.parametrize("action", ["PUBLISH_LISTING", "CREATE_CAMPAIGN", "SPEND", "PAUSE_MEDIA", "REFUND"])
def test_live_external_effect_without_golive_is_denied(action):
    assert run(action, money=100, mode="LIVE").decision is Decision.DENY
    denied = run(action, money=100, mode="LIVE", ctx=PolicyContext(False, golive_decision="DENY"))
    assert denied.decision is Decision.DENY


def test_live_with_golive_allowed_still_requires_human_approval():
    r = run("PUBLISH_LISTING", mode="LIVE", ctx=PolicyContext(False, golive_decision="ALLOW"))
    assert r.decision is Decision.REQUIRE_APPROVAL


@pytest.mark.parametrize("action", ["SPEND", "CREATE_CAMPAIGN", "INCREASE_BUDGET", "PUBLISH_LISTING"])
def test_emergency_stop_denies_spend_and_publication(action):
    assert run(action, money=100, ctx=PolicyContext(True, None)).decision is Decision.DENY


def test_emergency_stop_does_not_block_pausing():
    r = run("PAUSE_LISTING", ctx=PolicyContext(True, None))
    assert r.decision is Decision.ALLOW


def test_publication_without_evidence_requests_changes():
    assert run("PUBLISH_LISTING", evidence=()).decision is Decision.REQUEST_CHANGES


def test_monetary_action_requires_positive_scope():
    assert run("SPEND", money=0).decision is Decision.REQUEST_CHANGES


@pytest.mark.parametrize("action", ["PUBLISH_LISTING", "SPEND", "INCREASE_BUDGET", "REFUND", "CHANGE_SUPPLIER"])
def test_material_actions_require_human_approval(action):
    assert run(action, money=500).decision is Decision.REQUIRE_APPROVAL


def test_low_risk_research_is_allowed_but_high_risk_is_escalated():
    assert run("RESEARCH").decision is Decision.ALLOW
    assert run("RESEARCH", risk=75).decision is Decision.REQUIRE_APPROVAL


def test_media_pause_needs_explicit_delegation():
    assert run("PAUSE_MEDIA").decision is Decision.REQUIRE_APPROVAL
    delegated = PolicyContext(False, None, delegations=frozenset({"PAUSE_MEDIA"}))
    assert run("PAUSE_MEDIA", ctx=delegated).decision is Decision.ALLOW


def test_invalid_currency_and_negative_amount_denied():
    assert run("SPEND", money=10, currency="USD").decision is Decision.DENY
    assert run("SPEND", money=-1).decision is Decision.DENY


def test_result_carries_policy_version_and_stable_hash():
    a, b = run("RESEARCH"), run("SPEND", money=1)
    assert a.policy_version == POLICY_V1_VERSION and a.policy_sha256 == b.policy_sha256 and len(a.policy_sha256) == 64
