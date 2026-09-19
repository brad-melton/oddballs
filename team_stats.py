"""
Shared feature-engineering logic for the score / win-probability models.

Used by BOTH the training script ("prediction model.py") and the live
prediction path (backend/simulation.py -> predict_single_game.py), so the
features a model is trained on and the features it's fed at inference time
never drift apart -- that drift is what silently breaks a ColumnTransformer.
"""

import os
import sqlite3
import numpy as np
import pandas as pd

DB_PATH = os.environ.get("DB_PATH", r"C:/Users/bsmel/OneDrive/Documents/Baseball_data/10u data.db")

# Set by the deployed backend when reading from hosted Turso instead of the
# local sqlite file (see backend/db.py for why the two connection kinds are
# kept separate: pandas.read_sql_query needs a real SQLAlchemy engine, not
# backend/db.py's sqlite3-compatible wrapper).
TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN")

NUMERIC_FEATURES = [
    "home_avg_runs_scored",
    "home_avg_runs_allowed",
    "away_avg_runs_scored",
    "away_avg_runs_allowed",
    "home_seed",
    "visitor_seed",
    "home_age_group",
    "away_age_group",
    "home_level",
    "away_level",
    "age_group_diff",
    "level_diff",
    "home_rs_last_5",
    "home_ra_last_5",
    "away_rs_last_5",
    "away_ra_last_5",
    "home_rs_last_10",
    "home_ra_last_10",
    "away_rs_last_10",
    "away_ra_last_10",
    "hour",
    "is_weekend",
]

CATEGORICAL_FEATURES = [
    "ballpark",
    "format",
    "classification",
    "bracket_round",
    "month",
    "season",
    "time_bucket",
    "weekday",
]

LEVEL_MAP = {"A": 0, "AA": 1, "AAA": 2, "Majors": 3}


def season_from_month(m):
    if m in (12, 1, 2):
        return "winter"
    if m in (3, 4, 5):
        return "spring"
    if m in (6, 7, 8):
        return "summer"
    return "fall"


def time_bucket_label(h):
    if h is None or (isinstance(h, float) and np.isnan(h)):
        return None
    if h < 11:
        return "morning"
    if h < 17:
        return "afternoon"
    return "evening"


def parse_age_group(x):
    try:
        return int(str(x).replace("U", "").strip())
    except Exception:
        return np.nan


_GAMES_SQL = """
    SELECT id, eventid, game_date, game_time, game_num, format, ballpark, field_num,
           away_team, away_team_key, away_score,
           home_team, home_team_key, home_score,
           bracket, bracket_round, seed, sourceurl, bracketurl,
           classification, home_seed, visitor_seed
    FROM games
    WHERE home_score IS NOT NULL AND away_score IS NOT NULL
"""
_TEAMS_SQL = "SELECT id, team_name, age_group, level, last_updated FROM teams"


def load_games_and_teams():
    """
    Loads + lightly cleans completed games and teams from the DB. Always
    manages its own connection -- pandas.read_sql_query wants either a
    plain sqlite3.Connection or a real SQLAlchemy engine, so this can't
    reuse backend/db.py's sqlite3-compatible wrapper (built for row["col"]
    access instead). Reads from Turso when TURSO_DATABASE_URL is set,
    otherwise the local sqlite file.
    """
    if TURSO_DATABASE_URL:
        from sqlalchemy import create_engine  # deferred -- Linux/macOS-only wheels, see backend/db.py
        host = TURSO_DATABASE_URL.replace("libsql://", "").replace("https://", "")
        engine = create_engine(f"sqlite+libsql://{host}?secure=true", connect_args={"auth_token": TURSO_AUTH_TOKEN})
        try:
            games = pd.read_sql_query(_GAMES_SQL, engine)
            teams = pd.read_sql_query(_TEAMS_SQL, engine)
        finally:
            engine.dispose()
    else:
        conn = sqlite3.connect(DB_PATH)
        try:
            games = pd.read_sql_query(_GAMES_SQL, conn)
            teams = pd.read_sql_query(_TEAMS_SQL, conn)
        finally:
            conn.close()

    games["game_date"] = pd.to_datetime(games["game_date"], errors="coerce")
    games = games.sort_values("game_date")
    games["home_team"] = games["home_team"].str.strip()
    games["away_team"] = games["away_team"].str.strip()
    teams["team_name"] = teams["team_name"].str.strip()

    return games, teams


