"use client";
// As 7 salas da sede (UXD-017, SDD v3 §7 "ambientes obrigatórios"), em coordenadas LOCAIS (centro = 0,0, piso em
// y=0.06). Paredes do fundo/esquerda: externas altas com janela na borda do prédio, divisórias baixas + vidro no
// interior (deixam ver a sala de trás). Móveis das estações de trabalho seguem os "spots" de lib/officeHq.
import { type ReactNode } from "react";
import { type Spot, wallKind, type Zone, type ZoneId, zoneSize } from "@/lib/officeHq";
import {
  Board, Box, C, Cabinet, Carton, Chair, Cyl, Desk, FloorLamp, G, Laptop, M, Monitor, Mug, Pallet, Papers, Plant, RoundTable,
  Rug, Shelf, Sofa, Stool, TallPlant, type V3, WallScreen, WaterCooler, Window,
} from "./kit";

export const FLOOR_Y = 0.06;
export const WALL_H = 2.5;
const PART_H = 0.95; // parte sólida da divisória
const GLASS_H = 2.1;
export const FACE = Math.PI / 4; // agentes de frente para a câmera isométrica
const DIR: [number, number] = [Math.sin(FACE), Math.cos(FACE)];
// monitores nas pontas da mesa, virados para o agente: nunca tampam o rosto na vista isométrica
const MR = Math.PI + 0.7;
const ML = Math.PI - 0.7;

/** Mesa à frente (diagonal) e cadeira atrás do agente — o mesmo arranjo da vertical slice. */
export function station(s: Spot) {
  return {
    desk: [s.x + DIR[0] * 0.62, FLOOR_Y, s.z + DIR[1] * 0.62] as V3,
    chair: [s.x - DIR[0] * 0.42, FLOOR_Y, s.z - DIR[1] * 0.42] as V3,
  };
}

// Portas nas divisórias (coordenada ao longo da parede), escolhidas para não colidir com os móveis.
const DOORS: Partial<Record<ZoneId, { back?: number; left?: number }>> = {
  research: { left: 1.55 }, marketing: { left: -1.5 }, commerce: { back: -1.2 }, meeting: { back: 1.6, left: 1.5 },
  finance: { back: 0.0 }, support: { back: -1.6, left: 1.6 },
};

function WallSegments({ len, door, children }: { len: number; door?: number; children: (from: number, to: number) => ReactNode }) {
  if (door === undefined) return <>{children(-len / 2, len / 2)}</>;
  const g = 0.6;
  return <>{children(-len / 2, door - g)}{children(door + g, len / 2)}</>;
}

function Wall({ along, at, len, kind, color, door }: { along: "x" | "z"; at: number; len: number; kind: "exterior" | "partition"; color: string; door?: number }) {
  const dark = shade(color, -0.12);
  const seg = (a: number, b: number, h: number, y0: number, col: string, t: number, glass = false) => {
    const L = b - a;
    if (L <= 0.01) return null;
    const mid = (a + b) / 2;
    const pos: V3 = along === "x" ? [mid, y0 + h / 2, at] : [at, y0 + h / 2, mid];
    const size: V3 = along === "x" ? [L, h, t] : [t, h, L];
    if (glass) return <mesh key={`g${a}`} position={pos}><boxGeometry args={size} /><meshStandardMaterial color="#d8f0ff" roughness={0.1} transparent opacity={0.22} /></mesh>;
    return <Box key={`${a}${y0}${col}`} p={pos} s={size} c={col} r={0.85} />;
  };
  const inward = along === "x" ? [0, 0, 1] : [1, 0, 0];
  const trimPos = (mid: number, y: number): V3 => (along === "x" ? [mid, y, at + inward[2]! * 0.09] : [at + inward[0]! * 0.09, y, mid]);
  return (
    <WallSegments len={len} door={kind === "partition" ? door : undefined}>
      {(a, b) => {
        const mid = (a + b) / 2, L = b - a;
        const trimSize: V3 = along === "x" ? [L, 0.05, 0.04] : [0.04, 0.05, L];
        const wainSize: V3 = along === "x" ? [L, 0.72, 0.02] : [0.02, 0.72, L];
        if (kind === "exterior") return (
          <group key={a}>
            {seg(a, b, WALL_H, FLOOR_Y, color, 0.16)}
            <Box p={trimPos(mid, FLOOR_Y + 0.36)} s={wainSize} c={dark} cast={false} />
            <Box p={trimPos(mid, FLOOR_Y + 0.74)} s={trimSize} c={C.trim} cast={false} />
          </group>
        );
        return (
          <group key={a}>
            {seg(a, b, PART_H, FLOOR_Y, color, 0.12)}
            <Box p={along === "x" ? [mid, FLOOR_Y + PART_H + 0.02, at] : [at, FLOOR_Y + PART_H + 0.02, mid]} s={along === "x" ? [L, 0.04, 0.16] : [0.16, 0.04, L]} c={C.trim} />
            {seg(a, b, GLASS_H - PART_H, FLOOR_Y + PART_H + 0.04, "", 0.03, true)}
            <Box p={along === "x" ? [mid, FLOOR_Y + GLASS_H + 0.04, at] : [at, FLOOR_Y + GLASS_H + 0.04, mid]} s={along === "x" ? [L, 0.05, 0.06] : [0.06, 0.05, L]} c={C.trim} />
            {[a, b].map((e) => <Box key={e} p={along === "x" ? [e, FLOOR_Y + GLASS_H / 2, at] : [at, FLOOR_Y + GLASS_H / 2, e]} s={[0.07, GLASS_H + 0.05, 0.07]} c={C.trim} />)}
          </group>
        );
      }}
    </WallSegments>
  );
}

