"""Email-safe HTML and plain-text views of one immutable NBA report snapshot."""

from dataclasses import dataclass
from html import escape
from math import isfinite
from typing import Optional
from urllib.parse import urlparse


@dataclass(frozen=True)
class Digest:
    subject: str
    html: str
    text: str


def _pct(value: float) -> str:
    return f"{100 * value:.1f}%"


def _american(value: int) -> str:
    return f"{value:+d}"


def render_nba_digest(report: dict, report_url: Optional[str] = None) -> Digest:
    """Render a stored report; never reprice or fetch fresh odds while sending.

    The linked web report can be interactive. Email clients receive a static
    chart preview and table because scripts are widely unsupported in email.
    """
    if report.get("kind") != "nba_daily_report" or not report.get("slate_date"):
        raise ValueError("expected a saved NBA daily report")
    games = report.get("games")
    if not isinstance(games, list) or not games:
        raise ValueError("no NBA games: do not send a digest")
    saved_picks = report.get("picks")
    if not isinstance(saved_picks, list):
        raise ValueError("saved NBA report has no picks list")
    if report_url is not None:
        parsed = urlparse(report_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("report_url must be an HTTPS web page")
    picks = [pick for pick in saved_picks if pick.get("tier") == "targeted"]
    slate = str(report["slate_date"])
    as_of = str(report.get("odds_as_of") or "unavailable")
    subject = f"NBA outlook — {slate}"
    game_rows = []
    text_games = []
    for game in games:
        away, home = str(game["away"]), str(game["home"])
        win = float(game["p_home_win"])
        if not isfinite(win) or not 0 <= win <= 1:
            raise ValueError("invalid game probability")
        margin = float(game["pred_margin"])
        if not isfinite(margin):
            raise ValueError("invalid predicted margin")
        market = game.get("market_home_win_prob")
        if market is not None and (not isfinite(float(market)) or not 0 <= float(market) <= 1):
            raise ValueError("invalid market probability")
        market_text = "—" if market is None else _pct(float(market))
        width = round(100 * win)
        matchup = f"{away} @ {home}"
        game_rows.append(
            f'<tr><td style="padding:8px;border-bottom:1px solid #ddd">{escape(matchup)}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #ddd">{_pct(win)}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #ddd">{margin:+.1f}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #ddd">{market_text}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #ddd;width:120px">'
            f'<div style="background:#e7ebef;height:10px;width:100px">'
            f'<div style="background:#386ac8;height:10px;width:{width}px"></div>'
            '</div></td></tr>'
        )
        text_games.append(
            f"{matchup}: home win {_pct(win)}, margin {margin:+.1f}, market {market_text}"
        )
    pick_rows = []
    text_picks = []
    for pick in picks:
        matchup = f"{pick['away']} @ {pick['home']}"
        team, book = str(pick["team"]), str(pick["book"])
        price, ev = int(pick["price_american"]), float(pick["ev"])
        fanduel_price = pick.get("fanduel_price_american")
        fd_text = "—" if fanduel_price is None else _american(int(fanduel_price))
        if not isfinite(ev):
            raise ValueError("invalid pick EV")
        pick_rows.append(
            f'<tr><td style="padding:8px;border-bottom:1px solid #ddd">{escape(matchup)}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #ddd">{escape(team)}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #ddd">{escape(book)}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #ddd">{_american(price)}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #ddd">{fd_text}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #ddd">{_pct(ev)}</td></tr>'
        )
        text_picks.append(
            f"{matchup}: {team} {book} {_american(price)}, FanDuel {fd_text}, net EV {_pct(ev)}"
        )
    pick_section = (
        '<table role="presentation" style="border-collapse:collapse;width:100%">'
        '<thead><tr><th>Game</th><th>Side</th><th>Book</th>'
        '<th>Selected price</th><th>FanDuel</th><th>Net EV</th></tr></thead>'
        f'<tbody>{"".join(pick_rows)}</tbody></table>'
        if pick_rows else '<p>No value picks cleared the current threshold.</p>'
    )
    link = (f'<p><a href="{escape(report_url, quote=True)}">Open the interactive report</a></p>'
            if report_url else "")
    html = (
        '<!doctype html><html><head><meta charset="utf-8"></head>'
        '<body style="font-family:Arial,sans-serif;color:#172033;max-width:720px;margin:auto">'
        f'<h1>{escape(subject)}</h1>'
        f'<p>Latest odds capture: {escape(as_of)}. '
        'Game quotes may have different capture times. '
        'Prices may change before a bet is placed.</p>'
        '<h2>Game outlook</h2>'
        '<table role="presentation" style="border-collapse:collapse;width:100%">'
        '<thead><tr><th>Game</th><th>Home win</th><th>Margin</th><th>Market</th>'
        f'<th>Probability preview</th></tr></thead><tbody>{"".join(game_rows)}</tbody></table>'
        f'<h2>Targeted value picks</h2>{pick_section}{link}'
        '<p style="font-size:12px;color:#667">Model estimates, not guaranteed outcomes. '
        'Check the live book price and availability before acting.</p></body></html>'
    )
    text = "\n".join([
        subject,
        f"Latest odds capture: {as_of}. Game quotes may have different capture times.",
        "", "Game outlook", *text_games,
        "", "Targeted value picks",
        *(text_picks or ["No value picks cleared the current threshold."]),
        *(["", f"Interactive report: {report_url}"] if report_url else []),
        "", "Model estimates, not guaranteed outcomes. Check live prices and availability.",
    ])
    return Digest(subject, html, text)
