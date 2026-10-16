import { Link } from "react-router-dom";

import { GlassCard } from "./GlassCard";
import { betsText, formatSpread, starBadges, weatherText, type SlateGame } from "../lib/slate";

const STATUS_LABELS: Record<string, string> = {
  in_progress: "Live",
  final: "Final",
  postponed: "Postponed",
  canceled: "Canceled",
};

function kickoff(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
}

function statusText(game: SlateGame): string {
  const label = STATUS_LABELS[game.status];
  if (!label) return kickoff(game.start_time);
  const score = game.home_score !== null && game.away_score !== null
    ? ` · ${game.away.abbr} ${game.away_score}–${game.home_score} ${game.home.abbr}`
    : "";
  return `${label}${score}`;
}

export function GameCard({ game }: { game: SlateGame }) {
  const latest = game.lines.close ?? game.lines.current;
  const opener = game.lines.open;
  return (
    <GlassCard className="slate-card">
      <div className="slate-card-head">
        <h3><Link to={`/game/${game.id}`}>{game.away.abbr} @ {game.home.abbr}</Link></h3>
        <span className="slate-status">{statusText(game)}</span>
      </div>
      <p className="slate-venue">{game.venue ?? "Venue TBD"}{game.neutral ? " · neutral site" : ""}</p>
      {starBadges(game).length > 0 && (
        <p className="slate-stars">
          {starBadges(game).map((badge) => (
            <span key={`${badge.team}-${badge.kind}`} className={`star-badge ${badge.kind}`} title={badge.names.join(", ")}>
              ★ {badge.team} {badge.kind}
            </span>
          ))}
        </p>
      )}
      <dl className="slate-lines">
        <div><dt>Spread</dt><dd>{formatSpread(game, latest?.spread_home)}</dd></div>
        <div><dt>Total</dt><dd>{latest?.total != null ? `O/U ${latest.total}` : "—"}</dd></div>
        <div><dt>Open</dt><dd>{opener ? `${formatSpread(game, opener.spread_home)} · ${opener.total ?? "—"}` : "—"}</dd></div>
      </dl>
      {weatherText(game) && <p className="slate-weather">{weatherText(game)}</p>}
      {game.spread_move ? <p className="slate-move">Home line {game.spread_move > 0 ? "+" : ""}{game.spread_move} since open</p> : null}
      {latest && <p className="slate-source">{latest.source} {game.lines.close ? "close" : "current"} line</p>}
      {game.bets && <p className="slate-bets"><Link to="/bets">{betsText(game.bets)}</Link></p>}
    </GlassCard>
  );
}
