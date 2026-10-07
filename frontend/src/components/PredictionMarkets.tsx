import type { PredictionContract } from "../types";

// Contract prices are native probabilities (cents on a $1 contract), never sportsbook odds.
const cents = (value: number | null) => (value === null ? "—" : `${Math.round(value * 100)}¢`);
const count = (value: number | null) => (value === null ? "—" : Math.round(value).toLocaleString());

export const PREDICTION_DISCLOSURE =
  "Prediction-market contract prices are not sportsbook lines. They can have thin depth, wider " +
  "spreads, and different settlement rules (for example ties). Read each contract's rules.";

function ContractCard({ contract, showMatch }: { contract: PredictionContract; showMatch: boolean }) {
  const closes = contract.close_time ? new Date(contract.close_time).toLocaleString() : "—";
  return (
    <li className="prediction-card">
      <div className="prediction-card-head">
        <span className="venue-badge">{contract.venue}</span>
        {contract.status !== "active" && <span className="offer-suspended">{contract.status}</span>}
      </div>
      <p className="prediction-title">{contract.title}</p>
      <p className="prediction-price">
        <strong>{cents(contract.probability)}</strong> market probability
      </p>
      <dl className="prediction-stats">
        <div>
          <dt>Bid</dt>
          <dd>
            {cents(contract.best_bid)} × {count(contract.bid_size)}
          </dd>
        </div>
        <div>
          <dt>Ask</dt>
          <dd>
            {cents(contract.best_ask)} × {count(contract.ask_size)}
          </dd>
        </div>
        <div>
          <dt>Spread</dt>
          <dd>{cents(contract.spread)}</dd>
        </div>
        <div>
          <dt>Volume</dt>
          <dd>{count(contract.volume)}</dd>
        </div>
        <div>
          <dt>Open interest</dt>
          <dd>{count(contract.open_interest)}</dd>
        </div>
        <div>
          <dt>Closes</dt>
          <dd>{closes}</dd>
        </div>
      </dl>
      {showMatch && (
        <p className="prediction-match">
          {contract.match_status}: {contract.match_reason}
        </p>
      )}
      <details>
        <summary>Settlement rules</summary>
        <p className="prediction-rules">{contract.rules}</p>
      </details>
      <a href={contract.url} rel="noreferrer" target="_blank">
        View on {contract.venue}
      </a>
    </li>
  );
}

export function PredictionPanel({
  contracts,
  heading,
  showMatch = false,
}: {
  contracts: PredictionContract[];
  heading: string;
  showMatch?: boolean;
}) {
  if (contracts.length === 0) return null;
  return (
    <section className="prediction-panel" aria-label={heading}>
      <p className="eyebrow">{heading}</p>
      <p className="prediction-disclosure">{PREDICTION_DISCLOSURE}</p>
      <ul className="prediction-list">
        {contracts.map((contract) => (
          <ContractCard contract={contract} key={contract.contract_id} showMatch={showMatch} />
        ))}
      </ul>
    </section>
  );
}
