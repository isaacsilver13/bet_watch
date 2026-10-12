import type { PredictionContract, ProviderDetails } from "../types";

const cents = (value: number | null) => (value === null ? "—" : `${Math.round(value * 100)}¢`);
const count = (value: number | null) => (value === null ? "—" : Math.round(value).toLocaleString());

export const PREDICTION_DISCLOSURE =
  "Prediction-market contracts are not sportsbook lines. Prices, fees, depth, and settlement " +
  "rules differ. Last trade is historical; bid and ask are the current displayed quote.";

function freshness(details: ProviderDetails | undefined): string {
  if (!details?.last_refreshed_at) return "Not fetched yet";
  return `Observed ${new Date(details.last_refreshed_at).toLocaleString()}`;
}

function OutcomeRow({ contract, showMatch }: { contract: PredictionContract; showMatch: boolean }) {
  return (
    <div className="prediction-outcome">
      <div className="prediction-outcome-heading">
        <strong>{contract.matched_selection ?? contract.outcome}</strong>
        <span className={`contract-status status-${contract.status}`}>{contract.status}</span>
      </div>
      <dl className="prediction-stats">
        <div><dt>Last trade</dt><dd>{cents(contract.last_price)}</dd></div>
        <div><dt>Bid</dt><dd>{cents(contract.best_bid)} × {count(contract.bid_size)}</dd></div>
        <div><dt>Ask</dt><dd>{cents(contract.best_ask)} × {count(contract.ask_size)}</dd></div>
        <div><dt>Spread</dt><dd>{cents(contract.spread)}</dd></div>
        <div><dt>Volume</dt><dd>{count(contract.volume)}</dd></div>
        <div><dt>Open interest</dt><dd>{count(contract.open_interest)}</dd></div>
      </dl>
      {showMatch && <p className="prediction-match"><strong>{contract.match_status}</strong>: {contract.match_reason}</p>}
    </div>
  );
}

function ContractGroup({ contracts, showMatch }: { contracts: PredictionContract[]; showMatch: boolean }) {
  const first = contracts[0];
  const closes = first.close_time ? new Date(first.close_time).toLocaleString() : "Not supplied";
  return (
    <li className="prediction-card">
      <div className="prediction-card-head">
        <div>
          <span className="venue-badge">{first.venue}</span>
          <p className="prediction-title">{first.source_event_id}</p>
        </div>
        <small>Closes {closes}</small>
      </div>
      <div className="prediction-outcomes">
        {contracts.map((contract) => <OutcomeRow contract={contract} key={contract.contract_id} showMatch={showMatch} />)}
      </div>
      <details>
        <summary>Settlement rules</summary>
        <p className="prediction-rules">{first.rules}</p>
      </details>
      <div className="prediction-links">
        <a href={first.url} rel="noreferrer" target="_blank">View market on Kalshi</a>
      </div>
    </li>
  );
}

export function PredictionPanel({
  contracts,
  heading,
  details,
  showMatch = false,
}: {
  contracts: PredictionContract[];
  heading: string;
  details?: ProviderDetails;
  showMatch?: boolean;
}) {
  if (contracts.length === 0) return null;
  const groups = Array.from(
    contracts.reduce((map, contract) => {
      const current = map.get(contract.source_event_id) ?? [];
      current.push(contract);
      map.set(contract.source_event_id, current);
      return map;
    }, new Map<string, PredictionContract[]>()),
  );
  return (
    <section className="prediction-panel" aria-label={heading}>
      <div className="section-heading">
        <div><p className="eyebrow">Prediction market</p><h4>{heading}</h4></div>
        <span className={details?.stale ? "freshness stale" : "freshness"}>{freshness(details)}</span>
      </div>
      <p className="prediction-disclosure">{PREDICTION_DISCLOSURE}</p>
      <ul className="prediction-list">
        {groups.map(([eventId, group]) => <ContractGroup contracts={group} key={eventId} showMatch={showMatch} />)}
      </ul>
    </section>
  );
}
