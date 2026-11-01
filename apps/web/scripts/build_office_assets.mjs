// Gera os modelos-base do escritório em GLB (OFF-001). Placeholders procedurais de baixa contagem de polígonos:
// um designer pode substituir cada arquivo por arte do Blender com o MESMO nome, escala (metros) e origem (chão).
// Uso: node scripts/build_office_assets.mjs  →  public/assets/office/*.glb + manifest.json
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import * as THREE from "three";
import { GLTFExporter } from "three/examples/jsm/exporters/GLTFExporter.js";

// GLTFExporter usa FileReader (API de navegador); o Node tem Blob.arrayBuffer().
globalThis.FileReader ??= class {
  readAsArrayBuffer(blob) { blob.arrayBuffer().then((b) => { this.result = b; this.onloadend?.(); }); }
  readAsDataURL(blob) {
    blob.arrayBuffer().then((b) => {
      this.result = `data:${blob.type || "application/octet-stream"};base64,${Buffer.from(b).toString("base64")}`;
      this.onloadend?.();
    });
  }
};

const OUT = new URL("../public/assets/office/", import.meta.url);
const MAX_TRIANGLES = 2_000;
const mat = (color, extra = {}) => new THREE.MeshStandardMaterial({ color, roughness: 0.7, ...extra });
const box = (w, h, d, m, x = 0, y = 0, z = 0, name) => {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), m);
  mesh.position.set(x, y, z);
  mesh.name = name ?? "";
  return mesh;
};
const cyl = (rt, rb, h, m, x = 0, y = 0, z = 0, seg = 12) => {
  const mesh = new THREE.Mesh(new THREE.CylinderGeometry(rt, rb, h, seg), m);
  mesh.position.set(x, y, z);
  return mesh;
};

function desk() {
  const g = new THREE.Group(); g.name = "desk";
  const wood = mat("#3b4a6e"), leg = mat("#26304b");
  g.add(box(1.3, 0.06, 0.7, wood, 0, 0.72, 0, "top"));
  for (const [x, z] of [[-0.6, -0.3], [0.6, -0.3], [-0.6, 0.3], [0.6, 0.3]]) g.add(box(0.05, 0.72, 0.05, leg, x, 0.36, z));
  return g;
}
function chair() {
  const g = new THREE.Group(); g.name = "chair";
  const fabric = mat("#2c3858"), metal = mat("#9aa4b2", { metalness: 0.6, roughness: 0.4 });
  g.add(box(0.46, 0.08, 0.46, fabric, 0, 0.46, 0, "seat"));
  g.add(box(0.46, 0.5, 0.06, fabric, 0, 0.75, -0.2, "back"));
  g.add(cyl(0.03, 0.03, 0.42, metal, 0, 0.21, 0));
  g.add(cyl(0.25, 0.25, 0.03, metal, 0, 0.02, 0, 10));
  return g;
}
function monitor() {
  const g = new THREE.Group(); g.name = "monitor";
  const dark = mat("#111827"), screen = mat("#4f8be8", { emissive: "#1b3a73", emissiveIntensity: 0.6 });
  g.add(box(0.2, 0.02, 0.14, dark, 0, 0.01, 0));
  g.add(box(0.04, 0.22, 0.03, dark, 0, 0.12, 0));
  g.add(box(0.56, 0.34, 0.03, dark, 0, 0.38, 0, "frame"));
  g.add(box(0.52, 0.30, 0.005, screen, 0, 0.38, 0.016, "screen"));
  return g;
}
function plant() {
  const g = new THREE.Group(); g.name = "plant";
  g.add(cyl(0.16, 0.12, 0.3, mat("#c2703d"), 0, 0.15, 0, 12));
  const leaves = new THREE.Mesh(new THREE.IcosahedronGeometry(0.28, 1), mat("#2f9e6a", { flatShading: true }));
  leaves.position.set(0, 0.55, 0); leaves.name = "leaves";
  g.add(leaves);
  return g;
}
function shelf() {
  const g = new THREE.Group(); g.name = "shelf";
  const wood = mat("#3b4a6e");
  for (const y of [0.02, 0.5, 0.98, 1.46]) g.add(box(0.9, 0.04, 0.3, wood, 0, y, 0));
  for (const x of [-0.43, 0.43]) g.add(box(0.04, 1.5, 0.3, wood, x, 0.75, 0));
  const colors = ["#e5484d", "#f5b14c", "#3dd68c", "#4f8be8", "#a78bfa"];
  colors.forEach((c, i) => g.add(box(0.07, 0.3, 0.22, mat(c), -0.3 + i * 0.13, 0.67, 0)));
  return g;
}

const triangles = (obj) => {
  let n = 0;
  obj.traverse((o) => { if (o.isMesh) n += (o.geometry.index ? o.geometry.index.count : o.geometry.attributes.position.count) / 3; });
  return n;
};

mkdirSync(OUT, { recursive: true });
const manifest = { generator: "scripts/build_office_assets.mjs", units: "metros, origem no chão", assets: {} };
for (const make of [desk, chair, monitor, plant, shelf]) {
  const obj = make();
  const tris = triangles(obj);
  if (tris > MAX_TRIANGLES) throw new Error(`${obj.name}: ${tris} triângulos > orçamento ${MAX_TRIANGLES}`);
  const glb = Buffer.from(await new GLTFExporter().parseAsync(obj, { binary: true }));
  writeFileSync(new URL(`${obj.name}.glb`, OUT), glb);
  manifest.assets[obj.name] = {
    file: `${obj.name}.glb`, bytes: glb.length, triangles: tris,
    sha256: createHash("sha256").update(glb).digest("hex"), source: "procedural (substituível por arte do Blender)",
  };
}
writeFileSync(new URL("manifest.json", OUT), JSON.stringify(manifest, null, 2) + "\n");
console.log(Object.entries(manifest.assets).map(([k, v]) => `${k}: ${v.triangles} tri, ${v.bytes} B`).join(" · "));
