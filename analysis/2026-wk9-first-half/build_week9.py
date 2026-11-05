"""Build team-level full-game and first-half stats (2026 REG, weeks 1..8) and pair them into week 9 matchups.

Sources (nflverse, CC-BY-4.0): play_by_play_2026.csv, schedules games.csv.
Usage: python build_week9.py <raw_dir> <out_dir> [target_week]
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

raw, out = Path(sys.argv[1]), Path(sys.argv[2])
WEEK = int(sys.argv[3]) if len(sys.argv) > 3 else 9
out.mkdir(parents=True, exist_ok=True)

pbp = pd.read_csv(raw / "pbp.csv", low_memory=False)
games = pd.read_csv(raw / "games.csv")
games = games[(games.season == 2026) & (games.game_type == "REG")]
played = games[games.home_score.notna()].copy()
pbp = pbp[pbp.game_id.isin(played.game_id)].sort_values(["game_id", "play_id"])

# ---------- per-game halftime score and who scored first ----------
# total_*_score is the score at the START of the play, so the first Half2 play (the kickoff) holds the halftime score.
h2 = pbp[pbp.game_half == "Half2"].groupby("game_id").first()[["total_home_score", "total_away_score"]]
h2.columns = ["home_1h", "away_1h"]
first_score = (
    pbp[(pbp.total_home_score > 0) | (pbp.total_away_score > 0)].groupby("game_id").first()[
        ["total_home_score", "total_away_score"]
    ]
)
first_score["scored_first"] = np.where(first_score.total_home_score > 0, "home", "away")
g = played.set_index("game_id")[["week", "home_team", "away_team", "home_score", "away_score", "overtime"]].join(h2).join(
    first_score[["scored_first"]]
)

# ---------- per-team-game play stats, full game and first half ----------
scr = pbp[((pbp["pass"] == 1) | (pbp["rush"] == 1)) & pbp.epa.notna() & pbp.posteam.notna()].copy()
scr["is_1h"] = scr.game_half == "Half1"
scr["yds"] = scr.yards_gained.fillna(0)


def side_stats(df, team_col, prefix):
    grp = df.groupby(["game_id", team_col])
    res = pd.DataFrame(
        {
            f"{prefix}plays": grp.size(),
            f"{prefix}epa": grp.epa.sum(),
            f"{prefix}success": grp.success.sum(),
            f"{prefix}yds": grp.yds.sum(),
            f"{prefix}pass_epa": grp.apply(lambda x: x.loc[x["pass"] == 1, "epa"].sum(), include_groups=False),
            f"{prefix}pass_n": grp.apply(lambda x: (x["pass"] == 1).sum(), include_groups=False),
            f"{prefix}rush_epa": grp.apply(lambda x: x.loc[x["rush"] == 1, "epa"].sum(), include_groups=False),
            f"{prefix}rush_n": grp.apply(lambda x: (x["rush"] == 1).sum(), include_groups=False),
        }
    )
    res.index.names = ["game_id", "team"]
    return res


rows = []
for scope, df in (("fg_", scr), ("h1_", scr[scr.is_1h])):
    off = side_stats(df, "posteam", scope + "off_")
    de = side_stats(df, "defteam", scope + "def_")
    rows += [off, de]
ps = pd.concat(rows, axis=1).reset_index()

# turnovers / sacks / third downs (full game and 1H) by offense
pbp["to"] = (pbp.interception.fillna(0) + pbp.fumble_lost.fillna(0)).clip(upper=1)
pbp["is_1h"] = pbp.game_half == "Half1"
extra = []
for scope, df in (("fg_", pbp), ("h1_", pbp[pbp.is_1h])):
    o = df[df.posteam.notna()].groupby(["game_id", "posteam"]).agg(
        to_=("to", "sum"),
        sacks_taken=("sack", "sum"),
        third_conv=("third_down_converted", "sum"),
        third_fail=("third_down_failed", "sum"),
    )
    o.columns = [scope + "off_" + c for c in o.columns]
    o.index.names = ["game_id", "team"]
    d = df[df.defteam.notna()].groupby(["game_id", "defteam"]).agg(
        takeaways=("to", "sum"), sacks=("sack", "sum")
    )
    d.columns = [scope + "def_" + c for c in d.columns]
    d.index.names = ["game_id", "team"]
    extra += [o, d]
ex = pd.concat(extra, axis=1).reset_index()
ps = ps.merge(ex, on=["game_id", "team"], how="outer")

# ---------- assemble team-game table with scores / results ----------
recs = []
for gid, r in g.iterrows():
    for side, opp_side in (("home", "away"), ("away", "home")):
        pf, pa = r[f"{side}_score"], r[f"{opp_side}_score"]
        pf1, pa1 = r[f"{side}_1h"], r[f"{opp_side}_1h"]
        recs.append(
            {
                "game_id": gid, "week": r.week, "team": r[f"{side}_team"], "opp": r[f"{opp_side}_team"],
                "is_home": side == "home", "pf": pf, "pa": pa, "pf_1h": pf1, "pa_1h": pa1,
                "pf_2h": pf - pf1, "pa_2h": pa - pa1,
                "win": 1.0 if pf > pa else 0.5 if pf == pa else 0.0,
                "ht": "lead" if pf1 > pa1 else "tie" if pf1 == pa1 else "trail",
                "scored_first": r.scored_first == side,
            }
        )
tg = pd.DataFrame(recs).merge(ps, on=["game_id", "team"], how="left")
tg["margin_1h"] = tg.pf_1h - tg.pa_1h
tg["margin_2h"] = tg.pf_2h - tg.pa_2h
tg.to_csv(out / "team_games_2026_thru_wk8.csv", index=False)


# ---------- season-to-date team summary ----------
def summarize(d):
    n = len(d)
    s = lambda c: d[c].sum()  # noqa: E731
    lead, trail, tie = d[d.ht == "lead"], d[d.ht == "trail"], d[d.ht == "tie"]
    o = {
        "gp": n,
        "record": f"{int(d.win.eq(1).sum())}-{int(d.win.eq(0).sum())}" + (f"-{int(d.win.eq(.5).sum())}" if d.win.eq(.5).any() else ""),
        "win_pct": d.win.mean(),
        # full game
        "ppg": d.pf.mean(), "papg": d.pa.mean(), "margin_pg": (d.pf - d.pa).mean(),
        "ypp": s("fg_off_yds") / s("fg_off_plays"), "ypp_allowed": s("fg_def_yds") / s("fg_def_plays"),
        "off_epa": s("fg_off_epa") / s("fg_off_plays"), "def_epa": s("fg_def_epa") / s("fg_def_plays"),
        "off_sr": s("fg_off_success") / s("fg_off_plays"), "def_sr": s("fg_def_success") / s("fg_def_plays"),
        "third_pct": s("fg_off_third_conv") / (s("fg_off_third_conv") + s("fg_off_third_fail")),
        "to_pg": d.fg_off_to_.mean(), "takeaways_pg": d.fg_def_takeaways.mean(),
        "sacks_pg": d.fg_def_sacks.mean(), "sacks_taken_pg": d.fg_off_sacks_taken.mean(),
        # first half
        "h1_ppg": d.pf_1h.mean(), "h1_papg": d.pa_1h.mean(), "h1_margin_pg": d.margin_1h.mean(),
        "h2_margin_pg": d.margin_2h.mean(),
        "h1_off_epa": s("h1_off_epa") / s("h1_off_plays"), "h1_def_epa": s("h1_def_epa") / s("h1_def_plays"),
        "h1_off_sr": s("h1_off_success") / s("h1_off_plays"), "h1_def_sr": s("h1_def_success") / s("h1_def_plays"),
        "h1_off_pass_epa": s("h1_off_pass_epa") / s("h1_off_pass_n"), "h1_off_rush_epa": s("h1_off_rush_epa") / s("h1_off_rush_n"),
        "h1_def_pass_epa": s("h1_def_pass_epa") / s("h1_def_pass_n"), "h1_def_rush_epa": s("h1_def_rush_epa") / s("h1_def_rush_n"),
        "h1_to_pg": d.h1_off_to_.mean(), "h1_takeaways_pg": d.h1_def_takeaways.mean(),
        # halftime state -> final result
        "ht_lead_g": len(lead), "ht_tie_g": len(tie), "ht_trail_g": len(trail),
        "ht_lead_rate": len(lead) / n,
        "win_when_lead_ht": f"{int(lead.win.eq(1).sum())}-{int(lead.win.eq(0).sum())}",
        "win_when_trail_ht": f"{int(trail.win.eq(1).sum())}-{int(trail.win.eq(0).sum())}",
        "scored_first_rate": d.scored_first.mean(),
        "win_when_scored_first": f"{int(d[d.scored_first].win.eq(1).sum())}-{int(d[d.scored_first].win.eq(0).sum())}",
        "h1_win_share": (d.margin_1h > 0).mean(),  # share of games won the 1H outright
        "h1_cover_proxy": (d.margin_1h >= 0).mean(),
    }
    return pd.Series(o)


team = tg.groupby("team").apply(summarize, include_groups=False)
last3 = tg.sort_values("week").groupby("team").tail(3).groupby("team").agg(
    l3_h1_margin_pg=("margin_1h", "mean"), l3_margin_pg=("pf", lambda x: 0)  # placeholder replaced below
)
l3 = tg.sort_values("week").groupby("team").tail(3)
last3 = pd.DataFrame(
    {
        "l3_h1_margin_pg": l3.groupby("team").margin_1h.mean(),
        "l3_margin_pg": (l3.pf - l3.pa).groupby(l3.team).mean(),
        "l3_record": l3.groupby("team").win.apply(lambda w: f"{int(w.eq(1).sum())}-{int(w.eq(0).sum())}"),
    }
)
team = team.join(last3)
# league ranks (1 = best) for the headline numbers
better_high = ["ppg", "margin_pg", "ypp", "off_epa", "off_sr", "third_pct", "takeaways_pg", "sacks_pg", "h1_ppg",
               "h1_margin_pg", "h1_off_epa", "h1_off_sr", "h1_takeaways_pg", "ht_lead_rate", "h1_def_pass_epa_neg"]
team["h1_def_pass_epa_neg"] = -team.h1_def_pass_epa
better_low = ["papg", "ypp_allowed", "def_epa", "def_sr", "to_pg", "sacks_taken_pg", "h1_papg", "h1_def_epa",
              "h1_def_sr", "h1_to_pg"]
for c in better_high:
    team[c + "_rk"] = team[c].rank(ascending=False, method="min").astype(int)
for c in better_low:
    team[c + "_rk"] = team[c].rank(ascending=True, method="min").astype(int)
team = team.drop(columns=["h1_def_pass_epa_neg", "h1_def_pass_epa_neg_rk"])
team.round(4).to_csv(out / "team_summary_2026_thru_wk8.csv")

# ---------- week matchups ----------
wk = games[games.week == WEEK].copy()
pairs = []
for _, r in wk.iterrows():
    a, h = r.away_team, r.home_team
    row = {"game_id": r.game_id, "kickoff_et": f"{r.gameday} {r.gametime}", "away": a, "home": h,
           "location": r.location, "spread_line": r.spread_line, "total_line": r.total_line}
    for side, tm in (("away", a), ("home", h)):
        for c in team.columns:
            row[f"{side}_{c}"] = team.loc[tm, c] if tm in team.index else None
    pairs.append(row)
m = pd.DataFrame(pairs)
m.round(4).to_csv(out / f"week{WEEK}_matchups.csv", index=False)
print("teams:", len(team), "week", WEEK, "games:", len(m))
print("bye teams:", sorted(set(team.index) - set(wk.away_team) - set(wk.home_team)))
