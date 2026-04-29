"""
pipeline.py
-----------
Pulls 2025/26 regular season data for all 32 NHL teams.
Computes road trip decay, back-to-back splits, and home/road splits
for every skater with sufficient games.

Output: nhl_fatigue_all_teams.json

Usage:
    python pipeline.py

Runtime: ~10-15 minutes (NHL API rate limiting)
"""

import sys, time, json, math
sys.path.insert(0, './src')

import requests
import pandas as pd
from nhl_client import get_roster, get_team_schedule, get_player_game_log
from schedule_analysis import build_schedule_features, summarise_travel_burden

SEASON      = "20252026"
GAME_TYPE   = 2
MIN_GAMES   = 15   # minimum games for a player to be included
MIN_DEEP    = 3    # minimum deep road games for decay index
MIN_B2B     = 3    # minimum b2b games for b2b split
DELAY       = 0.4  # seconds between API calls

# All 32 NHL team abbreviations
ALL_TEAMS = [
    "ANA","BOS","BUF","CAR","CBJ","CGY","CHI","COL",
    "DAL","DET","EDM","FLA","LAK","MIN","MTL","NJD",
    "NSH","NYI","NYR","OTT","PHI","PIT","SEA","SJS",
    "STL","TBL","TOR","UTA","VAN","VGK","WSH","WPG"
]

