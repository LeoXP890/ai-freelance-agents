"use client";
// Cena isométrica compacta (SDD §13). Geometria procedural nesta fase; GLB/glTF do Blender entra em OFF-001.
// Estado visual vem exclusivamente da presença do backend; animação nunca dispara ação.
import { Html, useGLTF } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Component, type ReactNode, Suspense, useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  BufferAttribute, type BufferGeometry, Color, type Group, type InstancedMesh, type Mesh, MeshStandardMaterial, Object3D,
  Vector3,
} from "three";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import { type Accessory, ROOMS, STATE_COLOR, STATE_LABEL, type PresenceV1 } from "@/lib/contract";
import { slotOffset } from "@/lib/officeLayout";
import { agentsInRoom, type OfficeState } from "@/lib/officeStore";

const ROOM_SIZE = 4;
const GAP = 0.35;

export function roomOrigin(index: number): [number, number] {
  const col = index % 3;
  const row = Math.floor(index / 3);
  return [(col - 1) * (ROOM_SIZE + GAP), (row - 1) * (ROOM_SIZE + GAP)];
}

// Vista isométrica fixa: a câmera sempre mira o centro da planta (não depender do default do R3F).
// Extensão da planta no plano da tela isométrica (unidades de mundo) — usada para enquadrar a cena.
const PLAN_SCREEN_W = (3 * ROOM_SIZE + 2 * GAP) * Math.SQRT2;
const PLAN_SCREEN_H = PLAN_SCREEN_W * 0.62;

function IsometricRig() {
  const camera = useThree((st) => st.camera);
  const size = useThree((st) => st.size);
  const invalidate = useThree((st) => st.invalidate);
  useLayoutEffect(() => {
    camera.position.set(14, 14, 14);
    camera.lookAt(0, 0, 0);
    // Zoom ortográfico ajustado ao canvas: a planta ocupa ~92% da menor proporção disponível.
    camera.zoom = 0.92 * Math.min(size.width / PLAN_SCREEN_W, size.height / PLAN_SCREEN_H);
    camera.updateProjectionMatrix();
    invalidate();
  }, [camera, size.width, size.height, invalidate]);
  return null;
}

// Acessórios primitivos (sem assets externos): leves, legíveis na vista isométrica.
function AccessoryMesh({ kind }: { kind: Accessory }) {
  switch (kind) {
    case "hat":
      return (
        <group position={[0, 1.36, 0]}>
          <mesh><cylinderGeometry args={[0.3, 0.3, 0.03, 24]} /><meshStandardMaterial color="#2b2f3a" /></mesh>
          <mesh position={[0, 0.1, 0]}><cylinderGeometry args={[0.17, 0.19, 0.2, 24]} /><meshStandardMaterial color="#2b2f3a" /></mesh>
        </group>
      );
    case "cap":
      return (
        <group position={[0, 1.33, 0]}>
          <mesh><sphereGeometry args={[0.225, 20, 10, 0, Math.PI * 2, 0, Math.PI / 2]} /><meshStandardMaterial color="#e2e8f0" /></mesh>
          <mesh position={[0.12, 0, 0.12]} rotation={[0, Math.PI / 4, 0]}>
            <boxGeometry args={[0.22, 0.02, 0.16]} /><meshStandardMaterial color="#e2e8f0" />
          </mesh>
        </group>
      );
    case "glasses":
      return (
        <group position={[0.15, 1.2, 0.15]} rotation={[0, Math.PI / 4, 0]}>
          {[-0.08, 0.08].map((x) => (
            <mesh key={x} position={[x, 0, 0]}><torusGeometry args={[0.055, 0.014, 8, 20]} /><meshStandardMaterial color="#111827" /></mesh>
          ))}
        </group>
      );
    case "headset":
      return (
        <group position={[0, 1.18, 0]} rotation={[0, Math.PI / 4, 0]}>
          <mesh rotation={[0, 0, 0]}><torusGeometry args={[0.235, 0.02, 8, 24, Math.PI]} /><meshStandardMaterial color="#111827" /></mesh>
          {[-0.235, 0.235].map((x) => (
            <mesh key={x} position={[x, 0, 0]}><sphereGeometry args={[0.06, 12, 12]} /><meshStandardMaterial color="#111827" /></mesh>
          ))}
        </group>
      );
    default:
      return null;
  }
}

