// Pose de cada personagem a partir do estado REAL do backend (nada aleatório). Função pura: a cena aplica estes
// alvos aos membros a cada quadro; os testes conferem que todos os estados são distinguíveis — inclusive com
// movimento reduzido (k = 0), quando não há animação para diferenciar.

export type Pose = "idle" | "working" | "planning" | "awaiting" | "blocked" | "error" | "done";

export function poseFor(state: string): Pose {
  switch (state) {
    case "WORKING": case "TOOL_CALL": return "working";
    case "PLANNING": return "planning";
    case "WAITING_APPROVAL": return "awaiting";
    case "BLOCKED": return "blocked";
    case "ERROR": return "error";
    case "COMPLETED": return "done"; // além da pose, a transição para concluído dá um pulinho (uma vez)
    default: return "idle";
  }
}

export interface PoseTargets { headX: number; headY: number; armLX: number; armRX: number; armLZ: number; armRZ: number; breathe: number }

/** Alvos de rotação (radianos) no instante t; k = 1 com animação, 0 com movimento reduzido. */
export function poseTargets(pose: Pose, t: number, k: number): PoseTargets {
  const p: PoseTargets = { headX: 0, headY: 0, armLX: 0, armRX: 0, armLZ: 0.12, armRZ: -0.12, breathe: Math.sin(t * 1.6) * 0.012 * k };
  switch (pose) {
    case "working": // digitando: os dois braços à frente, cabeça baixa
      p.armLX = -1.15 + Math.sin(t * 14) * 0.08 * k; p.armRX = -1.15 + Math.sin(t * 14 + 1.4) * 0.08 * k; p.headX = 0.25; break;
    case "planning": // mão no queixo, cabeça inclinada
      p.armRX = -2.1; p.armRZ = 0.45; p.headX = 0.05; p.headY = Math.sin(t * 0.6) * 0.25 * k; break;
    case "awaiting": { // segura o pedido levantado e olha para cima; com animação, acena a cada 6 s
      const ph = (t % 6) / 6; const g = ph < 0.18 ? Math.sin((ph / 0.18) * Math.PI) : 0;
      p.armRX = -0.9 - 1.7 * g * k; p.armRZ = -0.2 - 0.3 * g * k; p.headX = -0.18; break;
    }
    case "blocked": // cabisbaixo, braços colados, quase sem respirar
      p.headX = 0.4; p.armLX = 0.08; p.armRX = 0.08; p.armLZ = 0.04; p.armRZ = -0.04; p.breathe *= 0.3; break;
    case "error": // mãos na cabeça
      p.headX = 0.15; p.armLX = -2.5; p.armRX = -2.5; p.armLZ = 0.55; p.armRZ = -0.55; break;
    case "done": // satisfeito: cabeça erguida, braço aberto
      p.headX = -0.12; p.armRX = -0.35; p.armRZ = -0.55; p.armLZ = 0.3; break;
    default: // parado: olha em volta devagar
      p.headY = Math.sin(t * 0.45) * 0.18 * k; p.armLX = Math.sin(t * 1.6) * 0.03 * k; p.armRX = -Math.sin(t * 1.6) * 0.03 * k;
  }
  return p;
}
