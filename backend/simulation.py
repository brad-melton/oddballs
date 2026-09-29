"""
This is the seam between the API and your existing simulation script.

Each function below is a placeholder that currently raises
NotImplementedError. Replace the body of each with calls into your
already-working script (reading from ./data/*.csv), keeping the
same function signature and return shape described in each docstring
— models.py defines those shapes as Pydantic models, so FastAPI will
validate whatever you return automatically.

Nothing in app.py needs to change once these are filled in.
"""
import os
import re
import sys
import sqlite3

# predict_single_game.py / monte_carlo_game.py / team_stats.py / load_models.py
# live at the project root (one level up from backend/), not inside backend/.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# db.py picks local sqlite vs. hosted Turso based on whether TURSO_DATABASE_URL
# is set -- see that module's docstring for why the switch lives there.
from db import get_connection as _get_connection


# Fallback colors used only if your teams table has no color columns —
# delete this and the fallback logic below once you add real colors.
_DEFAULT_PALETTE = [
    ("#4B5320", "#14150F"), ("#6B7A34", "#14150F"),
    ("#C9B37C", "#14150F"), ("#7A3B2E", "#14150F"),
]
 
 
def load_tournaments() -> list[dict]:
    """
    Pull tournaments with status "upcoming" or "in progress" from
    SQLite. Team data isn't wired up yet (see the commented-out block
    below) — location/teams are optional in models.Tournament so this
    works fine without them for now.
    """
    conn = _get_connection()
    try:
        tournament_rows = conn.execute(
            """
            SELECT eventid, name, status
            FROM events
            WHERE status IN ('upcoming', 'in progress')
            ORDER BY name
            """
        ).fetchall()
 
        tournaments = []
        for t_row in tournament_rows:
            # TODO: once you've decided how teams relate to events in
            # your schema, uncomment and adjust this block.
            # team_rows = conn.execute(
            #     """
            #     SELECT name, primary_color, secondary_color
            #     FROM teams
            #     WHERE tournament_id = ?
            #     ORDER BY name
            #     """,
            #     (t_row["eventid"],),
            # ).fetchall()
            #
            # teams = []
            # for i, t in enumerate(team_rows):
            #     c1 = t["primary_color"] or _DEFAULT_PALETTE[i % len(_DEFAULT_PALETTE)][0]
            #     c2 = t["secondary_color"] or _DEFAULT_PALETTE[i % len(_DEFAULT_PALETTE)][1]
            #     teams.append({"name": t["name"], "c1": c1, "c2": c2})
 
            tournaments.append({
                "id": str(t_row["eventid"]),
                "name": t_row["name"],
            })
 
        return tournaments
    finally:
        conn.close()
 
 
def populate_games(phase: str, tournament_id: str, date: str = None) -> dict:
    """
    Create the game schedule for a phase of the tournament, if it
    doesn't already exist.
 
    phase is either "pool-play" or "bracket-play" — for bracket-play,
    this typically depends on pool play being complete/scored first,
    so you may want to check that here and raise a clear error if not.
 
    `date` is optional and only relevant for pool-play: if the event
    spans more than 2 days, we don't know which date is pool play
    without asking, so the first call returns needs_date_selection=True
    with a list of candidate dates instead of scraping blind — the
    frontend then calls again with `date` set to the user's choice.
 
    Return shape:
    {
      "already_populated": False,  # True if games already existed and nothing new was created
      "games": [ {...}, ... ],
      "needs_date_selection": False,  # True means: don't scrape yet, show available_dates to the user
      "available_dates": []           # populated only when needs_date_selection is True
    }
    """
    if phase == "pool-play":
        return _populate_pool_play(tournament_id, date)
    if phase == "bracket-play":
        return _populate_bracket_play(tournament_id)
    raise NotImplementedError(f"Wire this up to your {phase} schedule-generation logic")
 
 
def _populate_pool_play(tournament_id: str, date: str = None) -> dict:
    """
    1. Look for existing pool-play games in the games table, matched to
       events by eventid, filtered to format='pool'.
    2. If none exist:
       a. If the event's start_date/end_date span more than 2 days and
          no `date` was given, stop and ask the user which date to use
          (returns needs_date_selection=True instead of scraping).
       b. Otherwise scrape (defaulting to start_date for short events,
          or the caller-supplied `date` for long ones), insert results,
          re-read from the DB.
    """
    conn = _get_connection()
    try:
        rows = _fetch_pool_games(conn, tournament_id)
        already_populated = len(rows) > 0
 
        if not already_populated:
            if date is None:
                start_date, end_date = _get_event_date_range(conn, tournament_id)
                if not start_date:
                    raise NotImplementedError(f"No start_date found in events for eventid {tournament_id}")
                span_dates = _enumerate_dates(start_date, end_date or start_date)
                if len(span_dates) > 2:
                    return {
                        "already_populated": False,
                        "games": [],
                        "needs_date_selection": True,
                        "available_dates": span_dates,
                    }
                date = span_dates[0]  # 1-2 day event — safe to assume pool play is on start_date
 
            scraped = _scrape_pool_games(tournament_id, date)
            _insert_pool_games(conn, tournament_id, scraped)
            conn.commit()
            rows = _fetch_pool_games(conn, tournament_id)
 
        games = [_game_row_to_dict(r) for r in rows]
        return {"already_populated": already_populated, "games": games, "needs_date_selection": False, "available_dates": []}
    finally:
        conn.close()
 
 
