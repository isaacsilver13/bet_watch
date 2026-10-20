import { errorDetail } from "./http";

export interface JobStatus {
  job: string;
  slot_key: string;
  state: string;
  attempts: number;
  updated_at: string;
}

export interface OpsHealth {
  generated_at: string;
  run_due_last_at: string | null;
  run_due_stale: boolean;
  jobs: JobStatus[];
  slots_today: Record<string, number>;
  credits_remaining: number | null;
  snapshot_coverage: {
    targeted: number;
    complete: number;
    books: Record<string, Record<string, number>>;
  } | null;
  unlinked_events: string[];
  stuck_raw_saved: string[];
  finals_missing_close: number;
  nba_context?: {
    requests_today: number;
    requests_today_estimated: boolean;
    last_success_at: string | null;
    last_covered_date: string | null;
    quota_remaining: number | null;
    last_error: string | null;
  };
}

export async function fetchHealth(signal?: AbortSignal): Promise<OpsHealth> {
  const response = await fetch("/api/v1/ops/health", { signal });
  if (!response.ok) throw new Error(await errorDetail(response, `Health request failed with status ${response.status}`));
  return response.json() as Promise<OpsHealth>;
}
