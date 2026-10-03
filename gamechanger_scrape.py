"""
GameChanger scouting-data scraper.
======================================
Pulls player-level hitting/pitching/fielding stats (and pitch counts, where
GC exposes them) from GameChanger (gc.com) into the local sqlite database,
for the "Scouting Report" feature. See gc_teams/gc_players/gc_games/
gc_batting_stats/gc_pitching_stats/gc_fielding_stats below.

gc_teams/gc_players/gc_games are keyed by GC's own external UUID/slug ids
directly (not a local autoincrement surrogate) -- those ids are already
globally stable, and using them as the real primary key means this data
syncs to Turso the same natural-key way turso_sync.py already handles
teams/games, with no risk of a local autoincrement id mismatching Turso's
own independently-assigned one once this gets pushed there.

GameChanger's data turned out to be a clean JSON API (api.team-manager.gc.com),
not HTML to scrape -- confirmed via live recon. This script drives Playwright
to navigate the real app pages and captures the JSON the app's own JS fetches
as a side effect (page.expect_response), rather than parsing rendered DOM or
replicating the raw HTTP requests -- every API call needs a live user JWT
*and* an AWS WAF bot-challenge token that can't be hand-replicated outside a
real browser session.

Login is the one genuinely different piece from fall2026catchupscoring.py:
GameChanger's bot detection blocks a Playwright-LAUNCHED browser even during
a real, manually-typed login (confirmed via recon). The workaround -- launch
a real chrome.exe directly (not via Playwright) with a remote-debugging port,
log in by hand, then have Playwright connect_over_cdp() to it -- is what
`--login` below does. Whether the resulting storage_state() alone is enough
to restore a working session on a later run, or whether every run needs this
same real-Chrome dance, is unconfirmed -- see scrape_team()'s docstring.

Usage:
    python gamechanger_scrape.py --login
    python gamechanger_scrape.py --team-url <gc team page URL> [--pg-team-key <id>]
    python gamechanger_scrape.py --gc-team-id <id> [--only-new]

    # Manual fallback, no live browser session needed -- for when --login's
    # live fetch path isn't working (e.g. session restore unconfirmed, see
    # above) and speed matters more than automation. Same DevTools "copy
    # response" trick used during this feature's own recon: open the page on
    # GC, Network tab, find the real (non-preflight) request, copy its
    # response body to a .json file, then import it:
    python gamechanger_scrape.py --import-schedule schedule.json --gc-team-id <id>
    python gamechanger_scrape.py --import-roster roster.json --gc-team-id <id>
    python gamechanger_scrape.py --import-boxscore boxscore.json --gc-team-id <id> --gc-game-id <id>
"""
import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

import sqlite3
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

import env_local  # noqa: F401 -- loads .env.local into os.environ on import

