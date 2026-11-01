"use client";
// Kit modular do escritório "Mini Empresa Viva" (UXD-017, art bible): móveis e objetos low-poly "soft-toy",
// materiais mate, cantos arredondados, cor por departamento. Geometria procedural original (sem assets externos).
// Tudo aqui é estático e passa pelo <Baked> (poucas malhas no fim). Texto NUNCA vai em textura.
import { RoundedBox } from "@react-three/drei";
import type { ReactNode } from "react";

export type V3 = [number, number, number];

export const C = {
  plinth: "#24485a", plinthTop: "#2e5a6e", trim: "#f3e9d8", wood: "#a8744a", woodDark: "#7c5334", woodLight: "#d6a874",
  metal: "#3b4352", metalLight: "#8e99ab", cork: "#c99a68", leaf: "#4caf7d", leafDark: "#2f8a5c", pot: "#e07a5f",
  glass: "#cfeeff", screen: "#202634", paper: "#f6f2e8", cardboard: "#c99e72", cardboardDark: "#a87c52", fabric: "#3f6f8f",
  teal: "#1e9c91", mint: "#7dd3b0", blue: "#88a9f3", amber: "#f4c47f", coral: "#f08b8b", lilac: "#b7a6ff", dark: "#20242c",
};

export function M({ c, r = 0.8, e, ei = 0.35, flat }: { c: string; r?: number; e?: string; ei?: number; flat?: boolean }) {
  return <meshStandardMaterial color={c} roughness={r} emissive={e ?? "#000"} emissiveIntensity={e ? ei : 0} flatShading={flat} />;
}

export function Box({ p, s, c, r, rot, round = 0, e, cast = true }: {
  p: V3; s: V3; c: string; r?: number; rot?: V3; round?: number; e?: string; cast?: boolean;
}) {
  // cantos arredondados só onde aparecem (peças pequenas viram caixa simples) e com poucos segmentos de chanfro
  if (round > 0 && Math.min(...s) >= 0.12) return <RoundedBox args={s} radius={round} smoothness={1} bevelSegments={2} position={p} rotation={rot} castShadow={cast} receiveShadow><M c={c} r={r} e={e} /></RoundedBox>;
  return <mesh position={p} rotation={rot} castShadow={cast} receiveShadow><boxGeometry args={s} /><M c={c} r={r} e={e} /></mesh>;
}

export function Cyl({ p, r1, r2 = r1, h, c, seg = 16, rot, e }: { p: V3; r1: number; r2?: number; h: number; c: string; seg?: number; rot?: V3; e?: string }) {
  return <mesh position={p} rotation={rot} castShadow receiveShadow><cylinderGeometry args={[r2, r1, h, seg]} /><M c={c} e={e} /></mesh>;
}

export function G({ p = [0, 0, 0], rot = 0, s = 1, children }: { p?: V3; rot?: number; s?: number; children: ReactNode }) {
  return <group position={p} rotation={[0, rot, 0]} scale={s}>{children}</group>;
}

export function Plant({ p, s = 1, pot = C.pot }: { p: V3; s?: number; pot?: string }) {
  return (
    <G p={p} s={s}>
      <Cyl p={[0, 0.17, 0]} r1={0.12} r2={0.16} h={0.34} c={pot} seg={14} />
      {([[0, 0.55, 0, 0.22], [0.12, 0.45, 0.06, 0.15], [-0.11, 0.48, -0.05, 0.16], [0.02, 0.72, -0.04, 0.14]] as [number, number, number, number][]).map(([x, y, z, r]) => (
        <mesh key={`${x}${y}`} position={[x, y, z]} castShadow><icosahedronGeometry args={[r, 1]} /><M c={y > 0.6 ? C.leaf : C.leafDark} r={0.9} flat /></mesh>
      ))}
    </G>
  );
}

