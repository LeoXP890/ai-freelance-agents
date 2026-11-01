"use client";
// Retrato 2D autoral (UXD-008/015): gerado da MESMA identidade usada no avatar 3D (lib/agentIdentity).
import { type AgentLook, lookFor } from "@/lib/agentIdentity";

function Hair({ l, back }: { l: AgentLook; back: boolean }) {
  const c = l.hairColor;
  // camada de trás (atrás da cabeça) e da frente (franja/topo)
  switch (l.hair) {
    case "long":
      return back ? <path d="M17 30 Q16 14 32 13 Q48 14 47 30 L48 50 L16 50 Z" fill={c} />
        : <path d="M20 27 Q22 15 32 15 Q43 15 44 27 Q38 20 32 20 Q25 20 20 27 Z" fill={c} />;
    case "bob":
      return back ? <path d="M18 34 Q17 15 32 14 Q47 15 46 34 L46 40 L18 40 Z" fill={c} />
        : <path d="M19 30 Q20 15 32 15 Q44 15 45 30 Q41 22 32 21 Q24 22 19 30 Z" fill={c} />;
    case "bun":
      return back ? <circle cx="32" cy="10" r="7" fill={c} />
        : <path d="M20 27 Q21 15 32 15 Q43 15 44 27 Q40 20 32 19 Q24 20 20 27 Z" fill={c} />;
    case "ponytail":
      return back ? <path d="M42 22 Q54 26 50 42 Q46 34 42 30 Z" fill={c} />
        : <path d="M20 27 Q21 15 32 15 Q43 15 44 26 Q38 19 31 19 Q24 20 20 27 Z" fill={c} />;
    case "curly":
      return back ? null : (
        <g fill={c}>
          {[[21, 22], [25, 16], [32, 13], [39, 16], [43, 22], [20, 29], [44, 29]].map(([x, y]) => (
            <circle key={`${x}${y}`} cx={x} cy={y} r="6.2" />
          ))}
        </g>
      );
    case "wavy":
      return back ? <path d="M18 30 Q16 15 32 14 Q48 15 46 30 Q49 40 44 46 L20 46 Q15 40 18 30 Z" fill={c} />
        : <path d="M20 27 Q22 15 32 15 Q43 15 44 26 Q40 19 33 21 Q27 18 20 27 Z" fill={c} />;
    case "beanie":
      return back ? null : (
        <g><path d="M19 25 Q19 11 32 11 Q45 11 45 25 Z" fill={c} /><rect x="18" y="22" width="28" height="5" rx="2.5" fill="#1b2840" /></g>
      );
    case "quiff":
      return back ? null : <path d="M20 26 Q20 16 30 14 Q38 9 44 16 Q45 21 44 26 Q40 19 31 20 Q24 21 20 26 Z" fill={c} />;
    case "bald":
      return back ? null : <path d="M22 37 Q26 46 32 46 Q38 46 42 37 Q40 42 32 42 Q24 42 22 37 Z" fill={c} />; // barba
    default: // short
      return back ? null : <path d="M20 26 Q20 14 32 14 Q44 14 44 26 Q40 19 32 19 Q24 19 20 26 Z" fill={c} />;
  }
}

function Accessory({ l }: { l: AgentLook }) {
  switch (l.accessory) {
    case "glasses":
      return <g fill="none" stroke="#1d2433" strokeWidth="1.6"><rect x="22.5" y="26" width="8" height="5.5" rx="1.5" />
        <rect x="33.5" y="26" width="8" height="5.5" rx="1.5" /><path d="M30.5 28.5 H33.5" /></g>;
    case "roundGlasses":
      return <g fill="none" stroke="#1d2433" strokeWidth="1.6"><circle cx="27" cy="28.5" r="3.6" /><circle cx="37" cy="28.5" r="3.6" />
        <path d="M30.6 28.5 H33.4" /></g>;
    case "headset":
      return <g fill="none" stroke="#1d2433" strokeWidth="2.2"><path d="M19 29 Q19 13 32 13 Q45 13 45 29" />
        <path d="M21 34 Q24 39 29 39" /><rect x="16.5" y="26" width="5" height="8" rx="2" fill="#1d2433" />
        <rect x="42.5" y="26" width="5" height="8" rx="2" fill="#1d2433" /></g>;
    case "headphones":
      return <path d="M20 46 Q20 54 32 54 Q44 54 44 46" fill="none" stroke="#1d2433" strokeWidth="3" />;
    case "cap":
      return <g fill={l.top}><path d="M19 23 Q19 12 32 12 Q45 12 45 23 Z" /><rect x="30" y="20" width="18" height="4" rx="2" /></g>;
    case "beret":
      return <ellipse cx="30" cy="15" rx="14" ry="5.5" fill="#2f2440" />;
    case "tie":
      return <path d="M32 47 L29.5 51 L32 61 L34.5 51 Z" fill="#b7a6ff" />;
    case "scarf":
      return <path d="M22 46 Q32 51 42 46 L42 50 Q32 55 22 50 Z" fill="#f4c47f" />;
    case "apron":
      return <path d="M25 50 H39 V64 H25 Z" fill="#e9fbf4" opacity="0.9" />;
    case "vest":
      return <g><path d="M22 50 L28 50 L28 64 L20 64 Z M42 50 L36 50 L36 64 L44 64 Z" fill="#f2a33a" />
        <path d="M21 57 H28 M36 57 H43" stroke="#f1f5f9" strokeWidth="1.6" /></g>;
    default:
      return null;
  }
}

export default function AgentPortrait({ agentId, size = 48, ring }: { agentId: string; size?: number; ring?: string }) {
  const l = lookFor(agentId);
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" role="img" aria-hidden="true" style={{ flex: "none" }}>
      <defs>
        <clipPath id={`clip-${agentId}`}><circle cx="32" cy="32" r="31" /></clipPath>
      </defs>
      <g clipPath={`url(#clip-${agentId})`}>
        <rect width="64" height="64" fill={l.top} opacity="0.22" />
        <rect width="64" height="64" fill="#1b2433" opacity="0.55" />
        <Hair l={l} back />
        <path d="M12 64 Q12 47 32 46 Q52 47 52 64 Z" fill={l.top} />
        <rect x="28.5" y="38" width="7" height="9" rx="3" fill={l.skin} />
        <ellipse cx="32" cy="28" rx="12" ry="13" fill={l.skin} />
        <Hair l={l} back={false} />
        <circle cx="27.5" cy="29" r="1.6" fill="#1d2433" />
        <circle cx="36.5" cy="29" r="1.6" fill="#1d2433" />
        <path d="M28.5 35 Q32 37.5 35.5 35" fill="none" stroke="#1d2433" strokeWidth="1.4" strokeLinecap="round" />
        <circle cx="25" cy="33" r="1.8" fill="#f08b8b" opacity="0.35" />
        <circle cx="39" cy="33" r="1.8" fill="#f08b8b" opacity="0.35" />
        <Accessory l={l} />
      </g>
      <circle cx="32" cy="32" r="31" fill="none" stroke={ring ?? "rgb(255 255 255 / 0.12)"} strokeWidth={ring ? 2.5 : 1} />
    </svg>
  );
}
