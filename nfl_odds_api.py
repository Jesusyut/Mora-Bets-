import os
import requests
from typing import List, Optional, Dict, Any

ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# Canonical player prop market names per The Odds API v4
# Map common aliases -> official keys
MARKET_ALIASES = {
    "player_rec_yds": "player_receiving_yds",
    "player_rec": "player_receptions",
    "receptions": "player_receptions",
    "receiving_yds": "player_receiving_yds",
    "rush_yds": "player_rush_yds",
    "pass_yds": "player_pass_yds",
}

VALID_PLAYER_MARKETS = {
    "player_anytime_td",
    "player_first_td",
    "player_pass_yds",
    "player_pass_tds",
    "player_pass_interceptions",
    "player_rush_yds",
    "player_rush_attempts",
    "player_receptions",
    "player_receiving_yds",
    "player_field_goals",
}

def _normalize_markets(markets: List[str]) -> List[str]:
    norm = []
    for m in markets:
        m = m.strip()
        m = MARKET_ALIASES.get(m, m)
        if m not in VALID_PLAYER_MARKETS:
            raise ValueError(f"Unsupported market: {m}. Valid: {sorted(VALID_PLAYER_MARKETS)}")
        norm.append(m)
    # de-dupe preserving order
    seen = set()
    out = []
    for m in norm:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out

def fetch_nfl_props(
    markets: Optional[List[str]] = None,
    *,
    regions: str = "us",
    bookmakers: Optional[List[str]] = None,
    odds_format: str = "american",
    date_format: str = "iso",
    event_ids: Optional[List[str]] = None,
    timeout: int = 20
) -> List[Dict[str, Any]]:
    """Fetch NFL player prop odds from The Odds API v4.

    Args:
        markets: List of player prop markets to fetch. Defaults to common yardage props.
        regions: 'us', 'us2', 'eu', 'uk', 'au' (per API). Ignored if 'bookmakers' is provided.
        bookmakers: Optional list of bookmaker keys to restrict results (e.g., ['draftkings','fanduel']).
        odds_format: 'american' or 'decimal'.
        date_format: 'iso' or 'unix'.
        event_ids: Optional list of specific event IDs to limit response size.
        timeout: Request timeout seconds.

    Returns:
        List of event objects with bookmaker odds for requested markets.
    """
    if not ODDS_API_KEY:
        raise EnvironmentError("ODDS_API_KEY is not set. Please set it in your environment.")

    if markets is None:
        markets = ["player_pass_yds", "player_rush_yds", "player_receiving_yds", "player_receptions"]
    markets = _normalize_markets(markets)

    base = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "markets": ",".join(markets),
        "oddsFormat": odds_format,
        "dateFormat": date_format,
        # includeLinks can be noisy; default to false
        "includeLinks": "false",
    }

    if bookmakers:
        params["bookmakers"] = ",".join(bookmakers)
    else:
        params["regions"] = regions

    if event_ids:
        params["eventIds"] = ",".join(event_ids)

    resp = requests.get(base, params=params, timeout=timeout)
    if resp.status_code != 200:
        # Try to include message from API
        try:
            msg = resp.json()
        except Exception:
            msg = resp.text
        raise RuntimeError(f"The Odds API error {resp.status_code}: {msg}")

    return resp.json()

def get_nfl_game_totals() -> List[Dict[str, Any]]:
    """Fetch NFL game totals for environment classification"""
    if not ODDS_API_KEY:
        raise EnvironmentError("ODDS_API_KEY is not set. Please set it in your environment.")
    
    base = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": "totals",
        "oddsFormat": "american"
    }
    
    resp = requests.get(base, params=params, timeout=20)
    if resp.status_code != 200:
        try:
            msg = resp.json()
        except Exception:
            msg = resp.text
        raise RuntimeError(f"The Odds API error {resp.status_code}: {msg}")
    
    return resp.json()