export function TallPlant({ p, s = 1 }: { p: V3; s?: number }) {
  return (
    <G p={p} s={s}>
      <Cyl p={[0, 0.22, 0]} r1={0.16} r2={0.2} h={0.44} c="#e9e4da" seg={14} />
      <Cyl p={[0, 0.8, 0]} r1={0.025} h={0.8} c={C.woodDark} seg={6} />
      {([[0, 1.35, 0, 0.3], [0.18, 1.12, 0.05, 0.22], [-0.17, 1.18, -0.06, 0.23], [0.05, 1.62, 0.02, 0.2]] as [number, number, number, number][]).map(([x, y, z, r]) => (
        <mesh key={`${x}${y}`} position={[x, y, z]} castShadow><icosahedronGeometry args={[r, 1]} /><M c={y > 1.3 ? C.leaf : C.leafDark} r={0.9} flat /></mesh>
      ))}
    </G>
  );
}

export function Desk({ p, rot = 0, w = 1.3, c = C.wood, legs = C.woodDark, children }: { p: V3; rot?: number; w?: number; c?: string; legs?: string; children?: ReactNode }) {
  return (
    <G p={p} rot={rot}>
      <Box p={[0, 0.74, 0]} s={[w, 0.07, 0.66]} c={c} round={0.025} />
      {([[-1, -1], [1, -1], [-1, 1], [1, 1]] as [number, number][]).map(([x, z]) => (
        <Box key={`${x}${z}`} p={[x * (w / 2 - 0.07), 0.37, z * 0.27]} s={[0.05, 0.74, 0.05]} c={legs} />
      ))}
      {children}
    </G>
  );
}

export function Monitor({ p, rot = 0, screen = C.blue, glow = "#3b5aa8", bars = true }: { p: V3; rot?: number; screen?: string; glow?: string; bars?: boolean }) {
  return (
    <G p={p} rot={rot}>
      <Cyl p={[0, 0.015, 0]} r1={0.12} r2={0.1} h={0.03} c={C.metal} />
      <Box p={[0, 0.16, 0]} s={[0.04, 0.24, 0.04]} c={C.metal} />
      <Box p={[0, 0.42, 0]} s={[0.62, 0.38, 0.04]} c={C.screen} r={0.4} round={0.02} />
      <mesh position={[0, 0.42, 0.022]}><planeGeometry args={[0.56, 0.32]} /><M c={screen} e={glow} r={0.3} /></mesh>
      {bars && [0.04, 0.08, 0.06, 0.12, 0.16].map((h, i) => (
        <mesh key={i} position={[-0.2 + i * 0.1, 0.33 + h / 2, 0.025]}><boxGeometry args={[0.06, h, 0.004]} /><M c={C.mint} e="#2f8a6c" /></mesh>
      ))}
    </G>
  );
}

export function Laptop({ p, rot = 0, screen = C.amber, glow = "#8a5a1a" }: { p: V3; rot?: number; screen?: string; glow?: string }) {
  return (
    <G p={p} rot={rot}>
      <Box p={[0, 0, 0]} s={[0.42, 0.02, 0.3]} c="#c3cad6" r={0.35} round={0.008} />
      <group position={[0, 0.01, -0.15]} rotation={[-0.35, 0, 0]}>
        <Box p={[0, 0.14, 0]} s={[0.42, 0.28, 0.015]} c="#c3cad6" r={0.35} round={0.006} />
        <mesh position={[0, 0.14, 0.009]}><planeGeometry args={[0.38, 0.24]} /><M c={screen} e={glow} r={0.3} /></mesh>
      </group>
    </G>
  );
}

export function Chair({ p, rot = 0, c = C.fabric }: { p: V3; rot?: number; c?: string }) {
  return (
    <G p={p} rot={rot}>
      <Box p={[0, 0.46, 0]} s={[0.46, 0.08, 0.46]} c={c} round={0.03} />
      <Box p={[0, 0.75, -0.2]} s={[0.46, 0.5, 0.07]} c={c} round={0.03} />
      <Cyl p={[0, 0.23, 0]} r1={0.03} h={0.42} c={C.metal} seg={8} />
      <Cyl p={[0, 0.03, 0]} r1={0.24} h={0.04} c={C.metal} seg={10} />
    </G>
  );
}

