import type { ContextFlag } from "./board";
import type { SlateGame } from "./slate";
import { formatSpread, weatherText } from "./slate";

/** A point and a half of total or a full point of spread is the smallest move worth a flag. */
export const SPREAD_MOVE_FLAG = 1;
export const TOTAL_MOVE_FLAG = 1.5;
export const WIND_FLAG_MPH = 15;
export const PRECIP_FLAG_PCT = 50;

export interface Flag { key: string; kind: "qb" | "move" | "weather" | "rest"; text: string }

export function moveFlag(game: Pick<SlateGame, "home" | "away" | "spread_move" | "total_move" | "lines">): Flag | null {
  const spread = game.spread_move;
  const total = game.total_move;
  const latest = game.lines.close ?? game.lines.current;
  if (spread !== null && Math.abs(spread) >= SPREAD_MOVE_FLAG) {
    const now = latest ? `, now ${formatSpread(game, latest.spread_home)}` : "";
    return { key: "move", kind: "move", text: `Spread moved ${Math.abs(spread)} point${Math.abs(spread) === 1 ? "" : "s"} since open${now}` };
  }
  if (total !== null && Math.abs(total) >= TOTAL_MOVE_FLAG) {
    return { key: "move", kind: "move", text: `Total moved ${Math.abs(total)} points since open${latest?.total != null ? `, now ${latest.total}` : ""}` };
  }
  return null;
}

export function weatherFlag(game: Pick<SlateGame, "roof" | "weather"> & { sport?: string }): Flag | null {
  const weather = game.weather;
  if (!weather || game.roof === "dome") return null;
  const windy = (weather.wind_mph ?? 0) >= WIND_FLAG_MPH;
  const wet = (weather.precip_pct ?? 0) >= PRECIP_FLAG_PCT;
  const text = weatherText(game);
  return (windy || wet) && text ? { key: "weather", kind: "weather", text } : null;
}

/** At most three facts for a collapsed game, in order of relevance. Never a recommendation. */
export function collapsedFlags(game: SlateGame, flags: ContextFlag[]): Flag[] {
  const qb = flags.filter((flag) => flag.kind === "qb").map((flag): Flag => ({ key: `qb-${flag.team}`, kind: "qb", text: flag.text }));
  const rest = flags.filter((flag) => flag.kind !== "qb").map((flag): Flag => ({ key: `${flag.kind}-${flag.team}`, kind: "rest", text: flag.text }));
  return [...qb, moveFlag(game), weatherFlag(game), ...rest].filter((flag): flag is Flag => flag !== null).slice(0, 3);
}
