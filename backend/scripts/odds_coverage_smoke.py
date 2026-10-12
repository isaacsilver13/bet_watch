"""One live Odds API call to record which books/markets the free tier really returns.

Costs about 3 credits per sport. Needs PROVIDER_MODE=live and THE_ODDS_API_KEY in .env.
Usage:  python backend/scripts/odds_coverage_smoke.py nfl
"""

import asyncio
import json
import sys

from app.analytics.coverage import coverage_matrix
from app.config import load_settings
from app.domain.models import Sport
from app.providers.the_odds_api import TheOddsApiProvider
from app.providers.the_odds_api.client import TheOddsApiClient


async def main(sport_name: str) -> int:
    settings = load_settings()
    if not settings.the_odds_api_key:
        print("Set THE_ODDS_API_KEY (and PROVIDER_MODE=live) in .env first.")
        return 2
    provider = TheOddsApiProvider(
        TheOddsApiClient(
            settings.the_odds_api_key,
            timeout=settings.provider_timeout_seconds,
            regions=settings.odds_regions,
            bookmakers=settings.odds_bookmakers,
        )
    )
    comparisons = await provider.get_comparisons(sport=Sport(sport_name))
    report = {
        "sport": sport_name,
        "events": len(comparisons),
        "requested_books": list(settings.odds_bookmakers),
        "returned_coverage": coverage_matrix(comparisons),
        "quota_remaining": provider.quota_remaining,
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "nfl")))