def _fetch_pool_games(conn: sqlite3.Connection, eventid: str):
    # TODO: adjust table/column names if these don't match your schema.
    return conn.execute(
        """
        SELECT * FROM games
        WHERE eventid = ? AND format = 'pool'
        """,
        (eventid,),
    ).fetchall()


def _populate_bracket_play(tournament_id: str) -> dict:
    """
    Bracket-play games aren't scraped on demand the way pool-play is --
    they come from the same whole-event scrape that produces the pool
    games (fall2026catchupscoring.py scrapes every day of an event in one
    pass; days after the first are classified format='bracket'). So
    "populate" here just means "read whatever's already in the DB" --
    there's nothing to fetch/insert if it isn't there yet.
    """
    conn = _get_connection()
    try:
        rows = _fetch_bracket_games(conn, tournament_id)
        games = [_game_row_to_dict(r) for r in rows]
        return {"already_populated": len(rows) > 0, "games": games, "needs_date_selection": False, "available_dates": []}
    finally:
        conn.close()


def _fetch_bracket_games(conn: sqlite3.Connection, eventid: str):
    return conn.execute(
        """
        SELECT * FROM games
        WHERE eventid = ? AND format = 'bracket'
        ORDER BY game_date, game_time
        """,
        (eventid,),
    ).fetchall()
 
 
def _game_row_to_dict(row: sqlite3.Row) -> dict:
    """
    Maps one row from the games table to the shape the frontend expects.
    Matches the real schema: id, eventid, game_date, game_time, game_num,
    format, ballpark, field_num, away_team, away_score, home_team,
    home_score, bracket, bracket_round, seed, sourceurl, bracketurl,
    classification, home_seed, visitor_seed.
 
    There's no status column, so status is derived: "completed" once
    both scores are recorded, otherwise "scheduled". home/away map onto
    the frontend's team_a/team_b (home = team_a).
    """
    has_scores = row["home_score"] is not None and row["away_score"] is not None
    # field_num is already stored as "Field N" (see fall2026catchupscoring.py's
    # scrape), not just the bare number -- don't prepend another "Field ".
    field_label = " ".join(filter(None, [row["ballpark"], row["field_num"]]))
    time_label = " ".join(filter(None, [row["game_date"], row["game_time"]]))
    # bracket/bracket_round only exist on bracket-format rows (None for pool
    # games) -- used to group bracket-play games into a visual bracket by
    # flight (Gold/Silver/...) and round, client-side.
    return {
        "id": str(row["id"]),
        "team_a": row["home_team"],
        "team_b": row["away_team"],
        "field": field_label or None,
        "scheduled_time": time_label or None,
        "status": "completed" if has_scores else "scheduled",
        "score_a": row["home_score"],
        "score_b": row["away_score"],
        "bracket_name": row["bracket"],
        "round_name": row["bracket_round"],
    }
 
 
 
