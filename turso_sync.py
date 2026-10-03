"""
Pushes freshly-scraped local data into the hosted Turso database, for when
the pipeline is run "connected via laptop" and its results should reach
the live deployed app, not just the local sqlite file.

Safe by design around the one real conflict risk: the deployed web app can
also write to games.home_score / games.away_score (the "Update Scores"
feature) and games.home_team_key / games.away_team_key (Phase 3B). This
never overwrites those four columns on a game that already has them set in
Turso -- local data only fills them in if Turso's copy is still empty, so
a live-entered score can never be clobbered by a stale rescrape. Everything
else (schedule details, bracket/seed enrichment, new games, new teams,
events) syncs freely since nothing else writes to those from the live app.

Uses turso_serverless -- a pure-Python, DB-API 2.0 HTTP client -- rather
than sqlalchemy-libsql (which the backend uses): that one only ships
Linux/macOS wheels and fails to build from source here (no Rust toolchain),
so it can't run on this laptop at all. turso_serverless has no such
restriction (confirmed: installs cleanly here).

Requires TURSO_DATABASE_URL / TURSO_AUTH_TOKEN (see .env.example, loaded
via env_local.py). Safe to call unconditionally from the pipeline's main()
-- no-ops with a notice if they aren't set, so local-only runs are
unaffected.
"""
import os
import sqlite3
import time

import env_local  # noqa: F401 -- loads .env.local into os.environ on import

TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN")
LOCAL_DB_PATH = os.environ.get("DB_PATH", r"C:\Users\bsmel\OneDrive\Documents\Baseball_data\10u data.db")

# A full sync makes thousands of individual HTTP round trips (confirmed:
# the first real run died partway through on a transient WinError 10060
# timeout, with no retry). Every remote call goes through this wrapper.
_RETRY_ATTEMPTS = 4
_RETRY_DELAY_SECONDS = 3


def _retry(fn):
    last_exc = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if attempt < _RETRY_ATTEMPTS - 1:
                time.sleep(_RETRY_DELAY_SECONDS)
    raise last_exc

# Columns the live web app can also write -- see module docstring. Never
# overwritten on a Turso game row that already has a non-null value here.
_PROTECTED_GAME_COLUMNS = ("home_score", "away_score", "home_team_key", "away_team_key")

_EVENT_COLUMNS = ["eventid", "name", "age", "classification", "start_date", "end_date", "status", "season"]
_TEAM_COLUMNS = ["team_name", "team_index", "age_group", "level", "last_updated"]
_GAME_COLUMNS = [
    "eventid", "game_date", "game_time", "game_num", "format", "ballpark", "field_num",
    "away_team", "away_team_key", "away_score", "home_team", "home_team_key", "home_score",
    "bracket", "bracket_round", "seed", "sourceurl", "bracketurl", "classification",
    "home_seed", "visitor_seed",
]

# GameChanger scouting tables -- see gamechanger_scrape.py's module docstring
# for why these are keyed by GC's own external ids rather than a local
# autoincrement surrogate: it means every one of these syncs the same
# natural-key way _sync_teams/_sync_games above already do, with no risk of
# a local row id mismatching Turso's own independently-assigned one.
_GC_TEAM_COLUMNS = ["gc_team_id", "gc_url", "gc_team_name", "pg_team_key", "season", "last_scraped"]
_GC_PLAYER_COLUMNS = ["gc_player_id", "gc_team_id", "player_name", "jersey_number", "first_seen_date", "last_seen_date"]
_GC_GAME_COLUMNS = [
    "gc_game_id", "gc_team_id", "game_date", "opponent_name", "opponent_id", "home_away",
    "final_score_for", "final_score_against", "boxscore_url", "pg_game_id", "pg_eventid",
    "match_method", "last_scraped",
]
_GC_BATTING_COLUMNS = ["gc_game_id", "gc_player_id", "ab", "r", "h", "doubles", "triples", "hr",
                       "rbi", "bb", "so", "sb", "hbp", "sf", "sh"]
_GC_PITCHING_COLUMNS = ["team_key", "player_name", "game_date", "gc_game_id", "gc_player_id",
                        "ip_outs", "h", "r", "er", "bb", "so", "hr", "pitches", "strikes", "source",
                        "entered_by", "entered_at", "notes"]
_GC_FIELDING_COLUMNS = ["gc_game_id", "gc_player_id", "errors"]


def enabled() -> bool:
    return bool(TURSO_DATABASE_URL and TURSO_AUTH_TOKEN)


