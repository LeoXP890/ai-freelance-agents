import type { Routine } from "./api";

/** Agenda da rotina em português (horário de Brasília). */
export function when(r: Pick<Routine, "schedule" | "time_of_day" | "interval_hours">): string {
  return r.schedule === "DAILY" ? `Todo dia às ${r.time_of_day}` : `A cada ${r.interval_hours} horas`;
}