def _parse_games_from_html(html: str, source_url: str = None, game_date: str = None) -> list[dict]:
    """
    Adapted directly from your existing scraper's get_data() — same
    BeautifulSoup selectors, same field extraction. Takes raw page HTML
    (already fetched) and returns a list of dicts matching the games
    table's real columns, ready for _insert_pool_games.
 
    game_date isn't on the page itself — your original script pulled it
    from the URL's ?Date= query param, so pass whatever date this page
    corresponds to (however you end up determining it per the
    _scrape_pool_games TODO below).
 
    game_num comes from the "GameID: 1555814" div (a stable numeric ID)
    rather than the display label span, per your latest sample.
 
    ballpark/field_num are split from a single div shaped like
    "Field 5 @ The Rac Waller" (with "The Rac Waller" as a link) —
    field_num keeps "Field 5", ballpark keeps "The Rac Waller".
    """
    from bs4 import BeautifulSoup
 
    soup = BeautifulSoup(html, "html.parser")
    games_source = soup.find_all("div", class_="mb-3")
    extracted = []
 
    def safe_int(val):
        try:
            return int(val.get_text(strip=True))
        except Exception:
            return None
 
    for game in games_source:
        try:
            team_v_raw = game.find("a", id=lambda x: x and "hlVisitorTeamName" in x)
            team_h_raw = game.find("a", id=lambda x: x and "hlHomeTeam" in x)
            score_v_raw = game.find("div", id=lambda x: x and "VisitorPGScore" in x)
            score_h_raw = game.find("div", id=lambda x: x and "HomeScoreFinal" in x)
            time_tag = game.find("span", id=lambda x: x and "GameTime" in x)
            game_id_raw = game.find("div", string=lambda x: x and "GameID:" in x)
            ballpark_container = game.find("div", id=lambda x: x and "pnlBallparkKnown" in x)
 
            if not team_v_raw or not team_h_raw:
                continue
 
            game_num = None
            if game_id_raw:
                game_num = game_id_raw.get_text(strip=True).replace("GameID:", "").strip()
 
            ballpark = None
            field_num = None
            if ballpark_container:
                field_div = ballpark_container.find("div")
                if field_div:
                    full_text = field_div.get_text(" ", strip=True)  # e.g. "Field 5 @ The Rac Waller"
                    if "@" in full_text:
                        field_part, ballpark_part = full_text.split("@", 1)
                        field_num = field_part.strip() or None
                        ballpark = ballpark_part.strip() or None
                    else:
                        ballpark = full_text or None
 
            extracted.append({
                "game_num": game_num,
                "ballpark": ballpark,
                "field_num": field_num,
                "game_date": game_date,
                "game_time": time_tag.get_text(strip=True) if time_tag else None,
                "away_team": team_v_raw.get_text(strip=True),
                "away_score": safe_int(score_v_raw),
                "home_team": team_h_raw.get_text(strip=True),
                "home_score": safe_int(score_h_raw),
                "sourceurl": source_url,
            })
        except Exception as e:
            print(f"   [Error] Parsing individual game row: {e}")
 
    return extracted
 
 
SCHEDULE_URL_TEMPLATE = "https://www.perfectgame.org/Events/TournamentSchedule.aspx?event={eventid}&Date={date}"
 
 
def _get_event_date_range(conn: sqlite3.Connection, eventid: str) -> tuple:
    """Returns (start_date, end_date) strings straight from the events table, as stored (e.g. "8/2/2025")."""
    row = conn.execute("SELECT start_date, end_date FROM events WHERE eventid = ?", (eventid,)).fetchone()
    if not row:
        return (None, None)
    return (row["start_date"], row["end_date"])
 
 
def _parse_db_date(value: str):
    """
    Parses whatever date format your events table actually stores
    (confirmed: ISO-style "2026-08-22 00:00:00") into a datetime.
    Tries a couple of common variants in case some rows differ.
    """
    from datetime import datetime
 
    formats = [
        "%Y-%m-%d %H:%M:%S",  # confirmed real format, e.g. "2026-08-22 00:00:00"
        "%Y-%m-%d",
        "%m/%d/%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"Couldn't parse date '{value}' — none of the expected formats matched")
 
 
def _enumerate_dates(start_date: str, end_date: str) -> list[str]:
    """
    Expands a start/end date pair (as stored in the DB) into every date
    string in between, inclusive, reformatted to the URL's expected
    style (M/D/YYYY, not zero-padded — matches the sample URL's
    "8/2/2025"). The DB's own format and the URL's format are
    different things — this function bridges the two.
    """
    from datetime import timedelta
 
    start = _parse_db_date(start_date)
    end = _parse_db_date(end_date) if end_date else start
 
    dates = []
    d = start
    while d <= end:
        dates.append(f"{d.month}/{d.day}/{d.year}")
        d += timedelta(days=1)
    return dates
 
 
def _fetch_schedule_html(url: str) -> str:
    """
    Adapted from your script's Selenium setup — same headless Chrome
    options, same 5-second wait for the page's JS to finish rendering.
 
    Selenium's built-in auto-detection can't figure out your platform
    (a known gap on Windows ARM64) for BOTH the chromedriver binary and
    the Chrome browser binary itself — setting only CHROMEDRIVER_PATH
    isn't enough, since Selenium still tries (and fails) to auto-locate
    Chrome.exe separately. Set both env vars before starting uvicorn:
      - CHROMEDRIVER_PATH: path to chromedriver.exe
      - CHROME_BINARY_PATH: path to chrome.exe (find it by visiting
        chrome://version in Chrome — look for "Executable Path")
 
    Reminder: this requires Chrome + a matching chromedriver on
    whatever machine runs it. That's fine locally; it is NOT available
    on Render's free tier by default (see _scrape_pool_games's
    docstring). Test this locally before assuming it'll work once
    deployed.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    import time
 
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--log-level=3")
    chrome_options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
    )
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
 
    chrome_binary_path = os.environ.get("CHROME_BINARY_PATH")
    if chrome_binary_path:
        chrome_options.binary_location = chrome_binary_path
 
    chromedriver_path = os.environ.get("CHROMEDRIVER_PATH")
    service = Service(executable_path=chromedriver_path) if chromedriver_path else Service()
 
    driver = webdriver.Chrome(service=service, options=chrome_options)
    try:
        driver.get(url)
        time.sleep(5)
        return driver.page_source
    finally:
        driver.quit()
 
 
def _scrape_pool_games(eventid: str, date: str) -> list[dict]:
    """
    Fetches and parses the single schedule page for eventid+date via
    Selenium + _parse_games_from_html. The date to use has already
    been resolved by the caller (_populate_pool_play) — either the
    event's start_date automatically (short events) or the date the
    user picked (long events, via needs_date_selection).
 
    Still open: Render deployment (see docstring further up this
    file). This will run fine locally; deploying it as-is likely won't
    work on a free Render instance.
    """
    url = SCHEDULE_URL_TEMPLATE.format(eventid=eventid, date=date)
    html = _fetch_schedule_html(url)
    return _parse_games_from_html(html, source_url=url, game_date=date)
 
 
def _insert_pool_games(conn: sqlite3.Connection, eventid: str, games: list[dict]):
    """Writes freshly-scraped games into the games table so future calls hit the DB instead of re-scraping."""
    for g in games:
        conn.execute(
            """
            INSERT INTO games (eventid, format, game_date, game_time, game_num, ballpark, field_num, home_team, away_team, sourceurl)
            VALUES (?, 'pool', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                eventid,
                g.get("game_date"), g.get("game_time"), g.get("game_num"),
                g.get("ballpark"), g.get("field_num"),
                g.get("home_team"), g.get("away_team"),
                g.get("sourceurl"),
            ),
        )
 
 
