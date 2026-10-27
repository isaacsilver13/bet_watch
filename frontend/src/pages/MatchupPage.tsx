import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";

import { AppHeader } from "../components/AppHeader";
import { GlassCard } from "../components/GlassCard";
import { LinesPanel } from "../components/LinesPanel";
import {
  fetchContext,
  fetchGame,
  formatMetric,
  formatTally,
  isStale,
  metricRowsFor,
  type ContextResponse,
  type Injury,
  type RecordSet,
  type TeamContext,
} from "../lib/matchup";
import { formatSpread, slatePath, weatherText, type SlateGame } from "../lib/slate";

const TAB_IDS = ["lines", "records", "efficiency", "situation", "injuries", "h2h"] as const;
type TabId = (typeof TAB_IDS)[number];
interface TabSpec { id: TabId; label: string; asOf: string | null }
// The data behind a tab has the same id in both sports; only its name differs.
const NFL_TABS: TabSpec[] = [
  { id: "lines", label: "Lines", asOf: null },
  { id: "records", label: "Records", asOf: "records" },
  { id: "efficiency", label: "Efficiency", asOf: "efficiency" },
  { id: "situation", label: "Situation", asOf: "situational" },
  { id: "injuries", label: "Injuries", asOf: "injuries" },
  { id: "h2h", label: "Head-to-head", asOf: "records" },
];
const NBA_TABS: TabSpec[] = NFL_TABS.map((tab) =>
  tab.id === "efficiency" ? { ...tab, label: "Ratings" }
    : tab.id === "situation" ? { ...tab, label: "Schedule" }
      : tab,
);
const LAST_N_CHOICES = [3, 5, 8, 10];

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

function recordCell(record: RecordSet | undefined): string {
  if (!record) return "—";
  return `${formatTally(record.su)} SU · ${formatTally(record.ats)} ATS · ${formatTally(record.ou)} O/U`;
}

function FreshnessLine({ asOf, keyName }: { asOf: string | null | undefined; keyName: string }) {
  if (!asOf) return <p className="freshness">No data loaded yet</p>;
  const stale = isStale(asOf, keyName);
  return (
    <p className={stale ? "freshness stale" : "freshness"}>
      Data as of {when(asOf)}{stale ? " · stale" : ""}
    </p>
  );
}

function TwoTeamTable({ ctx, rows }: { ctx: ContextResponse; rows: { label: string; away: string; home: string }[] }) {
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr><th scope="col" /><th scope="col">{ctx.away.team.abbr} (away)</th><th scope="col">{ctx.home.team.abbr} (home)</th></tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label}><th scope="row">{row.label}</th><td>{row.away}</td><td>{row.home}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RecordsPanel({ ctx }: { ctx: ContextResponse }) {
  const pick = (team: TeamContext, key: string) => recordCell(team.records[key]);
  return (
    <TwoTeamTable
      ctx={ctx}
      rows={[
        { label: "This season", away: pick(ctx.away, "season"), home: pick(ctx.home, "season") },
        { label: `Last ${ctx.last_n} games`, away: pick(ctx.away, "last_n"), home: pick(ctx.home, "last_n") },
        { label: "On the road / at home", away: pick(ctx.away, "away"), home: pick(ctx.home, "home") },
        { label: "As favorite", away: pick(ctx.away, "favorite"), home: pick(ctx.home, "favorite") },
        { label: "As underdog", away: pick(ctx.away, "underdog"), home: pick(ctx.home, "underdog") },
      ]}
    />
  );
}

function EfficiencyPanel({ ctx }: { ctx: ContextResponse }) {
  const rows = metricRowsFor(ctx.sport).filter((row) => ctx.away.efficiency[row.key] || ctx.home.efficiency[row.key]);
  if (!rows.length) return <p className="empty-grid">No team stats loaded for this season yet.</p>;
  return (
    <TwoTeamTable
      ctx={ctx}
      rows={rows.map((row) => ({
        label: row.label,
        away: formatMetric(ctx.away.efficiency[row.key], row),
        home: formatMetric(ctx.home.efficiency[row.key], row),
      }))}
    />
  );
}

function yesNo(value: boolean): string {
  return value ? "Yes" : "No";
}

