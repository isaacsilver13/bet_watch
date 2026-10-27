import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { GlassCard } from "./GlassCard";
import { isStale, whenZoned } from "../lib/board";
import {
  VENUES,
  createBet,
  describeBet,
  formatMoney,
  venueLabel,
  type BetDraft,
  type Market,
  type Selection,
} from "../lib/bets";
import type { SlateGame } from "../lib/slate";

const SIDES: Record<Market, Array<{ id: Selection; label: (game: SlateGame) => string }>> = {
  spread: [
    { id: "home", label: (game) => game.home.abbr },
    { id: "away", label: (game) => game.away.abbr },
  ],
  moneyline: [
    { id: "home", label: (game) => game.home.abbr },
    { id: "away", label: (game) => game.away.abbr },
  ],
  total: [
    { id: "over", label: () => "Over" },
    { id: "under", label: () => "Under" },
  ],
};

export function BetForm({ game, draft, onClose }: { game: SlateGame; draft: BetDraft | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [venue, setVenue] = useState(draft?.venue ?? "draftkings");
  const [market, setMarket] = useState<Market>(draft?.market ?? "spread");
  const [selection, setSelection] = useState<Selection>(draft?.selection ?? "home");
  const [line, setLine] = useState(draft?.line == null ? "" : String(draft.line));
  const [price, setPrice] = useState(draft?.price == null ? "" : String(draft.price));
  const [stake, setStake] = useState("");
  const [note, setNote] = useState("");
  const save = useMutation({
    mutationFn: createBet,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["bets"] });
      void queryClient.invalidateQueries({ queryKey: ["bets-summary"] });
    },
  });

  function changeMarket(next: Market) {
    setMarket(next);
    setSelection(SIDES[next][0].id);
    if (next === "moneyline") setLine("");
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    save.mutate({
      game_id: game.id,
      venue,
      market,
      selection,
      line: market === "moneyline" || line === "" ? null : Number(line),
      price_american: Number(price),
      stake: Number(stake),
      ...(note ? { note } : {}),
    });
  }

  if (save.data) {
    return (
      <GlassCard className="bet-form">
        <div className="notice info-notice" role="status">
          Logged {describeBet(save.data)} for {formatMoney(save.data.stake)} at {venueLabel(save.data.venue)}.
          Fee {formatMoney(save.data.fee)}.
        </div>
        <button className="text-button" onClick={onClose} type="button">Done</button>
      </GlassCard>
    );
  }

  return (
    <GlassCard className="bet-form">
      <form onSubmit={submit}>
        <h4>Log a bet · {game.away.abbr} @ {game.home.abbr}</h4>
        {draft?.quote && (
          <p className="quote-source" role="status">
            Quote: {draft.quote.book} · observed {whenZoned(draft.quote.observed_at)}
            {!draft.quote.actionable || isStale(draft.quote.observed_at)
              ? " · saved snapshot, may no longer be available" : ""}
          </p>
        )}
        <div className="bet-fields">
          <label>
            <span className="control-label">Book</span>
            <select value={venue} onChange={(event) => setVenue(event.target.value)}>
              {VENUES.map((entry) => <option key={entry.id} value={entry.id}>{entry.label}</option>)}
            </select>
          </label>
          <label>
            <span className="control-label">Market</span>
            <select value={market} onChange={(event) => changeMarket(event.target.value as Market)}>
              <option value="spread">Spread</option>
              <option value="total">Total</option>
              <option value="moneyline">Moneyline</option>
            </select>
          </label>
          <label>
            <span className="control-label">Side</span>
            <select value={selection} onChange={(event) => setSelection(event.target.value as Selection)}>
              {SIDES[market].map((side) => <option key={side.id} value={side.id}>{side.label(game)}</option>)}
            </select>
          </label>
          {market !== "moneyline" && (
            <label>
              <span className="control-label">Line</span>
              <input inputMode="decimal" onChange={(event) => setLine(event.target.value)} required step="0.5" type="number" value={line} />
            </label>
          )}
          <label>
            <span className="control-label">Price</span>
            <input onChange={(event) => setPrice(event.target.value)} placeholder="-110" required type="number" value={price} />
          </label>
          <label>
            <span className="control-label">Stake ($)</span>
            <input min="0.01" onChange={(event) => setStake(event.target.value)} required step="0.01" type="number" value={stake} />
          </label>
          <label>
            <span className="control-label">Note</span>
            <input maxLength={300} onChange={(event) => setNote(event.target.value)} type="text" value={note} />
          </label>
        </div>
        {save.error && <div className="notice error-notice" role="alert">{save.error.message}</div>}
        <div className="bet-actions">
          <button className="refresh-button" disabled={save.isPending} type="submit">
            {save.isPending ? "Saving…" : "Log bet"}
          </button>
          <button className="text-button" onClick={onClose} type="button">Cancel</button>
        </div>
        <p className="prediction-disclosure">
          The fee is worked out from the book and stake (Illinois per-bet fees) and kept with the bet.
        </p>
      </form>
    </GlassCard>
  );
}