_PHASE_TO_FORMAT = {"pool-play": "pool", "bracket-play": "bracket"}

N_SIMS = 2000


def predict_games(phase: str, tournament_id: str) -> dict:
    """
    Run the Monte Carlo simulation (monte_carlo_game.py) over every
    scheduled (not yet completed) game in this phase and return a
    win-probability split for each. Also includes already-completed games
    in the same phase, marked is_final=True with their real score, so the
    frontend can show the whole schedule with actuals visually distinct
    from predictions rather than just the still-open games. Team form (avg
    runs scored/allowed, rolling 5/10-game stats) is computed fresh from
    the current database on every call, so predictions reflect the latest
    results even between model retrains -- only the score/win-probability
    models themselves (models/*.pkl) need a retrain to pick up new
    patterns; the stats feeding them are always current.

    Return shape:
    {
      "sims_run": 5000,
      "predictions": [
        {"game_id": "g1", "team_a": "Tomball Thunder", "team_b": "Cy-Fair Comets",
         "win_pct_a": 58, "win_pct_b": 42, "is_final": false, "score_a": null, "score_b": null},
        {"game_id": "g2", "team_a": "...", "team_b": "...",
         "win_pct_a": 100, "win_pct_b": 0, "is_final": true, "score_a": 7, "score_b": 3},
        ...
      ]
    }
    """
    import team_stats
    from monte_carlo_game import monte_carlo_single_game

    game_format = _PHASE_TO_FORMAT.get(phase)
    if game_format is None:
        raise NotImplementedError(f"Wire this up to your {phase} prediction logic")

    conn = _get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, home_team, away_team, ballpark, format, classification,
                   bracket_round, home_seed, visitor_seed, game_date, game_time,
                   home_score, away_score
            FROM games
            WHERE eventid = ? AND format = ?
            ORDER BY game_date, game_time
            """,
            (tournament_id, game_format),
        ).fetchall()

        if not rows:
            return {"sims_run": N_SIMS, "predictions": []}

        games_df, teams_df = team_stats.load_games_and_teams()
    finally:
        conn.close()

    stats = team_stats.build_team_stats(games_df, teams_df)
    league_avg_rs = stats["avg_runs_scored"].mean() if not stats.empty else 0.0
    league_avg_ra = stats["avg_runs_allowed"].mean() if not stats.empty else 0.0

    predictions = []
    for row in rows:
        if row["home_score"] is not None and row["away_score"] is not None:
            home_score, away_score = row["home_score"], row["away_score"]
            if home_score > away_score:
                win_pct_a, win_pct_b = 100, 0
            elif away_score > home_score:
                win_pct_a, win_pct_b = 0, 100
            else:
                win_pct_a, win_pct_b = 50, 50
            predictions.append({
                "game_id": str(row["id"]),
                "team_a": row["home_team"],
                "team_b": row["away_team"],
                "win_pct_a": win_pct_a,
                "win_pct_b": win_pct_b,
                "is_final": True,
                "score_a": home_score,
                "score_b": away_score,
            })
            continue

        features = team_stats.build_game_features_row(
            home_team=row["home_team"],
            away_team=row["away_team"],
            team_stats=stats,
            ballpark=row["ballpark"],
            format=row["format"],
            classification=row["classification"],
            bracket_round=row["bracket_round"],
            home_seed=row["home_seed"],
            visitor_seed=row["visitor_seed"],
            game_date=row["game_date"],
            game_time=row["game_time"],
            league_avg_rs=league_avg_rs,
            league_avg_ra=league_avg_ra,
        )

        sim = monte_carlo_single_game(features, n_sims=N_SIMS)

        predictions.append({
            "game_id": str(row["id"]),
            "team_a": row["home_team"],
            "team_b": row["away_team"],
            "win_pct_a": round(sim["home_win_probability"] * 100),
            "win_pct_b": round(sim["away_win_probability"] * 100),
            "is_final": False,
            "score_a": None,
            "score_b": None,
        })

    return {"sims_run": N_SIMS, "predictions": predictions}


POOL_STANDINGS_N_SIMS = 2000
POOL_SCENARIO_N_SIMS = 4000  # higher than POOL_STANDINGS_N_SIMS -- conditioning on one exact
# placement thins the sample, so a bigger run keeps the filtered subset stable.

_SEED_LABEL_RE = re.compile(r"^Seed #(\d+)$")


def _lookup_next_bracket_slot(tournament_id: str) -> dict:
    """
    Maps a pool-standing place (1st, 2nd, ...) to the field/time of that
    seed's first bracket-play game, for as long as the schedule still shows
    it as a raw "Seed #N" placeholder rather than a resolved team name
    (true until that slot's game is actually played). Perfect Game numbers
    seeds tournament-wide across every pool, which lines up directly with
    the 1..pool_size place numbers predict_pool_standings already returns
    -- no separate join key needed.
    """
    conn = _get_connection()
    try:
        rows = conn.execute(
            """
            SELECT home_team, away_team, bracket, ballpark, field_num, game_date, game_time
            FROM games
            WHERE eventid = ? AND format = 'bracket'
            ORDER BY game_date, game_time
            """,
            (tournament_id,),
        ).fetchall()
    finally:
        conn.close()

    by_seed = {}
    for row in rows:
        for team in (row["home_team"], row["away_team"]):
            m = _SEED_LABEL_RE.match((team or "").strip())
            if not m:
                continue
            seed = int(m.group(1))
            by_seed.setdefault(seed, {
                "next_bracket_name": row["bracket"],
                "next_ballpark": row["ballpark"],
                "next_field": row["field_num"],
                "next_date": row["game_date"],
                "next_time": row["game_time"],
            })
    return by_seed


def _load_pool_games(tournament_id: str, team_name: str = None) -> list[dict]:
    """
    Shared by predict_pool_standings, explain_pool_placement and
    get_current_pool_standings: fetches this tournament's pool-play games
    and, for each not-yet-played one, builds the same feature dict
    predict_single_game() expects (team form computed fresh from the
    current database). Raises ValueError if the tournament has no pool
    games, or if team_name is given and isn't one of its teams (skip the
    team check entirely by leaving team_name out, e.g. for standings
    across the whole pool rather than one team).
    """
    import team_stats

    conn = _get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, home_team, away_team, home_score, away_score,
                   ballpark, format, classification, bracket_round,
                   home_seed, visitor_seed, game_date, game_time
            FROM games
            WHERE eventid = ? AND format = 'pool'
            """,
            (tournament_id,),
        ).fetchall()

        if not rows:
            raise ValueError(f"No pool-play games found for tournament {tournament_id}")

        if team_name is not None:
            team_names = {r["home_team"] for r in rows} | {r["away_team"] for r in rows}
            if team_name not in team_names:
                raise ValueError(f"'{team_name}' is not in this tournament's pool")

        games_df, teams_df = team_stats.load_games_and_teams()
    finally:
        conn.close()

    stats = team_stats.build_team_stats(games_df, teams_df)
    league_avg_rs = stats["avg_runs_scored"].mean() if not stats.empty else 0.0
    league_avg_ra = stats["avg_runs_allowed"].mean() if not stats.empty else 0.0

    pool_games = []
    for row in rows:
        if row["home_score"] is not None and row["away_score"] is not None:
            pool_games.append({
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "home_score": row["home_score"],
                "away_score": row["away_score"],
                "completed": True,
            })
        else:
            features = team_stats.build_game_features_row(
                home_team=row["home_team"],
                away_team=row["away_team"],
                team_stats=stats,
                ballpark=row["ballpark"],
                format=row["format"],
                classification=row["classification"],
                bracket_round=row["bracket_round"],
                home_seed=row["home_seed"],
                visitor_seed=row["visitor_seed"],
                game_date=row["game_date"],
                game_time=row["game_time"],
                league_avg_rs=league_avg_rs,
                league_avg_ra=league_avg_ra,
            )
            features["home_team"] = row["home_team"]
            features["away_team"] = row["away_team"]
            features["completed"] = False
            pool_games.append(features)

    return pool_games


