"""American-odds arithmetic. Pure functions, no I/O."""


def implied_prob(american: int) -> float:
    """Break-even probability of a price, vig included."""
    if american > 0:
        return 100 / (american + 100)
    return -american / (-american + 100)


def novig(p_a: float, p_b: float) -> tuple[float, float]:
    """Scale a two-way market's implied probabilities so they sum to one."""
    total = p_a + p_b
    return p_a / total, p_b / total


def fair_american(prob: float) -> int:
    """The American price that has exactly this probability and no vig."""
    if prob >= 0.5:
        return round(-100 * prob / (1 - prob))
    return round(100 * (1 - prob) / prob)


def decimal_odds(american: int) -> float:
    return 1 + american / 100 if american > 0 else 1 + 100 / -american
