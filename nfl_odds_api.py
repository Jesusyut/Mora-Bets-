# nfl_odds_api.py
import os
import requests
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
BASE = "https://api.the-odds-api.com/v4"

# Keep this list tight to maximize hit-rate for props availability
DEFAULT_MARKETS = [
    "player_pass_yds",
    "player_pass_tds",
    "player_rush_yds",
    "player_rush_tds",
    "player_receiving_yds",
    "player_receptions",
    "player_receiving_tds",
]

PREFERRED_BOOKMAKERS = [
    "draftkings",
    "fanduel",
    "betmgm",
    "caesars",
    "pointsbetus",
]

def _get(url: str, params: Dict[str, Any], timeout: int = 20) -> Any:
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY is not set")
    q = {**params, "apiKey": ODDS_API_KEY}
    r = requests.get(url, params=q, timeout=timeout)
    if r.status_code != 200:
        try:
            detail = r.json()
        except Exception:
            detail = r.text
        raise RuntimeError(f"Odds API error {r.status_code} at {url}: {detail}")
    return r.json()

def _detect_nfl_sport_key(hours_ahead: int = 48) -> str:
    """Prefer preseason key if there are upcoming events in window, else regular."""
    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=hours_ahead)
    window = {
        "commenceTimeFrom": now.replace(microsecond=0).isoformat(),
        "commenceTimeTo": end.replace(microsecond=0).isoformat(),
        "regions": "us",
        "oddsFormat": "american",
    }
    preseason = "americanfootball_nfl_preseason"
    regular = "americanfootball_nfl"
    try:
        ev = _get(f"{BASE}/sports/{preseason}/events", window)
        if ev:
            return preseason
    except Exception:
        pass
    return regular

def _list_events(sport_key: str, hours_ahead: int = 48) -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=hours_ahead)
    return _get(
        f"{BASE}/sports/{sport_key}/events",
        {
            "commenceTimeFrom": now.replace(microsecond=0).isoformat(),
            "commenceTimeTo": end.replace(microsecond=0).isoformat(),
            "regions": "us",
            "oddsFormat": "american",
        },
    )

def _event_props(sport_key: str, event_id: str, markets: List[str]) -> Dict[str, Any]:
    """Return the event odds payload (bookmakers → markets → outcomes) for selected markets."""
    data = _get(
        f"{BASE}/sports/{sport_key}/events/{event_id}/odds",
        {
            "regions": "us",
            "oddsFormat": "american",
            "markets": ",".join(markets),
            "bookmakers": ",".join(PREFERRED_BOOKMAKERS),
        },
    )
    # API sometimes returns a list; normalize to single dict with bookmakers
    payloads = data if isinstance(data, list) else [data]
    # Take the first that actually has bookmakers
    for p in payloads:
        if isinstance(p, dict) and p.get("bookmakers"):
            return p
    return payloads[0] if payloads else {}

def fetch_nfl_props(
    markets: Optional[List[str]] = None,
    hours_ahead: int = 48,
) -> List[Dict[str, Any]]:
    """
    Returns a list of event dicts shaped like The Odds API event odds results:
    [
      {
        "id": "...",
        "home_team": "...",
        "away_team": "...",
        "commence_time": "...",
        "bookmakers": [
           {"key":"draftkings","title":"DraftKings","markets":[
               {"key":"player_pass_yds","outcomes":[
                   {"name":"Over","price":-110,"point":275.5,"description":"Josh Allen"}, ...
               ]}
           ]}
        ]
      }, ...
    ]
    """
    mkts = markets or DEFAULT_MARKETS
    sport_key = _detect_nfl_sport_key(hours_ahead)
    events = _list_events(sport_key, hours_ahead)
    if not events:
        return []

    out: List[Dict[str, Any]] = []
    for ev in events:
        ev_id = ev["id"]
        try:
            props_payload = _event_props(sport_key, ev_id, mkts)
        except RuntimeError as e:
            # Skip this event if props not available; keep the app alive
            print(f"[NFL] Skipping event {ev_id}: {e}")
            continue

        # Build event-shaped object expected by /api/nfl/props code
        out.append(
            {
                "id": ev_id,
                "commence_time": ev.get("commence_time"),
                "home_team": ev.get("home_team"),
                "away_team": ev.get("away_team"),
                "teams": ev.get("teams", []),
                "bookmakers": props_payload.get("bookmakers", []),
            }
        )
    return out