def get_current_pool_standings(phase: str, tournament_id: str) -> dict:
    """
    Actual standings as of right now -- no simulation. Completed games
    count for real; anything not yet played is treated as a 0-0 tie (see
    pool_standings.compute_current_standings), same ranking as everywhere
    else: win% -> fewest runs allowed -> most runs scored.
    """
    if phase != "pool-play":
        raise NotImplementedError("Pool standings only applies to pool play")

    from pool_standings import compute_current_standings

    pool_games = _load_pool_games(tournament_id)
    return compute_current_standings(pool_games)


def predict_pool_standings(phase: str, tournament_id: str, team_name: str) -> dict:
    """
    Simulates this tournament's entire pool-play schedule POOL_STANDINGS_N_SIMS
    times (already-completed games are fixed; not-yet-played games are drawn
    randomly each run, jointly with each other) and returns the distribution
    of final placements for `team_name`, ranked by win% -> fewest runs
    allowed -> most runs scored (see pool_standings.py).

    Assumes every team scheduled for this tournament's pool play is in one
    single pool together.

    Only meaningful for pool play -- there's no "standings" concept in
    single-elimination bracket play.
    """
    if phase != "pool-play":
        raise NotImplementedError("Pool standings simulation only applies to pool play")

    from pool_standings import simulate_pool_standings

    pool_games = _load_pool_games(tournament_id, team_name)
    result = simulate_pool_standings(pool_games, team_name, n_sims=POOL_STANDINGS_N_SIMS)

    next_slot_by_seed = _lookup_next_bracket_slot(tournament_id)
    for placement in result["placements"]:
        slot = next_slot_by_seed.get(placement["place"])
        if slot:
            placement.update(slot)

    return result