function SituationPanel({ ctx }: { ctx: ContextResponse }) {
  const cell = (value: number | string | null, unit = "") => (value === null ? "—" : `${value}${unit}`);
  const both = (read: (team: TeamContext) => string) => ({ away: read(ctx.away), home: read(ctx.home) });
  if (ctx.sport === "nba") {
    return (
      <>
        <TwoTeamTable
          ctx={ctx}
          rows={[
            { label: "Days since last game", ...both((t) => cell(t.situation.rest_days)) },
            { label: "Back-to-back", ...both((t) => yesNo(Boolean(t.situation.back_to_back))) },
            { label: "Third game in four days", ...both((t) => yesNo(Boolean(t.situation.three_in_four))) },
            { label: "Fourth game in six days", ...both((t) => yesNo(Boolean(t.situation.four_in_six))) },
            { label: "Games in the last 7 days", ...both((t) => cell(t.situation.games_last_7 ?? null)) },
            { label: "Travel from last game", ...both((t) => cell(t.situation.travel_miles, " mi")) },
            { label: "Time zones crossed", ...both((t) => cell(t.situation.tz_crossed)) },
            { label: "Last opponent", ...both((t) => cell(t.situation.previous_opponent)) },
          ]}
        />
        {ctx.divisional && <p className="prediction-disclosure">Division game.</p>}
      </>
    );
  }
  return (
    <>
      <TwoTeamTable
        ctx={ctx}
        rows={[
          { label: "Days of rest", ...both((t) => cell(t.situation.rest_days)) },
          { label: "Short week", ...both((t) => yesNo(t.situation.short_week)) },
          { label: "Off a bye", ...both((t) => yesNo(t.situation.off_bye)) },
          { label: "Travel from last game", ...both((t) => cell(t.situation.travel_miles, " mi")) },
          { label: "Time zones crossed", ...both((t) => cell(t.situation.tz_crossed)) },
          { label: "Last opponent", ...both((t) => cell(t.situation.previous_opponent)) },
        ]}
      />
      <p className="prediction-disclosure">
        {ctx.divisional ? "Divisional game. " : ""}
        {ctx.primetime ? "Primetime kickoff." : "Not a primetime kickoff."}
      </p>
    </>
  );
}

