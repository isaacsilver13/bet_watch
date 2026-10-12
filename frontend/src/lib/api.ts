import type {
  ComparisonResponse,
  MarketType,
  PredictionMarketsResponse,
  Sport,
  StatusResponse,
} from "../types";

export type RefreshIgnoredReason = "unauthorized" | "throttled";

interface OddsRequestOptions {
  eventId?: string;
  marketType?: MarketType;
  forceRefresh?: boolean;
  includeProps?: boolean;
  signal?: AbortSignal;
}

export interface OddsFetchResult {
  data: ComparisonResponse;
  refreshIgnored: RefreshIgnoredReason | null;
}

export async function fetchOdds(
  sport: Sport,
  options: OddsRequestOptions = {},
): Promise<OddsFetchResult> {
  const params = new URLSearchParams({ sport });
  if (options.eventId) params.set("event_id", options.eventId);
  if (options.marketType) params.set("market_type", options.marketType);
  if (options.forceRefresh) params.set("force_refresh", "true");
  if (options.includeProps) params.set("include_props", "true");

  const response = await fetch(`/api/v1/odds?${params.toString()}`, {
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`Odds request failed with status ${response.status}`);
  }

  const ignored = response.headers.get("X-Refresh-Ignored");
  return {
    data: (await response.json()) as ComparisonResponse,
    refreshIgnored:
      ignored === "unauthorized" || ignored === "throttled" ? ignored : null,
  };
}

export async function fetchPredictionMarkets(
  sport: Sport,
  signal?: AbortSignal,
): Promise<PredictionMarketsResponse> {
  const response = await fetch(`/api/v1/prediction-markets?sport=${sport}`, { signal });
  if (!response.ok) {
    throw new Error(`Prediction markets request failed with status ${response.status}`);
  }
  return response.json() as Promise<PredictionMarketsResponse>;
}

export async function fetchStatus(signal?: AbortSignal): Promise<StatusResponse> {
  const response = await fetch("/api/v1/status", { signal });
  if (!response.ok) {
    throw new Error(`Status request failed with status ${response.status}`);
  }
  return response.json() as Promise<StatusResponse>;
}
