"use client";
// Avatar estilizado "soft-toy" (UXD-016/018). Aparência vem da character bible (lib/agentIdentity);
// pose/animação vem SOMENTE do estado real do backend — nada aleatório troca estado ou inventa tarefa.
import { RoundedBox } from "@react-three/drei";
import { type ThreeEvent, useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import { poseFor, poseTargets } from "@/lib/poses";
import { type Group, MathUtils } from "three";
import { type AgentLook, lookFor } from "@/lib/agentIdentity";
import Baked from "./Baked";

const DARK = "#1d2433";

function Mat({ c, r = 0.72 }: { c: string; r?: number }) {
  return <meshStandardMaterial color={c} roughness={r} metalness={0} />;
}

function HairMesh({ l }: { l: AgentLook }) {
  const c = l.hairColor;
  const cap = (
    <mesh position={[0, 0.06, -0.01]} castShadow>
      <sphereGeometry args={[0.275, 24, 16, 0, Math.PI * 2, 0, Math.PI / 1.9]} />
      <Mat c={c} r={0.85} />
    </mesh>
  );
  switch (l.hair) {
    case "bun":
      return <group>{cap}<mesh position={[0, 0.34, -0.06]} castShadow><sphereGeometry args={[0.13, 20, 14]} /><Mat c={c} r={0.85} /></mesh></group>;
    case "bob":
      // abertura de ~140° na frente (theta 0 = +z) para o rosto aparecer
      return <group>{cap}<mesh position={[0, -0.04, -0.02]} castShadow><cylinderGeometry args={[0.29, 0.3, 0.26, 24, 1, true, 1.22, Math.PI * 2 - 2.44]} /><meshStandardMaterial color={c} roughness={0.85} side={2} /></mesh></group>;
    case "ponytail":
      return <group>{cap}<mesh position={[0, 0.02, -0.3]} rotation={[0.5, 0, 0]} castShadow><capsuleGeometry args={[0.07, 0.24, 6, 12]} /><Mat c={c} r={0.85} /></mesh></group>;
    case "long":
      return <group>{cap}<RoundedBox args={[0.56, 0.62, 0.16]} radius={0.07} position={[0, -0.18, -0.16]} castShadow><Mat c={c} r={0.85} /></RoundedBox></group>;
    case "wavy":
      return <group>{cap}{[-0.25, 0.25].map((x) => <mesh key={x} position={[x, -0.1, -0.05]} castShadow><sphereGeometry args={[0.12, 16, 12]} /><Mat c={c} r={0.85} /></mesh>)}
        <mesh position={[0, -0.12, -0.2]} castShadow><sphereGeometry args={[0.2, 16, 12]} /><Mat c={c} r={0.85} /></mesh></group>;
    case "curly":
      return <group>{([[0, 0.22, 0], [-0.17, 0.16, 0.04], [0.17, 0.16, 0.04], [-0.24, 0.02, -0.04], [0.24, 0.02, -0.04], [0, 0.14, -0.18], [-0.13, 0.26, -0.08], [0.13, 0.26, -0.08]] as [number, number, number][])
        .map(([x, y, z]) => <mesh key={`${x}${y}${z}`} position={[x, y, z]} castShadow><sphereGeometry args={[0.13, 14, 10]} /><Mat c={c} r={0.9} /></mesh>)}</group>;
    case "beanie":
      return <group><mesh position={[0, 0.08, 0]} castShadow><sphereGeometry args={[0.285, 24, 16, 0, Math.PI * 2, 0, Math.PI / 2]} /><Mat c={c} r={0.9} /></mesh>
        <mesh position={[0, 0.06, 0]} rotation={[Math.PI / 2, 0, 0]}><torusGeometry args={[0.28, 0.04, 10, 28]} /><Mat c="#1b2840" /></mesh></group>;
    case "quiff":
      return <group>{cap}<mesh position={[0, 0.24, 0.12]} rotation={[0.6, 0, 0]} castShadow><sphereGeometry args={[0.15, 16, 12]} /><Mat c={c} r={0.85} /></mesh></group>;
    case "bald":
      return <mesh position={[0, -0.13, 0.11]} castShadow><sphereGeometry args={[0.2, 18, 12, 0, Math.PI * 2, Math.PI / 2, Math.PI / 2]} /><Mat c={c} r={0.9} /></mesh>;
    default:
      return cap;
  }
}

function Face({ l }: { l: AgentLook }) {
  return (
    <group position={[0, -0.01, 0.245]}>
      {[-0.085, 0.085].map((x) => (
        <mesh key={x} position={[x, 0.02, 0]}><sphereGeometry args={[0.028, 12, 10]} /><Mat c={DARK} r={0.3} /></mesh>
      ))}
      {[-0.14, 0.14].map((x) => (
        <mesh key={x} position={[x, -0.05, -0.02]} rotation={[0, x > 0 ? 0.5 : -0.5, 0]}>
          <circleGeometry args={[0.035, 16]} /><meshStandardMaterial color="#f08b8b" transparent opacity={0.45} />
        </mesh>
      ))}
      <mesh position={[0, -0.075, 0.005]} rotation={[0, 0, Math.PI]}><torusGeometry args={[0.035, 0.009, 6, 12, Math.PI]} /><Mat c={DARK} /></mesh>
      {(l.accessory === "glasses" || l.accessory === "roundGlasses") && (
        <group position={[0, 0.02, 0.02]}>
          {[-0.09, 0.09].map((x) => (
            <mesh key={x} position={[x, 0, 0]}>
              <torusGeometry args={[l.accessory === "roundGlasses" ? 0.06 : 0.055, 0.011, 8, l.accessory === "roundGlasses" ? 24 : 4]} />
              <Mat c={DARK} r={0.4} />
            </mesh>
          ))}
          <mesh><boxGeometry args={[0.06, 0.012, 0.012]} /><Mat c={DARK} /></mesh>
        </group>
      )}
    </group>
  );
}

function Prop({ l }: { l: AgentLook }) {
  switch (l.prop) {
    case "magnifier":
      return <group rotation={[0.3, 0, 0.4]}><mesh position={[0, 0.16, 0]}><torusGeometry args={[0.08, 0.016, 8, 24]} /><Mat c="#3b3f4a" r={0.4} /></mesh>
        <mesh position={[0, 0.16, 0]}><circleGeometry args={[0.07, 20]} /><meshStandardMaterial color="#bfe6ff" transparent opacity={0.45} roughness={0.1} /></mesh>
        <mesh position={[0, 0.03, 0]}><cylinderGeometry args={[0.018, 0.018, 0.14, 8]} /><Mat c="#6b4a2f" /></mesh></group>;
    case "clipboard":
      return <group rotation={[-0.4, 0, 0]}><RoundedBox args={[0.2, 0.27, 0.02]} radius={0.01}><Mat c="#8a5a35" /></RoundedBox>
        <mesh position={[0, -0.01, 0.012]}><planeGeometry args={[0.17, 0.21]} /><Mat c="#f6f2e8" /></mesh>
        <mesh position={[0, 0.12, 0.015]}><boxGeometry args={[0.07, 0.03, 0.02]} /><Mat c="#9aa3b5" r={0.3} /></mesh></group>;
    case "tablet":
      return <group rotation={[-0.6, 0, 0]}><RoundedBox args={[0.22, 0.3, 0.02]} radius={0.015}><Mat c="#2a3140" r={0.3} /></RoundedBox>
        <mesh position={[0, 0, 0.011]}><planeGeometry args={[0.19, 0.26]} /><meshStandardMaterial color="#88a9f3" emissive="#3b5aa8" emissiveIntensity={0.4} /></mesh></group>;
    case "laptop": // aberto apoiado no antebraço
      return <group rotation={[-1.1, 0, 0]} position={[0, 0.05, 0.04]}><RoundedBox args={[0.3, 0.02, 0.22]} radius={0.006}><Mat c="#c3cad6" r={0.35} /></RoundedBox>
        <group position={[0, 0.01, -0.11]} rotation={[-1.2, 0, 0]}><RoundedBox args={[0.3, 0.2, 0.012]} radius={0.006} position={[0, 0.1, 0]}><Mat c="#c3cad6" r={0.35} /></RoundedBox>
          <mesh position={[0, 0.1, 0.007]}><planeGeometry args={[0.27, 0.17]} /><meshStandardMaterial color="#88a9f3" emissive="#3b5aa8" emissiveIntensity={0.4} /></mesh></group></group>;
    case "box":
      return <group position={[0, 0.06, 0.04]}><RoundedBox args={[0.26, 0.2, 0.22]} radius={0.015}><Mat c="#c99e72" /></RoundedBox>
        <mesh position={[0, 0.101, 0]}><boxGeometry args={[0.05, 0.004, 0.222]} /><Mat c="#a87c52" /></mesh></group>;
    case "phone":
      return <group rotation={[-0.7, 0, 0]} position={[0, 0.06, 0]}><RoundedBox args={[0.08, 0.15, 0.015]} radius={0.01}><Mat c="#1d2433" r={0.3} /></RoundedBox>
        <mesh position={[0, 0, 0.009]}><planeGeometry args={[0.068, 0.13]} /><meshStandardMaterial color="#7dd3b0" emissive="#2f8a6c" emissiveIntensity={0.5} /></mesh></group>;
    case "camera":
      return <group position={[0, 0.06, 0.04]}><RoundedBox args={[0.2, 0.13, 0.1]} radius={0.02}><Mat c="#2a2f3a" r={0.45} /></RoundedBox>
        <mesh position={[0, 0, 0.07]} rotation={[Math.PI / 2, 0, 0]}><cylinderGeometry args={[0.045, 0.05, 0.07, 16]} /><Mat c="#3b4352" r={0.3} /></mesh>
        <mesh position={[0.06, 0.075, 0]}><boxGeometry args={[0.04, 0.02, 0.04]} /><Mat c="#f08b8b" /></mesh></group>;
    case "folder":
      return <group rotation={[-0.3, 0, 0]}><RoundedBox args={[0.22, 0.28, 0.03]} radius={0.008}><Mat c="#b7a6ff" /></RoundedBox>
        <mesh position={[0, 0.06, 0.017]}><planeGeometry args={[0.12, 0.05]} /><Mat c="#f6f2e8" /></mesh></group>;
    case "notebook":
      return <group rotation={[-0.5, 0, 0]}><RoundedBox args={[0.18, 0.24, 0.025]} radius={0.008}><Mat c="#e07a5f" /></RoundedBox>
        <mesh position={[-0.085, 0, 0.0]}><boxGeometry args={[0.012, 0.24, 0.03]} /><Mat c="#3b4352" /></mesh></group>;
    case "calculator":
      return <group rotation={[-0.6, 0, 0]}><RoundedBox args={[0.13, 0.19, 0.02]} radius={0.01}><Mat c="#2f3a4a" r={0.4} /></RoundedBox>
        <mesh position={[0, 0.055, 0.011]}><planeGeometry args={[0.1, 0.04]} /><meshStandardMaterial color="#cfe8c8" emissive="#6a8a5a" emissiveIntensity={0.3} /></mesh>
        {[-0.03, 0, 0.03].map((x) => [-0.01, -0.04, -0.07].map((y) => <mesh key={`${x}${y}`} position={[x, y, 0.012]}><planeGeometry args={[0.022, 0.02]} /><Mat c="#9aa3b5" /></mesh>))}</group>;
    default:
      return null;
  }
}

function Outfit({ l, w }: { l: AgentLook; w: number }) {
  switch (l.accessory) {
    case "tie": return <mesh position={[0, 0.66, 0.2 * w]} rotation={[0.1, 0, 0]}><boxGeometry args={[0.06, 0.22, 0.02]} /><Mat c="#b7a6ff" /></mesh>;
    case "apron": return <mesh position={[0, 0.55, 0.205 * w]}><planeGeometry args={[0.3, 0.36]} /><Mat c="#eafbf4" /></mesh>;
    case "vest": return <group>{[-0.12, 0.12].map((x) => <mesh key={x} position={[x * w, 0.6, 0.19 * w]}><boxGeometry args={[0.11, 0.34, 0.03]} /><Mat c="#f2a33a" /></mesh>)}</group>;
    case "scarf": return <mesh position={[0, 0.84, 0]} rotation={[Math.PI / 2, 0, 0]}><torusGeometry args={[0.17, 0.05, 10, 24]} /><Mat c="#f4c47f" /></mesh>;
    case "headphones": return <mesh position={[0, 0.86, 0.03]} rotation={[Math.PI / 2.2, 0, 0]}><torusGeometry args={[0.17, 0.025, 8, 24, Math.PI]} /><Mat c={DARK} /></mesh>;
    default: return null;
  }
}

export { type Pose, poseFor, poseTargets } from "@/lib/poses";

export default function Character({ agentId, state, position, rotationY = 0, scale = 1, selected, hovered, reducedMotion,
  celebrateKey, onSelect, onHover }: {
  agentId: string; state: string; position: [number, number, number]; rotationY?: number; scale?: number; selected: boolean;
  hovered: boolean; reducedMotion: boolean; celebrateKey: number; onSelect(id: string): void; onHover(id: string | null): void;
}) {
  const l = useMemo(() => lookFor(agentId), [agentId]);
  const root = useRef<Group>(null);
  const torso = useRef<Group>(null);
  const head = useRef<Group>(null);
  const armL = useRef<Group>(null);
  const armR = useRef<Group>(null);
  const celebrate = useRef({ key: celebrateKey, t: -1 });
  const pose = poseFor(state);
  const w = l.build;
  const invalidate = useThree((s3) => s3.invalidate);
  useEffect(() => { invalidate(); }, [pose, invalidate]); // modo sob demanda: desenha a pose nova

  useFrame(({ clock }, dt) => {
    const t = clock.getElapsedTime();
    if (celebrate.current.key !== celebrateKey) celebrate.current = { key: celebrateKey, t: 0 };
    if (celebrate.current.t >= 0) celebrate.current.t += dt;
    const hop = celebrate.current.t >= 0 && celebrate.current.t < 0.9 && !reducedMotion
      ? Math.sin((celebrate.current.t / 0.9) * Math.PI) * 0.18 : 0;
    const k = reducedMotion ? 0 : 1;
    // movimento reduzido: a cena só desenha sob demanda — a pose vai direto ao alvo (sem transição que pararia no meio)
    const lerp = (a: number, b: number) => (reducedMotion ? b : MathUtils.damp(a, b, 8, dt));
    const { headX, headY, armLX, armRX, armLZ, armRZ, breathe } = poseTargets(pose, t, k);
    if (root.current) root.current.position.y = position[1] + hop;
    if (torso.current) torso.current.scale.y = 1 + breathe;
    if (head.current) { head.current.rotation.x = lerp(head.current.rotation.x, headX); head.current.rotation.y = lerp(head.current.rotation.y, headY); }
    if (armL.current) { armL.current.rotation.x = lerp(armL.current.rotation.x, armLX); armL.current.rotation.z = lerp(armL.current.rotation.z, armLZ); }
    if (armR.current) { armR.current.rotation.x = lerp(armR.current.rotation.x, armRX); armR.current.rotation.z = lerp(armR.current.rotation.z, armRZ); }
  });

  const s = l.height * scale;
  const handlers = {
    onClick: (e: ThreeEvent<MouseEvent>) => { e.stopPropagation(); onSelect(agentId); },
    onPointerOver: (e: ThreeEvent<PointerEvent>) => { e.stopPropagation(); onHover(agentId); document.body.style.cursor = "pointer"; },
    onPointerOut: () => { onHover(null); document.body.style.cursor = "auto"; },
  };
  return (
    <group ref={root} name={`agent:${agentId}`} userData={{ pose }} position={position} rotation={[0, rotationY, 0]}>
      {/* anel de seleção/hover no chão */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.012, 0]}>
        <ringGeometry args={[0.36 * scale, (selected ? 0.46 : 0.41) * scale, 40]} />
        <meshBasicMaterial color={selected ? "#7dd3b0" : hovered ? "#a9c1ff" : "#000000"} transparent opacity={selected || hovered ? 0.95 : 0.18} />
      </mesh>
      <group scale={[s, s, s]} {...handlers}>
        {/* pernas e sapatos (cada parte rígida vira uma malha só: <Baked>) */}
        <Baked>
          {[-0.1, 0.1].map((x) => (
            <group key={x}>
              <mesh position={[x * w, 0.2, 0]} castShadow><capsuleGeometry args={[0.075, 0.2, 4, 10]} /><Mat c={l.bottom} /></mesh>
              <RoundedBox args={[0.13, 0.08, 0.2]} radius={0.035} smoothness={2} position={[x * w, 0.04, 0.03]} castShadow><Mat c="#2b2f38" /></RoundedBox>
            </group>
          ))}
        </Baked>
        {/* tronco */}
        <group ref={torso} position={[0, 0.36, 0]}>
          <Baked>
            <mesh position={[0, 0.24, 0]} scale={[w, 1, 0.82 * w]} castShadow><capsuleGeometry args={[0.2, 0.26, 6, 16]} /><Mat c={l.top} /></mesh>
            <group position={[0, -0.36, 0]}><Outfit l={l} w={w} /></group>
          </Baked>
          {/* braços (pivô no ombro) */}
          <group ref={armL} name="armL" position={[-0.25 * w, 0.42, 0]}>
            <Baked>
              <mesh position={[0, -0.17, 0]} castShadow><capsuleGeometry args={[0.06, 0.22, 4, 10]} /><Mat c={l.top} /></mesh>
              <mesh position={[0, -0.33, 0]}><sphereGeometry args={[0.06, 10, 8]} /><Mat c={l.skin} /></mesh>
            </Baked>
          </group>
          <group ref={armR} name="armR" position={[0.25 * w, 0.42, 0]}>
            <Baked>
              <mesh position={[0, -0.17, 0]} castShadow><capsuleGeometry args={[0.06, 0.22, 4, 10]} /><Mat c={l.top} /></mesh>
              <mesh position={[0, -0.33, 0]}><sphereGeometry args={[0.06, 10, 8]} /><Mat c={l.skin} /></mesh>
              <group position={[0.02, -0.38, 0.06]}><Prop l={l} /></group>
            </Baked>
          </group>
          {/* cabeça */}
          <group ref={head} name="head" position={[0, 0.78, 0]}>
            <Baked>
            <mesh castShadow><sphereGeometry args={[0.26, 24, 18]} /><Mat c={l.skin} r={0.65} /></mesh>
            {[-0.255, 0.255].map((x) => <mesh key={x} position={[x, -0.02, 0]}><sphereGeometry args={[0.045, 10, 8]} /><Mat c={l.skin} /></mesh>)}
            <HairMesh l={l} />
            <Face l={l} />
            {l.accessory === "headset" && (
              <group>
                <mesh rotation={[0, 0, 0]}><torusGeometry args={[0.285, 0.018, 8, 28, Math.PI]} /><Mat c={DARK} /></mesh>
                {[-0.28, 0.28].map((x) => <mesh key={x} position={[x, 0, 0]}><cylinderGeometry args={[0.07, 0.07, 0.05, 16]} /><Mat c={DARK} /></mesh>)}
              </group>
            )}
            {l.accessory === "cap" && <group><mesh position={[0, 0.1, 0]}><sphereGeometry args={[0.28, 22, 14, 0, Math.PI * 2, 0, Math.PI / 2.2]} /><Mat c="#2f5d8a" /></mesh>
              <mesh position={[0, 0.12, 0.22]} rotation={[0.25, 0, 0]}><cylinderGeometry args={[0.16, 0.16, 0.025, 20, 1, false, -Math.PI / 2, Math.PI]} /><Mat c="#2f5d8a" /></mesh></group>}
            {l.accessory === "beret" && <mesh position={[-0.04, 0.24, 0]} rotation={[0, 0, 0.25]}><cylinderGeometry args={[0.24, 0.25, 0.07, 24]} /><Mat c="#2f2440" /></mesh>}
            </Baked>
          </group>
        </group>
      </group>
    </group>
  );
}