function InjuryList({ team }: { team: TeamContext }) {
  return (
    <div>
      <h3>{team.team.abbr}</h3>
      {team.injuries.length === 0 ? (
        <p className="slate-venue">Nothing on the report.</p>
      ) : (
        <ul className="injury-list">
          {team.injuries.map((injury: Injury) => (
            <li key={`${injury.player}-${injury.status}`}>
              <strong>{injury.star ? "★ " : ""}{injury.player}</strong>{injury.position ? ` (${injury.position})` : ""} — {injury.status}
              {injury.detail ? `, ${injury.detail}` : ""}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function InjuriesPanel({ ctx }: { ctx: ContextResponse }) {
  const flags = [ctx.away, ctx.home].filter((team) => team.qb_flag);
  const starNotices = [ctx.away, ctx.home].flatMap((team) => {
    const flag = team.star_flag;
    if (!flag) return [];
    return [
      ...(flag.out.length ? [{ key: `${team.team.id}-out`, text: `${team.team.abbr}: Stars out: ${flag.out.join(", ")}` }] : []),
      ...(flag.questionable.length ? [{ key: `${team.team.id}-q`, text: `${team.team.abbr}: Stars questionable: ${flag.questionable.join(", ")}` }] : []),
    ];
  });
  return (
    <>
      {flags.map((team) => (
        <div className="notice" role="status" key={team.team.id}>
          {team.team.abbr}'s last starting QB, {team.qb_flag?.player}, is listed {team.qb_flag?.status}.
        </div>
      ))}
      {starNotices.map((notice) => (
        <div className="notice" role="status" key={notice.key}>{notice.text}</div>
      ))}
      <div className="injury-grid">
        <InjuryList team={ctx.away} />
        <InjuryList team={ctx.home} />
      </div>
    </>
  );
}

function HeadToHeadPanel({ ctx }: { ctx: ContextResponse }) {
  const { meetings, home_su: su, home_ats: ats } = ctx.head_to_head;
  if (!meetings.length) return <p className="empty-grid">No earlier meetings on record.</p>;
  return (
    <>
      <p className="prediction-disclosure">
        {ctx.home.team.abbr}: {formatTally(su)} straight up, {formatTally(ats)} against the spread
        over the last {meetings.length} meeting{meetings.length === 1 ? "" : "s"}.
      </p>
      <div className="table-scroll">
        <table>
          <thead><tr><th scope="col">Date</th><th scope="col">Result</th><th scope="col">Closing spread</th></tr></thead>
          <tbody>
            {meetings.map((m) => (
              <tr key={m.game_id}>
                <th scope="row">{new Date(m.start_time).toLocaleDateString()}</th>
                <td>{m.away} {m.away_score} @ {m.home} {m.home_score}</td>
                <td>{m.spread_home === null ? "—" : `${m.home} ${m.spread_home > 0 ? "+" : ""}${m.spread_home}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function GameHeader({ game }: { game: SlateGame }) {
  const latest = game.lines.close ?? game.lines.current;
  const weather = weatherText(game);
  return (
    <GlassCard className="slate-card">
      <div className="slate-card-head">
        <h2>{game.away.abbr} @ {game.home.abbr}</h2>
        <span className="slate-status">{when(game.start_time)}</span>
      </div>
      <p className="slate-venue">{game.venue ?? "Venue TBD"}{game.neutral ? " · neutral site" : ""}</p>
      <dl className="slate-lines">
        <div><dt>Spread</dt><dd>{formatSpread(game, latest?.spread_home)}</dd></div>
        <div><dt>Total</dt><dd>{latest?.total != null ? `O/U ${latest.total}` : "—"}</dd></div>
        {game.sport !== "nba" && <div><dt>Weather</dt><dd>{weather ?? "—"}</dd></div>}
      </dl>
    </GlassCard>
  );
}

export function MatchupPage() {
  const { id = "" } = useParams();
  const [tab, setTab] = useState<TabId>("lines");
  const [lastN, setLastN] = useState(5);
  const game = useQuery({ queryKey: ["game", id], queryFn: ({ signal }) => fetchGame(id, signal) });
  const context = useQuery({
    queryKey: ["context", id, lastN],
    queryFn: ({ signal }) => fetchContext(id, lastN, signal),
  });
  const sport = game.data?.sport ?? context.data?.sport ?? "nfl";
  const tabs = sport === "nba" ? NBA_TABS : NFL_TABS;
  const active = tabs.find((entry) => entry.id === tab) ?? tabs[0];
  // Back returns to the board exactly as it was left (market, books, expanded game), when it
  // was the page that opened this one.
  const from = (useLocation().state as { from?: string } | null)?.from;
  const slateLink = from ?? (game.data ? slatePath(game.data) : "/nfl");
  const onContextTab = active.asOf !== null;
  const error = game.error ?? (onContextTab ? context.error : null);

  return (
    <main className="app-shell">
      <AppHeader
        eyebrow="Matchup"
        sport={sport === "nba" ? "nba" : "nfl"}
        title={game.data ? `${game.data.away.name} at ${game.data.home.name}` : "Matchup"}
      />
      <p className="back-row"><Link to={slateLink}>{sport === "nba" ? "Back to slate" : "Back to week"}</Link></p>

      {error && <div className="notice error-notice" role="alert">{error instanceof Error ? error.message : "Could not load the matchup"}</div>}
      {game.data && <GameHeader game={game.data} />}

      <section className="control-band" aria-label="Matchup sections">
        <div className="segmented-control" role="tablist">
          {tabs.map((entry) => (
            <button
              aria-selected={tab === entry.id}
              className={tab === entry.id ? "active" : ""}
              key={entry.id}
              onClick={() => setTab(entry.id)}
              role="tab"
              type="button"
            >
              {entry.label}
            </button>
          ))}
        </div>
        {onContextTab && (
          <label>
            <span className="control-label">Recent games</span>
            <select value={lastN} onChange={(event) => setLastN(Number(event.target.value))}>
              {LAST_N_CHOICES.map((choice) => <option key={choice} value={choice}>Last {choice}</option>)}
            </select>
          </label>
        )}
      </section>

      {tab === "lines" && game.data && (
        <section role="tabpanel"><LinesPanel context={context.data ?? null}
          contextError={context.error instanceof Error ? context.error : null}
          contextLoading={context.isLoading} game={game.data} /></section>
      )}
      {onContextTab && context.isLoading && (
        <div className="skeleton-card" aria-label="Loading context"><span /><span /><span /></div>
      )}
      {onContextTab && context.data && active.asOf && (
        <section aria-live="polite" role="tabpanel">
          <FreshnessLine asOf={context.data.data_as_of[active.asOf]} keyName={active.asOf} />
          {tab === "records" && <RecordsPanel ctx={context.data} />}
          {tab === "efficiency" && <EfficiencyPanel ctx={context.data} />}
          {tab === "situation" && <SituationPanel ctx={context.data} />}
          {tab === "injuries" && <InjuriesPanel ctx={context.data} />}
          {tab === "h2h" && <HeadToHeadPanel ctx={context.data} />}
          <p className="prediction-disclosure">
            Context, not betting advice.{" "}
            {sport === "nba"
              ? "ATS and O/U use ESPN's closing line (DraftKings)."
              : "ATS and O/U use the closing line, ESPN first, then nflverse."}
          </p>
        </section>
      )}
    </main>
  );
}
