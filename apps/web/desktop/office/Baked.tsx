"use client";
// "Assa" geometria estática (UXD-020): os filhos são escritos como JSX legível (caixas, cilindros, plantas…) e,
// depois de montados, viram poucas malhas — uma por tipo de material (opaco, emissivo, transparente) com a cor de
// cada peça gravada por vértice. A sede inteira cai de milhares para dezenas de draw calls, sem perder o desenho.
// Os originais ficam invisíveis (seguem recebendo cliques via o grupo pai, como antes).
import { type ReactNode, useLayoutEffect, useRef } from "react";
import { BufferAttribute, type BufferGeometry, Color, DoubleSide, type Group, Matrix4, Mesh, MeshStandardMaterial } from "three";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";

interface Bucket { geos: BufferGeometry[]; mat: MeshStandardMaterial; cast: boolean; receive: boolean }

function keyOf(m: MeshStandardMaterial): string {
  const e = m.emissiveIntensity > 0 ? `${m.emissive.getHexString()}:${m.emissiveIntensity.toFixed(2)}` : "-";
  return [m.transparent ? `t${m.opacity.toFixed(2)}` : "o", e, m.side, m.roughness.toFixed(1), m.metalness.toFixed(1), m.flatShading ? "f" : "s"].join("|");
}

export function bake(root: Group): { meshes: Mesh[]; hidden: Mesh[] } {
  root.updateWorldMatrix(true, true);
  const inv = new Matrix4().copy(root.matrixWorld).invert();
  const buckets = new Map<string, Bucket>();
  const hidden: Mesh[] = [];
  const m4 = new Matrix4();
  root.traverse((o) => {
    const mesh = o as Mesh;
    if (!mesh.isMesh || o.userData.noBake) return;
    const mat = mesh.material as MeshStandardMaterial;
    if (Array.isArray(mat) || !(mat instanceof MeshStandardMaterial) || mat.map) return;
    let g = mesh.geometry.index ? mesh.geometry.toNonIndexed() : mesh.geometry.clone();
    for (const name of Object.keys(g.attributes)) if (name !== "position" && name !== "normal") g.deleteAttribute(name);
    if (!g.attributes.normal) g.computeVertexNormals();
    g.applyMatrix4(m4.multiplyMatrices(inv, mesh.matrixWorld));
    const n = g.attributes.position!.count;
    const col = new Float32Array(n * 3);
    const c = new Color().copy(mat.color);
    for (let i = 0; i < n; i++) { col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b; }
    g.setAttribute("color", new BufferAttribute(col, 3));
    const k = keyOf(mat);
    let b = buckets.get(k);
    if (!b) {
      b = { geos: [], cast: false, receive: false, mat: new MeshStandardMaterial({
        vertexColors: true, roughness: mat.roughness, metalness: mat.metalness, transparent: mat.transparent,
        opacity: mat.opacity, side: mat.side === DoubleSide ? DoubleSide : mat.side, emissive: mat.emissive.clone(),
        emissiveIntensity: mat.emissiveIntensity, depthWrite: !mat.transparent, flatShading: mat.flatShading,
      }) };
      buckets.set(k, b);
    }
    b.geos.push(g);
    b.cast ||= mesh.castShadow;
    b.receive ||= mesh.receiveShadow;
    hidden.push(mesh);
  });
  const meshes: Mesh[] = [];
  for (const b of buckets.values()) {
    const merged = mergeGeometries(b.geos, false);
    b.geos.forEach((g) => g.dispose());
    if (!merged) continue;
    merged.computeBoundingSphere();
    const m = new Mesh(merged, b.mat);
    m.castShadow = b.cast; m.receiveShadow = b.receive;
    m.userData.baked = true;
    meshes.push(m);
  }
  return { meshes, hidden };
}

/** Envolve conteúdo estático (relativo a este grupo). Mudanças nos filhos depois de montado não são refletidas. */
export default function Baked({ children, enabled = true }: { children: ReactNode; enabled?: boolean }) {
  const ref = useRef<Group>(null);
  useLayoutEffect(() => {
    const root = ref.current;
    if (!root || !enabled) return;
    const { meshes, hidden } = bake(root);
    hidden.forEach((h) => { h.visible = false; });
    meshes.forEach((m) => root.add(m));
    return () => {
      meshes.forEach((m) => { root.remove(m); m.geometry.dispose(); (m.material as MeshStandardMaterial).dispose(); });
      hidden.forEach((h) => { h.visible = true; });
    };
  }, [enabled]);
  return <group ref={ref}>{children}</group>;
}
