// Sincronização snapshot + offset (SDD §13): conecta → baixa snapshot → WS com after_seq=as_of_seq.
// Em queda: backoff exponencial, novo snapshot e retomada pelo offset. Após falhas repetidas de WS,
// cai para polling HTTP com a mesma semântica de cursor e tenta voltar ao WS periodicamente.
// Nunca inventa estado local: tudo que a cena mostra veio do backend.
import { api, wsUrl } from "./api";
import type { OfficeEventV1, PresenceSnapshotV1 } from "./contract";

export type Transport = "connecting" | "websocket" | "polling" | "offline";

export interface SyncHandlers {
  onSnapshot(s: PresenceSnapshotV1): void;
  onEvent(e: OfficeEventV1): void;
  onTransport(t: Transport, detail?: string): void;
  getLastSeq(): number;
}

export const WS_FAILURES_BEFORE_POLLING = 3;
export const POLL_INTERVAL_MS = 1500;
export const POLLS_BEFORE_WS_RETRY = 20;

export function backoffMs(attempt: number): number {
  return Math.min(30_000, 500 * 2 ** Math.min(attempt, 6));
}

export function startOfficeSync(token: string, h: SyncHandlers): () => void {
  let stopped = false;
  let ws: WebSocket | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let errors = 0; // falhas HTTP consecutivas (backoff)
  let wsFailures = 0; // quedas de WS sem chegar a "ready"
  let polls = 0;

  const schedule = (fn: () => void, ms: number) => {
    if (!stopped) timer = setTimeout(fn, ms);
  };

  const poll = async () => {
    try {
      const r = await api.eventsAfter(token, h.getLastSeq());
      r.items.forEach(h.onEvent);
      errors = 0;
      h.onTransport("polling");
      polls += 1;
      if (polls % POLLS_BEFORE_WS_RETRY === 0) {
        wsFailures = 0;
        schedule(connect, 0);
      } else {
        schedule(poll, POLL_INTERVAL_MS);
      }
    } catch (e) {
      h.onTransport("offline", String(e));
      schedule(poll, backoffMs(errors++));
    }
  };

  const connect = async () => {
    h.onTransport("connecting");
    try {
      h.onSnapshot(await api.snapshot(token));
      errors = 0;
    } catch (e) {
      h.onTransport("offline", String(e));
      schedule(connect, backoffMs(errors++));
      return;
    }
    if (typeof WebSocket === "undefined" || wsFailures >= WS_FAILURES_BEFORE_POLLING) {
      void poll();
      return;
    }
    let readied = false;
    ws = new WebSocket(wsUrl());
    ws.onopen = () => ws?.send(JSON.stringify({ type: "auth", token, after_seq: h.getLastSeq() }));
    ws.onmessage = (m) => {
      const msg = JSON.parse(String(m.data));
      if (msg.type === "ready") {
        readied = true;
        wsFailures = 0;
        h.onTransport("websocket");
      } else if (msg.type === "event") {
        h.onEvent(msg.event as OfficeEventV1);
      }
    };
    ws.onclose = (ev) => {
      ws = null;
      if (stopped) return;
      if (ev.code === 4401) {
        h.onTransport("offline", "sessão expirada — entre novamente");
        return;
      }
      if (!readied) wsFailures += 1;
      h.onTransport("connecting", "reconectando");
      schedule(connect, backoffMs(wsFailures));
    };
  };

  void connect();
  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    ws?.close();
  };
}
