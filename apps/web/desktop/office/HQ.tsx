"use client";
// Sede 3D completa (UXD-017/018/019/020): 7 ambientes do SDD v3 §7 num diorama único, os 13 agentes nos lugares
// da sala REAL de cada um (presença do backend), câmera isométrica com foco suave por sala, placas em DOM e
// qualidade adaptável. A geometria estática é "assada" (Baked) em poucas malhas. Nada aqui inventa estado.
import { OrbitControls, OrthographicCamera, PerformanceMonitor } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { type MutableRefObject, type RefObject, useEffect, useMemo, useRef, useState } from "react";
import { type Camera, Vector3 } from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import type { PresenceV1 } from "@/lib/contract";
import { STATE_UI } from "@/lib/agentIdentity";
import { declutter, nearby, type Plate, platePriority, type Rect } from "@/lib/plates";
import { HQ_D, HQ_W, placeAgents, ZONES, type ZoneId, zoneById, zoneCenter, zoneSize } from "@/lib/officeHq";
import Baked from "./Baked";
import Character from "./Character";
import { C, M } from "./kit";
import { CEO_BEACON, FACE, ROOM_COMPONENTS } from "./rooms";

export type Quality = "auto" | "high" | "balanced" | "low";
const AV_SCALE = 1.15;
const CAM_OFFSET = new Vector3(18, 16.5, 18);

// ───────── câmera: enquadramento calculado e foco suave ─────────
type Box3 = { min: [number, number, number]; max: [number, number, number] };
const HQ_BOX: Box3 = { min: [-HQ_W / 2 - 0.4, -0.5, -HQ_D / 2 - 0.4], max: [HQ_W / 2 + 0.4, 2.6, HQ_D / 2 + 0.4] };
function zoneBox(id: ZoneId): Box3 {
  const z = zoneById(id); const [cx, cz] = zoneCenter(z); const { w, d } = zoneSize(z);
  return { min: [cx - w / 2 - 0.2, 0, cz - d / 2 - 0.2], max: [cx + w / 2 + 0.2, 2.4, cz + d / 2 + 0.2] };
}
/** Zoom ortográfico que faz a caixa caber na tela, na orientação atual da câmera. */
function fitZoom(camera: Camera, target: Vector3, b: Box3, w: number, h: number, margin: number): number {
  camera.position.copy(target).add(CAM_OFFSET);
  camera.lookAt(target);
  camera.updateMatrixWorld();
  const inv = camera.matrixWorldInverse;
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  const v = new Vector3();
  for (const x of [b.min[0], b.max[0]]) for (const y of [b.min[1], b.max[1]]) for (const z of [b.min[2], b.max[2]]) {
    v.set(x, y, z).applyMatrix4(inv);
    x0 = Math.min(x0, v.x); x1 = Math.max(x1, v.x); y0 = Math.min(y0, v.y); y1 = Math.max(y1, v.y);
  }
  return Math.min(w / ((x1 - x0) * margin), h / ((y1 - y0) * margin));
}

interface Goal { target: Vector3; zoom: number; active: boolean }

function CameraRig({ controls, goal, focus, reduced, onReady }: {
  controls: RefObject<OrbitControlsImpl | null>; goal: MutableRefObject<Goal>; focus: ZoneId | null; reduced: boolean; onReady(): void;
}) {
  const camera = useThree((s) => s.camera);
  const size = useThree((s) => s.size);
  const first = useRef(true);
  // novo foco (ou redimensionamento): calcula alvo/zoom; a primeira vez posiciona sem animar
  useEffect(() => {
    const box = focus ? zoneBox(focus) : HQ_BOX;
    const target = new Vector3((box.min[0] + box.max[0]) / 2, focus ? 0.9 : 0.6, (box.min[2] + box.max[2]) / 2);
    const keep = camera.position.clone(); const keepZoom = camera.zoom;
    const TOP = 104; // faixa do HUD (título + salas)
    const RIGHT = focus ? 450 : 80; // coluna de agentes (+ painel da sala quando focada)
    const zoom = fitZoom(camera, target, box, size.width - RIGHT - 30, size.height - TOP - 30, focus ? 1.08 : 1.04);
    // desloca o alvo para o centro da área livre (abaixo do HUD, à esquerda dos painéis)
    const up = new Vector3(0, 1, 0).applyQuaternion(camera.quaternion);
    const right = new Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
    target.addScaledVector(up, (TOP / 2) / zoom).addScaledVector(right, (RIGHT / 2) / zoom);
    goal.current = { target, zoom, active: true };
    if (first.current || reduced) {
      first.current = false;
      camera.zoom = zoom; camera.updateProjectionMatrix();
      controls.current?.target.copy(target); controls.current?.update();
      goal.current.active = false;
      if (!focus) controls.current?.saveState();
      onReady();
    } else {
      camera.position.copy(keep); camera.zoom = keepZoom; camera.updateProjectionMatrix();
    }
  }, [focus, size.width, size.height]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const c = controls.current;
    if (!c) return;
    const stop = () => { goal.current.active = false; };
    c.addEventListener("start", stop);
    return () => c.removeEventListener("start", stop);
  }, [controls, goal]);
  useFrame((_, dt) => {
    const g = goal.current; const c = controls.current;
    if (!g.active || !c) return;
    const k = 1 - Math.exp(-5 * dt);
    const d = g.target.clone().sub(c.target).multiplyScalar(k);
    c.target.add(d); camera.position.add(d);
    camera.zoom += (g.zoom - camera.zoom) * k;
    camera.updateProjectionMatrix();
    c.update();
    if (g.target.distanceTo(c.target) < 0.005 && Math.abs(g.zoom - camera.zoom) < 0.3) g.active = false;
  });
  return null;
}