// ───── Móveis (OFF-001): GLB sob demanda na qualidade alta; procedural na baixa ou se o arquivo falhar ─────
const ASSET = (name: string) => `/assets/office/${name}.glb`;
const DESK_AT: [number, number, number] = [-1.0, 0, -1.25];

function ProceduralDesk() {
  return (
    <mesh position={[DESK_AT[0], 0.3, DESK_AT[2]]} castShadow>
      <boxGeometry args={[1.3, 0.08, 0.7]} />
      <meshStandardMaterial color="#3b4a6e" />
    </mesh>
  );
}

// Cada GLB vira UMA geometria (cores das peças gravadas nos vértices) desenhada com InstancedMesh em todas as salas:
// 5 draw calls no total, em vez de ~180 malhas clonadas (medido: 51 → 13–17 FPS em SwiftShader com clones).
function useMergedModel(name: string): { geometry: BufferGeometry; material: MeshStandardMaterial } {
  const { scene } = useGLTF(ASSET(name), false); // sem Draco: nenhum decodificador de CDN externo
  return useMemo(() => {
    scene.updateMatrixWorld(true);
    const parts: BufferGeometry[] = [];
    scene.traverse((o) => {
      const m = o as Mesh;
      if (!m.isMesh) return;
      const g = m.geometry.clone().applyMatrix4(m.matrixWorld);
      const c = new Color((m.material as MeshStandardMaterial).color);
      const em = (m.material as MeshStandardMaterial).emissive;
      if (em) c.add(em.clone().multiplyScalar(0.6)); // tela "acesa" sem material próprio
      const n = g.getAttribute("position").count;
      const colors = new Float32Array(n * 3);
      for (let k = 0; k < n; k++) colors.set([c.r, c.g, c.b], k * 3);
      g.setAttribute("color", new BufferAttribute(colors, 3));
      for (const attr of Object.keys(g.attributes)) if (!["position", "normal", "color"].includes(attr)) g.deleteAttribute(attr);
      parts.push(g.index ? g.toNonIndexed() : g);
    });
    const geometry = mergeGeometries(parts, false);
    return { geometry, material: new MeshStandardMaterial({ vertexColors: true, roughness: 0.7 }) };
  }, [scene]);
}

type Placement = { pos: [number, number, number]; rotY?: number };

function InstancedModel({ name, placements, castShadow = false }: {
  name: string; placements: Placement[]; castShadow?: boolean;
}) {
  const { geometry, material } = useMergedModel(name);
  const ref = useRef<InstancedMesh>(null);
  useLayoutEffect(() => {
    const m = ref.current;
    if (!m) return;
    const o = new Object3D();
    placements.forEach((p, k) => {
      o.position.set(...p.pos);
      o.rotation.set(0, p.rotY ?? 0, 0);
      o.updateMatrix();
      m.setMatrixAt(k, o.matrix);
    });
    m.instanceMatrix.needsUpdate = true;
    m.computeBoundingSphere();
  }, [placements]);
  return <instancedMesh ref={ref} args={[geometry, material, placements.length]} castShadow={castShadow} />;
}

const LAYOUT: Record<string, Placement[]> = (() => {
  const at = (i: number, x: number, y: number, z: number, rotY = 0): Placement => {
    const [ox, oz] = roomOrigin(i);
    return { pos: [ox + x, y, oz + z], rotY };
  };
  const rooms = ROOMS.map((_, i) => i);
  const [dx, , dz] = DESK_AT;
  return {
    desk: rooms.map((i) => at(i, dx, 0, dz)),
    monitor: rooms.map((i) => at(i, dx, 0.75, dz - 0.15)),
    chair: rooms.map((i) => at(i, dx, 0, dz + 0.65, Math.PI)),
    plant: rooms.map((i) => at(i, 1.55, 0, -1.55)),
    shelf: rooms.filter((i) => i % 3 === 0).map((i) => at(i, 0.6, 0, -1.8)),
  };
})();

