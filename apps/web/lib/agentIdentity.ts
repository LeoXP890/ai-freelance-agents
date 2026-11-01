// Character bible em dados (UXD-015): fonte ÚNICA do retrato 2D e do avatar 3D de cada agente.
// Identidade original do AI Commerce — silhueta, cabelo, roupa e acessório distintos por agente.
// Nome, função e sala reais vêm do backend (agent_presence); aqui só a aparência e a linguagem humana.

export type HairStyle = "short" | "bun" | "bob" | "curly" | "ponytail" | "long" | "beanie" | "quiff" | "bald" | "wavy";
export type Accessory = "tie" | "glasses" | "roundGlasses" | "headset" | "cap" | "beret" | "scarf" | "headphones"
  | "apron" | "vest" | "none";
export type Prop = "tablet" | "notebook" | "clipboard" | "laptop" | "box" | "magnifier" | "folder" | "calculator"
  | "phone" | "camera" | "none";

export interface AgentLook {
  id: string;
  department: string;           // linguagem humana (diretório, filtros)
  short: string;                // função em poucas palavras
  hair: HairStyle;
  hairColor: string;
  skin: string;
  top: string;                  // cor principal da roupa (combina com a cor do agente no backend)
  bottom: string;
  accessory: Accessory;
  prop: Prop;
  height: number;               // 0.94–1.06: silhueta reconhecível
  build: number;                // largura do tronco 0.9–1.1
  traits: string;               // personalidade visual (character bible)
}

export const AGENT_LOOKS: Record<string, AgentLook> = {
  atlas: { id: "atlas", department: "Direção", short: "Planeja missões e prioriza o dia", hair: "short",
    hairColor: "#9AA3B5", skin: "#E8C4A0", top: "#4C3D99", bottom: "#2A2F45", accessory: "tie", prop: "tablet",
    height: 1.06, build: 1.05, traits: "Calmo, postura ereta, gravata lilás; o 'capitão' da equipe." },
  luna: { id: "luna", department: "Pesquisa", short: "Pesquisa mercados e tendências", hair: "bun",
    hairColor: "#3B2A26", skin: "#C98E6B", top: "#1E9C91", bottom: "#24364A", accessory: "roundGlasses",
    prop: "magnifier", height: 0.98, build: 0.95, traits: "Curiosa; coque alto, óculos redondos e lupa." },
  vera: { id: "vera", department: "Pesquisa", short: "Garante qualidade e conversão", hair: "bob",
    hairColor: "#E7C766", skin: "#F1D2B6", top: "#D9A93C", bottom: "#3A3A4E", accessory: "none", prop: "clipboard",
    height: 0.96, build: 0.95, traits: "Meticulosa; corte chanel loiro, prancheta sempre à mão." },
  orion: { id: "orion", department: "Finanças", short: "Analisa dados e experimentos", hair: "short",
    hairColor: "#1F1B1A", skin: "#8D5B3E", top: "#3F5B7A", bottom: "#222833", accessory: "headphones", prop: "laptop",
    height: 1.02, build: 1.0, traits: "Moletom azul-ardósia, fones no pescoço, notebook aberto." },
  nexus: { id: "nexus", department: "Suprimentos", short: "Encontra e avalia fornecedores", hair: "beanie",
    hairColor: "#2C3E66", skin: "#D7A57E", top: "#3A86C8", bottom: "#2B3240", accessory: "none", prop: "box",
    height: 1.0, build: 1.08, traits: "Gorro e jaqueta azul; carrega caixas de amostra." },
  nova: { id: "nova", department: "Loja e canais", short: "Cuida dos anúncios e canais", hair: "ponytail",
    hairColor: "#5B3A29", skin: "#E3B48F", top: "#16B88A", bottom: "#2D3B45", accessory: "apron", prop: "phone",
    height: 0.97, build: 0.95, traits: "Rabo de cavalo e avental verde-menta de vitrine." },
  logan: { id: "logan", department: "Loja e canais", short: "Acompanha envios e prazos", hair: "short",
    hairColor: "#6B4E3D", skin: "#C78D6A", top: "#8C96A3", bottom: "#33373F", accessory: "vest", prop: "clipboard",
    height: 1.04, build: 1.1, traits: "Colete com faixa refletiva e boné; ritmo de expedição." },
  iris: { id: "iris", department: "Marketing", short: "Cria peças e conteúdo", hair: "wavy",
    hairColor: "#A23D5E", skin: "#F0C6A8", top: "#E2558E", bottom: "#2E2A3E", accessory: "beret", prop: "camera",
    height: 0.95, build: 0.92, traits: "Boina, cabelo ondulado vinho, câmera de bolso." },
  pixel: { id: "pixel", department: "Marketing", short: "Gerencia mídia paga", hair: "curly",
    hairColor: "#2A1E1A", skin: "#9C6A4A", top: "#E6A21A", bottom: "#2B2F3A", accessory: "headset", prop: "tablet",
    height: 0.99, build: 1.0, traits: "Cabelo cacheado volumoso e headset amarelo." },
  finn: { id: "finn", department: "Finanças", short: "Controla custos, orçamento e ledger", hair: "bald",
    hairColor: "#4A3426", skin: "#E5B994", top: "#5E9E4C", bottom: "#2E3A2C", accessory: "glasses",
    prop: "calculator", height: 1.01, build: 1.06, traits: "Careca com barba, colete verde e calculadora." },
  clara: { id: "clara", department: "Compliance", short: "Confere regras fiscais e jurídicas", hair: "long",
    hairColor: "#1C1A24", skin: "#D9A07A", top: "#9C6FD6", bottom: "#2B2638", accessory: "glasses", prop: "folder",
    height: 1.0, build: 0.94, traits: "Cabelo longo liso, blazer lilás, pasta de documentos." },
  eva: { id: "eva", department: "Atendimento", short: "Atende clientes e devoluções", hair: "wavy",
    hairColor: "#C46A3A", skin: "#F3CBAF", top: "#F07EA0", bottom: "#33303F", accessory: "headset", prop: "phone",
    height: 0.96, build: 0.95, traits: "Ruiva de cabelo ondulado, headset e sorriso fácil." },
  dante: { id: "dante", department: "Reuniões", short: "Modela o negócio de dropshipping", hair: "quiff",
    hairColor: "#2B211C", skin: "#B47B57", top: "#E07A4A", bottom: "#2C2A33", accessory: "scarf", prop: "notebook",
    height: 1.03, build: 1.0, traits: "Topete, jaqueta laranja e cachecol; vive fazendo contas no caderno." },
};

