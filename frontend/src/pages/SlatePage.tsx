import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { AppHeader } from "../components/AppHeader";
import { NflBoard, type Expanded, type Panel } from "../components/NflBoard";
import {
  MARKETS,
  ageText,
  fetchBoard,
  isActionable,
  isStale,
  parseMarket,
  weekLabel,
  weekPath,
  whenZoned,
  DEFAULT_MARKET,
} from "../lib/board";
import { REFRESH_REASONS, refreshGame } from "../lib/lines";

const CARRIED = ["market", "books", "team"];

export function SlatePage() {
  const { season, seasonType, week } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const market = parseMarket(params.get("market"));
  const team = params.get("team") ?? "";
  const books = (params.get("books") ?? "").split(",").filter(Boolean);
  const gameParam = Number(params.get("game"));
  const panelParam = params.get("panel");
  const expanded: Expanded | null =
    gameParam && (panelParam === "books" || panelParam === "research") ? { game: gameParam, panel: panelParam } : null;

  const { data, error, isLoading } = useQuery({
    queryKey: ["board", "nfl", season ?? "current", seasonType ?? "", week ?? "", books.join(",")],
    queryFn: ({ signal }) => fetchBoard({ season, seasonType, week, books, signal }),
    placeholderData: keepPreviousData,
  });
  const refresh = useMutation({
    mutationFn: (gameId: number) => refreshGame(gameId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["board"] }),
  });

  const update = (changes: Record<string, string | null>) =>
    setParams((current) => {
      const next = new URLSearchParams(current);
      for (const [key, value] of Object.entries(changes)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      return next;
    }, { replace: true });

  const toggle = (game: number, panel: Panel) =>
    update(expanded?.game === game && expanded.panel === panel ? { game: null, panel: null } : { game: String(game), panel });

  const goToWeek = (target: string) => {
    const [kind, number] = target.split(":");
    if (!data) return;
    const carried = new URLSearchParams();
    for (const key of CARRIED) if (params.get(key)) carried.set(key, params.get(key)!);
    const query = carried.toString();
    navigate(`${weekPath(data.season, { season_type: kind, week: Number(number) })}${query ? `?${query}` : ""}`);
  };

  const weeks = data?.weeks ?? [];
  const index = data ? weeks.findIndex((ref) => ref.season_type === data.season_type && ref.week === data.week) : -1;
  const previous = index > 0 ? weeks[index - 1] : undefined;
  const next = index >= 0 && index < weeks.length - 1 ? weeks[index + 1] : undefined;
  const current = data ? `${data.season_type}:${data.week}` : "";
  const goTo = (ref?: { season_type: string; week: number }) => ref && goToWeek(`${ref.season_type}:${ref.week}`);

  const stale = isStale(data?.snapshot_at);
  const games = (data?.games ?? []).filter(
    (entry) => !team || entry.game.home.abbr === team || entry.game.away.abbr === team,
  );
  const teams = [...new Set((data?.games ?? []).flatMap((entry) => [entry.game.away.abbr, entry.game.home.abbr]))].sort();
  const upcoming = data?.games.find((entry) => isActionable(entry.game))?.game.id;
  const available = (data?.books_available ?? []).filter((book) => book.venue !== null);
  const selectedCount = books.length || available.length;
  const outcome = refresh.data;

  const toggleBook = (venue: string) => {
    const chosen = books.length ? books : available.map((book) => book.venue!);
    const nextBooks = chosen.includes(venue) ? chosen.filter((v) => v !== venue) : [...chosen, venue];
    update({ books: nextBooks.length === 0 || nextBooks.length === available.length ? null : nextBooks.join(","), game: null, panel: null });
  };

  return (
    <main className="app-shell">
      <AppHeader eyebrow="NFL week board" sport="nfl" title={data ? `${data.season} · ${weekLabel(data)}` : "Week board"} />

      <section className="control-band week-controls" aria-label="Week picker">
        <button className="text-button" disabled={!previous} onClick={() => goTo(previous)} type="button">Previous week</button>
        <label>
          <span className="control-label">Week</span>
          <select onChange={(event) => goToWeek(event.target.value)} value={current}>
            {weeks.map((ref) => <option key={`${ref.season_type}:${ref.week}`} value={`${ref.season_type}:${ref.week}`}>{weekLabel(ref)}</option>)}
          </select>
        </label>
        <button className="text-button" disabled={!next} onClick={() => goTo(next)} type="button">Next week</button>
      </section>

      <section className="board-status" aria-label="Snapshot status">
        <p className={stale ? "freshness stale" : "freshness"}>
          {data?.snapshot_at
            ? `Odds observed ${whenZoned(data.snapshot_at)}${stale ? ` · ${ageText(data.snapshot_at)}` : ""}`
            : "No sportsbook snapshot yet"}
          {data ? ` · ${available.length} tracked book${available.length === 1 ? "" : "s"}` : ""}
        </p>
        {data?.can_refresh && (
          <button className="refresh-button" disabled={refresh.isPending || upcoming === undefined}
            onClick={() => upcoming !== undefined && refresh.mutate(upcoming)} type="button">
            {refresh.isPending ? "Refreshing…" : "Refresh prices (3 credits)"}
          </button>
        )}
      </section>

      <section className="control-band board-controls" aria-label="Board controls">
        <div className="segmented-control market-tabs" role="tablist" aria-label="Choose market">
          {MARKETS.map((entry) => (
            <button aria-selected={market === entry.id} className={market === entry.id ? "active" : ""} key={entry.id}
              onClick={() => update({ market: entry.id === DEFAULT_MARKET ? null : entry.id })} role="tab" type="button">
              {entry.label}
            </button>
          ))}
        </div>
        <label>
          <span className="control-label">Team</span>
          <select onChange={(event) => update({ team: event.target.value || null })} value={team}>
            <option value="">All teams</option>
            {teams.map((abbr) => <option key={abbr} value={abbr}>{abbr}</option>)}
          </select>
        </label>
        <details className="book-filter">
          <summary>Books ({selectedCount} selected)</summary>
          <fieldset>
            <legend className="control-label">Compare prices across</legend>
            {available.map((book) => (
              <label className="check" key={book.venue}>
                <input checked={books.length === 0 || books.includes(book.venue!)} onChange={() => toggleBook(book.venue!)} type="checkbox" />
                {book.book}
              </label>
            ))}
          </fieldset>
        </details>
      </section>

      {data && books.length > 0 && (
        <p className="scope-note">Best prices are among {books.length} selected book{books.length === 1 ? "" : "s"}, not all tracked books.</p>
      )}
      {stale && (
        <div className="notice" role="status">
          The saved odds snapshot is {ageText(data!.snapshot_at!)}. Prices are kept for research only; treat them as the best in the saved snapshot, not what is available now.
        </div>
      )}
      {refresh.error && <div className="notice error-notice" role="alert">{refresh.error.message}</div>}
      {outcome && (
        <div className="notice info-notice" role="status">
          {outcome.refreshed ? "Prices refreshed." : REFRESH_REASONS[outcome.reason ?? ""] ?? "Nothing was refreshed."}
        </div>
      )}
      {error && <div className="notice error-notice" role="alert">{error instanceof Error ? error.message : "Could not load the board"}{data ? " Showing the last loaded data." : ""}</div>}
      {isLoading && <div className="skeleton-card" aria-label="Loading games"><span /><span /><span /></div>}
      {data && data.games.length === 0 && <div className="notice info-notice" role="status">No games loaded for this week yet.</div>}
      {data && data.games.length > 0 && !data.snapshot_at && (
        <div className="notice info-notice" role="status">
          No sportsbook prices yet. They arrive with the Tuesday, Thursday, Saturday and Sunday snapshots and shortly before kickoff{data.can_refresh ? ", or use Refresh prices" : ""}.
          Schedule and reference lines are shown below.
        </div>
      )}
      {data && data.games.length > 0 && games.length === 0 && <div className="notice info-notice" role="status">No games for {team} this week.</div>}

      {data && <NflBoard expanded={expanded} games={games} market={market} onToggle={toggle} />}
    </main>
  );
}