function ProceduralDesks() {
  return <>{ROOMS.map((_, i) => {
    const [ox, oz] = roomOrigin(i);
    return <group key={i} position={[ox, 0, oz]}><ProceduralDesk /></group>;
  })}</>;
}

class AssetBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

function FurnitureLayer({ quality }: { quality: "low" | "high" }) {
  if (quality === "low") return <ProceduralDesks />;
  return (
    <AssetBoundary fallback={<ProceduralDesks />}>
      <Suspense fallback={<ProceduralDesks />}>
        {Object.entries(LAYOUT).map(([name, placements]) => (
          <InstancedModel key={name} name={name} placements={placements} castShadow={name === "desk" || name === "shelf"} />
        ))}
      </Suspense>
    </AssetBoundary>
  );
}

const ANIMATED = new Set(["WORKING", "TOOL_CALL", "PLANNING"]);

function Avatar({ agent, position, selected, reducedMotion, onSelect }: {
  agent: PresenceV1; position: [number, number, number]; selected: boolean; reducedMotion: boolean;
  onSelect(id: string): void;
}) {
  const ref = useRef<Group>(null);
  const phase = useMemo(() => (agent.agent_id.charCodeAt(0) % 7) / 7 * Math.PI * 2, [agent.agent_id]);
  useFrame(({ clock }) => {
    if (!ref.current) return;
    const t = clock.getElapsedTime();
    ref.current.position.y = !reducedMotion && ANIMATED.has(agent.state) ? Math.abs(Math.sin(t * 3 + phase)) * 0.18 : 0;
  });
  const ring = STATE_COLOR[agent.state];
  return (
    <group position={position}>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.02, 0]}>
        <ringGeometry args={[0.42, selected ? 0.62 : 0.52, 32]} />
        <meshBasicMaterial color={ring} />
      </mesh>
      <group ref={ref}
        onClick={(e) => { e.stopPropagation(); onSelect(agent.agent_id); }}
        onPointerOver={() => { document.body.style.cursor = "pointer"; }}
        onPointerOut={() => { document.body.style.cursor = "auto"; }}>
        <mesh position={[0, 0.55, 0]} castShadow>
          <capsuleGeometry args={[0.28, 0.5, 6, 16]} />
          <meshStandardMaterial color={agent.color} roughness={0.55} />
        </mesh>
        <mesh position={[0, 1.18, 0]} castShadow>
          <sphereGeometry args={[0.22, 20, 20]} />
          <meshStandardMaterial color={agent.appearance?.skin ?? "#f1e3d3"} roughness={0.7} />
        </mesh>
        <AccessoryMesh kind={agent.appearance?.accessory ?? "none"} />
      </group>
      <Html position={[0, 1.75, 0]} center zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
        <div className={`tag ${selected ? "tag-on" : ""}`}>
          <strong>{agent.name}</strong>
          <span style={{ color: ring }}>{STATE_LABEL[agent.state]}</span>
        </div>
      </Html>
    </group>
  );
}

function Room({ index, label, agents, selectedId, reducedMotion, onSelect }: {
  index: number; label: string; agents: PresenceV1[]; selectedId: string | null; reducedMotion: boolean;
  onSelect(id: string): void;
}) {
  const [x, z] = roomOrigin(index);
  const busy = agents.some((a) => a.state !== "IDLE" && a.state !== "COMPLETED");
  return (
    <group position={[x, 0, z]}>
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[ROOM_SIZE, ROOM_SIZE]} />
        <meshStandardMaterial color={busy ? "#25314d" : "#1c2438"} />
      </mesh>
      <mesh position={[0, 0.35, -ROOM_SIZE / 2]}>
        <boxGeometry args={[ROOM_SIZE, 0.7, 0.08]} />
        <meshStandardMaterial color="#2c3858" />
      </mesh>
      <mesh position={[-ROOM_SIZE / 2, 0.35, 0]}>
        <boxGeometry args={[0.08, 0.7, ROOM_SIZE]} />
        <meshStandardMaterial color="#26304b" />
      </mesh>
      <Html position={[ROOM_SIZE / 2 - 0.45, 0.05, ROOM_SIZE / 2 - 0.45]} center zIndexRange={[5, 0]}
        style={{ pointerEvents: "none" }}>
        <div className="room-label">{label}</div>
      </Html>
      {agents.map((a, i) => (
        <Avatar key={a.agent_id} agent={a} selected={selectedId === a.agent_id} reducedMotion={reducedMotion}
          onSelect={onSelect}
          position={[slotOffset(i, agents.length)[0], 0, slotOffset(i, agents.length)[1]]} />
      ))}
    </group>
  );
}

