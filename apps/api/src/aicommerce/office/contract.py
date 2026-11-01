"""Contrato versionado dos eventos do escritório (SDD §13, §14, §23.2).

Fonte de verdade do schema: este módulo. `scripts/export_contracts.py` gera
`packages/contracts/office-event.v1.schema.json`, consumido pelo frontend; um teste detecta drift.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


class AgentState(enum.StrEnum):
    IDLE = "IDLE"
    PLANNING = "PLANNING"
    WORKING = "WORKING"
    TOOL_CALL = "TOOL_CALL"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


class EventType(enum.StrEnum):
    AGENT_TASK_CREATED = "agent.task.created"
    AGENT_TASK_STARTED = "agent.task.started"
    AGENT_TASK_STATE_CHANGED = "agent.task.state_changed"
    AGENT_TASK_COMPLETED = "agent.task.completed"
    AGENT_TASK_FAILED = "agent.task.failed"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_DECIDED = "approval.decided"
    APPROVAL_EXPIRED = "approval.expired"
    EXECUTION_SUCCEEDED = "execution.succeeded"
    EXECUTION_FAILED = "execution.failed"
    BUDGET_RESERVED = "budget.reserved"
    LISTING_PUBLISH_REQUESTED = "listing.publish.requested"
    LISTING_PUBLISH_CONFIRMED = "listing.publish.confirmed"
    ORDER_RECEIVED = "order.received"
    SUPPLIER_STOCK_STALE = "supplier.stock.stale"
    SUPPLIER_STATUS_CHANGED = "supplier.status_changed"
    CATALOG_PRODUCT_CREATED = "catalog.product.created"
    CATALOG_PRODUCT_UPDATED = "catalog.product.updated"
    LISTING_PREPARED = "listing.prepared"
    WEBHOOK_RECEIVED = "webhook.received"
    ORDER_STATUS_CHANGED = "order.status_changed"
    RETURN_REQUESTED = "return.requested"
    SETTLEMENT_IMPORTED = "settlement.imported"
    INVOICE_AUTHORIZED = "invoice.authorized"
    INTEGRATION_UNKNOWN_RESULT = "integration.unknown_result"
    GO_LIVE_REVOKED = "go_live.revoked"
    EMERGENCY_STOP_ACTIVATED = "emergency_stop.activated"
    EMERGENCY_STOP_RELEASED = "emergency_stop.released"
    CAMPAIGN_CREATED = "campaign.created"
    CAMPAIGN_PAUSED = "campaign.paused"


class OfficeEventV1(BaseModel):
    model_config = ConfigDict(extra="forbid", title="OfficeEventV1", use_enum_values=True)

    schema_version: Literal[1] = SCHEMA_VERSION
    seq: int = Field(ge=1, description="Ordem global monotônica; cliente aplica somente seq > último aplicado")
    event_id: uuid.UUID
    type: EventType
    agent_id: str | None = None
    task_id: uuid.UUID | None = None
    state: AgentState | None = None
    timestamp: dt.datetime
    mode: Literal["DRY_RUN", "LIVE"]
    simulated: bool = Field(description="true em DRY_RUN — UI deve exibir 'Simulado'")
    payload: dict[str, Any] = Field(default_factory=dict)


class PresenceV1(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)
    agent_id: str
    name: str
    role: str
    room: str
    color: str
    appearance: dict[str, Any] = Field(default_factory=dict)
    state: AgentState
    task_id: uuid.UUID | None
    last_seq: int
    # Opcional (aditivo, v1 compatível): quando o estado mudou pela última vez — a UI mostra "há 2 min".
    last_event_at: dt.datetime | None = None


class PresenceSnapshotV1(BaseModel):
    model_config = ConfigDict(extra="forbid", title="PresenceSnapshotV1")
    schema_version: Literal[1] = SCHEMA_VERSION
    as_of_seq: int = Field(ge=0, description="Reconectar com after_seq=as_of_seq")
    mode: Literal["DRY_RUN", "LIVE"]
    simulated: bool
    emergency_stop: bool
    agents: list[PresenceV1]


def json_schemas() -> dict[str, dict]:
    return {
        "office-event.v1.schema.json": OfficeEventV1.model_json_schema(),
        "presence-snapshot.v1.schema.json": PresenceSnapshotV1.model_json_schema(),
    }