def explain_pool_placement(phase: str, tournament_id: str, team_name: str, place: int) -> dict:
    """
    "What has to happen" for team_name to finish in `place`: reruns the
    same pool simulation, but this time looks only at the runs where the
    team actually landed on that exact place, and checks how the pool's
    still-open games went in THOSE runs specifically. A game counts as a
    hard requirement when one side won it in almost every one of those
    runs; games that split roughly evenly aren't decisive on their own and
    are left out. This is empirically derived from the simulation, not a
    formal guarantee -- see pool_standings.explain_placement_scenario for
    the actual mechanics.
    """
    if phase != "pool-play":
        raise NotImplementedError("Pool standings simulation only applies to pool play")

    from pool_standings import explain_placement_scenario

    pool_games = _load_pool_games(tournament_id, team_name)
    return explain_placement_scenario(pool_games, team_name, place, n_sims=POOL_SCENARIO_N_SIMS)


def update_scores(phase: str, tournament_id: str, scores: list[dict]) -> dict:
    """
    Write final scores back to the games table for this phase.

    `scores` is a list of dicts already validated by FastAPI, each
    shaped like: {"game_id": "g1", "score_a": 6, "score_b": 4}

    Once written, home_score/away_score are no longer NULL, which is the
    same signal predict_games() and predict_pool_standings() already key
    off of to tell a completed game from a pending one -- predict_games()
    stops predicting it (its WHERE clause excludes anything with scores
    set) and predict_pool_standings() treats it as a fixed, non-random
    result in every simulated pool rather than a predicted one. It also
    feeds into team_stats.load_games_and_teams(), which only pulls
    completed games -- so a newly-finalized score immediately factors into
    that team's rolling averages for every other prediction too. No
    separate "lock this in" step is needed; writing the score here is
    what makes it constant everywhere else.

    Return shape:
    { "updated": 3 }   # count of games actually written
    """
    game_format = _PHASE_TO_FORMAT.get(phase)
    if game_format is None:
        raise NotImplementedError(f"Wire this up to your {phase} score-update logic")

    conn = _get_connection()
    try:
        updated = 0
        for s in scores:
            cur = conn.execute(
                """
                UPDATE games
                SET home_score = ?, away_score = ?
                WHERE id = ? AND eventid = ? AND format = ?
                """,
                (s["score_a"], s["score_b"], int(s["game_id"]), tournament_id, game_format),
            )
            updated += cur.rowcount
        conn.commit()
        return {"updated": updated}
    finally:
        conn.close()


