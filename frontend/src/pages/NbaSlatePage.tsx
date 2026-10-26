import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import { GameCard } from "../components/GameCard";
import { fetchDaySlate } from "../lib/slate";

function dayTitle(date: string): string {
  // Noon avoids a timezone shifting the calendar day in the label.
  return new Date(`${date}T12:00:00`).toLocaleDateString(undefined, {
    weekday: "long", year: "numeric", month: "long", day: "numeric",
  });
}

export function NbaSlatePage() {
  const { date } = useParams();
  const navigate = useNavigate();
  const { data, error, isLoading } = useQuery({
    queryKey: ["slate", "nba", date ?? "current"],
    queryFn: ({ signal }) => fetchDaySlate({ date, signal }),
  });
  const go = (target: string | null | undefined) => target && navigate(`/nba/${target}`);

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">NBA slate</p>
          <h1>{data ? dayTitle(data.date) : "Slate"}</h1>
        </div>
        <nav className="app-nav" aria-label="Sections">
          <Link to="/nfl">NFL</Link> · <Link to="/bets">Bets</Link> · <Link to="/odds">Odds board</Link> · <Link to={`/nba/report/${data?.date ?? date ?? ""}`}>Daily report</Link>
        </nav>
      </header>

      <section className="control-band" aria-label="Day picker">
        <button className="text-button" disabled={!data?.previous} onClick={() => go(data?.previous)} type="button">
          Previous game day
        </button>
        <button className="text-button" disabled={!data?.next} onClick={() => go(data?.next)} type="button">
          Next game day
        </button>
        <label>
          <span className="control-label">Date</span>
          <input onChange={(event) => go(event.target.value)} type="date" value={data?.date ?? ""} />
        </label>
      </section>

      {error && <div className="notice error-notice" role="alert">{error instanceof Error ? error.message : "Could not load the slate"}</div>}
      {isLoading && <div className="skeleton-card" aria-label="Loading games"><span /><span /><span /></div>}
      {data && data.games.length === 0 && <div className="notice info-notice" role="status">No games on this day.</div>}

      <section className="slate-grid" aria-live="polite">
        {data?.games.map((game) => <GameCard game={game} key={game.id} />)}
      </section>
    </main>
  );
}