logging.basicConfig(
    filename="gamechanger_scrape.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

SQLITE_DB = os.environ.get("DB_PATH", r"C:\Users\bsmel\OneDrive\Documents\Baseball_data\10u data.db")

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
GC_STORAGE_STATE = os.path.join(_PROJECT_ROOT, "gc_storage_state.json")
GC_LOGIN_PROFILE_DIR = os.path.join(_PROJECT_ROOT, ".gc_login_profile")
CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DEBUG_PORT = 9222


# ==============================================================================
# LOGIN (see module docstring for why this doesn't just use Playwright's own
# launch())
# ==============================================================================

def _wait_for_cdp(port, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://localhost:{port}/json/version", timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


def cmd_login():
    if not os.path.exists(CHROME_EXE):
        print(f"Chrome not found at {CHROME_EXE}. Edit CHROME_EXE at the top of this script.")
        return

    os.makedirs(GC_LOGIN_PROFILE_DIR, exist_ok=True)
    print("Launching a real Chrome window for you to log into GameChanger...")
    subprocess.Popen([
        CHROME_EXE,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={GC_LOGIN_PROFILE_DIR}",
        "https://web.gc.com/",
    ])

    if not _wait_for_cdp(DEBUG_PORT):
        print(
            f"Chrome never opened a debugging port on {DEBUG_PORT}. "
            "If Chrome was already running under this same dedicated profile, close every "
            "window for it and try again."
        )
        return

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{DEBUG_PORT}")
        context = browser.contexts[0]
        input(
            "\nLog into GameChanger in the Chrome window that just opened, and navigate to "
            "your team's page, then come back here and press Enter...\n"
        )
        context.storage_state(path=GC_STORAGE_STATE)
        print(f"Session saved to {GC_STORAGE_STATE}. You can close that Chrome window now.")


# ==============================================================================
# SCHEMA
# ==============================================================================

def _ensure_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS gc_teams (
            gc_team_id TEXT PRIMARY KEY,
            gc_url TEXT,
            gc_team_name TEXT,
            pg_team_key INTEGER,
            season TEXT,
            last_scraped TEXT
        );

        CREATE TABLE IF NOT EXISTS gc_players (
            gc_player_id TEXT PRIMARY KEY,
            gc_team_id TEXT,
            player_name TEXT,
            jersey_number TEXT,
            first_seen_date TEXT,
            last_seen_date TEXT
        );

        CREATE TABLE IF NOT EXISTS gc_games (
            gc_game_id TEXT PRIMARY KEY,
            gc_team_id TEXT,
            game_date TEXT,
            opponent_name TEXT,
            opponent_id TEXT,
            home_away TEXT,
            final_score_for INTEGER,
            final_score_against INTEGER,
            boxscore_url TEXT,
            pg_game_id INTEGER,
            pg_eventid TEXT,
            match_method TEXT,
            last_scraped TEXT
        );

        CREATE TABLE IF NOT EXISTS gc_batting_stats (
            gc_game_id TEXT,
            gc_player_id TEXT,
            ab INTEGER, r INTEGER, h INTEGER, doubles INTEGER, triples INTEGER, hr INTEGER,
            rbi INTEGER, bb INTEGER, so INTEGER, sb INTEGER, hbp INTEGER, sf INTEGER, sh INTEGER,
            PRIMARY KEY (gc_game_id, gc_player_id)
        );

        CREATE TABLE IF NOT EXISTS gc_pitching_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_key INTEGER,
            player_name TEXT NOT NULL,
            game_date TEXT NOT NULL,
            gc_game_id TEXT,
            gc_player_id TEXT,
            ip_outs INTEGER, h INTEGER, r INTEGER, er INTEGER, bb INTEGER, so INTEGER, hr INTEGER,
            pitches INTEGER,
            source TEXT NOT NULL CHECK(source IN ('scraped','manual')),
            entered_by TEXT, entered_at TEXT, notes TEXT,
            UNIQUE(gc_game_id, gc_player_id),
            UNIQUE(team_key, player_name, game_date, source)
        );

        CREATE TABLE IF NOT EXISTS gc_fielding_stats (
            gc_game_id TEXT,
            gc_player_id TEXT,
            errors INTEGER,
            PRIMARY KEY (gc_game_id, gc_player_id)
        );
    """)
    conn.commit()


# ==============================================================================
# URL / TEXT HELPERS
# ==============================================================================

def _normalize_team_url(url):
    """Accepts anything from a bare team home URL to a full box-score URL
    someone pasted, and returns just the team's base URL
    (https://web.gc.com/teams/{slug}/{name-slug})."""
    m = re.match(r"(https://web\.gc\.com/teams/[^/]+/[^/]+)", url.strip())
    if not m:
        logging.warning(f"Couldn't normalize team URL, using as-is: {url}")
        return url.rstrip("/")
    return m.group(1)


def _extract_gc_slug(gc_url):
    m = re.search(r"/teams/([^/]+)/", gc_url)
    return m.group(1) if m else None


def _extract_pg_eventid_from_notes(notes):
    if not notes:
        return None
    m = re.search(r"[Ee]vent=(\d+)", notes)
    return m.group(1) if m else None


def _player_display_name(first_name, last_name):
    parts = [p for p in (first_name, last_name) if p]
    return " ".join(parts) if parts else "Unknown"


def _parse_ip(raw_ip):
    """GC displays partial innings as x.1 / x.2 / x.0 (outs recorded, not a
    decimal fraction) -- confirmed by the user against real box scores.
    Converts to total outs. Logs (and clamps) if the fractional digit isn't
    0/1/2, since that would mean this assumption doesn't hold for some game."""
    if raw_ip is None:
        return None
    tenths = round(float(raw_ip) * 10)
    full_innings, frac = divmod(tenths, 10)
    if frac not in (0, 1, 2):
        logging.warning(f"Unexpected IP value from GC: {raw_ip} (fraction digit {frac}) -- clamping")
        frac = min(frac, 2)
    return full_innings * 3 + frac


_BATTING_EXTRA_MAP = {"2B": "doubles", "3B": "triples", "HR": "hr", "SB": "sb", "HBP": "hbp", "SF": "sf", "SH": "sh"}


# ==============================================================================
# AUTHENTICATED BROWSING
# ==============================================================================

def _authenticated_context(p):
    if not os.path.exists(GC_STORAGE_STATE):
        print(f"No saved session found at {GC_STORAGE_STATE}. Run: python gamechanger_scrape.py --login")
        sys.exit(1)
    # headed (not headless) for now -- whether headless mode trips GameChanger's
    # bot detection for plain page loads (as opposed to the login flow, which
    # it's confirmed to block) hasn't been tested yet.
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(storage_state=GC_STORAGE_STATE, viewport={"width": 1400, "height": 1000})
    return browser, context


def _fetch_json(page, goto_url, url_contains, timeout=30000):
    with page.expect_response(lambda r: url_contains in r.url and r.status == 200, timeout=timeout) as resp_info:
        page.goto(goto_url, wait_until="domcontentloaded")
    return resp_info.value.json()


def _fetch_roster(page, gc_team_url):
    try:
        return _fetch_json(page, gc_team_url, "/players")
    except PlaywrightTimeoutError:
        logging.info("Roster didn't load from team home page, trying /roster")
        return _fetch_json(page, gc_team_url.rstrip("/") + "/roster", "/players")


def _fetch_schedule(page, gc_team_url):
    return _fetch_json(page, gc_team_url.rstrip("/") + "/schedule", "/schedule")


def _fetch_box_score(page, gc_team_url, gc_game_id):
    box_score_url = f"{gc_team_url.rstrip('/')}/schedule/{gc_game_id}/box-score"
    return _fetch_json(page, box_score_url, "/boxscore"), box_score_url


# ==============================================================================
# DB UPSERTS -- all keyed on GC's own external ids, see module docstring.
# ==============================================================================

def _now():
    return datetime.now(timezone.utc).isoformat()


def _upsert_gc_team(conn, gc_team_id, gc_team_name, gc_url=None, pg_team_key=None):
    conn.execute(
        """INSERT INTO gc_teams (gc_team_id, gc_team_name, gc_url, pg_team_key, last_scraped)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(gc_team_id) DO UPDATE SET
             gc_team_name = excluded.gc_team_name,
             last_scraped = excluded.last_scraped,
             gc_url = COALESCE(excluded.gc_url, gc_teams.gc_url),
             pg_team_key = COALESCE(excluded.pg_team_key, gc_teams.pg_team_key)""",
        (gc_team_id, gc_team_name, gc_url, pg_team_key, _now()),
    )


def _upsert_gc_player(conn, gc_player_id, player_name, gc_team_id=None, jersey_number=None):
    today = datetime.now(timezone.utc).date().isoformat()
    conn.execute(
        """INSERT INTO gc_players (gc_player_id, gc_team_id, player_name, jersey_number, first_seen_date, last_seen_date)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(gc_player_id) DO UPDATE SET
             player_name = excluded.player_name,
             last_seen_date = excluded.last_seen_date,
             gc_team_id = COALESCE(excluded.gc_team_id, gc_players.gc_team_id),
             jersey_number = COALESCE(excluded.jersey_number, gc_players.jersey_number)""",
        (gc_player_id, gc_team_id, player_name, jersey_number, today, today),
    )


def _upsert_gc_game(conn, gc_team_id, gc_game_id, game_date, opponent_name, opponent_id, home_away,
                     final_score_for=None, final_score_against=None, boxscore_url=None, pg_eventid=None):
    conn.execute(
        """INSERT INTO gc_games
           (gc_game_id, gc_team_id, game_date, opponent_name, opponent_id, home_away,
            final_score_for, final_score_against, boxscore_url, pg_eventid, last_scraped)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(gc_game_id) DO UPDATE SET
             game_date = excluded.game_date, opponent_name = excluded.opponent_name,
             opponent_id = excluded.opponent_id, home_away = excluded.home_away,
             final_score_for = COALESCE(excluded.final_score_for, gc_games.final_score_for),
             final_score_against = COALESCE(excluded.final_score_against, gc_games.final_score_against),
             boxscore_url = COALESCE(excluded.boxscore_url, gc_games.boxscore_url),
             pg_eventid = COALESCE(excluded.pg_eventid, gc_games.pg_eventid),
             last_scraped = excluded.last_scraped""",
        (gc_game_id, gc_team_id, game_date, opponent_name, opponent_id, home_away,
         final_score_for, final_score_against, boxscore_url, pg_eventid, _now()),
    )


def _upsert_batting(conn, gc_game_id, gc_player_id, stats):
    conn.execute(
        """INSERT INTO gc_batting_stats
           (gc_game_id, gc_player_id, ab, r, h, doubles, triples, hr, rbi, bb, so, sb, hbp, sf, sh)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(gc_game_id, gc_player_id) DO UPDATE SET
             ab=excluded.ab, r=excluded.r, h=excluded.h, doubles=excluded.doubles,
             triples=excluded.triples, hr=excluded.hr, rbi=excluded.rbi, bb=excluded.bb,
             so=excluded.so, sb=excluded.sb, hbp=excluded.hbp, sf=excluded.sf, sh=excluded.sh""",
        (gc_game_id, gc_player_id,
         stats.get("ab", 0), stats.get("r", 0), stats.get("h", 0), stats.get("doubles", 0),
         stats.get("triples", 0), stats.get("hr", 0), stats.get("rbi", 0), stats.get("bb", 0),
         stats.get("so", 0), stats.get("sb", 0), stats.get("hbp", 0), stats.get("sf", 0), stats.get("sh", 0)),
    )


def _upsert_fielding(conn, gc_game_id, gc_player_id, errors):
    conn.execute(
        """INSERT INTO gc_fielding_stats (gc_game_id, gc_player_id, errors)
           VALUES (?, ?, ?)
           ON CONFLICT(gc_game_id, gc_player_id) DO UPDATE SET errors=excluded.errors""",
        (gc_game_id, gc_player_id, errors),
    )


def _upsert_pitching_scraped(conn, team_key, player_name, game_date, gc_game_id, gc_player_id,
                              ip_outs, h, r, er, bb, so, hr, pitches):
    conn.execute(
        """INSERT INTO gc_pitching_stats
           (team_key, player_name, game_date, gc_game_id, gc_player_id, ip_outs, h, r, er, bb, so, hr, pitches, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'scraped')
           ON CONFLICT(gc_game_id, gc_player_id) DO UPDATE SET
             team_key=excluded.team_key, player_name=excluded.player_name, game_date=excluded.game_date,
             ip_outs=excluded.ip_outs, h=excluded.h, r=excluded.r, er=excluded.er, bb=excluded.bb,
             so=excluded.so, hr=excluded.hr, pitches=excluded.pitches""",
        (team_key, player_name, game_date, gc_game_id, gc_player_id,
         ip_outs, h, r, er, bb, so, hr, pitches),
    )


# ==============================================================================
# BOX SCORE PARSING
# ==============================================================================

def _pick_own_team_block(box_score, own_hint):
    """The box-score response is keyed by team identifier, but confusingly not
    consistently: the team whose page you're viewing is keyed by its URL slug
    (e.g. "Hs8vhcpzKVtF"), while the opponent is keyed by its internal team
    UUID -- confirmed via recon, these are NOT the same identifier space as
    the team_id used everywhere else in the API. own_hint can be that raw
    slug/id directly (manual-import path) or a full gc_url to extract it
    from (live-scrape path); whichever other key remains is the opponent."""
    keys = list(box_score.keys())
    if len(keys) != 2:
        logging.warning(f"Expected exactly 2 teams in box score, got {len(keys)}: {keys}")
    own_key = own_hint if own_hint in box_score else _extract_gc_slug(own_hint)
    if own_key not in box_score:
        own_key = keys[0]
    other_keys = [k for k in keys if k != own_key]
    opponent_key = other_keys[0] if other_keys else None
    return own_key, opponent_key


def _parse_team_block(conn, team_block, gc_game_id, team_key, gc_team_id=None):
    """Upserts gc_players + batting/pitching/fielding rows for one team's
    block of a box-score response. team_key is the local PG teams.id this
    team's pitching rows should be attributed to (nullable -- unmapped teams
    just get None, still fully scoutable via gc_team_id later)."""
    players_by_id = {p["id"]: p for p in team_block.get("players", [])}

    for group in team_block.get("groups", []):
        category = group.get("category")

        if category == "lineup":
            extra_by_player = {}
            errors_by_player = {}
            for extra in group.get("extra", []):
                stat_name = extra.get("stat_name")
                if stat_name == "E":
                    for s in extra.get("stats", []):
                        errors_by_player[s["player_id"]] = s["value"]
                    continue
                col = _BATTING_EXTRA_MAP.get(stat_name)
                if not col:
                    continue
                for s in extra.get("stats", []):
                    extra_by_player.setdefault(s["player_id"], {})[col] = s["value"]

            for entry in group.get("stats", []):
                gc_player_id = entry["player_id"]
                p = players_by_id.get(gc_player_id, {})
                _upsert_gc_player(
                    conn, gc_player_id, _player_display_name(p.get("first_name"), p.get("last_name")),
                    gc_team_id=gc_team_id, jersey_number=p.get("number"),
                )
                stats = dict(entry.get("stats", {}))
                stats = {k.lower(): v for k, v in stats.items()}
                stats.update(extra_by_player.get(gc_player_id, {}))
                _upsert_batting(conn, gc_game_id, gc_player_id, stats)

                if gc_player_id in errors_by_player:
                    _upsert_fielding(conn, gc_game_id, gc_player_id, errors_by_player[gc_player_id])

        elif category == "pitching":
            pitches_by_player = {}
            for extra in group.get("extra", []):
                if extra.get("stat_name") != "#P":
                    continue
                for s in extra.get("stats", []):
                    pitches_by_player[s["player_id"]] = s["value"]

            game_row = conn.execute("SELECT game_date FROM gc_games WHERE gc_game_id = ?", (gc_game_id,)).fetchone()
            game_date = game_row[0] if game_row else None

            for entry in group.get("stats", []):
                gc_player_id = entry["player_id"]
                p = players_by_id.get(gc_player_id, {})
                player_name = _player_display_name(p.get("first_name"), p.get("last_name"))
                _upsert_gc_player(
                    conn, gc_player_id, player_name, gc_team_id=gc_team_id, jersey_number=p.get("number"),
                )
                stats = entry.get("stats", {})
                _upsert_pitching_scraped(
                    conn, team_key, player_name, game_date, gc_game_id, gc_player_id,
                    ip_outs=_parse_ip(stats.get("IP")),
                    h=stats.get("H", 0), r=stats.get("R", 0), er=stats.get("ER", 0),
                    bb=stats.get("BB", 0), so=stats.get("SO", 0), hr=stats.get("HR", 0),
                    pitches=pitches_by_player.get(gc_player_id),
                )

    # No richer fielding breakdown (no putouts/assists/position) -- GC's box
    # score doesn't expose one, matches the schema design.


def _team_final_score(team_block):
    for group in team_block.get("groups", []):
        if group.get("category") == "lineup":
            return group.get("team_stats", {}).get("R")
    return None


def _process_box_score(conn, gc_team_id, own_pg_team_key, own_hint, gc_game_id, box_score, boxscore_url=None):
    """Shared by the live scrape and the manual --import-boxscore path: given
    an already-fetched box-score JSON blob, resolves own-vs-opponent and
    upserts everything. Ensures a bare gc_games row exists first so this
    also works standalone (e.g. testing one game without ever importing a
    schedule)."""
    existing = conn.execute("SELECT gc_game_id FROM gc_games WHERE gc_game_id = ?", (gc_game_id,)).fetchone()
    if not existing:
        # Placeholder date -- gc_pitching_stats.game_date is NOT NULL (it's
        # load-bearing for Phase B's by-date pitch-count rule), and nothing
        # real is known yet without a schedule import. A later --import-
        # schedule overwrites this with the real date (unconditional SET in
        # _upsert_gc_game's ON CONFLICT, not COALESCE).
        placeholder_date = datetime.now(timezone.utc).date().isoformat()
        _upsert_gc_game(conn, gc_team_id, gc_game_id, placeholder_date, None, None, None)

    own_key, opponent_key = _pick_own_team_block(box_score, own_hint)
    own_block = box_score.get(own_key, {})
    opponent_block = box_score.get(opponent_key, {}) if opponent_key else {}

    final_for = _team_final_score(own_block)
    final_against = _team_final_score(opponent_block)
    conn.execute(
        "UPDATE gc_games SET final_score_for = COALESCE(?, final_score_for), "
        "final_score_against = COALESCE(?, final_score_against), "
        "boxscore_url = COALESCE(?, boxscore_url) WHERE gc_game_id = ?",
        (final_for, final_against, boxscore_url, gc_game_id),
    )

    _parse_team_block(conn, own_block, gc_game_id, own_pg_team_key, gc_team_id=gc_team_id)

    if opponent_block:
        # Resolve the opponent's own gc_teams row (auto-created from the
        # schedule's pregame_data, if one was imported) so their players/
        # pitching attribute to it. Falls back to the box score's own
        # opponent key if no schedule-derived opponent_id is on file yet.
        game_row = conn.execute("SELECT opponent_id FROM gc_games WHERE gc_game_id = ?", (gc_game_id,)).fetchone()
        opponent_gc_team_id = (game_row[0] if game_row and game_row[0] else opponent_key)
        opponent_pg_team_key = None
        if opponent_gc_team_id:
            t = conn.execute("SELECT pg_team_key FROM gc_teams WHERE gc_team_id = ?", (opponent_gc_team_id,)).fetchone()
            if t:
                opponent_pg_team_key = t[0]
            else:
                # No name known for this opponent yet (no schedule imported)
                # -- placeholder so the row exists; a later schedule import
                # overwrites gc_team_name with the real one on conflict.
                _upsert_gc_team(conn, opponent_gc_team_id, opponent_gc_team_id)
            # Backfill gc_games.opponent_id too -- without a schedule import
            # it's still NULL at this point, which would otherwise silently
            # drop this opponent from any gc_team_id-scoped sync/query that
            # looks it up via gc_games.opponent_id (e.g. turso_sync's
            # _sync_gc_teams/_sync_gc_players).
            conn.execute(
                "UPDATE gc_games SET opponent_id = COALESCE(opponent_id, ?) WHERE gc_game_id = ?",
                (opponent_gc_team_id, gc_game_id),
            )
        _parse_team_block(conn, opponent_block, gc_game_id, opponent_pg_team_key, gc_team_id=opponent_gc_team_id)

    conn.commit()


def scrape_box_score(conn, gc_team_id, own_pg_team_key, gc_url, gc_game_id):
    """Fetches and stores one game's box score, for BOTH teams it contains --
    one boxscore call covers the opponent's stats too, no separate access to
    their own GC page needed."""
    with sync_playwright() as p:
        browser, context = _authenticated_context(p)
        page = context.new_page()
        try:
            box_score, boxscore_url = _fetch_box_score(page, gc_url, gc_game_id)
        finally:
            browser.close()

    _process_box_score(conn, gc_team_id, own_pg_team_key, gc_url, gc_game_id, box_score, boxscore_url=boxscore_url)


# ==============================================================================
# ORCHESTRATION
# ==============================================================================

def scrape_team(gc_team_url=None, gc_team_id=None, pg_team_key=None, only_new=True):
    """
    Full scrape for one team: roster, schedule, and box scores for every
    game found (or just new ones, if only_new). First scrape of a team needs
    gc_team_url; later re-scrapes can pass gc_team_id instead (stored URL is
    reused). Requires a saved session from --login.
    """
    conn = sqlite3.connect(SQLITE_DB)
    _ensure_schema(conn)

    if gc_team_url:
        gc_team_url = _normalize_team_url(gc_team_url)
    else:
        row = conn.execute("SELECT gc_url FROM gc_teams WHERE gc_team_id = ?", (gc_team_id,)).fetchone()
        if not row or not row[0]:
            print(f"No stored URL for gc_team_id={gc_team_id}. Provide --team-url the first time you scrape a team.")
            return
        gc_team_url = row[0]

    with sync_playwright() as p:
        browser, context = _authenticated_context(p)
        page = context.new_page()
        try:
            roster = _fetch_roster(page, gc_team_url)
            schedule = _fetch_schedule(page, gc_team_url)
        except PlaywrightTimeoutError:
            print(
                "Didn't see the expected GameChanger data load in time -- your session may "
                "have expired. Run: python gamechanger_scrape.py --login"
            )
            browser.close()
            return
        finally:
            browser.close()

    slug = _extract_gc_slug(gc_team_url) or gc_team_id
    real_gc_team_id = gc_team_id or slug
    _upsert_gc_team(conn, real_gc_team_id, slug, gc_url=gc_team_url, pg_team_key=pg_team_key)

    for player in roster:
        _upsert_gc_player(
            conn, player["id"], _player_display_name(player.get("first_name"), player.get("last_name")),
            gc_team_id=real_gc_team_id, jersey_number=player.get("number"),
        )
    conn.commit()

    current_pg_eventid = None
    games = []
    for entry in schedule:
        event = entry.get("event", {})
        etype = event.get("event_type")

        if etype == "other":
            pg_eid = _extract_pg_eventid_from_notes(event.get("notes"))
            if pg_eid:
                current_pg_eventid = pg_eid
            continue

        if etype != "game":
            continue

        pregame = entry.get("pregame_data") or {}
        gc_game_id = event["id"]
        start = event.get("start", {})
        game_date = start.get("datetime") or start.get("date")
        opponent_name = pregame.get("opponent_name")
        opponent_id = pregame.get("opponent_id")
        home_away = pregame.get("home_away")

        if opponent_id and opponent_name:
            _upsert_gc_team(conn, opponent_id, opponent_name)

        _upsert_gc_game(
            conn, real_gc_team_id, gc_game_id, game_date, opponent_name, opponent_id, home_away,
            pg_eventid=current_pg_eventid,
        )
        games.append(gc_game_id)
    conn.commit()

    if only_new:
        already_scraped = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT gc_game_id FROM gc_pitching_stats WHERE source = 'scraped'"
            ).fetchall()
        }
        games = [gid for gid in games if gid not in already_scraped]

    print(f"Scraping box scores for {len(games)} game(s)...")
    for gc_game_id in games:
        try:
            scrape_box_score(conn, real_gc_team_id, pg_team_key, gc_team_url, gc_game_id)
            print(f"  scraped game {gc_game_id}")
        except PlaywrightTimeoutError:
            logging.warning(f"Box score didn't load for game {gc_game_id} -- probably not played yet, skipping")
        except Exception as e:
            logging.error(f"Failed to scrape box score for game {gc_game_id}: {e}")

    conn.close()
    _sync_after_import(real_gc_team_id)
    print("Done.")


