// Drift do contrato no frontend: enums e campos obrigatórios devem coincidir com o schema exportado pelo backend.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { AGENT_STATES, ROOMS } from "../lib/contract";

const dir = resolve(__dirname, "../../../packages/contracts");
const eventSchema = JSON.parse(readFileSync(resolve(dir, "office-event.v1.schema.json"), "utf8"));
const snapSchema = JSON.parse(readFileSync(resolve(dir, "presence-snapshot.v1.schema.json"), "utf8"));

describe("contrato office v1", () => {
  it("AgentState igual ao backend", () => {
    expect([...AGENT_STATES].sort()).toEqual([...eventSchema.$defs.AgentState.enum].sort());
  });

  it("campos obrigatórios do evento", () => {
    expect(eventSchema.required).toEqual(expect.arrayContaining(["seq", "event_id", "type", "timestamp", "mode",
      "simulated"]));
    expect(Object.keys(eventSchema.properties)).toEqual(expect.arrayContaining(["agent_id", "task_id", "state",
      "payload", "schema_version"]));
  });

  it("snapshot expõe as_of_seq para reconexão por offset", () => {
    expect(snapSchema.required).toEqual(expect.arrayContaining(["as_of_seq", "agents", "mode", "simulated"]));
  });

  it("nove salas do SDD §23.2", () => {
    expect(ROOMS.map((r) => r.id)).toEqual(["ceo", "research", "supply", "marketing", "store", "finance", "support",
      "compliance", "meeting"]);
  });
});

import { serverUrlAllowed } from "../lib/api";
describe("servidor configurável (auditoria A11)", () => {
  it("https em qualquer host; http só local", () => {
    expect(serverUrlAllowed("https://api.loja.com.br/api")).toBe(true);
    expect(serverUrlAllowed("http://localhost:8080/api")).toBe(true);
    expect(serverUrlAllowed("http://127.0.0.1:8080")).toBe(true);
    expect(serverUrlAllowed("http://api.loja.com.br/api")).toBe(false);
    expect(serverUrlAllowed("http://localhost.evil.com")).toBe(false);
    expect(serverUrlAllowed("javascript:alert(1)")).toBe(false);
  });
});