_FEATURE_LABELS = {
    "home_avg_runs_scored": "Home avg. runs scored",
    "home_avg_runs_allowed": "Home avg. runs allowed",
    "away_avg_runs_scored": "Away avg. runs scored",
    "away_avg_runs_allowed": "Away avg. runs allowed",
    "home_seed": "Home seed",
    "visitor_seed": "Away seed",
    "home_age_group": "Home age group",
    "away_age_group": "Away age group",
    "home_level": "Home level",
    "away_level": "Away level",
    "age_group_diff": "Age group difference",
    "level_diff": "Level difference",
    "home_rs_last_5": "Home runs scored (last 5)",
    "home_ra_last_5": "Home runs allowed (last 5)",
    "away_rs_last_5": "Away runs scored (last 5)",
    "away_ra_last_5": "Away runs allowed (last 5)",
    "home_rs_last_10": "Home runs scored (last 10)",
    "home_ra_last_10": "Home runs allowed (last 10)",
    "away_rs_last_10": "Away runs scored (last 10)",
    "away_ra_last_10": "Away runs allowed (last 10)",
    "hour": "Game hour",
    "is_weekend": "Weekend game",
    "ballpark": "Ballpark",
    "format": "Format",
    "classification": "Classification",
    "bracket_round": "Round",
    "month": "Month",
    "season": "Season",
    "time_bucket": "Time of day",
    "weekday": "Day of week",
}

_MONTH_NAMES = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _format_feature_value(name: str, value) -> str:
    if value is None:
        return "—"
    if name == "is_weekend":
        return "Yes" if value else "No"
    if name == "month":
        try:
            return _MONTH_NAMES[int(value)]
        except (ValueError, IndexError):
            return str(value)
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def _aggregate_feature_importances(transformer, importances) -> dict:
    """
    A fitted ColumnTransformer expands each categorical feature into one
    column per category it saw during training (one-hot encoding), so
    model.feature_importances_ is indexed per expanded column, not per
    original feature -- e.g. "ballpark" becomes a dozen separate
    "cat__ballpark_<venue name>" columns. This sums those back down to one
    importance score per ORIGINAL feature (numeric features pass through
    as a single column already).
    """
    import team_stats

    names = transformer.get_feature_names_out()
    agg = {}
    for name, imp in zip(names, importances):
        prefix, _, rest = name.partition("__")
        if prefix == "num":
            original = rest
        else:
            original = next(
                (f for f in team_stats.CATEGORICAL_FEATURES if rest.startswith(f + "_")),
                rest,
            )
        agg[original] = agg.get(original, 0.0) + float(imp)
    return agg


def explain_game(phase: str, tournament_id: str, game_id: str) -> dict:
    """
    "Key factors" behind one game's prediction. Not true per-prediction
    attribution (that would need something like SHAP) -- this combines the
    win-probability model's overall feature importances (what the model
    leans on in general) with this specific game's actual values for those
    features, which is a much lighter-weight way to give a concrete,
    honest answer to "why this prediction" without adding a new dependency.

    Return shape:
    {
      "game_id": "g1", "team_a": "...", "team_b": "...",
      "pred_home_score": 6, "pred_away_score": 4, "pred_win_probability": 63,
      "factors": [{"feature": "Home avg. runs scored", "value": "6.2", "weight_pct": 24}, ...]
    }
    """
    game_format = _PHASE_TO_FORMAT.get(phase)
    if game_format is None:
        raise NotImplementedError(f"Wire this up to your {phase} prediction logic")

    import team_stats
    from load_models import load_win_probability_model, load_column_transformer
    from predict_single_game import predict_single_game

    conn = _get_connection()
    try:
        row = conn.execute(
            """
            SELECT id, home_team, away_team, ballpark, format, classification,
                   bracket_round, home_seed, visitor_seed, game_date, game_time
            FROM games
            WHERE id = ? AND eventid = ? AND format = ?
            """,
            (int(game_id), tournament_id, game_format),
        ).fetchone()

        if not row:
            raise ValueError(f"Game {game_id} not found for this tournament")

        games_df, teams_df = team_stats.load_games_and_teams()
    finally:
        conn.close()

    stats = team_stats.build_team_stats(games_df, teams_df)
    league_avg_rs = stats["avg_runs_scored"].mean() if not stats.empty else 0.0
    league_avg_ra = stats["avg_runs_allowed"].mean() if not stats.empty else 0.0

    features = team_stats.build_game_features_row(
        home_team=row["home_team"],
        away_team=row["away_team"],
        team_stats=stats,
        ballpark=row["ballpark"],
        format=row["format"],
        classification=row["classification"],
        bracket_round=row["bracket_round"],
        home_seed=row["home_seed"],
        visitor_seed=row["visitor_seed"],
        game_date=row["game_date"],
        game_time=row["game_time"],
        league_avg_rs=league_avg_rs,
        league_avg_ra=league_avg_ra,
    )

    preds = predict_single_game(features)

    win_model = load_win_probability_model()
    transformer = load_column_transformer()
    importances = _aggregate_feature_importances(transformer, win_model.feature_importances_)

    top = sorted(importances.items(), key=lambda kv: kv[1], reverse=True)[:8]
    top_total = sum(v for _, v in top) or 1.0

    factors = [
        {
            "feature": _FEATURE_LABELS.get(name, name),
            "value": _format_feature_value(name, features.get(name)),
            "weight_pct": round(val / top_total * 100),
        }
        for name, val in top
    ]

    return {
        "game_id": str(row["id"]),
        "team_a": row["home_team"],
        "team_b": row["away_team"],
        "pred_home_score": preds["pred_home_score"],
        "pred_away_score": preds["pred_away_score"],
        "pred_win_probability": round(preds["pred_win_probability"] * 100),
        "factors": factors,
    }


