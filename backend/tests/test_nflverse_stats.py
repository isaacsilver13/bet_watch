from app.ingest.nflverse_stats import STAT_FIELDS, parse_team_stats_csv

from tests.matchup_helpers import FIXTURES


def _sample() -> str:
    return (FIXTURES / "nflverse" / "stats_team_week_sample.csv").read_text(encoding="utf-8")


def test_rows_become_one_record_per_team_with_curated_stats() -> None:
    records = parse_team_stats_csv(_sample())
    assert len(records) == 4
    ari = records[0]
    assert (ari.season, ari.season_type, ari.week) == (2026, "REG", 1)
    assert (ari.team, ari.opponent) == ("ARI", "LAC")
    assert ari.stats["attempts"] == 37.0 and ari.stats["passing_epa"] == 9.01286468515324
    assert ari.stats["rushing_epa"] == -0.59585879921744
    assert set(ari.stats) <= set(STAT_FIELDS)


def test_postseason_keeps_nflverse_week_numbers_and_the_rams_code() -> None:
    car = parse_team_stats_csv(_sample())[3]
    assert (car.season_type, car.week, car.team, car.opponent) == ("POST", 19, "CAR", "LA")


def test_blank_and_na_cells_are_left_out_not_zeroed() -> None:
    header = "season,week,team,season_type,game_id,opponent_team,attempts,passing_epa,carries"
    text = f"{header}\n2026,2,KC,REG,2026_02_KC_BUF,BUF,NA,,12\n"
    [record] = parse_team_stats_csv(text)
    assert record.stats == {"carries": 12.0}


def test_preseason_and_unknown_season_types_are_skipped() -> None:
    header = "season,week,team,season_type,game_id,opponent_team,attempts"
    text = f"{header}\n2026,1,KC,PRE,2026_01_KC_BUF,BUF,30\n"
    assert parse_team_stats_csv(text) == []