def sync_to_turso(verbose: bool = True, event_id: str = None, gc_team_id: str = None,
                   sync_pg: bool = True, sync_gc: bool = True) -> dict:
    """
    Pushes events / teams / games from the local sqlite file into Turso.
    No-ops (returns {"synced": False}) if Turso isn't configured.

    With event_id given (matching --event on the pipeline), scopes the PG
    syncs to just that event instead of the whole local database -- a full
    sync re-pushes every event/team/game every run regardless of what
    actually changed (confirmed: ~248 event calls + ~607 team calls + one
    call per already-existing game, ~6,000+ round trips total even when only
    one event's worth of games actually changed), which is fine on wifi but
    can take tens of minutes on a slower/higher-latency connection like a
    phone hotspot for what should be a quick single-event rescrape.

    With gc_team_id given (matching a gamechanger_scrape.py run), scopes the
    GameChanger syncs to just that team's games/stats the same way -- GC
    scrapes are per-team, not per-event, so this is a separate scope
    parameter from event_id, not a reuse of it.

    sync_pg / sync_gc skip that whole side entirely (both default True, the
    original full-sync behavior). gamechanger_scrape.py calls this with
    sync_pg=False, since a GC scrape/import never touches events/teams/games
    and re-syncing all of PG's data on every single GC import was a real
    confirmed slowdown (the ~6,000+ round trip issue above), not just a
    theoretical one.
    """
    if not enabled():
        if verbose:
            print("TURSO_DATABASE_URL/TURSO_AUTH_TOKEN not set -- skipping Turso sync (local-only run).")
        return {"synced": False}

    import turso_serverless

    local = sqlite3.connect(LOCAL_DB_PATH)
    local.row_factory = sqlite3.Row
    remote = turso_serverless.connect(TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN)

    summary = {"synced": True}
    try:
        if sync_pg:
            summary["events_synced"] = _sync_events(local, remote, event_id)
            summary["teams_synced"] = _sync_teams(local, remote, event_id)
            summary["games_inserted"], summary["games_updated"] = _sync_games(local, remote, event_id)
        if sync_gc:
            _ensure_remote_gc_schema(remote)
            summary["gc_teams_synced"] = _sync_gc_teams(local, remote, gc_team_id)
            summary["gc_players_synced"] = _sync_gc_players(local, remote, gc_team_id)
            summary["gc_games_synced"] = _sync_gc_games(local, remote, gc_team_id)
            summary["gc_batting_synced"] = _sync_gc_batting(local, remote, gc_team_id)
            summary["gc_pitching_synced"] = _sync_gc_pitching(local, remote, gc_team_id)
            summary["gc_fielding_synced"] = _sync_gc_fielding(local, remote, gc_team_id)
        remote.commit()
    finally:
        local.close()
        remote.close()

    if verbose:
        print(f"Turso sync complete: {summary}")
    return summary


def _sync_events(local, remote, event_id: str = None) -> int:
    """
    Upserts every event (or just event_id, if given) -- the live app never
    writes to the events table, so a plain overwrite is safe. events.eventid
    has no unique constraint (confirmed via the schema), so this uses
    delete-then-insert rather than ON CONFLICT.
    """
    cols = _EVENT_COLUMNS
    if event_id:
        rows = local.execute(
            f"SELECT {', '.join(cols)} FROM events WHERE eventid = ?", (event_id,)
        ).fetchall()
    else:
        rows = local.execute(f"SELECT {', '.join(cols)} FROM events").fetchall()
    for row in rows:
        eventid = row["eventid"]
        _retry(lambda: remote.execute("DELETE FROM events WHERE eventid = ?", [eventid]))
        _retry(lambda: remote.execute(
            f"INSERT INTO events ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
            [row[c] for c in cols],
        ))
    return len(rows)


def _sync_teams(local, remote, event_id: str = None) -> int:
    """Inserts any team not already present in Turso, matched by
    team_name (which does have a real UNIQUE constraint). With event_id
    given, only considers teams that actually played in that event's
    games -- teams have no eventid column of their own, so this is
    matched via a subquery against games instead."""
    cols = _TEAM_COLUMNS
    if event_id:
        rows = local.execute(
            f"""
            SELECT {', '.join(cols)} FROM teams
            WHERE team_name IN (
                SELECT home_team FROM games WHERE eventid = ?
                UNION
                SELECT away_team FROM games WHERE eventid = ?
            )
            """,
            (event_id, event_id),
        ).fetchall()
    else:
        rows = local.execute(f"SELECT {', '.join(cols)} FROM teams").fetchall()
    for row in rows:
        _retry(lambda: remote.execute(
            f"""
            INSERT INTO teams ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})
            ON CONFLICT(team_name) DO NOTHING
            """,
            [row[c] for c in cols],
        ))
    return len(rows)