const FALLBACK: AgentLook = {
  id: "?", department: "Equipe", short: "Agente", hair: "short", hairColor: "#555", skin: "#D9A07A", top: "#6B7A90",
  bottom: "#2B3240", accessory: "none", prop: "none", height: 1, build: 1, traits: "",
};

export function lookFor(agentId: string): AgentLook {
  return AGENT_LOOKS[agentId] ?? { ...FALLBACK, id: agentId };
}

/** Estado do backend → texto humano, tom e ícone (cor nunca sozinha). */
export const STATE_UI: Record<string, { label: string; tone: "ok" | "info" | "warn" | "crit" | "muted"; icon: string }> = {
  IDLE: { label: "Disponível", tone: "muted", icon: "circle" },
  PLANNING: { label: "Planejando", tone: "info", icon: "lightbulb" },
  WORKING: { label: "Trabalhando", tone: "info", icon: "loader" },
  TOOL_CALL: { label: "Usando ferramenta", tone: "info", icon: "wrench" },
  WAITING_APPROVAL: { label: "Aguardando aprovação", tone: "warn", icon: "hand" },
  BLOCKED: { label: "Impedido", tone: "warn", icon: "octagon" },
  COMPLETED: { label: "Concluiu a última tarefa", tone: "ok", icon: "check" },
  ERROR: { label: "Com erro", tone: "crit", icon: "alert" },
};

export function since(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "sem registro";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return "agora";
  if (s < 3600) return `há ${Math.floor(s / 60)} min`;
  if (s < 86400) return `há ${Math.floor(s / 3600)} h`;
  return `há ${Math.floor(s / 86400)} d`;
}
