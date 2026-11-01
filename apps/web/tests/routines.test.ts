import { describe, expect, it } from "vitest";
import { when } from "../lib/routines";
import type { Routine } from "../lib/api";

const base = { id: "1", agent_id: "luna", kind: "research.opportunities", title: "t", text: null, enabled: true,
  next_run_at: null, last_run_at: null, last_task_id: null, last_outcome: null } as const;

describe("rotinas", () => {
  it("descreve a agenda em português", () => {
    expect(when({ ...base, schedule: "DAILY", time_of_day: "08:30", interval_hours: null } as Routine)).toBe("Todo dia às 08:30");
    expect(when({ ...base, schedule: "INTERVAL", time_of_day: null, interval_hours: 6 } as Routine)).toBe("A cada 6 horas");
  });
});

import { economicsOf, payloadRows } from "../lib/workflow";

describe("conta por venda na aprovação", () => {
  it("lê a conta anexada e não repete o objeto na lista de campos", () => {
    const payload = { title: "Kit", economics: { lines: [{ key: "price", label: "Preço", amount_minor: 10990, source: "x" }], profit_minor: 3646, margin_pct: 33.2, currency: "BRL" } };
    expect(economicsOf(payload)?.profit_minor).toBe(3646);
    expect(payloadRows(payload).map((r) => r.key)).toEqual(["title"]);
    expect(economicsOf({ title: "x" })).toBeNull();
  });
});

import { marketCheckOf } from "../lib/workflow";

describe("concorrência na aprovação", () => {
  it("lê a pesquisa de preço e não repete na lista de campos", () => {
    const payload = { title: "Kit", market_check: { count: 2, min: 36.9, median: 37.8, max: 38.79, offers: [], note: "ok" } };
    expect(marketCheckOf(payload)?.min).toBe(36.9);
    expect(payloadRows(payload).map((r) => r.key)).toEqual(["title"]);
  });
});
