// Layout puro da sede 3D (UXD-017): 7 ambientes do SDD v3 §7 numa grade 3×3, cada um agrupando uma ou mais salas
// do backend (a sala de cada agente vem SEMPRE da presença real). Testável sem WebGL.

export const CW = 6.2; // largura de uma coluna (x)
export const RD = 5.2; // profundidade de uma linha (z)
export const COLS = 3;
export const ROWS = 3;
export const HQ_W = CW * COLS;
export const HQ_D = RD * ROWS;

export type ZoneId = "ceo" | "research" | "marketing" | "commerce" | "meeting" | "finance" | "support";

export interface Spot { x: number; z: number; for?: string }
export interface Zone {
  id: ZoneId; label: string; rooms: string[]; col: number; row: number; cols: number; rows: number;
  wall: string; floor: string; spots: Spot[]; extra: Spot[];
}

// Spots em coordenadas LOCAIS da sala (centro = 0,0). `for` = agente preferido; os móveis de cada sala são
// desenhados em torno destes pontos (mesa à frente na diagonal, cadeira atrás).
export const ZONES: Zone[] = [
  { id: "ceo", label: "Sala do CEO", rooms: ["ceo"], col: 0, row: 0, cols: 1, rows: 1, wall: "#c9b8f0", floor: "#b98a62",
    spots: [{ x: -0.4, z: -0.6, for: "atlas" }], extra: [{ x: 1.6, z: 1.2 }, { x: -1.8, z: 1.4 }] },
  { id: "research", label: "Laboratório de Pesquisa", rooms: ["research"], col: 1, row: 0, cols: 1, rows: 1, wall: "#9fd3c7", floor: "#d9b48a",
    spots: [{ x: -1.45, z: -0.9, for: "luna" }, { x: 0.75, z: -1.05, for: "vera" }], extra: [{ x: -0.6, z: 1.5 }, { x: 1.2, z: 1.4 }] },
  { id: "marketing", label: "Estúdio de Marketing", rooms: ["marketing"], col: 2, row: 0, cols: 1, rows: 1, wall: "#f2b8c6", floor: "#e6e1da",
    spots: [{ x: -1.6, z: -0.7, for: "iris" }, { x: 0.6, z: -0.9, for: "pixel" }], extra: [{ x: -0.4, z: 1.5 }, { x: 1.6, z: 1.6 }] },
  { id: "commerce", label: "Comércio & Fornecedores", rooms: ["store", "supply"], col: 0, row: 1, cols: 2, rows: 1, wall: "#f3d38f", floor: "#dcd6cc",
    spots: [{ x: -4.3, z: -0.7, for: "nova" }, { x: 1.0, z: -0.8, for: "logan" }, { x: 3.6, z: -0.8, for: "nexus" }],
    extra: [{ x: -1.8, z: 1.5 }, { x: 0.6, z: 1.6 }, { x: 4.6, z: 1.6 }] },
  { id: "meeting", label: "Sala de Reuniões", rooms: ["meeting"], col: 2, row: 1, cols: 1, rows: 1, wall: "#f2c4a0", floor: "#c99e72",
    spots: [{ x: -1.7, z: -1.2, for: "dante" }], extra: [{ x: 1.8, z: -0.9 }, { x: 1.8, z: 1.5 }, { x: -1.6, z: 1.6 }] },
  { id: "finance", label: "Finanças & Compliance", rooms: ["finance", "compliance"], col: 0, row: 2, cols: 2, rows: 1, wall: "#a9c7f0", floor: "#8fa2bd",
    spots: [{ x: -4.4, z: -0.8, for: "finn" }, { x: -1.9, z: -0.8, for: "orion" }, { x: 2.9, z: -0.8, for: "clara" }],
    extra: [{ x: 0.5, z: 1.5 }, { x: 5.0, z: 1.5 }] },
  { id: "support", label: "Operações & Atendimento", rooms: ["support"], col: 2, row: 2, cols: 1, rows: 1, wall: "#b9e3a8", floor: "#d7cbb8",
    spots: [{ x: -1.0, z: -0.9, for: "eva" }], extra: [{ x: 1.4, z: -0.6 }, { x: 1.5, z: 1.5 }, { x: -1.5, z: 1.6 }] },
];

const BY_ID = Object.fromEntries(ZONES.map((z) => [z.id, z])) as Record<ZoneId, Zone>;
export const zoneById = (id: ZoneId): Zone => BY_ID[id];

/** Ambiente da sala do backend; salas desconhecidas vão para a Sala de Reuniões (nunca somem da cena). */
export function zoneOfRoom(room: string): ZoneId {
  return ZONES.find((z) => z.rooms.includes(room))?.id ?? "meeting";
}

export function zoneSize(z: Zone): { w: number; d: number } {
  return { w: z.cols * CW, d: z.rows * RD };
}

/** Centro do ambiente no mundo (prédio centrado na origem). */
export function zoneCenter(z: Zone): [number, number] {
  const { w, d } = zoneSize(z);
  return [-HQ_W / 2 + z.col * CW + w / 2, -HQ_D / 2 + z.row * RD + d / 2];
}

/** Paredes do fundo/esquerda: externas (altas, com janela) na borda do prédio, divisórias de vidro no interior. */
export function wallKind(z: Zone): { back: "exterior" | "partition"; left: "exterior" | "partition" } {
  return { back: z.row === 0 ? "exterior" : "partition", left: z.col === 0 ? "exterior" : "partition" };
}

export interface Placement { agentId: string; zone: ZoneId; x: number; z: number; atDesk: boolean }

/**
 * Posiciona cada agente no ambiente da SUA sala (presença real): primeiro o lugar reservado a ele, depois os
 * lugares livres com mesa, depois pontos extras sem mesa. Determinístico (ordem dada), sem aleatoriedade.
 */
export function placeAgents(agents: { agent_id: string; room: string }[]): Placement[] {
  const out: Placement[] = [];
  for (const zone of ZONES) {
    const here = agents.filter((a) => zoneOfRoom(a.room) === zone.id).map((a) => a.agent_id);
    const free = zone.spots.map((s, i) => ({ ...s, i }));
    const taken = new Set<number>();
    const assigned = new Map<string, { s: Spot; desk: boolean }>();
    for (const id of here) {
      const s = free.find((f) => f.for === id && !taken.has(f.i));
      if (s) { taken.add(s.i); assigned.set(id, { s, desk: true }); }
    }
    let extra = 0;
    for (const id of here) {
      if (assigned.has(id)) continue;
      const s = free.find((f) => !f.for && !taken.has(f.i)) ?? free.find((f) => !taken.has(f.i) && !here.includes(f.for ?? ""));
      if (s) { taken.add(s.i); assigned.set(id, { s, desk: true }); continue; }
      const e = zone.extra[extra % Math.max(1, zone.extra.length)] ?? { x: 0, z: 0 };
      const ring = Math.floor(extra / Math.max(1, zone.extra.length)) * 0.7;
      assigned.set(id, { s: { x: e.x - ring, z: e.z }, desk: false });
      extra += 1;
    }
    const [cx, cz] = zoneCenter(zone);
    for (const id of here) {
      const a = assigned.get(id)!;
      out.push({ agentId: id, zone: zone.id, x: cx + a.s.x, z: cz + a.s.z, atDesk: a.desk });
    }
  }
  return out;
}
