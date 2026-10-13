import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import { GlassCard } from "../components/GlassCard";
import { fetchSlate, formatSpread, weatherText, type SlateGame, type WeekRef } from "../lib/slate";

const STATUS_LABELS: Record<string, string> = {
  in_progress: "Live",
  final: "Final",
  postponed: "Postponed",
  canceled: "Canceled",
};

function weekLabel(ref: WeekRef): string {
  if (ref.season_type === "POST") return `Playoffs round ${ref.week}`;
  return ref.season_type === "PRE" ? `Preseason week ${ref.week}` : `Week ${ref.week}`;
}

function weekPath(season: number, ref: WeekRef): string {
  return `/nfl/${season}/${ref.season_type.toLowerCase()}/${ref.week}`;
}

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

function GameCard({ game }: { game: SlateGame }) {
  const latest = game.lines.close ?? game.lines.current;
  const opener = game.lines.open;
  return (
    <GlassCard className="slate-card">
      <div className="slate-card-head">
        <h3><Link to={`/game/${game.id}`}>{game.away.abbr} @ {game.home.abbr}</Link></h3>
        <span className="slate-status">{statusText(game)}</span>
      </div>
      <p className="slate-venue">{game.venue ?? "Venue TBD"}{game.neutral ? " · neutral site" : ""}</p>
      <dl className="slate-lines">
        <div><dt>Spread</dt><dd>{formatSpread(game, latest?.spread_home)}</dd></div>
        <div><dt>Total</dt><dd>{latest?.total != null ? `O/U ${latest.total}` : "—"}</dd></div>
        <div><dt>Open</dt><dd>{opener ? `${formatSpread(game, opener.spread_home)} · ${opener.total ?? "—"}` : "—"}</dd></div>
      </dl>
      {weatherText(game) && <p className="slate-weather">{weatherText(game)}</p>}
      {game.spread_move ? <p className="slate-move">Home line {game.spread_move > 0 ? "+" : ""}{game.spread_move} since open</p> : null}
      {latest && <p className="slate-source">{latest.source} {game.lines.close ? "close" : "current"} line</p>}
    </GlassCard>
  );
}

export function SlatePage() {
  const { season, seasonType, week } = useParams();
  const navigate = useNavigate();
  const { data, error, isLoading } = useQuery({
    queryKey: ["slate", "nfl", season ?? "current", seasonType ?? "", week ?? ""],
    queryFn: ({ signal }) => fetchSlate({ season, seasonType, week, signal }),
  });

  const index = data ? data.weeks.findIndex((ref) => ref.season_type === data.season_type && ref.week === data.week) : -1;
  const previous = data && index > 0 ? data.weeks[index - 1] : undefined;
  const next = data && index >= 0 && index < data.weeks.length - 1 ? data.weeks[index + 1] : undefined;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">NFL slate</p>
          <h1>{data ? `${data.season} · ${weekLabel(data)}` : "Slate"}</h1>
        </div>
        <nav className="app-nav" aria-label="Sections">
          <Link to="/bets">Bets</Link> · <Link to="/odds">Odds board</Link>
        </nav>
      </header>

      <section className="control-band" aria-label="Week picker">
        <button className="text-button" disabled={!previous} onClick={() => data && previous && navigate(weekPath(data.season, previous))} type="button">Previous week</button>
        <button className="text-button" disabled={!next} onClick={() => data && next && navigate(weekPath(data.season, next))} type="button">Next week</button>
      </section>

      {error && <div className="notice error-notice" role="alert">{error instanceof Error ? error.message : "Could not load the slate"}</div>}
      {isLoading && <div className="skeleton-card" aria-label="Loading games"><span /><span /><span /></div>}
      {data && data.games.length === 0 && <div className="notice info-notice" role="status">No games loaded for this week yet.</div>}

      <section className="slate-grid" aria-live="polite">
        {data?.games.map((game) => <GameCard game={game} key={game.id} />)}
      </section>
    </main>
  );
}
