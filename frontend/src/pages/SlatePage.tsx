import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import { GameCard } from "../components/GameCard";
import { fetchSlate, type WeekRef } from "../lib/slate";

function weekLabel(ref: WeekRef): string {
  if (ref.season_type === "POST") return `Playoffs round ${ref.week}`;
  return ref.season_type === "PRE" ? `Preseason week ${ref.week}` : `Week ${ref.week}`;
}

function weekPath(season: number, ref: WeekRef): string {
  return `/nfl/${season}/${ref.season_type.toLowerCase()}/${ref.week}`;
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
          <Link to="/nba">NBA</Link> · <Link to="/bets">Bets</Link> · <Link to="/odds">Odds board</Link>
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
