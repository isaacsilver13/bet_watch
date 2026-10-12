from app.analytics.injuries import InjuredPlayer, QbFlag, name_key, qb_flag


def test_suffixes_and_punctuation_do_not_break_the_match() -> None:
    assert name_key("Michael Penix Jr.") == name_key("Michael Penix") == "michael penix"
    assert name_key("Gardner Minshew II") == "gardner minshew"
    injured = [InjuredPlayer("Michael Penix Jr.", "QB", "Questionable")]
    assert qb_flag("Michael Penix", injured) == QbFlag("Michael Penix Jr.", "Questionable")


def test_only_a_quarterback_who_is_actually_at_risk_counts() -> None:
    assert qb_flag("Josh Allen", [InjuredPlayer("Josh Allen", "LB", "Out")]) is None  # namesake
    assert qb_flag("Josh Allen", [InjuredPlayer("Josh Allen", "QB", "Active")]) is None
    assert qb_flag(None, [InjuredPlayer("Josh Allen", "QB", "Out")]) is None
    assert qb_flag("Josh Allen", []) is None
