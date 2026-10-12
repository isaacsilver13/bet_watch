import type { EventComparison, MarketType, Offer } from "../types";

export const TOP_LINE_MARKETS: Array<{ id: MarketType; label: string }> = [
  { id: "moneyline", label: "Moneyline" },
  { id: "spread", label: "Spread" },
  { id: "total", label: "Total" },
];

export function offerKey(offer: Offer): string {
  return [offer.selection, offer.line ?? "", offer.player_name ?? ""].join("|");
}

export function isBestOffer(offers: Offer[], offer: Offer): boolean {
  if (offer.status !== "available" || offer.price_decimal === null) return false;
  const matching = offers.filter(
    (candidate) =>
      candidate.status === "available" &&
      candidate.price_decimal !== null &&
      offerKey(candidate) === offerKey(offer),
  );
  return (
    matching.length > 1 &&
    matching.every((candidate) => candidate.price_decimal! <= offer.price_decimal!)
  );
}

function formatOffer(offer: Offer): string {
  if (offer.status !== "available") return offer.status;
  if (offer.price_american === null) return "Unavailable";
  return `${offer.price_american > 0 ? "+" : ""}${offer.price_american}`;
}

function impliedProbability(offer: Offer): string | null {
  return offer.price_decimal === null ? null : `${(100 / offer.price_decimal).toFixed(1)}%`;
}

function OfferCell({ offer, offers }: { offer: Offer | undefined; offers: Offer[] }) {
  if (!offer) return <span className="offer-missing">—</span>;
  const className = `${isBestOffer(offers, offer) ? "best-price" : ""} offer-${offer.status}`;
  const content = (
    <>
      <strong>{formatOffer(offer)}</strong>
      {impliedProbability(offer) && <small>{impliedProbability(offer)} implied</small>}
    </>
  );
  return offer.deep_link ? (
    <a className={className} href={offer.deep_link} rel="noreferrer" target="_blank">
      {content}
    </a>
  ) : (
    <span className={className}>{content}</span>
  );
}

function marketRows(offers: Offer[]): Offer[] {
  return Array.from(new Map(offers.map((offer) => [offerKey(offer), offer])).values());
}

export function OddsMatrix({
  comparison,
  mobileMarket,
}: {
  comparison: EventComparison;
  mobileMarket: MarketType;
}) {
  const topLineOffers = comparison.offers.filter((offer) =>
    TOP_LINE_MARKETS.some((market) => market.id === offer.market_type),
  );
  const bookmakers = Array.from(new Set(topLineOffers.map((offer) => offer.bookmaker)));

  if (topLineOffers.length === 0) {
    return <div className="empty-grid">No main lines are available for this event.</div>;
  }

  return (
    <div className="table-scroll odds-matrix-scroll">
      <table className="odds-matrix">
        <thead>
          <tr>
            <th className="sticky-selection" scope="col">Market / selection</th>
            {bookmakers.map((bookmaker) => <th scope="col" key={bookmaker}>{bookmaker}</th>)}
          </tr>
        </thead>
        {TOP_LINE_MARKETS.map((market) => {
          const offers = topLineOffers.filter((offer) => offer.market_type === market.id);
          const rows = marketRows(offers);
          return (
            <tbody
              className={mobileMarket === market.id ? "mobile-market-active" : ""}
              data-market={market.id}
              key={market.id}
            >
              <tr className="market-divider">
                <th colSpan={bookmakers.length + 1} scope="rowgroup">{market.label}</th>
              </tr>
              {rows.length > 0 ? rows.map((row) => (
                <tr key={offerKey(row)}>
                  <th className="sticky-selection" scope="row">
                    <span>{row.player_name ?? row.selection}</span>
                    {row.line !== null && <small>{row.line > 0 ? `+${row.line}` : row.line}</small>}
                  </th>
                  {bookmakers.map((bookmaker) => {
                    const offer = offers.find(
                      (candidate) => candidate.bookmaker === bookmaker && offerKey(candidate) === offerKey(row),
                    );
                    return <td key={bookmaker}><OfferCell offer={offer} offers={offers} /></td>;
                  })}
                </tr>
              )) : (
                <tr className="market-empty-row">
                  <td colSpan={bookmakers.length + 1}>No {market.label.toLowerCase()} lines.</td>
                </tr>
              )}
            </tbody>
          );
        })}
      </table>
    </div>
  );
}

export function SecondaryMarketTable({
  comparison,
  market,
}: {
  comparison: EventComparison;
  market: MarketType;
}) {
  const offers = comparison.offers.filter((offer) => offer.market_type === market);
  const bookmakers = Array.from(new Set(offers.map((offer) => offer.bookmaker)));
  const rows = marketRows(offers);
  if (rows.length === 0) return <div className="empty-grid">No lines are currently cached for this market.</div>;

  return (
    <div className="table-scroll">
      <table>
        <thead><tr><th scope="col">Selection</th>{bookmakers.map((book) => <th key={book} scope="col">{book}</th>)}</tr></thead>
        <tbody>
          {rows.map((row) => (
            <tr key={offerKey(row)}>
              <th scope="row"><span>{row.player_name ?? row.selection}</span>{row.line !== null && <small>{row.line}</small>}</th>
              {bookmakers.map((book) => {
                const offer = offers.find((candidate) => candidate.bookmaker === book && offerKey(candidate) === offerKey(row));
                return <td key={book}><OfferCell offer={offer} offers={offers} /></td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
