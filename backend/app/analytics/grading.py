"""Grade a bet from the final score, and work out what it returned. Pure functions."""

from app.analytics.pricing import decimal_odds


def grade(market: str, selection: str, line, home_score: int, away_score: int) -> str:
    """won | lost | push. `line` is the bet side's own number (home -2.5, away +2.5, over 44.5)."""
    if market == "moneyline":
        margin = home_score - away_score if selection == "home" else away_score - home_score
        edge = float(margin)
    elif market == "spread":
        margin = home_score - away_score if selection == "home" else away_score - home_score
        edge = margin + line
    elif market == "total":
        points = home_score + away_score
        edge = points - line if selection == "over" else line - points
    else:
        raise ValueError(f"cannot grade market {market!r}")
    if edge > 0:
        return "won"
    return "push" if edge == 0 else "lost"


def payout(status: str, stake: float, price_american: int) -> float:
    """Total returned, stake included."""
    if status == "won":
        return round(stake * decimal_odds(price_american), 2)
    if status in ("push", "void"):
        return stake
    return 0.0


def profit(payout_amount: float, stake: float, fee: float) -> float:
    return round(payout_amount - stake - fee, 2)
