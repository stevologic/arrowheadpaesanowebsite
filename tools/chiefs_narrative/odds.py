"""Predictions, Vegas odds, and prediction-market signals.

Three public, no-key sources, each failing soft:

* **Model prediction** — ESPN's FPI "Matchup Predictor" game projection for the
  next game (a genuine simulation-style win probability, the reliable public
  stand-in for a Madden-style game sim).
* **Vegas odds** — ESPN "pickcenter" (DraftKings) spread / total / moneylines.
* **Prediction markets** — Polymarket futures (Chiefs to win the Super Bowl and
  the AFC), read straight from the public Gamma API.

If ``ODDS_API_KEY`` is set, we additionally pull a sportsbook **consensus** line
from The Odds API. Everything is optional; the pipeline runs without any of it.
"""
from __future__ import annotations

import requests

from . import config

ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={event}"
PM_EVENTS = "https://gamma-api.polymarket.com/events?closed=false&limit=6&order=volume&ascending=false&tag_slug=nfl"
CHIEFS_TEAM_NAME = "Kansas City Chiefs"


def _get_json(url: str, timeout: int = None):
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": config.USER_AGENT, "Accept": "application/json"},
            timeout=timeout or config.HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        print(f"  [odds] warning: {url[:70]}… -> {exc}")
        return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# ESPN: model prediction + Vegas line for the next game
# ---------------------------------------------------------------------------
def fetch_game_prediction(event_id: str) -> dict:
    """Return {model, vegas} for a single ESPN event id."""
    out = {"model": None, "vegas": None}
    if not event_id:
        return out
    data = _get_json(ESPN_SUMMARY.format(event=event_id))
    if not data:
        return out

    # --- Model (FPI Matchup Predictor) ---
    pred = data.get("predictor") or {}
    try:
        header = data.get("header", {})
        comp = header.get("competitions", [{}])[0]
        competitors = comp.get("competitors", [])
        kc_home = None
        for c in competitors:
            if c.get("team", {}).get("abbreviation") == "KC":
                kc_home = c.get("homeAway") == "home"
        if kc_home is not None and pred:
            home_proj = _num(pred.get("homeTeam", {}).get("gameProjection"))
            away_proj = _num(pred.get("awayTeam", {}).get("gameProjection"))
            kc_win = home_proj if kc_home else away_proj
            opp_win = away_proj if kc_home else home_proj
            if kc_win is not None:
                out["model"] = {
                    "kcWin": round(kc_win, 1),
                    "oppWin": round(opp_win, 1) if opp_win is not None else None,
                    "label": "ESPN FPI game projection",
                    "source": "ESPN FPI",
                    "url": "https://www.espn.com/nfl/fpi",
                }
    except Exception as exc:  # noqa: BLE001
        print(f"  [odds] predictor parse issue: {exc}")

    # --- Vegas (pickcenter / DraftKings) ---
    pc = data.get("pickcenter") or []
    if pc:
        p = pc[0]
        home_ml = _num(p.get("homeTeamOdds", {}).get("moneyLine"))
        away_ml = _num(p.get("awayTeamOdds", {}).get("moneyLine"))
        # Determine KC side from favorite flag on the detail string when possible.
        details = p.get("details", "")  # e.g. "KC -3"
        out["vegas"] = {
            "provider": p.get("provider", {}).get("name", "Sportsbook"),
            "spreadDetail": details,
            "overUnder": _num(p.get("overUnder")),
            "homeMoneyline": home_ml,
            "awayMoneyline": away_ml,
            "source": "ESPN / " + p.get("provider", {}).get("name", "Sportsbook"),
            "url": "https://www.espn.com/nfl/lines",
        }
    return out


# ---------------------------------------------------------------------------
# The Odds API (optional consensus, needs ODDS_API_KEY)
# ---------------------------------------------------------------------------
def fetch_odds_api_consensus() -> dict | None:
    key = config.env("ODDS_API_KEY")
    if not key:
        return None
    url = (
        "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"
        f"?regions=us&markets=h2h,spreads,totals&oddsFormat=american&apiKey={key}"
    )
    data = _get_json(url, timeout=25)
    if not isinstance(data, list):
        return None
    for game in data:
        teams = f"{game.get('home_team','')} {game.get('away_team','')}"
        if "Chiefs" not in teams:
            continue
        opp = (
            game.get("away_team")
            if game.get("home_team") == CHIEFS_TEAM_NAME
            else game.get("home_team")
        )
        books = game.get("bookmakers", [])
        if not books:
            continue
        return {
            "opponent": opp,
            "books": len(books),
            "sample": books[0].get("title"),
            "source": "The Odds API (consensus)",
            "url": "https://the-odds-api.com/",
        }
    return None


# ---------------------------------------------------------------------------
# Polymarket futures
# ---------------------------------------------------------------------------
def _pct_from_market(market: dict) -> float | None:
    prices = market.get("outcomePrices")
    outcomes = market.get("outcomes")
    import json as _json

    if isinstance(prices, str):
        try:
            prices = _json.loads(prices)
        except Exception:  # noqa: BLE001
            prices = None
    if isinstance(outcomes, str):
        try:
            outcomes = _json.loads(outcomes)
        except Exception:  # noqa: BLE001
            outcomes = None
    if prices and outcomes:
        for name, price in zip(outcomes, prices):
            if str(name).strip().lower() == "yes":
                v = _num(price)
                return round(v * 100, 1) if v is not None else None
    last = _num(market.get("lastTradePrice"))
    return round(last * 100, 1) if last is not None else None


def fetch_polymarket_futures() -> list[dict]:
    """Return Chiefs futures probabilities from the biggest NFL markets."""
    data = _get_json(PM_EVENTS)
    if not isinstance(data, list):
        return []
    wanted = ("champion", "super bowl", "afc")
    out = []
    for event in data:
        title = (event.get("title") or "").strip()
        low = title.lower()
        if not any(w in low for w in wanted):
            continue
        # Skip "where will X play" style player markets.
        if "where will" in low or "mvp" in low:
            continue
        for market in event.get("markets", []):
            git = (market.get("groupItemTitle") or "").strip()
            if git != CHIEFS_TEAM_NAME:
                continue
            pct = _pct_from_market(market)
            if pct is None:
                continue
            slug = event.get("slug", "")
            out.append(
                {
                    "label": title,
                    "chiefsPct": pct,
                    "source": "Polymarket",
                    "url": f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com/",
                }
            )
            break
    # De-dup by label, keep first (highest volume ordering preserved).
    seen = set()
    unique = []
    for row in out:
        if row["label"] in seen:
            continue
        seen.add(row["label"])
        unique.append(row)
    return unique[:4]


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------
def collect_markets(next_game: dict | None) -> dict:
    print("  [odds] fetching model prediction, Vegas line, prediction markets…")
    game = fetch_game_prediction(next_game.get("id") if next_game else None)
    consensus = fetch_odds_api_consensus()
    if consensus and game.get("vegas"):
        game["vegas"]["consensus"] = consensus
    futures = fetch_polymarket_futures()
    result = {
        "model": game.get("model"),
        "vegas": game.get("vegas"),
        "consensus": consensus,
        "futures": futures,
    }
    bits = []
    if result["model"]:
        bits.append(f"model KC {result['model']['kcWin']}%")
    if result["vegas"]:
        bits.append(f"line {result['vegas'].get('spreadDetail','?')}")
    if futures:
        bits.append(f"{len(futures)} PM futures")
    print("  [odds] " + (", ".join(bits) if bits else "no market data available"))
    return result
