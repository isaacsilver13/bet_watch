import { whenZoned, type BoardMovement } from "../lib/board";
import { formatTally, type ContextResponse, type TeamContext } from "../lib/matchup";
import { moveFlag, weatherFlag } from "../lib/research";
import { formatSpread, weatherText, type SlateGame } from "../lib/slate";

interface Card { id: string; title: string; lines: string[]; source: string; asOf: string | null }

interface Props {
  game: SlateGame;
  context?: ContextResponse | null;
  contextLoading?: boolean;
  contextError?: Error | null;
  /** Book consensus at the first saved snapshot, for the "market moved" card. */
  bookMove?: BoardMovement | null;
  bookNow?: { spread_home: number | null; total: number | null } | null;
}

const rank = (team: TeamContext, key: string): string => {
  const metric = team.efficiency[key];
  return metric ? `#${metric.rank} of ${metric.of}` : "—";
};

function marketCard({ game, bookMove, bookNow }: Props): Card {
  const lines: string[] = [];
  const open = game.lines.open;
  const latest = game.lines.close ?? game.lines.current;
  if (open && latest) {
    lines.push(`Reference line: ${formatSpread(game, open.spread_home)} to ${formatSpread(game, latest.spread_home)}, total ${open.total ?? "—"} to ${latest.total ?? "—"}.`);
  } else {
    lines.push("No reference open line loaded.");
  }
  if (bookMove && bookNow && bookMove.snapshots > 1) {
    lines.push(`Book consensus: ${formatSpread(game, bookMove.spread_home)} to ${formatSpread(game, bookNow.spread_home)} over ${bookMove.snapshots} saved snapshots since ${whenZoned(bookMove.first_at)}.`);
  } else if (bookMove) {
    lines.push("Only one saved book snapshot, so no book movement to show yet.");
  }
  return {
    id: "market", title: "Market moved", lines,
    source: latest ? `${latest.source} reference line${bookMove ? " and sportsbook consensus" : ""}` : "Sportsbook snapshots",
    asOf: latest?.captured_at ?? null,
  };
}

function injuryCard(context: ContextResponse | null | undefined): Card {
  const asOf = context?.data_as_of.injuries ?? null;
  const lines: string[] = [];
  if (!context || !asOf) {
    lines.push("Injury report unavailable.");
  } else {
    const flagged = [context.away, context.home].filter((team) => team.qb_flag);
    for (const team of flagged) lines.push(`${team.team.abbr}'s last starting QB, ${team.qb_flag!.player}, is listed ${team.qb_flag!.status}.`);
    if (flagged.length === 0) {
      const listed = context.away.injuries.length + context.home.injuries.length;
      lines.push(`No starting-QB flag on the report (${listed} player${listed === 1 ? "" : "s"} listed in total).`);
    }
  }
  return { id: "injury", title: "QB and injury report", lines, source: "ESPN and nflverse injury reports", asOf };
}

function formCard(context: ContextResponse | null | undefined): Card {
  const lines: string[] = [];
  if (context) {
    for (const team of [context.away, context.home]) {
      const season = team.records.season;
      const record = season ? `${formatTally(season.su)} SU, ${formatTally(season.ats)} ATS` : "no record yet";
      lines.push(`${team.team.abbr}: ${record}; offense EPA/play ${rank(team, "off_epa_play")}, defense ${rank(team, "def_epa_play")}.`);
    }
    if (Object.keys(context.away.efficiency).length === 0 && Object.keys(context.home.efficiency).length === 0) lines.push("Team stats unavailable for this season so far.");
  } else {
    lines.push("Team records and stats unavailable.");
  }
  return {
    id: "form", title: "Form and efficiency", lines,
    source: "Final results before kickoff; nflverse team stats (rank 1 = best)",
    asOf: context?.data_as_of.efficiency ?? context?.data_as_of.records ?? null,
  };
}

function restCard(context: ContextResponse | null | undefined): Card {
  const lines: string[] = [];
  if (context) {
    for (const team of [context.away, context.home]) {
      const spot = team.situation;
      const notes = [
        spot.rest_days === null ? "rest unknown" : `${spot.rest_days} days rest`,
        spot.short_week ? "short week" : null,
        spot.off_bye ? "off a bye" : null,
        spot.travel_miles !== null ? `${spot.travel_miles} mi from last game` : null,
      ].filter(Boolean);
      lines.push(`${team.team.abbr}: ${notes.join(", ")}.`);
    }
    if (context.primetime) lines.push("Primetime kickoff.");
  } else {
    lines.push("Schedule context unavailable.");
  }
  return { id: "rest", title: "Rest and travel", lines, source: "Schedule and venue data", asOf: context?.data_as_of.situational ?? null };
}

function weatherCard(game: SlateGame): Card {
  const text = weatherText(game);
  const fallback = game.roof === "dome" ? "Indoors." : "Forecast unavailable.";
  return {
    id: "weather", title: "Weather", lines: [text ? `${text}.` : fallback],
    source: "Open-Meteo forecast", asOf: game.weather?.fetched_at ?? null,
  };
}

/** Facts already in Betwatch, with source and time. No pick, no edge, no advice. */
export function ResearchBrief(props: Props) {
  const { game, context, contextLoading, contextError } = props;
  const cards = [marketCard(props), injuryCard(context), formCard(context), restCard(context), weatherCard(game)];
  const standout =
    moveFlag(game) !== null || weatherFlag(game) !== null ||
    [context?.away, context?.home].some((team) => team && (team.qb_flag || team.situation.short_week || team.situation.off_bye));

  return (
    <section aria-label="Research brief" className="research-brief">
      <div className="section-heading"><h3>What changed / what to check</h3></div>
      {contextLoading && <div className="skeleton-card" aria-label="Loading research"><span /><span /><span /></div>}
      {contextError && <p className="missing-price">Records, injuries and rest context unavailable: {contextError.message}</p>}
      {!contextLoading && !standout && context?.data_as_of.injuries && <p className="prediction-disclosure">No standout changes in available data.</p>}
      <div className="brief-grid">
        {cards.map((card) => (
          <article className="brief-card" key={card.id}>
            <h4>{card.title}</h4>
            {card.lines.map((line) => <p key={line}>{line}</p>)}
            <small>{card.source}{card.asOf ? ` · as of ${whenZoned(card.asOf)}` : ""}</small>
          </article>
        ))}
      </div>
      <p className="prediction-disclosure">Context, not betting advice. Sportsbook consensus is a market estimate (vig removed), not a share of bettors.</p>
    </section>
  );
}
