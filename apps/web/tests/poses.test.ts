import { describe, expect, it } from "vitest";
import { type Pose, poseFor, poseTargets } from "../lib/poses";

const STATES = ["IDLE", "WORKING", "PLANNING", "WAITING_APPROVAL", "BLOCKED", "ERROR", "COMPLETED"];
const vec = (p: Pose, k: number) => { const x = poseTargets(p, 1.0, k); return [x.headX, x.armLX, x.armRX, x.armLZ, x.armRZ]; };
const dist = (a: number[], b: number[]) => Math.sqrt(a.reduce((s, v, i) => s + (v - b[i]!) ** 2, 0));

describe("poses do escritório 3D", () => {
  it("cada estado do backend tem a sua pose", () => {
    expect(STATES.map(poseFor)).toEqual(["idle", "working", "planning", "awaiting", "blocked", "error", "done"]);
    expect(poseFor("ESTADO_DESCONHECIDO")).toBe("idle");
  });
  for (const k of [1, 0]) {
    it(`os 7 estados são visualmente distintos ${k ? "com animação" : "com movimento reduzido"}`, () => {
      const poses = STATES.map(poseFor);
      for (let i = 0; i < poses.length; i++)
        for (let j = i + 1; j < poses.length; j++)
          expect(dist(vec(poses[i]!, k), vec(poses[j]!, k)), `${poses[i]} × ${poses[j]}`).toBeGreaterThan(0.25);
    });
  }
  it("com movimento reduzido a pose é estática (nada oscila)", () => {
    for (const p of STATES.map(poseFor)) expect(poseTargets(p, 0.3, 0)).toEqual(poseTargets(p, 4.7, 0));
  });
});