def get_admin_status() -> dict:
    """
    Monitoring data for the admin page: every event (any status -- this
    isn't scoped to complete/upcoming like the tournament picker, since
    the whole point here is visibility into scheduled/cancelled/ongoing
    events too) with its game counts. Works against whichever DB db.py
    resolves to (local sqlite or Turso), same as everything else.

    games.eventid is stored as TEXT and events.eventid as INTEGER -- SQLite
    (and Turso, which is wire-compatible) coerces this correctly via
    affinity rules in comparisons, but the two need explicit str()
    normalization here since they end up as Python dict keys.
    """
    conn = _get_connection()
    try:
        events = conn.execute("""
            SELECT eventid, name, status, classification, start_date, end_date
            FROM events
            ORDER BY start_date
        """).fetchall()

        game_counts = conn.execute("""
            SELECT eventid,
                   COUNT(*) AS total,
                   SUM(CASE WHEN home_score IS NOT NULL AND away_score IS NOT NULL THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN format = 'pool' THEN 1 ELSE 0 END) AS pool,
                   SUM(CASE WHEN format = 'bracket' THEN 1 ELSE 0 END) AS bracket
            FROM games
            WHERE eventid IN (SELECT eventid FROM events)
            GROUP BY eventid
        """).fetchall()

        total_teams_row = conn.execute("SELECT COUNT(*) AS n FROM teams").fetchone()
    finally:
        conn.close()

    counts_by_event = {str(r["eventid"]): r for r in game_counts}

    event_list = []
    total_games = 0
    total_completed = 0
    for e in events:
        c = counts_by_event.get(str(e["eventid"]))
        total = (c["total"] if c else 0) or 0
        completed = (c["completed"] if c else 0) or 0
        pool = (c["pool"] if c else 0) or 0
        bracket = (c["bracket"] if c else 0) or 0
        total_games += total
        total_completed += completed
        event_list.append({
            "eventid": str(e["eventid"]),
            "name": e["name"],
            "status": e["status"],
            "classification": e["classification"],
            "start_date": e["start_date"],
            "end_date": e["end_date"],
            "total_games": total,
            "completed_games": completed,
            "pending_games": total - completed,
            "pool_games": pool,
            "bracket_games": bracket,
        })

    return {
        "events": event_list,
        "total_events": len(event_list),
        "total_games": total_games,
        "total_completed_games": total_completed,
        "total_teams": (total_teams_row["n"] if total_teams_row else 0) or 0,
    }


# The distinct values actually seen in the events table's status column.
VALID_EVENT_STATUSES = ("complete", "upcoming", "ongoing", "scheduled", "cancelled")


def update_event_status(eventid: str, status: str) -> dict:
    """Admin page action: changes one event's status. Writes through
    db.py the same as everything else, so this lands on Turso in prod."""
    if status not in VALID_EVENT_STATUSES:
        raise ValueError(f"Unknown status '{status}', expected one of {VALID_EVENT_STATUSES}")

    conn = _get_connection()
    try:
        row = conn.execute("SELECT eventid FROM events WHERE eventid = ?", (eventid,)).fetchone()
        if row is None:
            raise ValueError(f"Event {eventid} not found")

        conn.execute("UPDATE events SET status = ? WHERE eventid = ?", (status, eventid))
        conn.commit()
    finally:
        conn.close()

    return {"eventid": str(eventid), "status": status}