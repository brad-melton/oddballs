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


def enabled() -> bool:
    return bool(TURSO_DATABASE_URL and TURSO_AUTH_TOKEN)


def sync_to_turso(verbose: bool = True) -> dict:
    """
    Pushes events / teams / games from the local sqlite file into Turso.
    No-ops (returns {"synced": False}) if Turso isn't configured.
    """
    if not enabled():
        if verbose:
            print("TURSO_DATABASE_URL/TURSO_AUTH_TOKEN not set -- skipping Turso sync (local-only run).")
        return {"synced": False}

    import turso_serverless

    local = sqlite3.connect(LOCAL_DB_PATH)
    local.row_factory = sqlite3.Row
    remote = turso_serverless.connect(TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN)

    try:
        events_synced = _sync_events(local, remote)
        teams_synced = _sync_teams(local, remote)
        games_inserted, games_updated = _sync_games(local, remote)
        remote.commit()
    finally:
        local.close()
        remote.close()

    summary = {
        "synced": True,
        "events_synced": events_synced,
        "teams_synced": teams_synced,
        "games_inserted": games_inserted,
        "games_updated": games_updated,
    }
    if verbose:
        print(f"Turso sync complete: {summary}")
    return summary


def _sync_events(local, remote) -> int:
    """
    Upserts every event -- the live app never writes to the events table,
    so a plain overwrite is safe. events.eventid has no unique constraint
    (confirmed via the schema), so this uses delete-then-insert rather
    than ON CONFLICT.
    """
    rows = local.execute(f"SELECT {', '.join(_EVENT_COLUMNS)} FROM events").fetchall()
    cols = _EVENT_COLUMNS
    for row in rows:
        eventid = row["eventid"]
        _retry(lambda: remote.execute("DELETE FROM events WHERE eventid = ?", [eventid]))
        _retry(lambda: remote.execute(
            f"INSERT INTO events ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
            [row[c] for c in cols],
        ))
    return len(rows)


def _sync_teams(local, remote) -> int:
    """Inserts any team not already present in Turso, matched by
    team_name (which does have a real UNIQUE constraint)."""
    rows = local.execute(f"SELECT {', '.join(_TEAM_COLUMNS)} FROM teams").fetchall()
    cols = _TEAM_COLUMNS
    for row in rows:
        _retry(lambda: remote.execute(
            f"""
            INSERT INTO teams ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})
            ON CONFLICT(team_name) DO NOTHING
            """,
            [row[c] for c in cols],
        ))
    return len(rows)


def _sync_games(local, remote) -> tuple[int, int]:
    """
    Scoped to eventid IN (SELECT eventid FROM events) -- this pipeline's
    own events, not the unrelated legacy dataset also present in the local
    games table (confirmed: the events table itself only ever holds our
    124 events, so this filter is exact, not approximate).

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
    rows = local.execute(f"""
        SELECT {', '.join(_GAME_COLUMNS)} FROM games
        WHERE eventid IN (SELECT eventid FROM events)
    """).fetchall()

    existing_rows = _retry(lambda: remote.execute(f"""
        SELECT eventid, game_num, {', '.join(_PROTECTED_GAME_COLUMNS)}
        FROM games
        WHERE eventid IN (SELECT eventid FROM events) AND game_num IS NOT NULL
    """).fetchall())
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


if __name__ == "__main__":
    sync_to_turso()
