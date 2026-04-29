"""
fatigue_model.py
----------------
Builds player-level and team-level fatigue signals from EDGE data
combined with schedule context.

The core idea: a player's physical output (speed, distance, acceleration)
should be relatively stable game-to-game when rested. Deviations below
their personal baseline — especially when correlated with schedule load —
are a fatigue signal.

This module:
  1. Establishes per-player baselines from their first N well-rested games
  2. Computes a "physical output index" (POI) for each game
  3. Calculates decay: POI relative to baseline
  4. Merges with schedule context to explain *why* a player is declining
"""

import pandas as pd
import numpy as np


# ---------------------------------------------------------------------------
# Physical Output Index (POI)
# ---------------------------------------------------------------------------

# Weights for combining EDGE metrics into a single index.
# Speed matters most; distance and hard accelerations are supporting signals.
# Adjust these as you learn more about what correlates with performance.
POI_WEIGHTS = {
    "avgSpeed":           0.40,
    "totalDistance":      0.30,
    "hardAccelerations":  0.20,
    "hardDecelerations":  0.10,
}


def compute_poi(edge_df: pd.DataFrame,
                weights: dict = POI_WEIGHTS) -> pd.DataFrame:
    """
    Add a Physical Output Index column to a per-game EDGE DataFrame.
    Each component is z-scored across the player's season before weighting,
    so the index is relative to the player's own norms (not league average).

    Parameters
    ----------
    edge_df : DataFrame with columns matching keys in `weights`
    weights : dict of column -> weight (should sum to 1.0)

    Returns
    -------
    DataFrame with added columns:
      - {col}_z    : z-scored component
      - poi        : weighted composite
    """
    df = edge_df.copy()
    poi = pd.Series(0.0, index=df.index)

    for col, w in weights.items():
        if col not in df.columns:
            print(f"  [warn] EDGE column '{col}' not found, skipping")
            continue
        series = pd.to_numeric(df[col], errors="coerce")
        mean, std = series.mean(), series.std()
        if std > 0:
            z = (series - mean) / std
        else:
            z = pd.Series(0.0, index=df.index)
        df[f"{col}_z"] = z.round(3)
        poi += w * z

    df["poi"] = poi.round(3)
    return df


# ---------------------------------------------------------------------------
# Baseline & Decay
# ---------------------------------------------------------------------------

def compute_baseline(player_game_log: pd.DataFrame,
                     schedule_features: pd.DataFrame,
                     baseline_games: int = 10,
                     min_rest_days: int = 2) -> float:
    """
    Compute a player's "well-rested" baseline POI.

    Uses the first `baseline_games` games where days_rest >= min_rest_days.
    This avoids contaminating the baseline with early-season back-to-backs.

    Returns the mean POI across those games.
    """
    merged = player_game_log.merge(
        schedule_features[["game_id", "days_rest", "is_back_to_back"]],
        on="game_id", how="left"
    )
    rested = merged[merged["days_rest"] >= min_rest_days]
    if "poi" not in rested.columns or rested.empty:
        return np.nan
    baseline_sample = rested.head(baseline_games)
    return baseline_sample["poi"].mean()


def add_fatigue_signals(player_df: pd.DataFrame,
                        schedule_df: pd.DataFrame,
                        rolling_window: int = 5) -> pd.DataFrame:
    """
    Merge player game-log (with POI) with schedule context and compute:

      - poi_vs_baseline  : deviation from player's well-rested baseline
      - poi_rolling      : rolling mean POI over last N games
      - fatigue_score    : composite fatigue estimate (0-100, higher = more fatigued)
      - fatigue_flag     : True when fatigue_score exceeds threshold

    Parameters
    ----------
    player_df   : per-game DataFrame with 'poi' column (from compute_poi)
    schedule_df : output of build_schedule_features()
    rolling_window : games to include in rolling average
    """
    df = player_df.merge(
        schedule_df[[
            "game_id", "date", "days_rest", "is_back_to_back",
            "travel_miles", "road_trip_game_num", "is_road_game"
        ]],
        on="game_id", how="left"
    ).sort_values("date").reset_index(drop=True)

    # Rolling POI
    df["poi_rolling"] = df["poi"].rolling(
        window=rolling_window, min_periods=2
    ).mean().round(3)

    # Baseline (first 10 well-rested games)
    rested_mask = df["days_rest"].fillna(99) >= 2
    baseline = df.loc[rested_mask, "poi"].head(10).mean()
    df["poi_baseline"] = round(baseline, 3)
    df["poi_vs_baseline"] = (df["poi"] - baseline).round(3)

    # Fatigue score (0-100):
    # Combines negative POI deviation, schedule load, and travel
    # Each component contributes to a 0-100 score.
    # This is a starting model — tune weights as you validate against outcomes.
    travel_norm = df["travel_miles"].fillna(0) / 3000  # 3000 mi = max realistic trip
    rest_penalty = (1 / df["days_rest"].clip(lower=1)).clip(upper=1)
    road_penalty  = df["road_trip_game_num"].clip(upper=5) / 5
    poi_penalty   = (-df["poi_vs_baseline"]).clip(lower=0) / 2  # max ~2 std dev

    raw_score = (
        0.35 * poi_penalty
        + 0.25 * rest_penalty
        + 0.25 * road_penalty
        + 0.15 * travel_norm
    )
    df["fatigue_score"] = (raw_score * 100).clip(0, 100).round(1)
    df["fatigue_flag"]  = df["fatigue_score"] > 55

    return df


# ---------------------------------------------------------------------------
# Team-level aggregation
# ---------------------------------------------------------------------------

def team_fatigue_summary(player_fatigue_dfs: dict) -> pd.DataFrame:
    """
    Given a dict of {player_name: fatigue_df}, produce a team-level summary.

    Returns one row per player with:
      - avg_fatigue_score
      - max_fatigue_score
      - pct_games_flagged
      - avg_poi_vs_baseline
    """
    rows = []
    for name, df in player_fatigue_dfs.items():
        if df.empty or "fatigue_score" not in df.columns:
            continue
        rows.append({
            "player":               name,
            "games":                len(df),
            "avg_fatigue_score":    df["fatigue_score"].mean().round(1),
            "max_fatigue_score":    df["fatigue_score"].max().round(1),
            "pct_flagged":          (df["fatigue_flag"].sum() / len(df) * 100).round(1),
            "avg_poi_vs_baseline":  df["poi_vs_baseline"].mean().round(3),
            "avg_speed_z":          df.get("avgSpeed_z", pd.Series()).mean(),
        })
    return pd.DataFrame(rows).sort_values("avg_fatigue_score", ascending=False)