# ==============================================================================
# MANUAL IMPORT (no live browser session needed) -- see module docstring
# ==============================================================================

def _sync_after_import(gc_team_id):
    """A sync hiccup shouldn't take down an otherwise-successful local
    import/scrape with a crash -- the local data is already saved either way."""
    import turso_sync
    if not turso_sync.enabled():
        print("TURSO_DATABASE_URL/TURSO_AUTH_TOKEN not set -- skipping Turso sync (local-only run).")
        return
    try:
        turso_sync.sync_to_turso(gc_team_id=gc_team_id)
    except Exception as e:
        print(f"Local data saved, but the Turso sync failed: {e}")
        logging.error(f"Turso sync failed after import for gc_team_id={gc_team_id}: {e}")


def cmd_import_schedule(gc_team_id, path, gc_team_name=None, pg_team_key=None):
    with open(path, encoding="utf-8") as f:
        schedule = json.load(f)

    conn = sqlite3.connect(SQLITE_DB)
    _ensure_schema(conn)
    # Own team's gc_teams row -- none of the three import commands create
    # this as a side effect of anything else (unlike scrape_team(), which
    # does this explicitly before touching roster/schedule/boxscore), so
    # without it there's nothing for the Scouting page to map to a PG team.
    _upsert_gc_team(conn, gc_team_id, gc_team_name or gc_team_id, pg_team_key=pg_team_key)

    current_pg_eventid = None
    count = 0
    for entry in schedule:
        event = entry.get("event", {})
        etype = event.get("event_type")

        if etype == "other":
            pg_eid = _extract_pg_eventid_from_notes(event.get("notes"))
            if pg_eid:
                current_pg_eventid = pg_eid
            continue
        if etype != "game":
            continue

        pregame = entry.get("pregame_data") or {}
        gc_game_id = event["id"]
        start = event.get("start", {})
        game_date = start.get("datetime") or start.get("date")
        opponent_name = pregame.get("opponent_name")
        opponent_id = pregame.get("opponent_id")
        home_away = pregame.get("home_away")

        if opponent_id and opponent_name:
            _upsert_gc_team(conn, opponent_id, opponent_name)

        _upsert_gc_game(conn, gc_team_id, gc_game_id, game_date, opponent_name, opponent_id, home_away,
                         pg_eventid=current_pg_eventid)
        count += 1

    conn.commit()
    conn.close()
    print(f"Imported {count} game(s) from {path}.")
    _sync_after_import(gc_team_id)