// ───────── placas DOM projetadas a cada quadro (sem re-render do React) ─────────
function Projector({ anchors, els, priority, bodies }: {
  anchors: Record<string, [number, number, number]>; els: RefObject<Record<string, HTMLElement | null>>; priority: Record<string, number>;
  bodies: { head: [number, number, number]; feet: [number, number, number] }[];  // corpo de cada personagem
}) {
  const camera = useThree((s) => s.camera);
  const size = useThree((s) => s.size);
  const v = useMemo(() => new Vector3(), []);
  const sizes = useRef<Record<string, [number, number]>>({});
  const frame = useRef(0);
  useFrame(() => {
    // tamanho das placas: medido a cada 20 quadros (texto muda pouco) — evita forçar layout em todo quadro
    const measure = frame.current++ % 20 === 0;
    const pos: Record<string, [number, number, boolean]> = {};
    const plates: Plate[] = [];
    const obstacles: Rect[] = [];
    for (const { head, feet } of bodies) {  // corpo do personagem = obstáculo fixo (da cabeça aos pés)
      v.set(head[0], head[1], head[2]).project(camera); const ty = ((1 - v.y) / 2) * size.height, tx = ((v.x + 1) / 2) * size.width;
      v.set(feet[0], feet[1], feet[2]).project(camera); const by = ((1 - v.y) / 2) * size.height;
      const half = Math.max(8, (by - ty) * 0.28);
      obstacles.push({ l: tx - half, r: tx + half, t: ty + 2, b: by });
    }
    for (const [id, a] of Object.entries(anchors)) {
      const el = els.current[id];
      if (!el) continue;
      v.set(a[0], a[1], a[2]).project(camera);
      const x = ((v.x + 1) / 2) * size.width, y = ((1 - v.y) / 2) * size.height;
      const vis = !(v.z > 1 || x < -40 || y < -20 || x > size.width + 40 || y > size.height + 20);
      pos[id] = [x, y, vis];
      const plate = el.firstElementChild as HTMLElement | null;
      if (plate && vis) {
        if (measure || !sizes.current[id]) sizes.current[id] = [plate.offsetWidth, plate.offsetHeight];
        const [w, h] = sizes.current[id]!;
        if (w === 0) continue;
        // placa de agente: âncora no centro-inferior; placa de sala: âncora na ponta esquerda, centrada na vertical
        if (id.startsWith("a:")) plates.push({ id, x, y, w, h, priority: priority[id] ?? 0 });
        else plates.push({ id, x: x + w / 2, y: y + h / 2, w, h, priority: -10, candidates: nearby(w, h) });
      }
    }
    const off = declutter(plates, obstacles);
    for (const [id, [x, y, vis]] of Object.entries(pos)) {
      const el = els.current[id]!;
      const o = off[id] ?? { dx: 0, dy: 0 };
      el.style.transform = `translate(${(x + o.dx).toFixed(1)}px, ${(y + o.dy).toFixed(1)}px)`;
      el.style.visibility = vis ? "visible" : "hidden";
    }
  });
  return null;
}

/** Farol sobre o painel de aprovações da Sala do CEO: aceso SÓ quando há aprovações reais pendentes. */
function Beacon({ on, p }: { on: boolean; p: [number, number, number] }) {
  return (
    <group position={p}>
      <mesh><cylinderGeometry args={[0.09, 0.11, 0.06, 16]} /><M c={C.metal} /></mesh>
      <mesh position={[0, 0.1, 0]}><sphereGeometry args={[0.09, 18, 14]} />
        <meshStandardMaterial color={on ? "#f4c47f" : "#5b6474"} emissive={on ? "#f4a53a" : "#000"} emissiveIntensity={on ? 1.4 : 0} roughness={0.3} /></mesh>
      {on && <pointLight position={[0, 0.2, 0.3]} intensity={2.2} distance={3} color="#ffc066" />}
    </group>
  );
}