# ---------- Environment (totals + favored team) ----------

def _bulk_odds(
    sport_key: str,
    markets: List[str],
    hours_ahead: int = 48,
) -> List[Dict[str, Any]]:
    """Bulk odds call for H2H/Totals works fine for NFL; use tight window."""
    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=hours_ahead)
    return _get(
        f"{BASE}/sports/{sport_key}/odds",
        {
            "regions": "us",
            "oddsFormat": "american",
            "dateFormat": "iso",
            "markets": ",".join(markets),
            "bookmakers": ",".join(PREFERRED_BOOKMAKERS),
            "commenceTimeFrom": now.replace(microsecond=0).isoformat(),
            "commenceTimeTo": end.replace(microsecond=0).isoformat(),
        },
    )

def _classify_environment(total_point: float, over_odds: int, under_odds: int) -> str:
    """
    Simple classification like your MLB env:
    - High: total >= 47.5 and Over priced better than Under
    - Low: total <= 41.5 and Under priced better than Over
    - Neutral: everything else
    """
    try:
        t = float(total_point)
    except Exception:
        return "Neutral"
    if t >= 47.5 and (isinstance(over_odds, (int, float)) and isinstance(under_odds, (int, float)) and over_odds <= under_odds):
        return "High"
    if t <= 41.5 and (isinstance(over_odds, (int, float)) and isinstance(under_odds, (int, float)) and under_odds <= over_odds):
        return "Low"
    return "Neutral"

def get_nfl_game_environment_map(hours_ahead: int = 72) -> Dict[str, Dict[str, Any]]:
    """
    Returns { "AWY @ HOME": {
        "environment": "High|Neutral|Low",
        "total": 46.5,
        "over_odds": -105,
        "under_odds": -115,
        "favored_team": "KC",
        "home_team": "KC",
        "away_team": "BUF"
    } }
    """
    from team_abbreviations import TEAM_ABBREVIATIONS  # you already use this in MLB

    sport_key = _detect_nfl_sport_key(hours_ahead)
    # H2H + Totals in one bulk call (bookmakers filtered)
    data = _bulk_odds(sport_key, ["h2h", "totals"], hours_ahead)
    env_map: Dict[str, Dict[str, Any]] = {}

    # Build per-event structures for quick lookup
    for event in data:
        home = event.get("home_team", "")
        away = event.get("away_team", "")
        if not home or not away:
            continue
        home_abbr = TEAM_ABBREVIATIONS.get(home, home)
        away_abbr = TEAM_ABBREVIATIONS.get(away, away)
        matchup_key = f"{away_abbr} @ {home_abbr}"

        total_point = None
        over_odds = None
        under_odds = None
        home_ml = None
        away_ml = None

        for bm in event.get("bookmakers", []):
            for market in bm.get("markets", []):
                mkey = market.get("key")
                if mkey == "totals":
                    for outc in market.get("outcomes", []):
                        if outc.get("name") == "Over":
                            total_point = outc.get("point")
                            over_odds = outc.get("price")
                        elif outc.get("name") == "Under":
                            under_odds = outc.get("price")
                elif mkey == "h2h":
                    for outc in market.get("outcomes", []):
                        if outc.get("name") == home:
                            home_ml = outc.get("price")
                        elif outc.get("name") == away:
                            away_ml = outc.get("price")

        favored_team = None
        if home_ml is not None and away_ml is not None:
            favored_team = home_abbr if home_ml < away_ml else away_abbr

        label = _classify_environment(total_point, over_odds, under_odds) if total_point is not None else "Neutral"

        env_map[matchup_key] = {
            "environment": label,
            "total": total_point,
            "over_odds": over_odds,
            "under_odds": under_odds,
            "favored_team": favored_team,
            "home_team": home_abbr,
            "away_team": away_abbr,
        }

    return env_map

# -------- CLI smoke test --------
if __name__ == "__main__":
    try:
        props = fetch_nfl_props()
        print(f"Fetched {len(props)} NFL events with player props.")
        env = get_nfl_game_environment_map()
        print(f"Classified {len(env)} NFL matchups for environment.")
    except Exception as e:
        print("Error:", e)
