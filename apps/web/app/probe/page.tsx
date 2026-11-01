"use client";
// Diagnóstico de WebGL no runtime desktop (ADR-DESK-001). Mesma pilha do escritório (three + R3F).
// Mede suporte, GPU, FPS e draw calls; com ?report=<url> envia o resultado (inclui um quadro em PNG).
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { useEffect, useRef, useState } from "react";
import type { Mesh } from "three";

type Result = Record<string, unknown>;

function Spinner({ i }: { i: number }) {
  const ref = useRef<Mesh>(null);
  useFrame((_, dt) => { if (ref.current) ref.current.rotation.y += dt * (0.5 + i * 0.1); });
  return (
    <mesh ref={ref} position={[(i % 8) - 3.5, 0.5, Math.floor(i / 8) - 3.5]} castShadow>
      <boxGeometry args={[0.6, 0.6, 0.6]} />
      <meshStandardMaterial color={`hsl(${(i * 37) % 360}, 55%, 60%)`} />
    </mesh>
  );
}

function Probe({ onDone }: { onDone(r: Result): void }) {
  const gl = useThree((s) => s.gl);
  const frames = useRef(0);
  const start = useRef(performance.now());
  const done = useRef(false);
  useFrame(() => {
    frames.current++;
    const el = performance.now() - start.current;
    if (!done.current && el > 4000) {
      done.current = true;
      const ctx = gl.getContext();
      const dbg = ctx.getExtension("WEBGL_debug_renderer_info");
      onDone({
        webgl2: typeof WebGL2RenderingContext !== "undefined" && ctx instanceof WebGL2RenderingContext,
        renderer: dbg ? ctx.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : ctx.getParameter(ctx.RENDERER),
        vendor: dbg ? ctx.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : ctx.getParameter(ctx.VENDOR),
        max_texture: ctx.getParameter(ctx.MAX_TEXTURE_SIZE),
        fps: Math.round((frames.current / (el / 1000)) * 10) / 10,
        draw_calls: gl.info.render.calls,
        triangles: gl.info.render.triangles,
        dpr: window.devicePixelRatio,
        viewport: [window.innerWidth, window.innerHeight],
        user_agent: navigator.userAgent,
        frame_png: gl.domElement.toDataURL("image/png"),
      });
    }
  });
  return null;
}

export default function ProbePage() {
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!result) return;
    const target = new URLSearchParams(window.location.search).get("report");
    if (target) fetch(target, { method: "POST", body: JSON.stringify(result) }).catch((e) => setError(String(e)));
  }, [result]);
  return (
    <main style={{ height: "100vh", background: "#10151D", color: "#F1F5F9", fontFamily: "system-ui" }}>
      <div style={{ position: "absolute", zIndex: 1, padding: 12 }}>
        <strong>Diagnóstico WebGL</strong>{" "}
        {result ? `· ${result.renderer} · ${result.fps} FPS · ${result.draw_calls} draw calls` : "· medindo…"}
        {error && <div role="alert">{error}</div>}
      </div>
      <Canvas shadows dpr={[1, 2]} camera={{ position: [6, 6, 6], fov: 45 }} gl={{ preserveDrawingBuffer: true }}
        onCreated={({ camera }) => camera.lookAt(0, 0, 0)}>
        <color attach="background" args={["#10151D"]} />
        <hemisphereLight args={["#fff4e0", "#20304a", 0.7]} />
        <directionalLight position={[5, 8, 4]} intensity={1.4} castShadow />
        <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
          <planeGeometry args={[10, 10]} />
          <meshStandardMaterial color="#2a3a52" />
        </mesh>
        {Array.from({ length: 64 }, (_, i) => <Spinner key={i} i={i} />)}
        <Probe onDone={setResult} />
      </Canvas>
    </main>
  );
}