TEAM_NAMES = {
    "ANA":"Anaheim Ducks","BOS":"Boston Bruins","BUF":"Buffalo Sabres",
    "CAR":"Carolina Hurricanes","CBJ":"Columbus Blue Jackets","CGY":"Calgary Flames",
    "CHI":"Chicago Blackhawks","COL":"Colorado Avalanche","DAL":"Dallas Stars",
    "DET":"Detroit Red Wings","EDM":"Edmonton Oilers","FLA":"Florida Panthers",
    "LAK":"Los Angeles Kings","MIN":"Minnesota Wild","MTL":"Montreal Canadiens",
    "NJD":"New Jersey Devils","NSH":"Nashville Predators","NYI":"New York Islanders",
    "NYR":"New York Rangers","OTT":"Ottawa Senators","PHI":"Philadelphia Flyers",
    "PIT":"Pittsburgh Penguins","SEA":"Seattle Kraken","SJS":"San Jose Sharks",
    "STL":"St. Louis Blues","TBL":"Tampa Bay Lightning","TOR":"Toronto Maple Leafs",
    "UTA":"Utah Mammoth","VAN":"Vancouver Canucks","VGK":"Vegas Golden Knights",
    "WSH":"Washington Capitals","WPG":"Winnipeg Jets"
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def safe_round(val, n=2):
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    return round(float(val), n)


def toi_to_min(toi_str):
    """Convert 'MM:SS' string to decimal minutes."""
    try:
        parts = toi_str.split(':')
        return int(parts[0]) + int(parts[1]) / 60
    except:
        return None


def build_player_context(game_log):
    """
    Add schedule context columns to a player game log using
    homeRoadFlag — no team schedule dependency.
    """
    df = game_log.copy()
    df['gameDate'] = pd.to_datetime(df['gameDate'])
    df = df.sort_values('gameDate').reset_index(drop=True)

    df['toi_min']         = df['toi'].apply(toi_to_min)
    df['shots_per_shift'] = (df['shots'] / df['shifts']).round(3)
    df['toi_per_shift']   = (df['toi_min'] / df['shifts']).round(3)
    df['is_road']         = df['homeRoadFlag'] == 'R'
    df['days_rest']       = df['gameDate'].diff().dt.days
    df['is_back_to_back'] = df['days_rest'] == 1

    road_trip_num = []
    counter = 0
    for _, row in df.iterrows():
        if not row['is_road']:
            counter = 0
        else:
            counter += 1
        road_trip_num.append(counter)
    df['road_trip_game_num'] = road_trip_num

    return df


def compute_player_metrics(df, min_deep=MIN_DEEP, min_b2b=MIN_B2B):
    """
    Compute all fatigue metrics for a single player's contextualised log.
    Returns a dict of metrics.
    """
    home   = df[df['road_trip_game_num'] == 0]
    fresh  = df[df['road_trip_game_num'] == 1]
    deep   = df[df['road_trip_game_num'] >= 3]
    b2b    = df[df['is_back_to_back'] == True]
    rested = df[df['days_rest'] >= 2]

    has_deep = len(deep) >= min_deep
    has_b2b  = len(b2b)  >= min_b2b

    decay = safe_round(deep['points'].mean() - fresh['points'].mean()) if has_deep else None

    # Monthly breakdown — points per game by month
    df['month'] = df['gameDate'].dt.to_period('M').astype(str)
    monthly = df.groupby('month')['points'].mean().round(2).to_dict()

    return {
        # Volume
        "games":              len(df),
        "total_points":       int(df['points'].sum()),
        "pts_per_game":       safe_round(df['points'].mean()),

        # Context splits
        "home_pts":           safe_round(home['points'].mean()),
        "home_games":         len(home),
        "road_fresh_pts":     safe_round(fresh['points'].mean()),
        "road_fresh_games":   len(fresh),
        "road_deep_pts":      safe_round(deep['points'].mean()) if has_deep else None,
        "road_deep_games":    len(deep),

        # Decay index
        "decay_index":        decay,
        "has_decay":          has_deep,

        # Back-to-back
        "b2b_pts":            safe_round(b2b['points'].mean()) if has_b2b else None,
        "b2b_games":          len(b2b),
        "rested_pts":         safe_round(rested['points'].mean()),
        "rested_games":       len(rested),
        "b2b_drop":           safe_round(b2b['points'].mean() - rested['points'].mean()) if has_b2b else None,
        "has_b2b":            has_b2b,

        # Physical proxies
        "avg_toi_min":        safe_round(df['toi_min'].mean()),
        "avg_toi_per_shift":  safe_round(df['toi_per_shift'].mean()),
        "avg_shots_per_shift":safe_round(df['shots_per_shift'].mean()),
        "b2b_shots":          safe_round(b2b['shots_per_shift'].mean()) if has_b2b else None,
        "rested_shots":       safe_round(rested['shots_per_shift'].mean()),
        "shot_drop_b2b":      safe_round(
            (b2b['shots_per_shift'].mean() - rested['shots_per_shift'].mean())
            / rested['shots_per_shift'].mean() * 100
        ) if has_b2b and rested['shots_per_shift'].mean() > 0 else None,

        # Monthly arc
        "monthly_pts":        monthly,
    }


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def process_team(abbr):
    """Pull and compute all metrics for one team. Returns team dict."""
    print(f"\n  [{abbr}] {TEAM_NAMES.get(abbr, abbr)}")

    # Schedule
    try:
        raw_sched = get_team_schedule(abbr, SEASON)
        time.sleep(DELAY)
        sched = build_schedule_features(raw_sched, abbr)
        sched = sched[
            (sched['game_type'] == 2) &
            (sched['game_state'].isin(['FINAL', 'OFF']))
        ].reset_index(drop=True)
        travel = summarise_travel_burden(sched)
        games_played = len(sched)
    except Exception as e:
        print(f"    Schedule error: {e}")
        return None

    # Roster
    try:
        roster = get_roster(abbr, SEASON)
        time.sleep(DELAY)
    except Exception as e:
        print(f"    Roster error: {e}")
        return None

    # Player metrics
    players = []
    for _, player in roster.iterrows():
        pid   = int(player['player_id'])
        fname = player['first_name']
        lname = player['last_name']
        pos   = player['position']

        try:
            log = get_player_game_log(pid, SEASON)
            time.sleep(DELAY)

            if len(log) < MIN_GAMES:
                continue

            df = build_player_context(log)
            metrics = compute_player_metrics(df)
            metrics['player_id']  = pid
            metrics['first_name'] = fname
            metrics['last_name']  = lname
            metrics['position']   = pos
            metrics['name']       = f"{fname} {lname}"
            players.append(metrics)
            print(f"    ✓ {fname} {lname} ({len(log)}g)")

        except Exception as e:
            print(f"    ✗ {fname} {lname}: {e}")
            continue

    # Convert travel burden values to JSON-safe types
    travel_clean = {}
    for k, v in travel.items():
        try:
            if v != v:  # NaN check
                travel_clean[k] = None
            else:
                travel_clean[k] = round(float(v), 1) if isinstance(v, float) else int(v)
        except:
            travel_clean[k] = None

    # Build ordered schedule for map visualization
    schedule_for_map = []
    for _, row in sched.iterrows():
        opp = row['away_team'] if row['is_home'] else row['home_team']
        schedule_for_map.append({
            "date":       str(row['date'].date()),
            "opponent":   opp,
            "is_home":    bool(row['is_home']),
            "home_score": int(row['home_score']) if row['home_score'] is not None and str(row['home_score']) != 'nan' else None,
            "away_score": int(row['away_score']) if row['away_score'] is not None and str(row['away_score']) != 'nan' else None,
        })

    return {
        "abbr":          abbr,
        "name":          TEAM_NAMES.get(abbr, abbr),
        "games_played":  games_played,
        "travel":        travel_clean,
        "schedule":      schedule_for_map,
        "players":       players,
    }


def run_pipeline(teams=None, output="nhl_fatigue_all_teams.json"):
    teams = teams or ALL_TEAMS
    print(f"Starting pipeline — {len(teams)} teams, season {SEASON}")
    print("=" * 60)

    results = {}
    failed  = []

    for i, abbr in enumerate(teams):
        print(f"\n[{i+1}/{len(teams)}]", end="")
        team_data = process_team(abbr)
        if team_data:
            results[abbr] = team_data
        else:
            failed.append(abbr)
        # Checkpoint save every 8 teams
        if (i + 1) % 8 == 0:
            with open(output, 'w') as f:
                json.dump(results, f)
            print(f"\n  💾 Checkpoint saved ({len(results)} teams)")

    # Final save
    with open(output, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\n{'='*60}")
    print(f"Pipeline complete.")
    print(f"  Teams processed: {len(results)}")
    print(f"  Teams failed:    {len(failed)} {failed if failed else ''}")
    print(f"  Output:          {output}")
    total_players = sum(len(t['players']) for t in results.values())
    print(f"  Total players:   {total_players}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--teams', nargs='+', help='Specific team abbreviations to run')
    parser.add_argument('--output', default='nhl_fatigue_all_teams.json')
    args = parser.parse_args()

    run_pipeline(teams=args.teams, output=args.output)