def get_nfl_moneylines() -> List[Dict[str, Any]]:
    """Fetch NFL moneylines for favored team identification"""
    if not ODDS_API_KEY:
        raise EnvironmentError("ODDS_API_KEY is not set. Please set it in your environment.")
    
    base = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us", 
        "markets": "h2h",
        "oddsFormat": "american"
    }
    
    resp = requests.get(base, params=params, timeout=20)
    if resp.status_code != 200:
        try:
            msg = resp.json()
        except Exception:
            msg = resp.text
        raise RuntimeError(f"The Odds API error {resp.status_code}: {msg}")
    
    return resp.json()

def get_nfl_game_environment_map() -> Dict[str, Dict[str, Any]]:
    """Get NFL game environment classifications and favored teams"""
    try:
        # Get totals and moneylines
        totals_data = get_nfl_game_totals()
        moneylines_data = get_nfl_moneylines()
        
        # Create lookup dictionaries
        totals_lookup = {}
        moneylines_lookup = {}
        
        # Process totals data
        for game in totals_data:
            matchup = f"{game['away_team']} @ {game['home_team']}"
            
            # Find totals market from bookmakers
            total_point = None
            over_odds = None
            under_odds = None
            
            for bookmaker in game.get('bookmakers', []):
                for market in bookmaker.get('markets', []):
                    if market['key'] == 'totals':
                        for outcome in market.get('outcomes', []):
                            if outcome['name'] == 'Over':
                                total_point = outcome.get('point', 0)
                                over_odds = outcome.get('price', 0)
                            elif outcome['name'] == 'Under':
                                under_odds = outcome.get('price', 0)
                        break
                if total_point is not None:
                    break
            
            if total_point is not None:
                totals_lookup[matchup] = {
                    'total': total_point,
                    'over_odds': over_odds,
                    'under_odds': under_odds
                }
        
        # Process moneylines data for favored teams
        for game in moneylines_data:
            matchup = f"{game['away_team']} @ {game['home_team']}"
            
            # Find moneyline market
            away_odds = None
            home_odds = None
            
            for bookmaker in game.get('bookmakers', []):
                for market in bookmaker.get('markets', []):
                    if market['key'] == 'h2h':
                        for outcome in market.get('outcomes', []):
                            if outcome['name'] == game['away_team']:
                                away_odds = outcome.get('price', 0)
                            elif outcome['name'] == game['home_team']:
                                home_odds = outcome.get('price', 0)
                        break
                if away_odds is not None and home_odds is not None:
                    break
            
            if away_odds is not None and home_odds is not None:
                # Determine favored team (lower odds = favored)
                favored_team = game['home_team'] if home_odds < away_odds else game['away_team']
                
                moneylines_lookup[matchup] = {
                    'away_team': game['away_team'],
                    'home_team': game['home_team'],
                    'favored_team': favored_team,
                    'away_odds': away_odds,
                    'home_odds': home_odds
                }
        
        # Combine data and classify environments
        environment_map = {}
        
        for matchup in set(list(totals_lookup.keys()) + list(moneylines_lookup.keys())):
            totals_info = totals_lookup.get(matchup, {})
            moneyline_info = moneylines_lookup.get(matchup, {})
            
            total_point = totals_info.get('total', 0)
            over_odds = totals_info.get('over_odds', 0)
            under_odds = totals_info.get('under_odds', 0)
            
            # NFL environment classification (different thresholds than MLB)
            environment = "Neutral"
            
            if total_point >= 50 or (over_odds <= -115 and total_point >= 47):
                environment = "High Scoring"
            elif total_point <= 42 or (under_odds <= -115 and total_point <= 45):
                environment = "Low Scoring"
            
            environment_map[matchup] = {
                'environment': environment,
                'total': total_point,
                'over_odds': over_odds,
                'under_odds': under_odds,
                **moneyline_info
            }
        
        return environment_map
        
    except Exception as e:
        print(f"Error getting NFL environment map: {e}")
        return {}


# Simple CLI test: prints number of events and first event keys
if __name__ == "__main__":
    try:
        data = fetch_nfl_props()
        print(f"Fetched {len(data)} NFL events with player props.")
        if data:
            print("First event keys:", list(data[0].keys()))
    except Exception as e:
        print("Error:", e)