def _sync_games(local, remote, event_id: str = None) -> tuple[int, int]:
    """
    Scoped to eventid IN (SELECT eventid FROM events) by default -- this
    pipeline's own events, not the unrelated legacy dataset also present in
    the local games table (confirmed: the events table itself only ever
    holds our 124 events, so this filter is exact, not approximate). With
    event_id given, scoped to just that one event instead, both for the
    local read and the remote bulk prefetch below -- a full sync otherwise
    unconditionally re-sends an UPDATE for every already-existing game
    under every event, not just the one that was actually rescraped.

    Matches games by (eventid, game_num) -- game_num is Perfect Game's own
    globally-unique GameID, not a display label, so this is a reliable
    natural key. Games with no game_num (rare, pre-dates a scraper fix) are
    skipped rather than risk creating a duplicate from a guessed key.

    Fetches every existing Turso game's protected columns in ONE bulk
    query up front rather than one SELECT per local game -- with ~5,400
    games that's the difference between ~1 request and ~5,400 of them.
    (Confirmed the naive per-row version this replaced actually died mid-run
    on a transient network timeout, which a smaller request count also
    makes less likely to hit at all.)
    """
    if event_id:
        scope_sql = "eventid = ?"
        scope_params = (event_id,)
    else:
        scope_sql = "eventid IN (SELECT eventid FROM events)"
        scope_params = ()

    rows = local.execute(
        f"SELECT {', '.join(_GAME_COLUMNS)} FROM games WHERE {scope_sql}", scope_params
    ).fetchall()

    existing_rows = _retry(lambda: remote.execute(
        f"""
        SELECT eventid, game_num, {', '.join(_PROTECTED_GAME_COLUMNS)}
        FROM games
        WHERE {scope_sql} AND game_num IS NOT NULL
        """,
        scope_params,
    ).fetchall())
    existing_by_key = {(str(r[0]), r[1]): r[2:] for r in existing_rows}

    inserted = 0
    updated = 0

    for row in rows:
        eventid = row["eventid"]
        game_num = row["game_num"]
        if game_num is None:
            continue

        key = (str(eventid), game_num)
        values = {c: row[c] for c in _GAME_COLUMNS}

        if key not in existing_by_key:
            cols = list(values.keys())
            _retry(lambda: remote.execute(
                f"INSERT INTO games ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
                [values[c] for c in cols],
            ))
            inserted += 1
        else:
            existing_protected = dict(zip(_PROTECTED_GAME_COLUMNS, existing_by_key[key]))
            set_cols = [c for c in _GAME_COLUMNS if c not in _PROTECTED_GAME_COLUMNS]
            for c in _PROTECTED_GAME_COLUMNS:
                if existing_protected[c] is None and values[c] is not None:
                    set_cols.append(c)

            if set_cols:
                _retry(lambda: remote.execute(
                    f"UPDATE games SET {', '.join(f'{c} = ?' for c in set_cols)} "
                    f"WHERE eventid = ? AND game_num = ?",
                    [values[c] for c in set_cols] + [eventid, game_num],
                ))
                updated += 1

    return inserted, updated


# ==============================================================================
# GAMECHANGER SCOUTING TABLES
# ==============================================================================

