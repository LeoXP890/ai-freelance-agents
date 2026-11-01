// Etiquetas do escritório 3D sem sobreposição (finalização §6). Placas de AGENTE ficam ancoradas acima da cabeça
// (ponto inferior-central) e, quando colidem, sobem até ficar livres. Placas de SALA testam poucas posições perto da
// âncora (nunca se afastam da própria sala); se nenhuma estiver livre, ficam onde estão. O corpo dos personagens é
// obstáculo fixo: etiqueta não esconde agente. A selecionada e as com estado que pede atenção são posicionadas
// primeiro e nunca saem do lugar. Função pura — testada em vitest.
export interface Plate {
  id: string; x: number; y: number; w: number; h: number; priority: number;
  /** Posições alternativas (dx, dy) em ordem de preferência; sem elas, a placa sobe até ficar livre. */
  candidates?: [number, number][];
}
export interface Rect { l: number; r: number; t: number; b: number }

const GAP = 3;
const hits = (a: Rect, placed: Rect[]) => placed.filter((q) => a.l < q.r && a.r > q.l && a.t < q.b && a.b > q.t);
const box = (p: Plate, dx: number, dy: number): Rect => ({ l: p.x - p.w / 2 + dx, r: p.x + p.w / 2 + dx, t: p.y - p.h + dy, b: p.y + dy });

/** Deslocamento (px) de cada placa para que nenhuma cubra outra placa nem o corpo de um personagem. */
export function declutter(plates: Plate[], obstacles: Rect[] = [], maxSteps = 8): Record<string, { dx: number; dy: number }> {
  const order = [...plates].sort((a, b) => b.priority - a.priority || b.y - a.y || a.id.localeCompare(b.id));
  const placed: Rect[] = [...obstacles];
  const out: Record<string, { dx: number; dy: number }> = {};
  for (const p of order) {
    let dx = 0, dy = 0;
    if (p.candidates) {
      const free = [[0, 0] as [number, number], ...p.candidates].find(([cx, cy]) => hits(box(p, cx, cy), placed).length === 0);
      [dx, dy] = free ?? [0, 0];
    } else {
      for (let step = 0; step < maxSteps; step++) {
        const hit = hits(box(p, 0, dy), placed);
        if (hit.length === 0) break;
        dy = Math.min(...hit.map((q) => q.t)) - GAP - p.y;  // sobe até a borda de cima da mais alta que colidiu
      }
    }
    placed.push(box(p, dx, dy));
    out[p.id] = { dx, dy };
  }
  return out;
}

/** Posições próximas para a placa de uma sala: acima/abaixo, para os lados e diagonais curtas. */
export function nearby(w: number, h: number): [number, number][] {
  const v = h + GAP, s = Math.round(w * 0.45);
  return [[0, -v], [0, v], [s, 0], [-s, 0], [s, -v], [-s, -v], [s, v], [-s, v], [0, -2 * v]];
}

/** Prioridade da placa: selecionada > estado crítico > estado de atenção > ativo > parado. */
export function platePriority(selected: boolean, tone: string | undefined, quiet: boolean): number {
  return (selected ? 100 : 0) + (tone === "crit" ? 30 : tone === "warn" ? 20 : 0) + (quiet ? 0 : 10);
}
