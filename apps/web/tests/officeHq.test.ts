import { describe, expect, it } from "vitest";
import { HQ_D, HQ_W, placeAgents, ZONES, zoneCenter, zoneOfRoom, zoneSize } from "../lib/officeHq";

const BACKEND = [
  ["atlas", "ceo"], ["clara", "compliance"], ["dante", "meeting"], ["eva", "support"], ["finn", "finance"], ["iris", "marketing"],
  ["logan", "store"], ["luna", "research"], ["nexus", "supply"], ["nova", "store"], ["orion", "finance"], ["pixel", "marketing"],
  ["vera", "research"],
].map(([agent_id, room]) => ({ agent_id: agent_id!, room: room! }));

describe("sede 3D (UXD-017)", () => {
  it("7 ambientes cobrem as 9 salas do backend e a grade 3×3 sem sobreposição", () => {
    expect(ZONES).toHaveLength(7);
    for (const r of ["ceo", "research", "supply", "marketing", "store", "finance", "support", "compliance", "meeting"]) {
      expect(ZONES.some((z) => z.rooms.includes(r))).toBe(true);
    }
    const cells = ZONES.flatMap((z) => Array.from({ length: z.cols * z.rows }, (_, i) => `${z.col + (i % z.cols)},${z.row + Math.floor(i / z.cols)}`));
    expect(new Set(cells).size).toBe(9);
    expect(cells).toHaveLength(9);
  });

  it("todos os 13 agentes ficam no ambiente da sua sala real, no seu lugar com mesa e dentro da sala", () => {
    const p = placeAgents(BACKEND);
    expect(p).toHaveLength(13);
    for (const a of p) {
      const room = BACKEND.find((b) => b.agent_id === a.agentId)!.room;
      expect(a.zone).toBe(zoneOfRoom(room));
      expect(a.atDesk).toBe(true);
      const z = ZONES.find((x) => x.id === a.zone)!;
      const [cx, cz] = zoneCenter(z);
      const { w, d } = zoneSize(z);
      expect(Math.abs(a.x - cx)).toBeLessThan(w / 2);
      expect(Math.abs(a.z - cz)).toBeLessThan(d / 2);
    }
    expect(Math.max(...p.map((a) => Math.abs(a.x)))).toBeLessThan(HQ_W / 2);
    expect(Math.max(...p.map((a) => Math.abs(a.z)))).toBeLessThan(HQ_D / 2);
    const keys = p.map((a) => `${a.x.toFixed(2)},${a.z.toFixed(2)}`);
    expect(new Set(keys).size).toBe(13); // ninguém empilhado
  });

  it("agente movido pelo backend para outra sala aparece lá; sala desconhecida não some", () => {
    const moved = BACKEND.map((a) => (a.agent_id === "luna" ? { ...a, room: "meeting" } : a.agent_id === "eva" ? { ...a, room: "nova-sala" } : a));
    const p = placeAgents(moved);
    expect(p.find((a) => a.agentId === "luna")!.zone).toBe("meeting");
    expect(p.find((a) => a.agentId === "eva")!.zone).toBe("meeting");
    const meeting = p.filter((a) => a.zone === "meeting");
    expect(new Set(meeting.map((a) => `${a.x},${a.z}`)).size).toBe(meeting.length);
    expect(placeAgents(moved)).toEqual(p); // determinístico
  });
});