function shade(hex: string, k: number): string {
  const n = parseInt(hex.slice(1), 16);
  const f = (v: number) => Math.max(0, Math.min(255, Math.round(v + (k < 0 ? v * k : (255 - v) * k))));
  return `#${[(n >> 16) & 255, (n >> 8) & 255, n & 255].map(f).map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

function Floor({ w, d, color, kind }: { w: number; d: number; color: string; kind: "planks" | "tiles" | "carpet" }) {
  const lines: ReactNode[] = [];
  const lc = shade(color, -0.1);
  if (kind === "planks") for (let x = -w / 2 + 0.55; x < w / 2; x += 0.55) lines.push(<Box key={x} p={[x, 0.062, 0]} s={[0.012, 0.004, d]} c={lc} cast={false} />);
  if (kind === "tiles") {
    for (let x = -w / 2 + 1; x < w / 2; x += 1) lines.push(<Box key={`x${x}`} p={[x, 0.062, 0]} s={[0.012, 0.004, d]} c={lc} cast={false} />);
    for (let z = -d / 2 + 1; z < d / 2; z += 1) lines.push(<Box key={`z${z}`} p={[0, 0.062, z]} s={[w, 0.004, 0.012]} c={lc} cast={false} />);
  }
  return <group><Box p={[0, 0.035, 0]} s={[w - 0.02, 0.05, d - 0.02]} c={color} r={kind === "carpet" ? 1 : 0.85} cast={false} />{lines}</group>;
}

function Shell({ z, floor }: { z: Zone; floor: "planks" | "tiles" | "carpet" }) {
  const { w, d } = zoneSize(z);
  const k = wallKind(z);
  const doors = DOORS[z.id] ?? {};
  return (
    <group>
      <Floor w={w} d={d} color={z.floor} kind={floor} />
      <Wall along="x" at={-d / 2 - (k.back === "exterior" ? 0.08 : 0)} len={w + (k.back === "exterior" ? 0.16 : 0)} kind={k.back} color={z.wall} door={doors.back} />
      <Wall along="z" at={-w / 2 - (k.left === "exterior" ? 0.08 : 0)} len={d} kind={k.left} color={z.wall} door={doors.left} />
    </group>
  );
}

function Station({ s, children, w, deskColor, chair = C.fabric }: { s: Spot; children?: ReactNode; w?: number; deskColor?: string; chair?: string }) {
  const st = station(s);
  return <>
    <Desk p={st.desk} rot={FACE} w={w} c={deskColor}>{children}</Desk>
    <Chair p={st.chair} rot={FACE} c={chair} />
  </>;
}

const back = (d: number) => -d / 2 + 0.06;
const left = (w: number) => -w / 2 + 0.06;

// ───────────── 1. Sala do CEO (Atlas) ─────────────
function CeoRoom({ z }: { z: Zone }) {
  const { w, d } = zoneSize(z);
  const [atlas] = z.spots;
  return (
    <group>
      <Shell z={z} floor="planks" />
      <Rug p={[0.1, 0.064, 0.2]} r={0} c="#5b4a8a" c2="#7d6bb3" rect={[3.2, 2.4]} />
      {/* painel de aprovações (o "farol" acima dele acende com aprovações reais pendentes — fora do Baked) */}
      <Board p={[-1.55, 1.55, back(d)]} w={1.7} h={0.95} base="#f3efe6" notes={[C.amber, C.amber, C.mint, C.coral, C.blue, C.amber]} />
      <Window p={[1.5, 1.55, back(d) - 0.02]} w={1.6} />
      {/* mapa simplificado do negócio (parede esquerda) */}
      <G p={[left(w), 1.5, -0.5]} rot={Math.PI / 2}>
        <Box p={[0, 0, 0]} s={[1.8, 1.05, 0.05]} c={C.woodDark} round={0.03} />
        <mesh position={[0, 0, 0.03]}><planeGeometry args={[1.66, 0.92]} /><M c="#cfe8f5" /></mesh>
        {([[-0.45, 0.1, 0.3], [0.2, -0.12, 0.38], [0.55, 0.22, 0.2]] as V3[]).map(([x, y, r]) => (
          <mesh key={x} position={[x, y, 0.034]}><circleGeometry args={[r, 7]} /><M c="#9fd39b" /></mesh>
        ))}
        {([[-0.5, 0.15], [0.15, -0.05], [0.6, 0.25], [0.3, -0.25]] as [number, number][]).map(([x, y], i) => (
          <mesh key={i} position={[x, y, 0.06]} rotation={[Math.PI / 2, 0, 0]}><coneGeometry args={[0.035, 0.08, 8]} /><M c={i === 0 ? C.coral : C.lilac} /></mesh>
        ))}
      </G>
      <Shelf p={[left(w) + 0.26, FLOOR_Y, 1.55]} rot={Math.PI / 2} w={1.3} levels={3} items={[
        { x: -0.45, l: 1, c: "#5b4a8a", k: "book" }, { x: -0.36, l: 1, c: "#e07a5f", k: "book" }, { x: -0.27, l: 1, c: "#3f6f8f", k: "book" },
        { x: 0.3, l: 1, c: C.amber, k: "jar" }, { x: -0.2, l: 2, c: "#f3e9d8", k: "box" }, { x: 0.35, l: 0, c: "#7d6bb3", k: "binder" }, { x: 0.45, l: 0, c: "#5b4a8a", k: "binder" },
      ]} />
      {/* mesa de liderança */}
      <Station s={atlas!} w={1.75} deskColor="#6b4430" chair="#4c3d99">
        <Monitor p={[0.5, 0.775, -0.1]} rot={MR} screen="#b7a6ff" glow="#4c3d99" />
        <Papers p={[-0.05, 0.79, 0.05]} rot={0.3} />
        <Mug p={[0.72, 0.82, -0.12]} c="#b7a6ff" />
        {/* porta-retrato: detalhe pessoal discreto */}
        <G p={[-0.72, 0.78, 0.1]} rot={Math.PI - 0.4}><Box p={[0, 0.1, 0]} s={[0.16, 0.2, 0.03]} c={C.woodDark} rot={[-0.2, 0, 0]} /></G>
      </Station>
      <Sofa p={[1.7, FLOOR_Y, 1.2]} rot={-Math.PI / 2} w={1.0} c="#7d6bb3" />
      <Cyl p={[0.95, FLOOR_Y + 0.25, 1.9]} r1={0.25} h={0.5} c={C.woodLight} seg={20} />
      <TallPlant p={[w / 2 - 0.45, FLOOR_Y, -d / 2 + 0.5]} s={1.05} />
      <FloorLamp p={[-w / 2 + 0.45, FLOOR_Y, -d / 2 + 0.45]} />
    </group>
  );
}

// ───────────── 2. Laboratório de Pesquisa (Luna, Vera) — a vertical slice aprovada, agora parte da sede ─────────────
function ResearchRoom({ z }: { z: Zone }) {
  const { w, d } = zoneSize(z);
  const [luna, vera] = z.spots;
  return (
    <group>
      <Shell z={z} floor="planks" />
      <Rug p={[0.3, 0.064, 0.55]} r={1.45} c="#e9c46a" c2="#f4d98a" />
      {/* quadro de tendências com linha de tendência */}
      <G p={[-1.45, 1.55, back(d)]}>
        <Board p={[0, 0, 0]} w={1.66} h={0.86} />
        {([[-0.7, -0.32], [-0.4, -0.12], [-0.1, -0.2], [0.2, 0.02], [0.55, 0.06], [0.75, -0.05]] as [number, number][]).slice(1).map(([x, y], i, arr) => {
          const [px, py] = i === 0 ? [-0.7, -0.32] : arr[i - 1]!;
          return <Box key={i} p={[(x + px) / 2, (y + py) / 2 - 0.1, 0.045]} s={[Math.hypot(x - px, y - py), 0.025, 0.01]} rot={[0, 0, Math.atan2(y - py, x - px)]} c="#2b6f5c" cast={false} />;
        })}
      </G>
      <Window p={[1.4, 1.55, back(d) - 0.02]} />
      <Plant p={[0.95, 0.99, back(d) + 0.1]} s={0.5} />
      {/* estante de amostras junto à divisória */}
      <Shelf p={[left(w) + 0.28, FLOOR_Y, -0.05]} rot={Math.PI / 2} items={[
        { x: -0.45, l: 1, c: "#e07a5f" }, { x: -0.15, l: 1, c: "#7dd3b0" }, { x: 0.2, l: 1, c: "#88a9f3" }, { x: 0.48, l: 1, c: "#f4c47f" },
        { x: -0.4, l: 2, c: "#b7a6ff" }, { x: 0.05, l: 2, c: "#f08b8b" }, { x: 0.42, l: 2, c: "#76cfa6" },
        { x: -0.35, l: 3, c: "#cfeeff", k: "jar" }, { x: -0.05, l: 3, c: "#cfeeff", k: "jar" }, { x: 0.25, l: 3, c: "#cfeeff", k: "jar" },
      ]} />
      {/* bancada de amostras com o produto */}
      <G p={[1.95, FLOOR_Y, 1.35]} rot={-Math.PI / 2}>
        <Box p={[0, 0.9, 0]} s={[1.2, 0.08, 0.6]} c={C.trim} round={0.025} />
        {([[-0.52, -0.24], [0.52, -0.24], [-0.52, 0.24], [0.52, 0.24]] as [number, number][]).map(([x, zz]) => <Box key={`${x}${zz}`} p={[x, 0.45, zz]} s={[0.05, 0.9, 0.05]} c={C.metal} />)}
        <Cyl p={[-0.2, 1.1, 0]} r1={0.07} h={0.32} c={C.teal} seg={20} />
        <Cyl p={[-0.2, 1.28, 0]} r1={0.06} r2={0.055} h={0.05} c={C.dark} seg={20} />
        <Box p={[0.25, 1.03, 0.02]} s={[0.3, 0.18, 0.22]} c={C.cardboard} round={0.02} />
      </G>
      <Station s={luna!}>
        <Laptop p={[0, 0.79, 0.02]} rot={Math.PI} />
        <Mug p={[0.45, 0.82, 0.08]} />
        <Box p={[-0.42, 0.79, 0.05]} s={[0.2, 0.03, 0.27]} rot={[0, 0.3, 0]} c="#e07a5f" round={0.006} />
      </Station>
      <Station s={vera!}>
        <Laptop p={[0.05, 0.79, 0.02]} rot={Math.PI} />
        <Papers p={[-0.42, 0.79, 0.05]} />
      </Station>
      <FloorLamp p={[w / 2 - 0.4, FLOOR_Y, -d / 2 + 0.45]} />
      <Plant p={[-w / 2 + 0.4, FLOOR_Y, -d / 2 + 0.4]} s={1.1} />
    </group>
  );
}

// ───────────── 3. Estúdio de Marketing (Iris, Pixel) ─────────────
function MarketingRoom({ z }: { z: Zone }) {
  const { w, d } = zoneSize(z);
  const [iris, pixel] = z.spots;
  return (
    <group>
      <Shell z={z} floor="tiles" />
      <WallScreen p={[-1.3, 1.62, back(d)]} w={1.7} h={0.9} bg="#3a1f3d" bar="#f2b8c6" bars={[0.35, 0.55, 0.42, 0.78, 0.6, 0.9]} line={C.amber} />
      <Window p={[1.6, 1.55, back(d) - 0.02]} w={1.4} />
      {/* canto de foto: fundo infinito, pedestal com produto, câmera no tripé, dois softboxes */}
      <G p={[-1.75, FLOOR_Y, 1.35]}>
        <Box p={[-0.95, 0.95, 0]} s={[0.05, 1.8, 1.7]} c="#fdf6ee" />
        <Box p={[-0.45, 0.012, 0]} s={[1.0, 0.012, 1.7]} c="#fdf6ee" cast={false} />
        <Cyl p={[-0.4, 0.25, 0]} r1={0.24} h={0.5} c="#f3e9d8" seg={20} />
        <Cyl p={[-0.4, 0.66, 0]} r1={0.07} h={0.32} c={C.teal} seg={20} />
        <G p={[0.75, 0, 0.1]} rot={-Math.PI / 2}>
          {[-0.12, 0.12].map((x) => <Box key={x} p={[x, 0.55, 0]} s={[0.025, 1.1, 0.025]} rot={[0, 0, x * 1.4]} c={C.metal} />)}
          <Box p={[0, 1.12, 0]} s={[0.22, 0.15, 0.14]} c={C.dark} round={0.02} />
          <Cyl p={[0, 1.12, 0.12]} r1={0.05} h={0.12} rot={[Math.PI / 2, 0, 0]} c={C.metal} seg={14} />
        </G>
        {[[-0.1, -0.85], [-0.1, 0.85]].map(([x, zz]) => (
          <G key={zz} p={[x!, 0, zz!]} rot={zz! > 0 ? -0.7 : 0.7}>
            <Cyl p={[0, 0.7, 0]} r1={0.02} h={1.4} c={C.metal} seg={6} />
            <mesh position={[0, 1.45, 0]} rotation={[0, 0, -Math.PI / 2]}><coneGeometry args={[0.3, 0.32, 4, 1, true]} /><meshStandardMaterial color="#fff6e6" emissive="#fff0d0" emissiveIntensity={0.5} side={2} /></mesh>
          </G>
        ))}
      </G>
      <Station s={iris!}>
        <Monitor p={[-0.48, 0.775, -0.1]} rot={ML} screen="#f2b8c6" glow="#8a3a5a" bars={false} />
        <Box p={[0.15, 0.79, 0.05]} s={[0.3, 0.02, 0.22]} c="#2a3140" round={0.01} />
      </Station>
      <Station s={pixel!}>
        <Monitor p={[0.5, 0.775, -0.1]} rot={MR} />
        <Mug p={[-0.4, 0.82, 0.05]} c={C.amber} />
      </Station>
      {/* pufe e prateleira de referências */}
      <mesh position={[1.9, FLOOR_Y + 0.22, 1.55]} castShadow scale={[1, 0.6, 1]}><sphereGeometry args={[0.42, 18, 14]} /><M c="#f08b8b" /></mesh>
      <Plant p={[w / 2 - 0.4, FLOOR_Y, -d / 2 + 0.45]} s={1.15} />
      <Box p={[0.3, 2.15, back(d) + 0.05]} s={[0.9, 0.18, 0.04]} c="#f2b8c6" e="#f2b8c6" round={0.04} />
    </group>
  );
}

// ───────────── 4. Comércio & Fornecedores (Nova, Logan, Nexus) ─────────────
function CommerceRoom({ z }: { z: Zone }) {
  const { w, d } = zoneSize(z);
  const [nova, logan, nexus] = z.spots;
  const tiles = ["#ffcf6e", "#ff8a65", "#ff6f9c", "#6fd0ff"]; // canais (Woo / ML / Shopee / TikTok) por cor, sem logos
  return (
    <group>
      <Shell z={z} floor="tiles" />
      <Window p={[left(w) - 0.02, 1.55, -1.3]} rot={Math.PI / 2} w={1.4} />
      {/* vitrine de produtos (loja) */}
      <Shelf p={[left(w) + 0.28, FLOOR_Y, 1.1]} rot={Math.PI / 2} w={1.7} levels={3} c={C.woodLight} items={[
        { x: -0.6, l: 0, c: "#e07a5f" }, { x: -0.25, l: 0, c: "#88a9f3" }, { x: 0.15, l: 0, c: C.teal, k: "jar" }, { x: 0.5, l: 0, c: "#f4c47f" },
        { x: -0.55, l: 1, c: "#b7a6ff" }, { x: -0.15, l: 1, c: "#7dd3b0" }, { x: 0.3, l: 1, c: "#f08b8b" }, { x: 0.62, l: 1, c: "#cfeeff", k: "jar" },
        { x: -0.4, l: 2, c: "#f3e9d8" }, { x: 0.25, l: 2, c: "#e9c46a" },
      ]} />
      <G p={[-3.0, FLOOR_Y, 1.5]}>
        <Box p={[0, 0.5, 0]} s={[1.5, 1.0, 0.55]} c="#f3d38f" round={0.04} />
        <Box p={[0, 1.02, 0]} s={[1.6, 0.05, 0.62]} c={C.trim} round={0.02} />
        <G p={[-0.4, 1.05, 0]} rot={0.3}><Box p={[0, 0.12, 0]} s={[0.3, 0.2, 0.03]} c={C.screen} rot={[-0.3, 0, 0]} /></G>
        <Carton p={[0.35, 1.05, 0]} s={[0.3, 0.2, 0.24]} />
      </G>
      <Station s={nova!} deskColor={C.woodLight}>
        <G p={[0.45, 0.775, -0.12]} rot={MR}>
          <Box p={[0, 0.42, 0]} s={[0.7, 0.42, 0.04]} c={C.screen} round={0.02} />
          {tiles.map((c, i) => <mesh key={c} position={[-0.16 + (i % 2) * 0.32, 0.5 - Math.floor(i / 2) * 0.17, -0.025]} rotation={[0, Math.PI, 0]}><planeGeometry args={[0.28, 0.14]} /><M c={c} e={c} ei={0.4} /></mesh>)}
          <Box p={[0, 0.15, 0]} s={[0.05, 0.25, 0.05]} c={C.metal} />
        </G>
        <Carton p={[-0.45, 0.775, 0.1]} s={[0.22, 0.16, 0.18]} tape={false} />
      </Station>
      {/* divisor baixo entre loja e expedição */}
      <Shelf p={[-1.25, FLOOR_Y, 0.9]} rot={Math.PI / 2} w={1.4} levels={2} c={C.woodLight} items={[{ x: -0.3, l: 1, c: C.cardboard }, { x: 0.3, l: 1, c: "#e07a5f" }]} />
      {/* expedição (Logan): mesa de embalagem com caixa, fita e etiquetas */}
      <Station s={logan!} w={1.5} deskColor="#c8b79b" chair="#5f6b7a">
        <Carton p={[-0.25, 0.775, 0.02]} s={[0.4, 0.26, 0.32]} />
        <Cyl p={[0.38, 0.81, 0.0]} r1={0.08} h={0.05} c={C.dark} seg={14} rot={[Math.PI / 2, 0, 0]} />
        <Box p={[0.55, 0.8, 0.15]} s={[0.16, 0.05, 0.1]} c={C.paper} />
      </Station>
      <Pallet p={[1.7, FLOOR_Y, 1.1]} />
      {/* esteira de roletes */}
      <G p={[3.6, FLOOR_Y, 1.55]}>
        <Box p={[0, 0.55, -0.32]} s={[2.0, 0.06, 0.06]} c={C.metal} />
        <Box p={[0, 0.55, 0.32]} s={[2.0, 0.06, 0.06]} c={C.metal} />
        {Array.from({ length: 9 }, (_, i) => <Cyl key={i} p={[-0.9 + i * 0.225, 0.56, 0]} r1={0.04} h={0.62} rot={[Math.PI / 2, 0, 0]} c={C.metalLight} seg={10} />)}
        {[-0.8, 0.8].map((x) => [-0.3, 0.3].map((zz) => <Box key={`${x}${zz}`} p={[x, 0.27, zz]} s={[0.05, 0.54, 0.05]} c={C.metal} />))}
        <Carton p={[-0.3, 0.6, 0]} s={[0.36, 0.24, 0.3]} />
        <Carton p={[0.5, 0.6, 0]} s={[0.3, 0.2, 0.26]} rot={0.2} />
      </G>
      {/* fornecedores (Nexus): tela de ofertas, amostras e prancheta */}
      <Station s={nexus!} chair="#2f5d8a">
        <Monitor p={[-0.48, 0.775, -0.1]} rot={ML} screen={C.amber} glow="#8a5a1a" />
        <Carton p={[0.45, 0.775, 0.08]} s={[0.24, 0.18, 0.2]} />
        <Box p={[0.05, 0.79, 0.1]} s={[0.2, 0.02, 0.27]} c="#8a5a35" round={0.006} />
      </Station>
      <Shelf p={[w / 2 - 0.9, FLOOR_Y, back(d) + 0.3]} w={1.5} levels={2} c={C.metal} items={[{ x: -0.45, l: 0, c: C.cardboard }, { x: 0.05, l: 0, c: C.cardboard }, { x: 0.45, l: 1, c: C.cardboard }, { x: -0.2, l: 1, c: C.cardboardDark }]} />
      <Carton p={[5.6, FLOOR_Y, 0.2]} rot={0.3} />
      <Carton p={[5.65, FLOOR_Y + 0.3, 0.25]} s={[0.32, 0.24, 0.28]} rot={-0.2} />
      <TallPlant p={[left(w) + 0.45, FLOOR_Y, back(d) + 0.45]} />
    </group>
  );
}

// ───────────── 5. Sala de Reuniões (Dante) ─────────────
function MeetingRoom({ z }: { z: Zone }) {
  const { w, d } = zoneSize(z);
  const [dante] = z.spots;
  const st = station(dante!);
  return (
    <group>
      <Shell z={z} floor="planks" />
      <Rug p={[0.55, 0.064, 0.55]} r={1.55} c="#e9a978" c2="#f2c4a0" />
      <RoundTable p={[0.55, FLOOR_Y, 0.55]} r={0.8} />
      {[0, 1, 2, 3, 4].map((i) => {
        const a = (i / 5) * Math.PI * 2 + 0.3;
        return <Chair key={i} p={[0.55 + Math.sin(a) * 1.2, FLOOR_Y, 0.55 + Math.cos(a) * 1.2]} rot={a + Math.PI} c="#e07a5f" />;
      })}
      <Laptop p={[0.35, FLOOR_Y + 0.76, 0.4]} rot={0.6} screen={C.blue} glow="#3b5aa8" />
      <Papers p={[0.85, FLOOR_Y + 0.76, 0.75]} rot={-0.4} />
      <Mug p={[0.6, FLOOR_Y + 0.8, 0.95]} c="#f2c4a0" />
      {/* painel de apresentação em pedestal e púlpito do Dante */}
      <G p={[0.4, FLOOR_Y, -2.05]}>
        {[-0.5, 0.5].map((x) => <Box key={x} p={[x, 0.5, 0]} s={[0.06, 1.0, 0.06]} c={C.metal} />)}
        <Box p={[0, 0.02, 0]} s={[1.3, 0.04, 0.4]} c={C.metal} />
        <WallScreen p={[0, 1.4, 0]} w={1.6} h={0.85} bg="#25324a" bars={[0.4, 0.6, 0.5, 0.8]} bar={C.amber} line={C.mint} />
      </G>
      <Desk p={st.desk} rot={FACE} w={0.8} c={C.woodLight}><Papers p={[0, 0.79, 0]} /></Desk>
      <Board p={[left(w) + 0.06, 1.55, -1.2]} rot={Math.PI / 2} w={1.2} h={0.75} base="#f7f7f2" notes={[C.blue, C.amber, C.mint]} />
      <WaterCooler p={[w / 2 - 0.4, FLOOR_Y, -d / 2 + 0.45]} />
      <Plant p={[w / 2 - 0.45, FLOOR_Y, d / 2 - 0.45]} s={1.1} />
      <Plant p={[-w / 2 + 0.45, FLOOR_Y, d / 2 - 0.5]} s={0.9} />
    </group>
  );
}

// ───────────── 6. Finanças & Compliance (Finn, Orion, Clara) ─────────────
function FinanceRoom({ z }: { z: Zone }) {
  const { w, d } = zoneSize(z);
  const [finn, orion, clara] = z.spots;
  const coins = (p: V3) => <G p={p}>{[0, 1, 2, 3].map((i) => <Cyl key={i} p={[0, 0.012 + i * 0.024, 0]} r1={0.05} h={0.022} c="#e9c46a" seg={14} />)}</G>;
  return (
    <group>
      <Shell z={z} floor="carpet" />
      <WallScreen p={[left(w), 1.6, -0.9]} rot={Math.PI / 2} w={1.6} h={0.85} bg="#16304a" bar={C.mint} bars={[0.5, 0.45, 0.6, 0.55, 0.72, 0.8]} line={C.amber} />
      <Window p={[left(w) - 0.02, 1.55, 1.3]} rot={Math.PI / 2} w={1.3} />
      <Station s={finn!} chair="#2f6b4a">
        <Monitor p={[-0.52, 0.775, -0.1]} rot={ML} />
        <Monitor p={[0.52, 0.775, -0.1]} rot={MR} screen="#7dd3b0" glow="#2f8a6c" bars={false} />
        {coins([0.1, 0.775, 0.18])}
        <Box p={[-0.15, 0.79, 0.15]} s={[0.14, 0.03, 0.2]} c={C.dark} round={0.01} />
      </Station>
      <Station s={orion!} chair="#2f5d8a">
        <Laptop p={[0, 0.79, 0.02]} rot={Math.PI} screen={C.blue} glow="#3b5aa8" />
        <Mug p={[0.45, 0.82, 0.1]} c={C.blue} />
      </Station>
      {/* cofre e arquivos */}
      <G p={[-3.3, FLOOR_Y, 1.75]} rot={-0.2}>
        <Box p={[0, 0.4, 0]} s={[0.7, 0.8, 0.6]} c="#4a5568" r={0.45} round={0.04} />
        <Cyl p={[0.1, 0.45, 0.31]} r1={0.1} h={0.04} rot={[Math.PI / 2, 0, 0]} c={C.metalLight} seg={18} />
        <Box p={[-0.2, 0.45, 0.31]} s={[0.04, 0.2, 0.04]} c={C.metalLight} />
      </G>
      <Cabinet p={[-5.65, FLOOR_Y, -1.95]} rot={Math.PI / 2} />
      <Cabinet p={[-5.65, FLOOR_Y, -1.35]} rot={Math.PI / 2} />
      {/* compliance: estantes de pastas e livros como divisória baixa, mesa da Clara com balança */}
      <Shelf p={[0.6, FLOOR_Y, 0.6]} rot={Math.PI / 2} w={1.8} levels={3} items={[
        { x: -0.75, l: 0, c: "#5b4a8a", k: "binder" }, { x: -0.64, l: 0, c: "#3f6f8f", k: "binder" }, { x: -0.53, l: 0, c: "#7d6bb3", k: "binder" },
        { x: 0.1, l: 0, c: "#b7a6ff", k: "binder" }, { x: 0.21, l: 0, c: "#5b4a8a", k: "binder" },
        { x: -0.6, l: 1, c: "#e07a5f", k: "book" }, { x: -0.5, l: 1, c: "#f4c47f", k: "book" }, { x: -0.4, l: 1, c: "#3f6f8f", k: "book" },
        { x: 0.4, l: 1, c: C.cardboard }, { x: 0.0, l: 2, c: C.paper },
      ]} />
      <Station s={clara!} chair="#6b5bb3">
        <Laptop p={[-0.1, 0.79, 0.02]} rot={Math.PI} screen="#e8e2ff" glow="#6b5bb3" />
        {/* balança da justiça (símbolo de compliance) */}
        <G p={[0.48, 0.775, 0.05]}>
          <Cyl p={[0, 0.02, 0]} r1={0.08} h={0.03} c="#c9a227" seg={14} />
          <Cyl p={[0, 0.17, 0]} r1={0.012} h={0.28} c="#c9a227" seg={6} />
          <Box p={[0, 0.3, 0]} s={[0.3, 0.015, 0.015]} c="#c9a227" />
          {[-0.14, 0.14].map((x) => <Cyl key={x} p={[x, 0.22, 0]} r1={0.05} r2={0.06} h={0.02} c="#c9a227" seg={12} />)}
        </G>
        <Box p={[-0.52, 0.8, 0.12]} s={[0.22, 0.04, 0.3]} c="#7d6bb3" round={0.01} />
      </Station>
      <Shelf p={[w / 2 - 0.9, FLOOR_Y, back(d) + 0.3]} w={1.5} levels={2} items={[
        { x: -0.55, l: 0, c: "#7d6bb3", k: "binder" }, { x: -0.44, l: 0, c: "#5b4a8a", k: "binder" }, { x: -0.33, l: 0, c: "#b7a6ff", k: "binder" },
        { x: 0.3, l: 0, c: C.cardboard }, { x: -0.1, l: 1, c: C.paper },
      ]} />
      <TallPlant p={[w / 2 - 0.5, FLOOR_Y, d / 2 - 0.5]} />
      <Plant p={[-1.0, FLOOR_Y, 1.8]} />
      <Rug p={[-3.1, 0.064, -0.2]} r={0} c="#3f5a7a" c2="#4f6d90" rect={[4.6, 2.2]} />
    </group>
  );
}

// ───────────── 7. Operações & Atendimento (Eva) ─────────────
function SupportRoom({ z }: { z: Zone }) {
  const { w, d } = zoneSize(z);
  const [eva] = z.spots;
  return (
    <group>
      <Shell z={z} floor="planks" />
      <Rug p={[1.1, 0.064, 1.0]} r={0} c="#8fc27d" c2="#b9e3a8" rect={[2.6, 1.9]} />
      {/* painel de rastreio de envios: mapa com rota e pontos */}
      <G p={[0.9, FLOOR_Y, -2.1]}>
        {[-0.55, 0.55].map((x) => <Box key={x} p={[x, 0.55, 0]} s={[0.06, 1.1, 0.06]} c={C.metal} />)}
        <Box p={[0, 1.45, 0]} s={[1.6, 0.9, 0.06]} c={C.screen} round={0.02} />
        <mesh position={[0, 1.45, 0.032]}><planeGeometry args={[1.5, 0.8]} /><M c="#1f3a33" e="#1f3a33" ei={0.5} /></mesh>
        {([[-0.55, -0.2], [-0.2, 0.1], [0.15, -0.05], [0.55, 0.22]] as [number, number][]).map(([x, y], i, arr) => (
          <group key={i}>
            <mesh position={[x, 1.45 + y, 0.04]}><circleGeometry args={[0.045, 12]} /><M c={i === arr.length - 1 ? C.amber : C.mint} e={i === arr.length - 1 ? C.amber : C.mint} ei={0.6} /></mesh>
            {i > 0 && <Box p={[(x + arr[i - 1]![0]) / 2, 1.45 + (y + arr[i - 1]![1]) / 2, 0.038]} s={[Math.hypot(x - arr[i - 1]![0], y - arr[i - 1]![1]), 0.018, 0.004]} rot={[0, 0, Math.atan2(y - arr[i - 1]![1], x - arr[i - 1]![0])]} c="#76cfa6" cast={false} />}
          </group>
        ))}
      </G>
      <Station s={eva!} chair="#c25b7a">
        <Monitor p={[-0.52, 0.775, -0.1]} rot={ML} screen="#ffd1dc" glow="#8a3a5a" bars={false} />
        <Monitor p={[0.52, 0.775, -0.1]} rot={MR} />
        <Mug p={[0.0, 0.82, 0.15]} c="#f08b8b" />
      </Station>
      {/* quadro de chamados (kanban) */}
      <Board p={[left(w) + 0.06, 1.55, -0.4]} rot={Math.PI / 2} w={1.3} h={0.8} base="#f7f7f2" notes={[C.coral, C.amber, C.mint, C.coral, C.mint, C.mint]} />
      <Sofa p={[1.1, FLOOR_Y, 0.55]} c="#7fb07a" />
      <Box p={[1.1, FLOOR_Y + 0.2, 1.35]} s={[0.9, 0.06, 0.5]} c={C.woodLight} round={0.02} />
      {[-0.35, 0.35].map((x) => <Box key={x} p={[1.1 + x, FLOOR_Y + 0.1, 1.35]} s={[0.05, 0.2, 0.4]} c={C.woodDark} />)}
      <Shelf p={[w / 2 - 0.35, FLOOR_Y, -0.6]} rot={-Math.PI / 2} w={1.4} levels={3} c={C.metal} items={[
        { x: -0.4, l: 0, c: C.cardboard }, { x: 0.1, l: 0, c: C.cardboard }, { x: 0.45, l: 1, c: C.cardboard }, { x: -0.3, l: 1, c: C.cardboardDark }, { x: 0.0, l: 2, c: C.cardboard },
      ]} />
      <Plant p={[-w / 2 + 0.45, FLOOR_Y, d / 2 - 0.45]} s={1.1} />
      <TallPlant p={[w / 2 - 0.45, FLOOR_Y, d / 2 - 0.45]} s={0.9} />
    </group>
  );
}

export const ROOM_COMPONENTS: Record<ZoneId, (p: { z: Zone }) => ReactNode> = {
  ceo: CeoRoom, research: ResearchRoom, marketing: MarketingRoom, commerce: CommerceRoom, meeting: MeetingRoom,
  finance: FinanceRoom, support: SupportRoom,
};

/** Ponto do "farol" de aprovações sobre o painel da Sala do CEO (coordenadas locais da sala). */
export const CEO_BEACON: V3 = [-1.55, 2.2, -2.5];