_GC_SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS gc_teams (
        gc_team_id TEXT PRIMARY KEY, gc_url TEXT, gc_team_name TEXT,
        pg_team_key INTEGER, season TEXT, last_scraped TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS gc_players (
        gc_player_id TEXT PRIMARY KEY, gc_team_id TEXT, player_name TEXT,
        jersey_number TEXT, first_seen_date TEXT, last_seen_date TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS gc_games (
        gc_game_id TEXT PRIMARY KEY, gc_team_id TEXT, game_date TEXT,
        opponent_name TEXT, opponent_id TEXT, home_away TEXT,
        final_score_for INTEGER, final_score_against INTEGER, boxscore_url TEXT,
        pg_game_id INTEGER, pg_eventid TEXT, match_method TEXT, last_scraped TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS gc_batting_stats (
        gc_game_id TEXT, gc_player_id TEXT,
        ab INTEGER, r INTEGER, h INTEGER, doubles INTEGER, triples INTEGER, hr INTEGER,
        rbi INTEGER, bb INTEGER, so INTEGER, sb INTEGER, hbp INTEGER, sf INTEGER, sh INTEGER,
        PRIMARY KEY (gc_game_id, gc_player_id)
    )""",
    """CREATE TABLE IF NOT EXISTS gc_pitching_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT, team_key INTEGER,
        player_name TEXT NOT NULL, game_date TEXT NOT NULL,
        gc_game_id TEXT, gc_player_id TEXT,
        ip_outs INTEGER, h INTEGER, r INTEGER, er INTEGER, bb INTEGER, so INTEGER, hr INTEGER,
        pitches INTEGER, strikes INTEGER, source TEXT NOT NULL CHECK(source IN ('scraped','manual')),
        entered_by TEXT, entered_at TEXT, notes TEXT,
        UNIQUE(gc_game_id, gc_player_id), UNIQUE(team_key, player_name, game_date, source)
    )""",
    """CREATE TABLE IF NOT EXISTS gc_fielding_stats (
        gc_game_id TEXT, gc_player_id TEXT, errors INTEGER,
        PRIMARY KEY (gc_game_id, gc_player_id)
    )""",
]


def _ensure_remote_gc_schema(remote):
    """Turso needs these tables created once too -- unlike events/teams/games,
    they don't already exist there from an earlier manual setup."""
    for stmt in _GC_SCHEMA_STATEMENTS:
        _retry(lambda stmt=stmt: remote.execute(stmt))
    # CREATE TABLE IF NOT EXISTS doesn't add a column to an already-existing
    # table -- same ALTER TABLE fallback as gamechanger_scrape.py's local
    # _ensure_schema, for whatever's already live on Turso.
    try:
        _retry(lambda: remote.execute("ALTER TABLE gc_pitching_stats ADD COLUMN strikes INTEGER"))
    except Exception:
        pass  # column already exists


def _sync_gc_teams(local, remote, gc_team_id: str = None) -> int:
    """With gc_team_id given, also carries along any opponent teams it has
    played (auto-created gc_teams rows keyed by their GC opponent id) --
    otherwise a scoped sync would push the team itself but strand its
    opponents' names/mappings on Turso."""
    cols = _GC_TEAM_COLUMNS
    if gc_team_id:
        rows = local.execute(
            f"""SELECT {', '.join(cols)} FROM gc_teams
                WHERE gc_team_id = ? OR gc_team_id IN (
                    SELECT DISTINCT opponent_id FROM gc_games WHERE gc_team_id = ?
                )""",
            (gc_team_id, gc_team_id),
        ).fetchall()
    else:
        rows = local.execute(f"SELECT {', '.join(cols)} FROM gc_teams").fetchall()
    for row in rows:
        _retry(lambda row=row: remote.execute(
            f"""INSERT INTO gc_teams ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})
                ON CONFLICT(gc_team_id) DO UPDATE SET
                  gc_team_name=excluded.gc_team_name, last_scraped=excluded.last_scraped,
                  gc_url=COALESCE(excluded.gc_url, gc_teams.gc_url),
                  pg_team_key=COALESCE(excluded.pg_team_key, gc_teams.pg_team_key)""",
            [row[c] for c in cols],
        ))
    return len(rows)


def _sync_gc_players(local, remote, gc_team_id: str = None) -> int:
    cols = _GC_PLAYER_COLUMNS
    if gc_team_id:
        rows = local.execute(
            f"""SELECT {', '.join(cols)} FROM gc_players
                WHERE gc_team_id = ? OR gc_team_id IN (
                    SELECT DISTINCT opponent_id FROM gc_games WHERE gc_team_id = ?
                )""",
            (gc_team_id, gc_team_id),
        ).fetchall()
    else:
        rows = local.execute(f"SELECT {', '.join(cols)} FROM gc_players").fetchall()
    for row in rows:
        _retry(lambda row=row: remote.execute(
            f"""INSERT INTO gc_players ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})
                ON CONFLICT(gc_player_id) DO UPDATE SET
                  player_name=excluded.player_name, last_seen_date=excluded.last_seen_date,
                  gc_team_id=COALESCE(excluded.gc_team_id, gc_players.gc_team_id),
                  jersey_number=COALESCE(excluded.jersey_number, gc_players.jersey_number)""",
            [row[c] for c in cols],
        ))
    return len(rows)


def _sync_gc_games(local, remote, gc_team_id: str = None) -> int:
    cols = _GC_GAME_COLUMNS
    if gc_team_id:
        rows = local.execute(f"SELECT {', '.join(cols)} FROM gc_games WHERE gc_team_id = ?", (gc_team_id,)).fetchall()
    else:
        rows = local.execute(f"SELECT {', '.join(cols)} FROM gc_games").fetchall()
    for row in rows:
        _retry(lambda row=row: remote.execute(
            f"""INSERT INTO gc_games ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})
                ON CONFLICT(gc_game_id) DO UPDATE SET
                  game_date=excluded.game_date, opponent_name=excluded.opponent_name,
                  opponent_id=excluded.opponent_id, home_away=excluded.home_away,
                  final_score_for=COALESCE(excluded.final_score_for, gc_games.final_score_for),
                  final_score_against=COALESCE(excluded.final_score_against, gc_games.final_score_against),
                  boxscore_url=COALESCE(excluded.boxscore_url, gc_games.boxscore_url),
                  pg_eventid=COALESCE(excluded.pg_eventid, gc_games.pg_eventid),
                  last_scraped=excluded.last_scraped""",
            [row[c] for c in cols],
        ))
    return len(rows)


def _sync_gc_batting(local, remote, gc_team_id: str = None) -> int:
    cols = _GC_BATTING_COLUMNS
    if gc_team_id:
        rows = local.execute(
            f"""SELECT {', '.join(cols)} FROM gc_batting_stats
                WHERE gc_game_id IN (SELECT gc_game_id FROM gc_games WHERE gc_team_id = ?)""",
            (gc_team_id,),
        ).fetchall()
    else:
        rows = local.execute(f"SELECT {', '.join(cols)} FROM gc_batting_stats").fetchall()
    for row in rows:
        _retry(lambda row=row: remote.execute(
            f"""INSERT INTO gc_batting_stats ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})
                ON CONFLICT(gc_game_id, gc_player_id) DO UPDATE SET
                  ab=excluded.ab, r=excluded.r, h=excluded.h, doubles=excluded.doubles,
                  triples=excluded.triples, hr=excluded.hr, rbi=excluded.rbi, bb=excluded.bb,
                  so=excluded.so, sb=excluded.sb, hbp=excluded.hbp, sf=excluded.sf, sh=excluded.sh""",
            [row[c] for c in cols],
        ))
    return len(rows)


def _sync_gc_pitching(local, remote, gc_team_id: str = None) -> int:
    """Only pushes source='scraped' rows -- manual entries are written
    directly to the live Turso database by the backend and never exist
    locally, so there's nothing to push, and nothing at risk of being
    overwritten: a manual row's key never collides with a scraped row's,
    since source is part of both tables' uniqueness constraint."""
    cols = _GC_PITCHING_COLUMNS
    if gc_team_id:
        rows = local.execute(
            f"""SELECT {', '.join(cols)} FROM gc_pitching_stats
                WHERE source = 'scraped' AND gc_game_id IN (
                    SELECT gc_game_id FROM gc_games WHERE gc_team_id = ?
                )""",
            (gc_team_id,),
        ).fetchall()
    else:
        rows = local.execute(f"SELECT {', '.join(cols)} FROM gc_pitching_stats WHERE source = 'scraped'").fetchall()
    for row in rows:
        _retry(lambda row=row: remote.execute(
            f"""INSERT INTO gc_pitching_stats ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})
                ON CONFLICT(gc_game_id, gc_player_id) DO UPDATE SET
                  team_key=excluded.team_key, player_name=excluded.player_name, game_date=excluded.game_date,
                  ip_outs=excluded.ip_outs, h=excluded.h, r=excluded.r, er=excluded.er, bb=excluded.bb,
                  so=excluded.so, hr=excluded.hr, pitches=excluded.pitches, strikes=excluded.strikes""",
            [row[c] for c in cols],
        ))
    return len(rows)


def _sync_gc_fielding(local, remote, gc_team_id: str = None) -> int:
    cols = _GC_FIELDING_COLUMNS
    if gc_team_id:
        rows = local.execute(
            f"""SELECT {', '.join(cols)} FROM gc_fielding_stats
                WHERE gc_game_id IN (SELECT gc_game_id FROM gc_games WHERE gc_team_id = ?)""",
            (gc_team_id,),
        ).fetchall()
    else:
        rows = local.execute(f"SELECT {', '.join(cols)} FROM gc_fielding_stats").fetchall()
    for row in rows:
        _retry(lambda row=row: remote.execute(
            f"""INSERT INTO gc_fielding_stats ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})
                ON CONFLICT(gc_game_id, gc_player_id) DO UPDATE SET errors=excluded.errors""",
            [row[c] for c in cols],
        ))
    return len(rows)


if __name__ == "__main__":
    sync_to_turso()