function Building() {
  return (
    <Baked>
      <mesh position={[0, -0.3, 0]} receiveShadow><boxGeometry args={[HQ_W + 0.9, 0.5, HQ_D + 0.9]} /><M c={C.plinth} /></mesh>
      <mesh position={[0, -0.02, 0]} receiveShadow><boxGeometry args={[HQ_W + 0.5, 0.1, HQ_D + 0.5]} /><M c={C.plinthTop} /></mesh>
      {ZONES.map((z) => {
        const [x, zz] = zoneCenter(z);
        const Room = ROOM_COMPONENTS[z.id];
        return <group key={z.id} position={[x, 0, zz]}><Room z={z} /></group>;
      })}
    </Baked>
  );
}

export default function HQ({ agents, selectedId, onSelect, focus, onFocus, reducedMotion, quality, pendingApprovals, background = "#141b26" }: {
  agents: PresenceV1[]; selectedId: string | null; onSelect(id: string | null): void; focus: ZoneId | null; onFocus(z: ZoneId | null): void;
  reducedMotion: boolean; quality: Quality; pendingApprovals: number; background?: string;
}) {
  const controls = useRef<OrbitControlsImpl>(null);
  const goal = useRef<Goal>({ target: new Vector3(), zoom: 40, active: false });
  const [hover, setHover] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [degraded, setDegraded] = useState(false); // modo Auto: caiu para baixa qualidade por FPS medido
  // alta: sombras + DPR da tela; equilibrada: sombras leves e DPR 1; baixa: sem sombras. Auto começa alta e cai.
  const hi = quality === "high" || quality === "balanced" || (quality === "auto" && !degraded);
  const dpr: number | [number, number] = quality === "high" || (quality === "auto" && !degraded) ? [1, 2] : 1;
  const shadowMap = quality === "balanced" ? 1024 : 2048;
  const prev = useRef<Record<string, string>>({});
  const [celebrate, setCelebrate] = useState<Record<string, number>>({});
  useEffect(() => { // celebra UMA vez na transição real para COMPLETED
    for (const a of agents) {
      const before = prev.current[a.agent_id];
      if (before && before !== "COMPLETED" && a.state === "COMPLETED") setCelebrate((c) => ({ ...c, [a.agent_id]: (c[a.agent_id] ?? 0) + 1 }));
      prev.current[a.agent_id] = a.state;
    }
  }, [agents]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return;
      if (e.key === "r" || e.key === "R") { onFocus(null); controls.current?.reset(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onFocus]);

  const placed = useMemo(() => placeAgents(agents.map((a) => ({ agent_id: a.agent_id, room: a.room }))),
    [agents.map((a) => `${a.agent_id}:${a.room}`).join()]); // eslint-disable-line react-hooks/exhaustive-deps
  const byId = useMemo(() => Object.fromEntries(agents.map((a) => [a.agent_id, a])), [agents]);
  const els = useRef<Record<string, HTMLElement | null>>({});
  const anchors = useMemo(() => {
    const a: Record<string, [number, number, number]> = {};
    for (const p of placed) a[`a:${p.agentId}`] = [p.x, 2.05, p.z];
    for (const z of ZONES) { const [cx, cz] = zoneCenter(z); const { w, d } = zoneSize(z); a[`z:${z.id}`] = [cx - w / 2 + 0.3, 0.1, cz + d / 2 - 0.3]; }
    return a;
  }, [placed]);
  const ceo = zoneById("ceo"); const [ceoX, ceoZ] = zoneCenter(ceo);
  const bodies = useMemo(() => placed.map((p) => ({ head: [p.x, 2.05, p.z] as [number, number, number], feet: [p.x, 0.06, p.z] as [number, number, number] })), [placed]);
  const platePrio = useMemo(() => {
    const out: Record<string, number> = {};
    for (const p of placed) {
      const a = byId[p.agentId]; if (!a) continue;
      out[`a:${p.agentId}`] = platePriority(selectedId === p.agentId, STATE_UI[a.state]?.tone, a.state === "IDLE" || a.state === "COMPLETED");
    }
    return out;
  }, [placed, byId, selectedId]);

  return (
    <div style={{ position: "absolute", inset: 0 }} data-ready={ready ? "1" : "0"}>
      <Canvas shadows={hi} dpr={dpr} frameloop={reducedMotion ? "demand" : "always"}
        onPointerMissed={() => onSelect(null)} aria-label="Escritório 3D: sede completa com 7 salas"
        gl={{ antialias: true }} onCreated={({ gl, scene }) => {
          try { if (localStorage.getItem("aic.debug") === "1") Object.assign(window as unknown as object, { __aicGl: gl, __aicScene: scene }); } catch { /* */ }
        }}>
        {quality === "auto" && <PerformanceMonitor onDecline={() => setDegraded(true)} flipflops={2} />}
        <color attach="background" args={[background]} />
        <OrthographicCamera makeDefault position={CAM_OFFSET.toArray()} zoom={40} near={0.1} far={200} />
        <OrbitControls ref={controls} target={[0, 0.6, 0]} enableDamping minZoom={18} maxZoom={260}
          minAzimuthAngle={Math.PI / 4 - 0.6} maxAzimuthAngle={Math.PI / 4 + 0.6} minPolarAngle={0.7} maxPolarAngle={1.12}
          screenSpacePanning makeDefault />
        <hemisphereLight args={["#fff4e2", "#3a5470", 0.9]} />
        <directionalLight position={[10, 16, 7]} intensity={1.55} color="#fff1dc" castShadow={hi}
          shadow-mapSize={[shadowMap, shadowMap]} shadow-camera-left={-14} shadow-camera-right={14} shadow-camera-top={14} shadow-camera-bottom={-14}
          shadow-camera-far={60} shadow-bias={-0.0004} shadow-normalBias={0.03} />
        <directionalLight position={[-8, 6, 10]} intensity={0.45} color="#a9c1ff" />
        <Building />
        {/* áreas clicáveis do piso (foco na sala) */}
        {ZONES.map((z) => {
          const [x, zz] = zoneCenter(z); const { w, d } = zoneSize(z);
          return (
            <mesh key={z.id} position={[x, 0.07, zz]} rotation={[-Math.PI / 2, 0, 0]} userData={{ noBake: true }}
              onClick={(e) => { e.stopPropagation(); onFocus(z.id); }}
              onPointerOver={(e) => { e.stopPropagation(); document.body.style.cursor = "zoom-in"; }}
              onPointerOut={() => { document.body.style.cursor = "auto"; }}>
              <planeGeometry args={[w - 0.1, d - 0.1]} />
              <meshBasicMaterial transparent opacity={focus === z.id ? 0.07 : 0} color="#ffffff" depthWrite={false} />
            </mesh>
          );
        })}
        <Beacon on={pendingApprovals > 0} p={[ceoX + CEO_BEACON[0], CEO_BEACON[1], ceoZ + CEO_BEACON[2]]} />
        {placed.map((p) => {
          const a = byId[p.agentId];
          if (!a) return null;
          return (
            <Character key={p.agentId} agentId={p.agentId} state={a.state} position={[p.x, 0.06, p.z]} rotationY={FACE} scale={AV_SCALE}
              selected={selectedId === p.agentId} hovered={hover === p.agentId} reducedMotion={reducedMotion}
              celebrateKey={celebrate[p.agentId] ?? 0} onSelect={(id) => onSelect(id)} onHover={setHover} />
          );
        })}
        <Projector anchors={anchors} els={els} priority={platePrio} bodies={bodies} />
        <CameraRig controls={controls} goal={goal} focus={focus} reduced={reducedMotion} onReady={() => setReady(true)} />
      </Canvas>
      <div style={{ position: "absolute", inset: 0, pointerEvents: "none", overflow: "hidden" }}>
        {!focus && ZONES.map((z) => (
          <div key={z.id} ref={(el) => { els.current[`z:${z.id}`] = el; }} className="plate-anchor">
            <button type="button" className={`room-tag ${focus === z.id ? "on" : ""}`}
              style={{ ["--tag" as string]: z.wall }} onClick={() => onFocus(focus === z.id ? null : z.id)}
              aria-label={`${focus === z.id ? "Voltar à sede inteira" : "Focar"}: ${z.label}`}>
              {z.label}
            </button>
          </div>
        ))}
        {placed.map((p) => {
          const a = byId[p.agentId];
          if (!a) return null;
          const st = STATE_UI[a.state] ?? STATE_UI.IDLE!;
          const quiet = a.state === "IDLE" || a.state === "COMPLETED";
          const show = !quiet || selectedId === p.agentId || hover === p.agentId || focus === p.zone;
          if (!show) return <div key={p.agentId} ref={(el) => { els.current[`a:${p.agentId}`] = el; }} className="plate-anchor" />;
          return (
            <div key={p.agentId} ref={(el) => { els.current[`a:${p.agentId}`] = el; }} aria-hidden="true" className="plate-anchor">
              <div className={`nameplate ${selectedId === p.agentId ? "sel" : ""} ${quiet ? "quiet" : ""}`} data-tone={st.tone}>
                {a.name}{(!quiet || selectedId === p.agentId || hover === p.agentId) && <span className="st">· {st.label}</span>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
