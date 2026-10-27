import type { ReactNode } from "react";
import { Link } from "react-router-dom";

type Sport = "nfl" | "nba";

/** The one header every sport page shares: wordmark, sport switch, and the owner's other pages. */
export function AppHeader({
  sport, eyebrow, title, extra,
}: { sport: Sport | null; eyebrow: string; title: string; extra?: ReactNode }) {
  const current = (id: Sport) => (sport === id ? "page" : undefined);
  return (
    <header className="topbar app-header">
      <div>
        <p className="eyebrow"><Link className="wordmark" to="/nfl">Betwatch</Link> · {eyebrow}</p>
        <h1 className="page-title">{title}</h1>
      </div>
      <nav className="app-nav" aria-label="Sections">
        <span aria-label="Sport" className="sport-switch" role="group">
          <Link aria-current={current("nfl")} to="/nfl">NFL</Link>
          <Link aria-current={current("nba")} to="/nba">NBA</Link>
        </span>
        <Link to="/bets">Bets</Link>
        <Link to="/ops">Ops</Link>
        <Link to="/odds">Full odds board</Link>
        {extra}
      </nav>
    </header>
  );
}
