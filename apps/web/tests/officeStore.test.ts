import { describe, expect, it } from "vitest";
import type { OfficeEventV1, PresenceSnapshotV1 } from "../lib/contract";
import { agentsInRoom, applyEvent, applyEvents, applySnapshot, FEED_MAX, initialOffice } from "../lib/officeStore";
import { backoffMs } from "../lib/officeSync";

const snap = (as_of_seq: number, state: "IDLE" | "WORKING" = "IDLE"): PresenceSnapshotV1 => ({
  schema_version: 1, as_of_seq, mode: "DRY_RUN", simulated: true, emergency_stop: false,
  agents: [
    { agent_id: "luna", name: "Luna", role: "Pesquisa", room: "research", color: "#2bc1b3", state, task_id: null, last_seq: 0 },
    { agent_id: "atlas", name: "Atlas", role: "Direção", room: "ceo", color: "#8e75fb", state: "IDLE", task_id: null, last_seq: 0 },
  ],
});

const ev = (seq: number, agent_id: string | null, state: OfficeEventV1["state"], type = "agent.task.state_changed"):
  OfficeEventV1 => ({
  schema_version: 1, seq, event_id: `e-${seq}`, type, agent_id, task_id: agent_id ? `t-${agent_id}` : null, state,
  timestamp: "2026-10-23T00:00:00Z", mode: "DRY_RUN", simulated: true, payload: {},
});

describe("officeStore", () => {
  it("ignora eventos antes do snapshot", () => {
    const s = applyEvent(initialOffice, ev(1, "luna", "WORKING"));
    expect(s.ready).toBe(false);
    expect(s.ignored).toBe(1);
  });

  it("aplica snapshot e eventos posteriores ao offset", () => {
    let s = applySnapshot(initialOffice, snap(10));
    s = applyEvent(s, ev(11, "luna", "WORKING"));
    expect(s.agents.luna?.state).toBe("WORKING");
    expect(s.agents.luna?.last_seq).toBe(11);
    expect(s.lastSeq).toBe(11);
    expect(s.byAgent.luna).toHaveLength(1);
    expect(s.agents.luna?.last_event_at).toBe("2026-10-23T00:00:00Z"); // diretório mostra "há quanto tempo" real
    s = applyEvent(s, { ...ev(12, "luna", null), timestamp: "2026-10-23T00:01:00Z" });
    expect(s.agents.luna?.state).toBe("WORKING");
    expect(s.agents.luna?.last_event_at).toBe("2026-10-23T00:01:00Z");
  });

  it("descarta duplicados e eventos fora de ordem (seq <= lastSeq)", () => {
    let s = applySnapshot(initialOffice, snap(10));
    s = applyEvent(s, ev(12, "luna", "COMPLETED"));
    s = applyEvent(s, ev(12, "luna", "COMPLETED"));
    s = applyEvent(s, ev(11, "luna", "WORKING"));
    s = applyEvent(s, ev(9, "luna", "ERROR"));
    expect(s.agents.luna?.state).toBe("COMPLETED");
    expect(s.ignored).toBe(3);
    expect(s.feed).toHaveLength(1);
  });

  it("lote desordenado é aplicado por seq", () => {
    const s = applyEvents(applySnapshot(initialOffice, snap(0)), [ev(3, "luna", "COMPLETED"), ev(1, "luna", "PLANNING"),
      ev(2, "luna", "WORKING")]);
    expect(s.agents.luna?.state).toBe("COMPLETED");
    expect(s.feed.map((e) => e.seq)).toEqual([3, 2, 1]);
  });

  it("lacunas de seq são aceitas (IDs de transações revertidas)", () => {
    const s = applyEvent(applySnapshot(initialOffice, snap(5)), ev(42, "atlas", "PLANNING"));
    expect(s.lastSeq).toBe(42);
    expect(s.agents.atlas?.state).toBe("PLANNING");
  });

  it("reconexão: snapshot mais novo substitui estado; snapshot velho não regride", () => {
    let s = applyEvent(applySnapshot(initialOffice, snap(10)), ev(11, "luna", "WORKING"));
    s = applySnapshot(s, snap(20, "IDLE"));
    expect(s.lastSeq).toBe(20);
    expect(s.agents.luna?.state).toBe("IDLE");
    const stale = applySnapshot(s, snap(15, "WORKING"));
    expect(stale).toBe(s);
  });

  it("evento sem agente não altera presença e kill switch reflete eventos", () => {
    let s = applySnapshot(initialOffice, snap(0));
    s = applyEvent(s, ev(1, null, null, "emergency_stop.activated"));
    expect(s.emergencyStop).toBe(true);
    s = applyEvent(s, ev(2, null, null, "emergency_stop.released"));
    expect(s.emergencyStop).toBe(false);
    expect(s.agents.luna?.state).toBe("IDLE");
  });

  it("evento de agente desconhecido não cria avatar fantasma", () => {
    const s = applyEvent(applySnapshot(initialOffice, snap(0)), ev(1, "ghost", "WORKING"));
    expect(s.agents.ghost).toBeUndefined();
  });

  it("feed é limitado", () => {
    let s = applySnapshot(initialOffice, snap(0));
    for (let i = 1; i <= FEED_MAX + 10; i++) s = applyEvent(s, ev(i, "luna", "WORKING"));
    expect(s.feed).toHaveLength(FEED_MAX);
  });

  it("agentsInRoom respeita ordem do snapshot", () => {
    const s = applySnapshot(initialOffice, snap(0));
    expect(agentsInRoom(s, "research").map((a) => a.agent_id)).toEqual(["luna"]);
    expect(agentsInRoom(s, "marketing")).toEqual([]);
  });

  it("backoff exponencial com teto", () => {
    expect(backoffMs(0)).toBe(500);
    expect(backoffMs(3)).toBe(4000);
    expect(backoffMs(50)).toBe(30_000);
  });
});

