"use client";
// Modo 2D equivalente (SDD §5, §23.2): mesma informação da cena 3D, navegável por teclado e leitor de tela,
// sem exigir GPU.
import { ROOMS, STATE_COLOR, STATE_LABEL } from "@/lib/contract";
import { agentsInRoom, type OfficeState } from "@/lib/officeStore";

export default function Office2D({ office, selectedId, onSelect, pendingApprovals, onOpenApprovals }: {
  office: OfficeState; selectedId: string | null; onSelect(id: string): void; pendingApprovals: number | null;
  onOpenApprovals(): void;
}) {
  return (
    <div className="grid2d" role="list" aria-label="Salas do escritório">
      {ROOMS.map((room) => {
        const agents = agentsInRoom(office, room.id);
        return (
          <section key={room.id} className="room2d" role="listitem" aria-labelledby={`room-${room.id}`}>
            <h3 id={`room-${room.id}`}>{room.label}</h3>
            {room.id === "ceo" && pendingApprovals !== null && (
              <button type="button" className={`ceo-badge ${pendingApprovals > 0 ? "on" : ""}`} onClick={onOpenApprovals}>
                Caixa de aprovações: {pendingApprovals} pendente{pendingApprovals === 1 ? "" : "s"}
              </button>
            )}
            {agents.length === 0 && <p className="muted small">Sem agentes alocados</p>}
            <ul>
              {agents.map((a) => (
                <li key={a.agent_id}>
                  <button type="button" className={`agent2d ${selectedId === a.agent_id ? "on" : ""}`}
                    aria-pressed={selectedId === a.agent_id} onClick={() => onSelect(a.agent_id)}>
                    <span className="dot" style={{ background: a.color }} aria-hidden="true" />
                    <span className="name">{a.name}</span>
                    <span className="state" style={{ color: STATE_COLOR[a.state] }}>{STATE_LABEL[a.state]}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