def cmd_import_roster(gc_team_id, path, gc_team_name=None, pg_team_key=None):
    with open(path, encoding="utf-8") as f:
        roster = json.load(f)

    conn = sqlite3.connect(SQLITE_DB)
    _ensure_schema(conn)
    _upsert_gc_team(conn, gc_team_id, gc_team_name or gc_team_id, pg_team_key=pg_team_key)
    for player in roster:
        _upsert_gc_player(conn, player["id"], _player_display_name(player.get("first_name"), player.get("last_name")),
                           gc_team_id=gc_team_id, jersey_number=player.get("number"))
    conn.commit()
    conn.close()
    print(f"Imported {len(roster)} player(s) from {path}.")
    _sync_after_import(gc_team_id)


def cmd_import_boxscore(gc_team_id, gc_game_id, path, gc_team_name=None, pg_team_key=None):
    with open(path, encoding="utf-8") as f:
        box_score = json.load(f)

    conn = sqlite3.connect(SQLITE_DB)
    _ensure_schema(conn)
    _upsert_gc_team(conn, gc_team_id, gc_team_name or gc_team_id, pg_team_key=pg_team_key)
    _process_box_score(conn, gc_team_id, pg_team_key, gc_team_id, gc_game_id, box_score)
    conn.close()
    print(f"Imported box score for game {gc_game_id} from {path}.")
    _sync_after_import(gc_team_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GameChanger scouting-data scraper")
    parser.add_argument("--login", action="store_true",
                         help="One-time interactive login: opens a real Chrome window for you to "
                              "log into GameChanger by hand, then saves the session for future runs.")
    parser.add_argument("--team-url", type=str, default=None,
                         help="Full GameChanger team page URL (needed the first time you scrape a team).")
    parser.add_argument("--gc-team-id", type=str, default=None,
                         help="Re-scrape a team already stored (its GC team id from a prior --team-url run).")
    parser.add_argument("--gc-game-id", type=str, default=None,
                         help="The GameChanger game id for --import-boxscore (from the box-score page URL).")
    parser.add_argument("--pg-team-key", type=int, default=None,
                         help="Link this team to an existing teams.id row in the local PG database.")
    parser.add_argument("--gc-team-name", type=str, default=None,
                         help="Display name for --gc-team-id on an --import-* command (defaults to the raw id "
                              "if not given -- fine to leave unset and fix later via the admin page).")
    parser.add_argument("--only-new", action="store_true",
                         help="Skip games that already have scraped stats locally (default: rescrape everything found).")
    parser.add_argument("--import-schedule", type=str, default=None, metavar="FILE",
                         help="Import a schedule JSON file saved manually from DevTools (Network tab -> "
                              "the /schedule request -> Copy response), instead of a live scrape. "
                              "Requires --gc-team-id.")
    parser.add_argument("--import-roster", type=str, default=None, metavar="FILE",
                         help="Import a roster JSON file (the /players request) the same way. Requires --gc-team-id.")
    parser.add_argument("--import-boxscore", type=str, default=None, metavar="FILE",
                         help="Import a box-score JSON file (the /boxscore request) the same way. "
                              "Requires --gc-team-id and --gc-game-id.")
    args = parser.parse_args()

    if args.login:
        cmd_login()
    elif args.import_schedule:
        if not args.gc_team_id:
            parser.error("--import-schedule requires --gc-team-id (the slug/id from the team's GC page URL)")
        cmd_import_schedule(args.gc_team_id, args.import_schedule,
                             gc_team_name=args.gc_team_name, pg_team_key=args.pg_team_key)
    elif args.import_roster:
        if not args.gc_team_id:
            parser.error("--import-roster requires --gc-team-id")
        cmd_import_roster(args.gc_team_id, args.import_roster,
                           gc_team_name=args.gc_team_name, pg_team_key=args.pg_team_key)
    elif args.import_boxscore:
        if not args.gc_team_id:
            parser.error("--import-boxscore requires --gc-team-id")
        if not args.gc_game_id:
            parser.error("--import-boxscore requires --gc-game-id (the game id from the box-score page URL)")
        cmd_import_boxscore(args.gc_team_id, args.gc_game_id, args.import_boxscore,
                             gc_team_name=args.gc_team_name, pg_team_key=args.pg_team_key)
    elif args.team_url or args.gc_team_id:
        scrape_team(gc_team_url=args.team_url, gc_team_id=args.gc_team_id,
                    pg_team_key=args.pg_team_key, only_new=args.only_new)
    else:
        parser.print_help()