describe("layout do escritório 3D", async () => {
  const { slotOffset } = await import("../lib/officeLayout");
  it("agentes da mesma sala ficam lado a lado no eixo horizontal da tela", () => {
    const [a, b] = [slotOffset(0, 2), slotOffset(1, 2)];
    // no eixo horizontal da tela isométrica x e z variam em sentidos opostos (sem sobreposição de rótulos)
    expect(a[0]).toBeCloseTo(-b[0]);
    expect(a[0] + a[1]).toBeCloseTo(0);
    expect(slotOffset(0, 1)).toEqual([0, -0]);
    expect(Math.hypot(a[0] - b[0], a[1] - b[1])).toBeCloseTo(2.3); // cabe o rótulo mais longo
  });
});

describe("aparência do avatar", () => {
  it("atualiza cor e acessório do agente sem tocar nos demais", async () => {
    const { applyAppearance, applySnapshot, initialOffice } = await import("../lib/officeStore");
    const base = applySnapshot(initialOffice, {
      schema_version: 1, as_of_seq: 1, mode: "DRY_RUN", simulated: true, emergency_stop: false,
      agents: [
        { agent_id: "luna", name: "Luna", role: "r", room: "research", color: "#111111", state: "IDLE", task_id: null, last_seq: 1 },
        { agent_id: "vera", name: "Vera", role: "r", room: "research", color: "#222222", state: "IDLE", task_id: null, last_seq: 1 },
      ],
    });
    const next = applyAppearance(base, "luna", "#22aa88", { accessory: "hat", skin: "#c69c7a" });
    expect(next.agents.luna?.color).toBe("#22aa88");
    expect(next.agents.luna?.appearance?.accessory).toBe("hat");
    expect(next.agents.vera).toBe(base.agents.vera);
    expect(applyAppearance(base, "x", "#000000", {})).toBe(base);
  });
});
