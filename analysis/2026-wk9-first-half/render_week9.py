import sys, pandas as pd
out = sys.argv[1]; wk = int(sys.argv[2])
m = pd.read_csv(f"{out}/week{wk}_matchups.csv")
t = pd.read_csv(f"{out}/team_summary_2026_thru_wk8.csv", index_col=0)
def f(x, p=1, sign=False): return f"{x:+.{p}f}" if sign else f"{x:.{p}f}"
def rk(team, c): return int(t.loc[team, c + "_rk"])
L = [f"# NFL Week {wk} matchups: full-game vs first-half profile",
     "", "2026 regular season through Week 8 (nflverse play-by-play, EPA/play on pass+rush plays). "
     "Rank in brackets is of 32, 1 = best. 7-8 games per team, so treat the splits as directional, not predictive.",
     "League baseline: 23.1 PPG, 11.2 first-half PPG; teams leading at half have won 76% (84 of 111); scoring first has won 62%.", ""]
for _, r in m.iterrows():
    a, h = r.away, r.home
    L += [f"## {a} @ {h}  ({r.kickoff_et} ET)" + (" - neutral site" if r.location == "Neutral" else ""), ""]
    L += [f"| | {a} | {h} |", "|---|---|---|"]
    def row(label, c, p=1, sign=False, rank=True):
        cells = []
        for tm in (a, h):
            v = t.loc[tm, c]
            cells.append(f"{f(v,p,sign)}" + (f" [{rk(tm,c)}]" if rank else ""))
        L.append(f"| {label} | {cells[0]} | {cells[1]} |")
    def txt(label, c):
        L.append(f"| {label} | {t.loc[a,c]} | {t.loc[h,c]} |")
    txt("Record (last 3)", "record")
    L[-1] = f"| Record (last 3) | {t.loc[a,'record']} ({t.loc[a,'l3_record']}) | {t.loc[h,'record']} ({t.loc[h,'l3_record']}) |"
    L.append("| **Full game** | | |")
    row("Points / game", "ppg"); row("Points allowed / game", "papg"); row("Yards / play", "ypp", 2); row("Yards / play allowed", "ypp_allowed", 2)
    row("Offense EPA/play", "off_epa", 3, True); row("Defense EPA/play (lower better)", "def_epa", 3, True)
    row("Turnovers / game", "to_pg", 2); row("Takeaways / game", "takeaways_pg", 2)
    L.append("| **First half** | | |")
    row("1H points / game", "h1_ppg"); row("1H points allowed / game", "h1_papg"); row("1H margin / game", "h1_margin_pg", 1, True)
    row("2H margin / game", "h2_margin_pg", 1, True, rank=False)
    row("1H offense EPA/play", "h1_off_epa", 3, True); row("1H defense EPA/play (lower better)", "h1_def_epa", 3, True)
    row("1H pass EPA/play (off)", "h1_off_pass_epa", 3, True, rank=False); row("1H rush EPA/play (off)", "h1_off_rush_epa", 3, True, rank=False)
    L.append("| **Winning the game from the half** | | |")
    L.append(f"| Led / tied / trailed at half | {t.loc[a,'ht_lead_g']}/{t.loc[a,'ht_tie_g']}/{t.loc[a,'ht_trail_g']} | {t.loc[h,'ht_lead_g']}/{t.loc[h,'ht_tie_g']}/{t.loc[h,'ht_trail_g']} |")
    txt("W-L when leading at half", "win_when_lead_ht"); txt("W-L when trailing at half", "win_when_trail_ht")
    L.append(f"| Scored first (W-L when they did) | {t.loc[a,'scored_first_rate']:.0%} ({t.loc[a,'win_when_scored_first']}) | {t.loc[h,'scored_first_rate']:.0%} ({t.loc[h,'win_when_scored_first']}) |")
    row("Last 3 1H margin / game", "l3_h1_margin_pg", 1, True, rank=False)
    # one-line read
    edge = (t.loc[h,'h1_margin_pg'] - t.loc[a,'h1_margin_pg'])
    eedge = ((t.loc[h,'h1_off_epa'] - t.loc[h,'h1_def_epa']) - (t.loc[a,'h1_off_epa'] - t.loc[a,'h1_def_epa']))
    side = h if eedge > 0 else a
    L += ["", f"**1H read:** 1H margin gap {abs(edge):.1f} pts favoring {h if edge>0 else a}; 1H net EPA/play favors {side} by {abs(eedge):.3f}.", ""]
open(f"{out}/week{wk}_matchups.md", "w").write("\n".join(L))
# compact leaderboard
cols = ["record","ppg","papg","h1_margin_pg","h1_off_epa","h1_def_epa","ht_lead_rate","win_when_lead_ht","win_when_trail_ht"]
print(t.sort_values("h1_margin_pg", ascending=False)[cols].round(3).to_string())
