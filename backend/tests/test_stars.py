from app.analytics.injuries import InjuredPlayer
from app.analytics.stars import PlayerMinutes, pick_stars, star_flag


def p(team, name, games, minutes):
    return PlayerMinutes(team, name, games, minutes)


CURRENT = [
    p(1, "a", 30, 36.0), p(1, "b", 30, 34.0), p(1, "c", 30, 31.0), p(1, "d", 30, 28.0),
    p(1, "cameo", 2, 40.0), p(2, "e", 30, 35.0),
]


def inj(name, status):
    return InjuredPlayer(name, None, status)


def test_the_stars_are_the_three_players_with_the_most_minutes_per_game() -> None:
    assert pick_stars(1, CURRENT, [], 12) == {"a", "b", "c"}
    assert pick_stars(2, CURRENT, [], 12) == {"e"}


def test_a_player_with_fewer_than_five_games_is_never_a_star() -> None:
    assert "cameo" not in pick_stars(1, CURRENT, [], 12)  # 40 mpg, but two games


def test_equal_minutes_go_to_the_alphabetically_first_name() -> None:
    tied = [p(1, "a", 30, 36.0), p(1, "b", 30, 34.0), p(1, "d", 30, 30.0), p(1, "c", 30, 30.0)]
    assert pick_stars(1, tied, [], 12) == {"a", "b", "c"}


def test_early_in_the_season_last_years_minutes_decide() -> None:
    previous = [p(1, "old1", 70, 35.0), p(1, "old2", 70, 33.0), p(1, "old3", 70, 30.0),
                p(1, "old4", 70, 29.0)]
    # this year's rows say otherwise, but the team has played fewer than 10 games
    assert pick_stars(1, CURRENT, previous, 3) == {"old1", "old2", "old3"}
    assert pick_stars(1, CURRENT, previous, 10) == {"a", "b", "c"}


def test_a_traded_player_belongs_to_his_new_team() -> None:
    previous = [p(1, "star", 70, 36.0), p(1, "b", 70, 33.0), p(1, "c", 70, 30.0),
                p(2, "e", 70, 32.0)]
    current = [p(2, "star", 4, 35.0)]  # four games in, he plays for team 2
    assert pick_stars(1, current, previous, 4) == {"b", "c"}
    assert pick_stars(2, current, previous, 4) == {"star", "e"}


def test_a_star_who_is_out_or_doubtful_is_flagged_and_questionable_is_soft() -> None:
    stars = {"nikola jokic", "jamal murray", "aaron gordon"}
    flag = star_flag(stars, [
        inj("Nikola Jokic", "Out"), inj("Jamal Murray", "Questionable"),
        inj("Aaron Gordon", "Probable"), inj("Bench Guy", "Out"),
    ])
    assert (flag.out, flag.questionable) == (["Nikola Jokic"], ["Jamal Murray"])


def test_names_match_without_accents_or_suffixes_and_other_statuses_count() -> None:
    stars = {"nikola jokic", "jabari smith", "star three"}
    flag = star_flag(stars, [
        inj("Nikola Jokić", "Doubtful"), inj("Jabari Smith Jr.", "Day-To-Day"),
        inj("Star Three", "Injured Reserve"),
    ])
    assert flag.out == ["Nikola Jokić", "Star Three"]
    assert flag.questionable == ["Jabari Smith Jr."]