def build_team_stats(games: pd.DataFrame, teams: pd.DataFrame) -> pd.DataFrame:
    """
    One row per team (indexed by team name): season-long offensive/defensive
    averages, plus each team's most recent rolling-5 / rolling-10 form, age
    group and level. Used both to build training features and to look up a
    team's current form when predicting an upcoming game.
    """
    long = pd.concat(
        [
            games[["game_date", "home_team", "home_score", "away_score"]].rename(
                columns={"home_team": "team", "home_score": "runs_scored", "away_score": "runs_allowed"}
            ),
            games[["game_date", "away_team", "away_score", "home_score"]].rename(
                columns={"away_team": "team", "away_score": "runs_scored", "home_score": "runs_allowed"}
            ),
        ],
        ignore_index=True,
    )

    long = long.sort_values(["team", "game_date"])

    long["rs_last_5"] = long.groupby("team")["runs_scored"].rolling(5).mean().reset_index(0, drop=True)
    long["ra_last_5"] = long.groupby("team")["runs_allowed"].rolling(5).mean().reset_index(0, drop=True)
    long["rs_last_10"] = long.groupby("team")["runs_scored"].rolling(10).mean().reset_index(0, drop=True)
    long["ra_last_10"] = long.groupby("team")["runs_allowed"].rolling(10).mean().reset_index(0, drop=True)

    recent_stats = long.groupby("team").agg(
        rs_last_5=("rs_last_5", "last"),
        ra_last_5=("ra_last_5", "last"),
        rs_last_10=("rs_last_10", "last"),
        ra_last_10=("ra_last_10", "last"),
    ).reset_index()

    team_stats = long.groupby("team").agg(
        games_played=("runs_scored", "count"),
        avg_runs_scored=("runs_scored", "mean"),
        avg_runs_allowed=("runs_allowed", "mean"),
    ).reset_index()

    team_stats = team_stats.merge(recent_stats, on="team", how="left")
    team_stats = team_stats.merge(teams, left_on="team", right_on="team_name", how="left")

    team_stats["age_group_num"] = team_stats["age_group"].apply(parse_age_group)
    team_stats["level_num"] = team_stats["level"].map(LEVEL_MAP).fillna(0)

    # Drop the raw string/lookup columns -- only the parsed numeric versions
    # (age_group_num, level_num) are used downstream. Keeping both would
    # collide once callers rename age_group_num -> age_group.
    team_stats = team_stats.drop(columns=["team_name", "age_group", "level", "id", "last_updated"])

    return team_stats.set_index("team")


def build_game_features_row(
    home_team,
    away_team,
    team_stats: pd.DataFrame,
    ballpark,
    format,
    classification,
    bracket_round,
    home_seed,
    visitor_seed,
    game_date,
    game_time,
    league_avg_rs=None,
    league_avg_ra=None,
) -> dict:
    """
    Builds one feature dict -- matching NUMERIC_FEATURES + CATEGORICAL_FEATURES
    exactly -- for a single not-yet-played matchup. Falls back to league
    averages for teams with no history yet.
    """
    home_team = (home_team or "").strip()
    away_team = (away_team or "").strip()

    parsed_date = pd.to_datetime(game_date, errors="coerce")
    month = int(parsed_date.month) if pd.notna(parsed_date) else None
    weekday = parsed_date.day_name() if pd.notna(parsed_date) else None
    is_weekend = int(weekday in ("Saturday", "Sunday")) if weekday else 0
    season = season_from_month(month) if month else None

    parsed_time = pd.to_datetime(game_time, errors="coerce")
    hour = int(parsed_time.hour) if pd.notna(parsed_time) else None
    time_bucket = time_bucket_label(hour)

    def lookup(team_name, col, default):
        if team_name in team_stats.index:
            val = team_stats.loc[team_name, col]
            if isinstance(val, pd.Series):  # duplicate team_name rows shouldn't happen, but be safe
                val = val.iloc[0]
            if pd.notna(val):
                return float(val)
        return float(default) if default is not None else 0.0

    h_rs = lookup(home_team, "avg_runs_scored", league_avg_rs)
    h_ra = lookup(home_team, "avg_runs_allowed", league_avg_ra)
    a_rs = lookup(away_team, "avg_runs_scored", league_avg_rs)
    a_ra = lookup(away_team, "avg_runs_allowed", league_avg_ra)

    h_age = lookup(home_team, "age_group_num", 0)
    a_age = lookup(away_team, "age_group_num", 0)
    h_lvl = lookup(home_team, "level_num", 0)
    a_lvl = lookup(away_team, "level_num", 0)

    # predict_single_game.py's tie-prevention does
    # game_features.get("bracket_round", "").lower() -- a present-but-None
    # value (pool games never get bracket_round populated) skips the
    # default and crashes on .lower(), so coerce it here.
    if bracket_round is None:
        bracket_round = "pool" if format == "pool" else ""

    return {
        "home_avg_runs_scored": h_rs,
        "home_avg_runs_allowed": h_ra,
        "away_avg_runs_scored": a_rs,
        "away_avg_runs_allowed": a_ra,
        "home_seed": home_seed or 0,
        "visitor_seed": visitor_seed or 0,
        "home_age_group": h_age,
        "away_age_group": a_age,
        "home_level": h_lvl,
        "away_level": a_lvl,
        "age_group_diff": h_age - a_age,
        "level_diff": h_lvl - a_lvl,
        "home_rs_last_5": lookup(home_team, "rs_last_5", h_rs),
        "home_ra_last_5": lookup(home_team, "ra_last_5", h_ra),
        "away_rs_last_5": lookup(away_team, "rs_last_5", a_rs),
        "away_ra_last_5": lookup(away_team, "ra_last_5", a_ra),
        "home_rs_last_10": lookup(home_team, "rs_last_10", h_rs),
        "home_ra_last_10": lookup(home_team, "ra_last_10", h_ra),
        "away_rs_last_10": lookup(away_team, "rs_last_10", a_rs),
        "away_ra_last_10": lookup(away_team, "ra_last_10", a_ra),
        "hour": hour if hour is not None else 12,
        "is_weekend": is_weekend,
        "ballpark": ballpark,
        "format": format,
        "classification": classification,
        "bracket_round": bracket_round,
        "month": month if month is not None else 0,
        "season": season if season is not None else "summer",
        "time_bucket": time_bucket if time_bucket is not None else "afternoon",
        "weekday": weekday if weekday is not None else "Saturday",
    }
