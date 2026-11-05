# ruff: noqa: E501  (inline HTML and CSS templates)
"""A Covers-style page of one NFL week's matchups: lines, team stats, first half, records, injuries, h2h.

Reads the same data the /games/{id}/context endpoint serves (SELECTs only), so run it against any
database the app is pointed at; it writes one standalone HTML file (no network, no assets).
Usage:  python backend/scripts/week_matchups_html.py 9 [out.html]
"""

import asyncio
import sys
from datetime import datetime, timezone
from html import escape as e
from pathlib import Path
from zoneinfo import ZoneInfo

from app.api.matchup import get_context
from app.api.slate import week_slate
from app.config import load_settings
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ET = ZoneInfo("America/New_York")


async def fetch(week: int) -> list[dict]:
    """Each game of the week as {"game": SlateGame, "ctx": ContextResponse}, as plain dicts."""
    engine = create_async_engine(load_settings().database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    try:
        async with factory() as session:
            slate = await week_slate(session, "nfl", None, None, week, now)
        items = []
        for game in slate.games:
            ctx = await get_context(game.id, 5, factory, now)
            items.append({"game": game.model_dump(mode="json"), "ctx": ctx.model_dump(mode="json")})
        return items
    finally:
        await engine.dispose()


def t(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET)


def clock(dt):
    return dt.strftime("%I:%M %p").lstrip("0")


def sp(x):  # signed spread, pick'em as PK
    return "PK" if x == 0 else f"{x:+g}"


def ml(x):
    return "-" if x is None else f"{x:+d}"


def tally(v):
    return f"{v['win']}-{v['loss']}" + (f"-{v['push']}" if v["push"] else "")


def cur(g):
    return g["lines"].get("current") or g["lines"].get("close") or g["lines"].get("open")


METRICS = [
    ("Scoring", [("points_for_pg", "Points / game", "{:.1f}"), ("points_against_pg", "Points allowed / game", "{:.1f}"),
                 ("yards_play", "Yards / play", "{:.2f}"), ("yards_play_allowed", "Yards / play allowed", "{:.2f}")]),
    ("EPA / play", [("off_epa_play", "Offense", "{:+.3f}"), ("def_epa_play", "Defense (lower is better)", "{:+.3f}"),
                    ("off_pass_epa_play", "Pass offense", "{:+.3f}"), ("def_pass_epa_play", "Pass defense", "{:+.3f}"),
                    ("off_rush_epa_play", "Rush offense", "{:+.3f}"), ("def_rush_epa_play", "Rush defense", "{:+.3f}")]),
    ("Ball security & pressure", [("turnovers_pg", "Turnovers / game", "{:.2f}"), ("takeaways_pg", "Takeaways / game", "{:.2f}"),
                                  ("sacks_pg", "Sacks / game", "{:.2f}"), ("sacks_taken_pg", "Sacks taken / game", "{:.2f}")]),
]


def chip(m, fmt):
    if not m:
        return '<td class="v">-</td>'
    cls = "good" if m["rank"] <= 10 else "bad" if m["rank"] > m["of"] - 10 else "mid"
    return f'<td class="v"><b>{fmt.format(m["value"])}</b> <span class="rk {cls}">{m["rank"]}</span></td>'


def stats_table(c):
    a, h = c["away"], c["home"]
    rows = [f'<tr><th>{e(a["team"]["abbr"])}</th><th></th><th>{e(h["team"]["abbr"])}</th></tr>']
    for group, items in METRICS:
        rows.append(f'<tr class="grp"><td colspan="3">{group}</td></tr>')
        for key, label, fmt in items:
            rows.append(f'<tr>{chip(a["efficiency"].get(key), fmt)}<td class="lab">{label}</td>'
                        f'{chip(h["efficiency"].get(key), fmt)}</tr>')
    return ('<table class="cmp">' + "".join(rows) + "</table>"
            "<p class='note'>Chip = league rank of 32 (1 = best), regular season to date.</p>")


FIRST_HALF_METRICS = [
    ("First half", [("h1_points_for_pg", "1H points / game", "{:.1f}"), ("h1_points_against_pg", "1H points allowed / game", "{:.1f}"),
                    ("h1_margin_pg", "1H margin / game", "{:+.1f}")]),
    ("First-half EPA / play", [("h1_off_epa_play", "Offense", "{:+.3f}"), ("h1_def_epa_play", "Defense (lower is better)", "{:+.3f}"),
                               ("h1_off_pass_epa_play", "Pass offense", "{:+.3f}"), ("h1_off_rush_epa_play", "Rush offense", "{:+.3f}")]),
    ("First-half ball security", [("h1_turnovers_pg", "Turnovers / game", "{:.2f}"), ("h1_takeaways_pg", "Takeaways / game", "{:.2f}")]),
]


def signed(x):
    return "-" if x is None else f"{x:+.1f}"


def first_half_table(c):
    a, h = c["away"], c["home"]
    if not (a.get("first_half") or h.get("first_half")):
        return "<p class='note'>No first-half data loaded yet.</p>"

    def metrics(tm):
        return (tm.get("first_half") or {}).get("metrics", {})

    def plain(tm, read):
        fh = tm.get("first_half")
        return f'<td class="v">{read(fh) if fh else "-"}</td>'

    def split(label, read):
        return f'<tr>{plain(a, read)}<td class="lab">{label}</td>{plain(h, read)}</tr>'

    def tally_cell(key):
        return lambda fh: ("-" if sum(fh["halftime"][key].values()) == 0
                           else tally(fh["halftime"][key]))

    def first_cell(fh):
        sf = fh["scored_first"]
        return "-" if sf["of"] == 0 else f'{sf["games"]} of {sf["of"]} ({tally(sf["record"])})'

    rows = [f'<tr><th>{e(a["team"]["abbr"])}</th><th></th><th>{e(h["team"]["abbr"])}</th></tr>']
    for group, items in FIRST_HALF_METRICS:
        rows.append(f'<tr class="grp"><td colspan="3">{group}</td></tr>')
        for key, label, fmt in items:
            rows.append(f'<tr>{chip(metrics(a).get(key), fmt)}<td class="lab">{label}</td>'
                        f'{chip(metrics(h).get(key), fmt)}</tr>')
        if group == "First half":
            rows.append(split(f"1H margin, last {c['last_n']}", lambda fh: signed(fh["recent_margin_pg"])))
            rows.append(split("2H margin / game", lambda fh: signed(fh["second_half_margin_pg"])))
    rows.append('<tr class="grp"><td colspan="3">From the half to the final (W-L)</td></tr>')
    rows.append(split("Led at the half", tally_cell("led")))
    rows.append(split("Trailed at the half", tally_cell("trailed")))
    rows.append(split("Tied at the half", tally_cell("tied")))
    rows.append(split("Scored first (record when they did)", first_cell))
    games = " vs ".join(f'{tm["team"]["abbr"]} {(tm.get("first_half") or {}).get("games", 0)}' for tm in (a, h))
    return ('<table class="cmp">' + "".join(rows) + "</table>"
            f"<p class='note'>Chip = league rank of 32 (1 = best), final games to date ({games}). "
            "Small samples early in the season. Source: nflverse play-by-play.</p>")


def records_table(c):
    a, h = c["away"], c["home"]
    spec = [("season", "Season"), ("last_n", f"Last {c['last_n']}"), ("away", "Away"), ("home", "Home"),
            ("favorite", "As favorite"), ("underdog", "As underdog")]
    head = '<tr><th rowspan="2"></th>' + "".join(
        f'<th colspan="2">{n}</th>' for n in ("Straight up", "Against the spread", "Over / under")) + "</tr>"
    head += "<tr>" + "".join(f"<th>{x}</th>" for x in (a["team"]["abbr"], h["team"]["abbr"]) * 3) + "</tr>"
    body = ""
    for key, label in spec:
        cells = "".join(f"<td>{tally(tm['records'][key][m])}</td>" for m in ("su", "ats", "ou") for tm in (a, h))
        body += f'<tr><td class="lab">{label}</td>{cells}</tr>'
    return (f'<div class="scroll"><table class="rec">{head}{body}</table></div>'
            "<p class='note'>Over/under shows overs-unders. Away and home rows are each team's own record in that role.</p>")


def situation(c):
    def line(s):
        bits = []
        if s["rest_days"] is not None:
            bits.append(f"{s['rest_days']} days rest" + (" (short week)" if s["short_week"] else " (off bye)" if s["off_bye"] else ""))
        if s["travel_miles"] is not None:
            bits.append(f"{s['travel_miles']:,} mi" + (f", {abs(s['tz_crossed'])} tz" if s["tz_crossed"] else ""))
        if s["previous_opponent"]:
            bits.append(f"last: {e(s['previous_opponent'])}")
        return " · ".join(bits) or "-"

    return "".join(
        f'<tr><td class="lab">{e(c[side]["team"]["abbr"])}</td><td>{line(c[side]["situation"])}</td></tr>'
        for side in ("away", "home")).join(['<table class="sit">', "</table>"])


def injuries(c):
    out = ""
    for side in ("away", "home"):
        tm = c[side]
        inj = tm["injuries"]
        grp = {k: [i for i in inj if i["status"] == k] for k in ("Out", "Doubtful", "Questionable", "Injured Reserve")}

        def names(items, cap=12):
            shown = [f'{e(i["player"])} <i>{e(i["position"] or "")}</i>' for i in items[:cap]]
            return ", ".join(shown) + (f" +{len(items) - cap} more" if len(items) > cap else "")

        qb = tm["qb_flag"]
        qbline = f'<div class="qb">QB: {e(qb["player"])} - {e(qb["status"])}</div>' if qb else ""
        rows = ""
        for k, cls in (("Out", "out"), ("Doubtful", "dbt"), ("Questionable", "q")):
            if grp[k]:
                rows += f'<div class="inj {cls}"><span>{k} ({len(grp[k])})</span> {names(grp[k])}</div>'
        if grp["Injured Reserve"]:
            rows += f'<div class="inj ir"><span>IR ({len(grp["Injured Reserve"])})</span> {names(grp["Injured Reserve"], 6)}</div>'
        out += f'<div class="injcol"><h4>{e(tm["team"]["name"])}</h4>{qbline}{rows or "<div class=inj>None reported</div>"}</div>'
    return f'<div class="injgrid">{out}</div>'


def h2h(c):
    hh = c["head_to_head"]
    if not hh["meetings"]:
        return "<p class='note'>No prior meetings in the database.</p>"
    rows = ""
    for m in reversed(hh["meetings"]):
        spread = "" if m["spread_home"] is None else f'{e(m["home"])} {sp(m["spread_home"])}'
        tot = "" if m["total"] is None else f'{m["total"]:g}'
        rows += (f'<tr><td>{t(m["start_time"]).strftime("%b %d, %Y")}</td>'
                 f'<td>{e(m["away"])} {m["away_score"]} @ {e(m["home"])} {m["home_score"]}</td>'
                 f'<td>{spread}</td><td>{tot}</td></tr>')
    return (f'<table class="h2h"><tr><th>Date</th><th>Score</th><th>Spread</th><th>Total</th></tr>{rows}</table>'
            f'<p class="note">{e(c["home"]["team"]["abbr"])} in this series: {tally(hh["home_su"])} SU, '
            f'{tally(hh["home_ats"])} ATS.</p>')


def angles(item):
    g, c = item["game"], item["ctx"]
    a = []
    for side in ("away", "home"):
        abbr = c[side]["team"]["abbr"]
        qb = c[side]["qb_flag"]
        if qb:
            a.append(("hot" if qb["status"] in ("Out", "Doubtful") else "warn", f'{abbr} QB {qb["player"]}: {qb["status"]}'))
        s = c[side]["situation"]
        if s["short_week"]:
            a.append(("warn", f'{abbr} short week ({s["rest_days"]} days)'))
        if s["off_bye"]:
            a.append(("", f"{abbr} off bye"))
        if s["tz_crossed"] and abs(s["tz_crossed"]) >= 2:
            a.append(("", f'{abbr} crosses {abs(s["tz_crossed"])} time zones'))
    if g["spread_move"] is not None and abs(g["spread_move"]) >= 3:
        a.append(("hot", f'Line moved {abs(g["spread_move"]):g} pts since open'))
    if g["total_move"] is not None and abs(g["total_move"]) >= 3:
        a.append(("warn", f'Total {"down" if g["total_move"] < 0 else "up"} {abs(g["total_move"]):g} since open'))
    if c["divisional"]:
        a.append(("", "Divisional game"))
    if c["primetime"]:
        a.append(("", "Primetime"))
    w = g["weather"]
    if g["roof"] == "outdoors" and w and (w["wind_mph"] >= 15 or w["precip_pct"] >= 50 or w["temp_f"] <= 35):
        bits = [x for x in (f'wind {w["wind_mph"]:.0f} mph' if w["wind_mph"] >= 15 else "",
                            f'{w["precip_pct"]:.0f}% precip' if w["precip_pct"] >= 50 else "",
                            f'{w["temp_f"]:.0f}F' if w["temp_f"] <= 35 else "") if x]
        a.append(("warn", "Weather: " + ", ".join(bits)))
    return "".join(f'<span class="ang {k}">{e(txt)}</span>' for k, txt in a)


def line_box(g):
    ln, op = cur(g), g["lines"].get("open")
    if not ln:
        return "<p class='note'>No line posted.</p>"
    a, h = g["away"]["abbr"], g["home"]["abbr"]
    sh = ln["spread_home"]
    sa = None if sh is None else -sh
    op_h = op["spread_home"] if op else None
    op_a = None if op_h is None else -op_h

    def opened(now, then, f=sp):
        return "" if then is None or now == then else f"<small>open {f(then)}</small>"

    def spread_cell(val, price, then):
        return f'<b>{"-" if val is None else sp(val)}</b> <small>({ml(price)})</small>{opened(val, then)}'

    total_open = op["total"] if op else None
    rows = (
        "<tr><th></th><th>Spread</th><th>Moneyline</th><th>Total</th></tr>"
        f'<tr><td class="lab">{e(a)}</td><td>{spread_cell(sa, ln["spread_away_price"], op_a)}</td>'
        f'<td><b>{ml(ln["ml_away"])}</b></td><td><b>O {ln["total"]:g}</b> <small>({ml(ln["over_price"])})</small></td></tr>'
        f'<tr><td class="lab">{e(h)}</td><td>{spread_cell(sh, ln["spread_home_price"], op_h)}</td>'
        f'<td><b>{ml(ln["ml_home"])}</b></td><td><b>U {ln["total"]:g}</b> <small>({ml(ln["under_price"])})</small>'
        f'{opened(ln["total"], total_open, lambda v: format(v, "g"))}</td></tr>'
    )
    cap = t(ln["captured_at"])
    return (f'<table class="lines">{rows}</table><p class="note">{e(ln["source"].upper())} line, captured '
            f'{cap.strftime("%a %b %d")} {clock(cap)} ET</p>')


def card(item):
    g, c = item["game"], item["ctx"]
    k = t(g["start_time"])
    w = g["weather"]
    if g["roof"] == "outdoors":
        wx = f'{w["temp_f"]:.0f}°F · wind {w["wind_mph"]:.0f} mph · {w["precip_pct"]:.0f}% precip · {e(w["conditions"])}' if w else ""
    else:
        wx = "Dome" if g["roof"] == "dome" else "Retractable roof"
    d = c["data_as_of"]
    def stamp(v):
        return t(v).strftime("%b %d") + " " + clock(t(v)) if v else "-"

    return f"""<article class="game" id="g{g['id']}">
<header><div class="when">{k.strftime("%a %b %d")} · {clock(k)} ET</div>
<h2><span class="tm">{e(g['away']['name'])}</span><span class="at">@</span><span class="tm">{e(g['home']['name'])}</span></h2>
<div class="where">{e(g['venue'] or '')}{(' · ' + wx) if wx else ''}</div>
<div class="angles">{angles(item)}</div></header>
<div class="cols"><section><h3>Betting lines</h3>{line_box(g)}</section>
<section><h3>Schedule spot</h3>{situation(c)}</section></div>
<section><h3>Team stats</h3>{stats_table(c)}</section>
<section><h3>First half</h3>{first_half_table(c)}</section>
<section><h3>Records</h3>{records_table(c)}</section>
<section><h3>Injuries</h3>{injuries(c)}</section>
<section><h3>Head to head</h3>{h2h(c)}</section>
<footer class="note">Data as of: records {stamp(d['records'])} · first half {stamp(d.get('first_half'))} · injuries {stamp(d['injuries'])} ET</footer></article>"""


def board_row(item):
    g = item["game"]
    ln = cur(g)
    k = t(g["start_time"])
    sh = ln["spread_home"] if ln else None
    fav = "-" if sh is None else (f'{g["home"]["abbr"]} {sp(sh)}' if sh <= 0 else f'{g["away"]["abbr"]} {sp(-sh)}')
    total = f'{ln["total"]:g}' if ln else "-"
    mls = f'{ml(ln["ml_away"])} / {ml(ln["ml_home"])}' if ln else "-"
    return (f'<tr><td>{k.strftime("%a")} {clock(k)}</td>'
            f'<td><a href="#g{g["id"]}">{e(g["away"]["abbr"])} @ {e(g["home"]["abbr"])}</a></td>'
            f'<td>{fav}</td><td>{total}</td><td>{mls}</td><td class="flags">{angles(item)}</td></tr>')


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#14181f;--mut:#5b6573;--line:#e2e6ec;--acc:#0b5fff;--good:#0f8a4b;--bad:#c4321f;--warn:#b26a00;--chip:#eef1f5}
@media (prefers-color-scheme:dark){:root{--bg:#0f1318;--card:#171c23;--ink:#e8ecf1;--mut:#97a1b0;--line:#2a313b;--acc:#6ea2ff;--good:#4cc38a;--bad:#ff7a66;--warn:#f0b04a;--chip:#222a34}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:980px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:26px;margin:4px 0 2px}.sub{color:var(--mut);margin:0 0 18px}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%}th,td{padding:6px 8px;text-align:center;border-bottom:1px solid var(--line)}
th{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);font-weight:600}
.lab{text-align:left;color:var(--mut)}small{color:var(--mut);margin-left:4px}
.board{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:6px 10px;margin-bottom:24px}
.board td{white-space:nowrap}.board td:nth-child(2){text-align:left;font-weight:600}.board td.flags{text-align:left;white-space:normal;min-width:220px}
.game{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:0 0 22px;scroll-margin-top:12px}
.when{color:var(--acc);font-weight:600;font-size:13px}.game h2{margin:4px 0;font-size:21px;display:flex;gap:10px;flex-wrap:wrap;align-items:baseline}
.at{color:var(--mut);font-weight:400}.where{color:var(--mut);font-size:13px}
h3{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin:18px 0 6px}
.cols{display:grid;grid-template-columns:1.4fr 1fr;gap:16px}@media(max-width:700px){.cols{grid-template-columns:1fr}}
.ang{display:inline-block;font-size:12px;padding:2px 8px;border-radius:999px;background:var(--chip);margin:6px 6px 0 0}
.ang.hot{color:var(--bad);font-weight:600}.ang.warn{color:var(--warn);font-weight:600}
.cmp .v{width:38%}.cmp .lab{text-align:center;width:24%}.cmp tr.grp td{background:var(--chip);font-size:12px;font-weight:700;text-align:left;color:var(--mut)}
.rk{display:inline-block;min-width:22px;padding:0 5px;border-radius:6px;font-size:12px;background:var(--chip)}
.rk.good{color:var(--good);font-weight:700}.rk.bad{color:var(--bad);font-weight:700}
.rec th,.rec td{font-size:13px;padding:5px 6px}.sit td{text-align:left}.sit .lab{width:46px;font-weight:600;color:var(--ink)}
.injgrid{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:700px){.injgrid{grid-template-columns:1fr}}
.injcol h4{margin:0 0 6px;font-size:14px}.inj{font-size:13px;margin:4px 0}.inj span{font-weight:700;margin-right:4px}
.inj.out span{color:var(--bad)}.inj.dbt span{color:var(--warn)}.inj i{color:var(--mut);font-style:normal;font-size:11px}
.qb{font-weight:700;color:var(--bad);font-size:13px;margin-bottom:4px}
.h2h td,.h2h th{font-size:13px}.note{color:var(--mut);font-size:12px;margin:6px 0 0}footer.note{margin-top:14px}
"""


def render(data: list[dict]) -> str:
    week, season = data[0]["game"]["week"], data[0]["game"]["season"]
    order = sorted(data, key=lambda i: i["game"]["start_time"])
    asof = max((cur(i["game"])["captured_at"] for i in order if cur(i["game"])), default=None)
    asof_txt = f'{t(asof).strftime("%a %b %d")} {clock(t(asof))} ET' if asof else "-"
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>NFL Week {week} Matchups</title><style>{CSS}</style></head><body><div class="wrap">
    <h1>NFL Week {week} matchups</h1>
    <p class="sub">{season} regular season · {len(order)} games · lines as of {asof_txt} · informational, not betting advice</p>
    <div class="board scroll"><table><tr><th>Kickoff</th><th>Game</th><th>Favorite</th><th>Total</th><th>ML (away / home)</th><th>Notes</th></tr>
    {''.join(board_row(i) for i in order)}</table></div>
    {''.join(card(i) for i in order)}
    </div></body></html>"""
    return html


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].isdigit():
        print(__doc__)
        return 2
    week = int(sys.argv[1])
    data = asyncio.run(fetch(week))
    if not data:
        print(f"No NFL games found for week {week}.")
        return 1
    out = Path(sys.argv[2] if len(sys.argv) > 2 else f"nfl_week{week}_matchups.html")
    out.write_text(render(data), encoding="utf-8")
    print(f"wrote {out} ({len(data)} games)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