export function Mug({ p, c = C.trim }: { p: V3; c?: string }) {
  return <Cyl p={p} r1={0.045} r2={0.05} h={0.1} c={c} seg={12} />;
}

export function Papers({ p, rot = 0, c = C.paper }: { p: V3; rot?: number; c?: string }) {
  return <Box p={p} s={[0.22, 0.02, 0.3]} c={c} rot={[0, rot, 0]} round={0.004} cast={false} />;
}

export function Rug({ p, r, c, c2, rect }: { p: V3; r: number; c: string; c2: string; rect?: [number, number] }) {
  if (rect) return (
    <group>
      <Box p={[p[0], p[1], p[2]]} s={[rect[0], 0.012, rect[1]]} c={c} r={1} cast={false} round={0.005} />
      <Box p={[p[0], p[1] + 0.004, p[2]]} s={[rect[0] - 0.3, 0.012, rect[1] - 0.3]} c={c2} r={1} cast={false} round={0.005} />
    </group>
  );
  return (
    <group>
      <mesh position={p} rotation={[-Math.PI / 2, 0, 0]} receiveShadow><circleGeometry args={[r, 40]} /><M c={c} r={1} /></mesh>
      <mesh position={[p[0], p[1] + 0.003, p[2]]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow><ringGeometry args={[r * 0.72, r * 0.81, 40]} /><M c={c2} r={1} /></mesh>
    </group>
  );
}

export function FloorLamp({ p }: { p: V3 }) {
  return (
    <G p={p}>
      <Cyl p={[0, 0.02, 0]} r1={0.2} r2={0.18} h={0.04} c={C.metal} />
      <Cyl p={[0, 0.75, 0]} r1={0.02} h={1.5} c={C.metal} seg={8} />
      <mesh position={[0, 1.55, 0]}><coneGeometry args={[0.24, 0.28, 18, 1, true]} /><meshStandardMaterial color="#fff1d6" emissive="#ffd59a" emissiveIntensity={0.8} side={2} /></mesh>
    </G>
  );
}

export function Window({ p, rot = 0, w = 1.5, h = 1.0 }: { p: V3; rot?: number; w?: number; h?: number }) {
  return (
    <G p={p} rot={rot}>
      <Box p={[0, 0, 0]} s={[w, h, 0.08]} c={C.trim} round={0.03} />
      <mesh position={[0, 0, 0.045]}><planeGeometry args={[w - 0.14, h - 0.14]} /><meshStandardMaterial color={C.glass} emissive="#bfe3ff" emissiveIntensity={0.55} roughness={0.15} /></mesh>
      <Box p={[0, 0, 0.05]} s={[0.04, h - 0.14, 0.02]} c={C.trim} />
      <Box p={[0, 0, 0.05]} s={[w - 0.14, 0.04, 0.02]} c={C.trim} />
      <Box p={[0, -h / 2 - 0.03, 0.08]} s={[w + 0.12, 0.06, 0.2]} c={C.trim} round={0.02} />
    </G>
  );
}

/** Quadro de parede com notas coloridas (cortiça) — kanban de tendências, de aprovações, de tickets. */
export function Board({ p, rot = 0, w = 1.6, h = 0.9, base = C.cork, notes = [C.amber, C.blue, C.coral, C.mint, C.lilac, C.amber] }: {
  p: V3; rot?: number; w?: number; h?: number; base?: string; notes?: string[];
}) {
  const cols = 3;
  return (
    <G p={p} rot={rot}>
      <Box p={[0, 0, 0]} s={[w + 0.12, h + 0.12, 0.05]} c={C.wood} round={0.03} />
      <mesh position={[0, 0, 0.03]}><planeGeometry args={[w, h]} /><M c={base} r={0.95} /></mesh>
      {notes.map((c, i) => {
        const x = -w / 2 + (w / cols) * ((i % cols) + 0.5);
        const y = h / 2 - (h / 2) * (Math.floor(i / cols) + 0.5);
        return <mesh key={i} position={[x, y, 0.036]} rotation={[0, 0, ((i % 3) - 1) * 0.08]}><planeGeometry args={[Math.min(0.26, w / cols - 0.1), Math.min(0.2, h / 2 - 0.1)]} /><M c={c} /></mesh>;
      })}
    </G>
  );
}

/** Tela de parede com gráfico de barras (mídia, finanças, rastreio). */
export function WallScreen({ p, rot = 0, w = 1.4, h = 0.8, bg = "#1f3550", bars = [0.3, 0.5, 0.4, 0.7, 0.6, 0.85], bar = C.mint, line }: {
  p: V3; rot?: number; w?: number; h?: number; bg?: string; bars?: number[]; bar?: string; line?: string;
}) {
  const bw = (w - 0.3) / bars.length;
  return (
    <G p={p} rot={rot}>
      <Box p={[0, 0, 0]} s={[w + 0.1, h + 0.1, 0.06]} c={C.screen} r={0.4} round={0.02} />
      <mesh position={[0, 0, 0.032]}><planeGeometry args={[w, h]} /><M c={bg} e={bg} ei={0.5} r={0.3} /></mesh>
      {bars.map((v, i) => (
        <mesh key={i} position={[-w / 2 + 0.15 + bw * (i + 0.5), -h / 2 + 0.1 + (v * (h - 0.25)) / 2, 0.036]}>
          <boxGeometry args={[bw * 0.6, v * (h - 0.25), 0.004]} /><M c={bar} e={bar} ei={0.45} />
        </mesh>
      ))}
      {line && <mesh position={[0, h / 2 - 0.1, 0.037]}><boxGeometry args={[w - 0.3, 0.025, 0.004]} /><M c={line} e={line} ei={0.6} /></mesh>}
    </G>
  );
}

export function Shelf({ p, rot = 0, w = 1.4, levels = 4, items = [], c = C.wood }: {
  p: V3; rot?: number; w?: number; levels?: number; items?: { x: number; l: number; c: string; k?: "box" | "jar" | "book" | "binder" }[]; c?: string;
}) {
  const ys = Array.from({ length: levels }, (_, i) => 0.05 + i * 0.55);
  const top = ys[ys.length - 1]! + 0.05;
  return (
    <G p={p} rot={rot}>
      {ys.map((y) => <Box key={y} p={[0, y, 0]} s={[w, 0.05, 0.42]} c={c} round={0.015} />)}
      {[-w / 2 + 0.02, w / 2 - 0.02].map((x) => <Box key={x} p={[x, top / 2, 0]} s={[0.05, top, 0.42]} c={C.woodDark} />)}
      {items.map((it, i) => {
        const y = ys[it.l]! + 0.03;
        if (it.k === "jar") return <mesh key={i} position={[it.x, y + 0.11, 0]} castShadow><cylinderGeometry args={[0.07, 0.07, 0.22, 12]} /><meshStandardMaterial color={it.c} roughness={0.1} transparent opacity={0.75} /></mesh>;
        if (it.k === "book") return <Box key={i} p={[it.x, y + 0.14, 0]} s={[0.07, 0.28, 0.3]} c={it.c} round={0.01} />;
        if (it.k === "binder") return <Box key={i} p={[it.x, y + 0.17, 0]} s={[0.09, 0.34, 0.32]} c={it.c} round={0.012} />;
        return <Box key={i} p={[it.x, y + 0.15, 0]} s={[0.24, 0.3, 0.26]} c={it.c} round={0.03} />;
      })}
    </G>
  );
}

export function Carton({ p, s = [0.4, 0.3, 0.32], rot = 0, tape = true }: { p: V3; s?: V3; rot?: number; tape?: boolean }) {
  return (
    <G p={p} rot={rot}>
      <Box p={[0, s[1] / 2, 0]} s={s} c={C.cardboard} round={0.015} />
      {tape && <Box p={[0, s[1] + 0.002, 0]} s={[0.07, 0.006, s[2] + 0.004]} c={C.cardboardDark} cast={false} />}
      <Box p={[s[0] / 4, s[1] * 0.6, s[2] / 2 + 0.002]} s={[0.12, 0.08, 0.004]} c={C.paper} cast={false} />
    </G>
  );
}

export function Pallet({ p, rot = 0, stack = [[0, 0], [0.42, 0], [0, 0.36], [0.42, 0.36], [0.2, 0.18, 1]] }: { p: V3; rot?: number; stack?: number[][] }) {
  return (
    <G p={p} rot={rot}>
      {[-0.4, 0, 0.4].map((z) => <Box key={z} p={[0.21, 0.05, z * 0.9 + 0.18]} s={[1.0, 0.1, 0.12]} c={C.woodLight} />)}
      <Box p={[0.21, 0.12, 0.18]} s={[1.0, 0.04, 0.95]} c={C.woodLight} />
      {stack.map(([x, z, l = 0], i) => <Carton key={i} p={[x!, 0.14 + l * 0.3, z!]} />)}
    </G>
  );
}

export function Cabinet({ p, rot = 0, c = "#8e99ab", drawers = 3 }: { p: V3; rot?: number; c?: string; drawers?: number }) {
  const h = drawers * 0.36;
  return (
    <G p={p} rot={rot}>
      <Box p={[0, h / 2, 0]} s={[0.5, h, 0.55]} c={c} r={0.5} round={0.02} />
      {Array.from({ length: drawers }, (_, i) => (
        <group key={i}>
          <Box p={[0, 0.18 + i * 0.36, 0.28]} s={[0.44, 0.3, 0.02]} c="#a7b1c1" r={0.5} cast={false} />
          <Box p={[0, 0.25 + i * 0.36, 0.3]} s={[0.14, 0.03, 0.03]} c={C.metal} cast={false} />
        </group>
      ))}
    </G>
  );
}

export function Sofa({ p, rot = 0, c = "#e07a5f", w = 1.6 }: { p: V3; rot?: number; c?: string; w?: number }) {
  return (
    <G p={p} rot={rot}>
      <Box p={[0, 0.22, 0]} s={[w, 0.3, 0.75]} c={c} round={0.08} />
      <Box p={[0, 0.55, -0.3]} s={[w, 0.45, 0.18]} c={c} round={0.08} />
      {[-w / 2 + 0.1, w / 2 - 0.1].map((x) => <Box key={x} p={[x, 0.42, 0]} s={[0.2, 0.32, 0.75]} c={c} round={0.08} />)}
      {[-w / 4, w / 4].map((x) => <Box key={x} p={[x, 0.42, 0.03]} s={[w / 2 - 0.15, 0.1, 0.55]} c="#f3e9d8" round={0.05} />)}
    </G>
  );
}

export function RoundTable({ p, r = 0.75, c = C.woodLight }: { p: V3; r?: number; c?: string }) {
  return (
    <G p={p}>
      <Cyl p={[0, 0.72, 0]} r1={r} h={0.06} c={c} seg={32} />
      <Cyl p={[0, 0.36, 0]} r1={0.06} h={0.7} c={C.metal} seg={10} />
      <Cyl p={[0, 0.02, 0]} r1={0.34} r2={0.3} h={0.04} c={C.metal} seg={16} />
    </G>
  );
}

export function Stool({ p, c = C.amber }: { p: V3; c?: string }) {
  return (
    <G p={p}>
      <Cyl p={[0, 0.45, 0]} r1={0.2} h={0.08} c={c} seg={16} />
      <Cyl p={[0, 0.22, 0]} r1={0.03} h={0.42} c={C.metal} seg={8} />
      <Cyl p={[0, 0.02, 0]} r1={0.18} h={0.03} c={C.metal} seg={12} />
    </G>
  );
}

export function WaterCooler({ p }: { p: V3 }) {
  return (
    <G p={p}>
      <Box p={[0, 0.5, 0]} s={[0.36, 1.0, 0.36]} c="#e9edf2" round={0.04} />
      <mesh position={[0, 1.22, 0]} castShadow><cylinderGeometry args={[0.15, 0.15, 0.4, 16]} /><meshStandardMaterial color="#9fd8ff" roughness={0.1} transparent opacity={0.7} /></mesh>
      <Box p={[0, 0.72, 0.19]} s={[0.12, 0.08, 0.04]} c={C.blue} cast={false} />
    </G>
  );
}