// Projeta um ponto do mundo para pixels do canvas (câmera ortográfica fixa: muda só no redimensionamento).
function ScreenAnchor({ at, onProject }: { at: [number, number, number]; onProject(p: { x: number; y: number }): void }) {
  const camera = useThree((st) => st.camera);
  const size = useThree((st) => st.size);
  useLayoutEffect(() => {
    camera.updateMatrixWorld();
    const v = new Vector3(...at).project(camera);
    onProject({ x: ((v.x + 1) / 2) * size.width, y: ((1 - v.y) / 2) * size.height });
  }, [camera, size.width, size.height, at, onProject]);
  return null;
}

const CEO_ANCHOR: [number, number, number] = (() => {
  const [x, z] = roomOrigin(0);
  return [x, 2.4, z];
})();

export default function Office3D({ office, selectedId, onSelect, reducedMotion, quality, pendingApprovals,
  onOpenApprovals }: {
  office: OfficeState; selectedId: string | null; onSelect(id: string | null): void; reducedMotion: boolean;
  quality: "low" | "high"; pendingApprovals: number | null; onOpenApprovals(): void;
}) {
  const [ceo, setCeo] = useState<{ x: number; y: number } | null>(null);
  const onProject = useCallback((p: { x: number; y: number }) => setCeo(p), []);
  return (
    <div className="scene3d">
      <Canvas
        orthographic
        shadows={quality === "high"}
        dpr={quality === "high" ? [1, 2] : 1}
        frameloop={reducedMotion ? "demand" : "always"}
        camera={{ position: [14, 14, 14], zoom: 42, near: 0.1, far: 200 }}
        onPointerMissed={() => onSelect(null)}
        onCreated={({ gl }) => {
          // diagnóstico de desempenho (testes): só com localStorage aic.debug=1
          try { if (localStorage.getItem("aic.debug") === "1") (window as unknown as { __aicGl: unknown }).__aicGl = gl; } catch { /* sem storage */ }
        }}
        aria-label="Escritório 3D dos agentes"
      >
        <IsometricRig />
        <ScreenAnchor at={CEO_ANCHOR} onProject={onProject} />
        <color attach="background" args={["#0b1020"]} />
        <ambientLight intensity={0.65} />
        <directionalLight position={[8, 14, 6]} intensity={1.1} castShadow={quality === "high"} />
        {ROOMS.map((r, i) => (
          <Room key={r.id} index={i} label={r.label} agents={agentsInRoom(office, r.id)} selectedId={selectedId}
            reducedMotion={reducedMotion} onSelect={onSelect} />
        ))}
        <FurnitureLayer quality={quality} />
      </Canvas>
      {/* Alternativa por teclado ao clique no canvas (OFF-007): oculta até receber foco. */}
      <nav className="agent-keys" aria-label="Agentes do escritório">
        {office.order.map((id) => {
          const a = office.agents[id];
          return a ? (
            <button key={id} type="button" aria-pressed={selectedId === id} onClick={() => onSelect(id)}>
              {a.name} — {STATE_LABEL[a.state]}
            </button>
          ) : null;
        })}
      </nav>
      {/* Selo da caixa de aprovações sobre a sala do CEO: botão DOM comum (acessível por teclado). */}
      {pendingApprovals !== null && ceo && (
        <button type="button" className={`ceo-badge overlay ${pendingApprovals > 0 ? "on" : ""}`}
          style={{ left: ceo.x, top: ceo.y }} onClick={onOpenApprovals}
          aria-label={`Caixa de aprovações da sala do CEO: ${pendingApprovals} pendentes`}>
          {pendingApprovals > 0 ? `${pendingApprovals} aprovaç${pendingApprovals === 1 ? "ão" : "ões"}` : "Sem pendências"}
        </button>
      )}
    </div>
  );
}
