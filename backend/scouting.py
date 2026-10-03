"""
Scouting-report business logic -- reads the gc_* tables that
gamechanger_scrape.py populates (see that module's docstring for the
schema). Kept separate from simulation.py (prediction/standings) as its own
concern, imported into app.py the same way.

Phase A only: team search, team-level rolled-up stats, and a player's full
game log. Pitch-count tracking (the pitches_remaining() rule engine, manual
entry, and the bracket-day UI) is Phase B, built after this is verified
against real scraped data -- see the project plan.
"""
from db import get_connection as _get_connection

# Same six tables gamechanger_scrape.py / turso_sync.py create -- repeated
# here (not imported from a shared module) deliberately, matching this
# codebase's existing convention of self-contained CREATE TABLE IF NOT
# EXISTS blocks per script rather than a shared schema module. Needed so a
# fresh Turso database doesn't 404 on a scouting-report lookup before any
# scrape has ever run.
_SCHEMA_STATEMENTS = [
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
        pitches INTEGER, strikes INTEGER, bf INTEGER, hbp INTEGER,
        source TEXT NOT NULL CHECK(source IN ('scraped','manual')),
        entered_by TEXT, entered_at TEXT, notes TEXT,
        UNIQUE(gc_game_id, gc_player_id), UNIQUE(team_key, player_name, game_date, source)
    )""",
    """CREATE TABLE IF NOT EXISTS gc_fielding_stats (
        gc_game_id TEXT, gc_player_id TEXT, errors INTEGER,
        PRIMARY KEY (gc_game_id, gc_player_id)
    )""",
]


def ensure_scouting_schema():
    """Called once from app.py's FastAPI startup hook."""
    conn = _get_connection()
    try:
        for stmt in _SCHEMA_STATEMENTS:
            conn.execute(stmt)
        # CREATE TABLE IF NOT EXISTS doesn't add a column to a table that
        # already exists from before it was added -- same ALTER TABLE
        # fallback as gamechanger_scrape.py/turso_sync.py use.
        for col in ("strikes", "bf", "hbp"):
            try:
                conn.execute(f"ALTER TABLE gc_pitching_stats ADD COLUMN {col} INTEGER")
            except Exception:
                pass  # column already exists
        conn.commit()
    finally:
        conn.close()


def _format_ip(outs: int) -> str:
    innings, rem = divmod(outs or 0, 3)
    return f"{innings}.{rem}"


def _gc_team_ids_for_pg_team(conn, team_key: int) -> list:
    rows = conn.execute("SELECT gc_team_id FROM gc_teams WHERE pg_team_key = ?", (team_key,)).fetchall()
    return [r["gc_team_id"] for r in rows]


def search_scouting_teams(query: str) -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT id, team_name FROM teams WHERE team_name LIKE ? ORDER BY team_name LIMIT 25",
            (f"%{query}%",),
        ).fetchall()
        results = []
        for r in rows:
            team_key, team_name = r["id"], r["team_name"]
            gc_row = conn.execute(
                "SELECT last_scraped FROM gc_teams WHERE pg_team_key = ? ORDER BY last_scraped DESC LIMIT 1",
                (team_key,),
            ).fetchone()
            results.append({
                "team_key": team_key,
                "team_name": team_name,
                "gc_linked": gc_row is not None,
                "last_scraped": gc_row["last_scraped"] if gc_row else None,
            })
        return results
    finally:
        conn.close()


def _top_n_value(values, n=3):
    """Given a list of numbers, returns the value at rank n (ties included),
    or None if there are no positive values at all. Used so "high X" badges
    flag roughly the top 3 on a roster rather than an arbitrary fixed
    threshold that wouldn't translate across age groups/skill levels."""
    positive = sorted((v for v in values if v and v > 0), reverse=True)
    if not positive:
        return None
    return positive[min(n, len(positive)) - 1]


# Minimum at-bats to be eligible for any batting badge -- keeps a 1-for-1
# fluke from flagging as "high average" on a small youth-ball sample.
_BADGE_MIN_AB = 3

BADGE_HIGH_AVG = "\U0001F3CF"     # high batting average
BADGE_POWER = "\U0001F4AA"        # extra-base-hit power
BADGE_HIGH_SO = "\U0001F300"      # strikeout-prone at the plate
BADGE_HIGH_BB = "\U0001F441️"  # plate discipline / walks


def _compute_badges(roster_raw: list[dict]) -> dict:
    """roster_raw: dicts with player_name, ab, avg, xbh, k, bb (bb already
    includes HBP -- see get_team_scouting_report). Returns
    {player_name: [badge emoji, ...]}, comparing each player against the
    rest of THIS roster (a scouting tool is inherently relative -- "who
    stands out on this team" matters more than a fixed league-wide cutoff)."""
    qualified = [r for r in roster_raw if (r["ab"] or 0) >= _BADGE_MIN_AB]
    avg_cut = _top_n_value([r["avg"] for r in qualified if r["avg"] is not None])
    xbh_cut = _top_n_value([r["xbh"] for r in qualified])
    so_cut = _top_n_value([r["k"] for r in qualified])
    bb_cut = _top_n_value([r["bb"] for r in qualified])

    badges = {}
    for r in qualified:
        earned = []
        if avg_cut is not None and (r["avg"] or 0) >= avg_cut:
            earned.append(BADGE_HIGH_AVG)
        if xbh_cut is not None and (r["xbh"] or 0) >= xbh_cut:
            earned.append(BADGE_POWER)
        if so_cut is not None and (r["k"] or 0) >= so_cut:
            earned.append(BADGE_HIGH_SO)
        if bb_cut is not None and (r["bb"] or 0) >= bb_cut:
            earned.append(BADGE_HIGH_BB)
        if earned:
            badges[r["player_name"]] = earned
    return badges


def get_team_scouting_report(team_key: int) -> dict:
    conn = _get_connection()
    try:
        team_row = conn.execute("SELECT team_name FROM teams WHERE id = ?", (team_key,)).fetchone()
        if team_row is None:
            raise ValueError(f"Team {team_key} not found")

        gc_team_ids = _gc_team_ids_for_pg_team(conn, team_key)
        if not gc_team_ids:
            return {"team_key": team_key, "team_name": team_row["team_name"], "last_scraped": None, "roster": []}

        placeholders = ", ".join("?" for _ in gc_team_ids)
        players = conn.execute(
            f"SELECT gc_player_id, player_name FROM gc_players WHERE gc_team_id IN ({placeholders})",
            tuple(gc_team_ids),
        ).fetchall()
        last_scraped_row = conn.execute(
            f"SELECT MAX(last_scraped) AS ls FROM gc_teams WHERE gc_team_id IN ({placeholders})",
            tuple(gc_team_ids),
        ).fetchone()

        roster_raw = []
        for p in players:
            gc_player_id, player_name = p["gc_player_id"], p["player_name"]

            bat = conn.execute(
                """SELECT COUNT(DISTINCT gc_game_id) AS games, SUM(ab) AS ab, SUM(h) AS h,
                          SUM(doubles) AS doubles, SUM(triples) AS triples, SUM(hr) AS hr,
                          SUM(bb) AS bb, SUM(hbp) AS hbp, SUM(so) AS so
                   FROM gc_batting_stats WHERE gc_player_id = ?""",
                (gc_player_id,),
            ).fetchone()
            # Scraped rows only -- manual pitch-count entries (Phase B) may
            # carry just a pitch count with no full stat line, and could
            # double-count a game the scraper later also picks up. Keeping
            # this report's totals scraped-only avoids that overlap.
            pitch = conn.execute(
                """SELECT SUM(ip_outs) AS ip_outs, SUM(h) AS h, SUM(er) AS er, SUM(so) AS so,
                          SUM(pitches) AS pitches, SUM(strikes) AS strikes,
                          SUM(bf) AS bf, SUM(bb) AS bb, SUM(hbp) AS hbp
                   FROM gc_pitching_stats WHERE gc_player_id = ? AND source = 'scraped'""",
                (gc_player_id,),
            ).fetchone()

            ab = bat["ab"] or 0
            hits = bat["h"] or 0
            avg = round(hits / ab, 3) if ab > 0 else None
            xbh = (bat["doubles"] or 0) + (bat["triples"] or 0) + (bat["hr"] or 0)
            # BB column combines walks + HBP (hit by pitch) -- both are
            # "reached base without putting the ball in play", a common
            # combined plate-discipline read, per the user's request.
            bb_total = (bat["bb"] or 0) + (bat["hbp"] or 0)
            ip_outs = pitch["ip_outs"] or 0
            pitches = pitch["pitches"] or 0
            strikes = pitch["strikes"]
            era = None
            if ip_outs > 0:
                # 9-inning convention -- youth games are shorter, but this is
                # the most recognizable ERA scale; shown alongside raw IP/ER
                # so it's not the only number in the picture.
                era = round((pitch["er"] or 0) * 9 / (ip_outs / 3), 2)

            # Batting average against = hits allowed / at-bats faced, where
            # at-bats faced = batters faced minus walks and hit batters
            # (the standard simplified formula -- sac flies/bunts aren't
            # broken out at the pitcher level in GC's data, same as real
            # box scores rarely bother at this level either).
            baa = None
            bf = pitch["bf"] or 0
            if bf > 0:
                pitcher_ab = bf - (pitch["bb"] or 0) - (pitch["hbp"] or 0)
                if pitcher_ab > 0:
                    baa = round((pitch["h"] or 0) / pitcher_ab, 3)

            roster_raw.append({
                "player_name": player_name,
                "games_played": bat["games"] or 0,
                "ab": ab, "avg": avg,
                "xbh": xbh, "bb": bb_total, "k": bat["so"] or 0,
                "ip": _format_ip(ip_outs) if ip_outs else None,
                "era": era,
                "so_pitching": pitch["so"] or 0,
                "pitches": pitches if pitches else None,
                "strike_pct": round(strikes / pitches * 100) if strikes is not None and pitches > 0 else None,
                "baa": baa,
            })

        badges_by_player = _compute_badges(roster_raw)
        roster = [
            {**r, "badges": badges_by_player.get(r["player_name"], [])}
            for r in roster_raw
        ]
        # ab is internal-only (badge eligibility threshold), not displayed.
        for r in roster:
            r.pop("ab", None)

        roster.sort(key=lambda r: r["player_name"])
        return {
            "team_key": team_key,
            "team_name": team_row["team_name"],
            "last_scraped": last_scraped_row["ls"] if last_scraped_row else None,
            "roster": roster,
        }
    finally:
        conn.close()


def get_player_scouting_profile(team_key: int, player_name: str) -> dict:
    conn = _get_connection()
    try:
        gc_team_ids = _gc_team_ids_for_pg_team(conn, team_key)
        if not gc_team_ids:
            raise ValueError(f"No GameChanger data linked to team {team_key}")
        t_placeholders = ", ".join("?" for _ in gc_team_ids)

        player_rows = conn.execute(
            f"SELECT gc_player_id, jersey_number FROM gc_players "
            f"WHERE gc_team_id IN ({t_placeholders}) AND player_name = ?",
            tuple(gc_team_ids) + (player_name,),
        ).fetchall()
        if not player_rows:
            raise ValueError(f"Player '{player_name}' not found for team {team_key}")

        gc_player_ids = [r["gc_player_id"] for r in player_rows]
        jersey_number = player_rows[0]["jersey_number"]
        p_placeholders = ", ".join("?" for _ in gc_player_ids)

        batting_log = conn.execute(
            f"""SELECT g.game_date, g.opponent_name, b.ab, b.r, b.h, b.doubles, b.triples,
                       b.hr, b.rbi, b.bb, b.so, b.sb
                FROM gc_batting_stats b JOIN gc_games g ON g.gc_game_id = b.gc_game_id
                WHERE b.gc_player_id IN ({p_placeholders}) ORDER BY g.game_date""",
            tuple(gc_player_ids),
        ).fetchall()

        pitching_log = conn.execute(
            f"""SELECT g.game_date, g.opponent_name, ps.ip_outs, ps.h, ps.r, ps.er,
                       ps.bb, ps.so, ps.pitches, ps.strikes
                FROM gc_pitching_stats ps JOIN gc_games g ON g.gc_game_id = ps.gc_game_id
                WHERE ps.gc_player_id IN ({p_placeholders}) AND ps.source = 'scraped'
                ORDER BY g.game_date""",
            tuple(gc_player_ids),
        ).fetchall()

        fielding_log = conn.execute(
            f"""SELECT g.game_date, g.opponent_name, f.errors
                FROM gc_fielding_stats f JOIN gc_games g ON g.gc_game_id = f.gc_game_id
                WHERE f.gc_player_id IN ({p_placeholders}) ORDER BY g.game_date""",
            tuple(gc_player_ids),
        ).fetchall()

        def _sum(rows, col):
            return sum((r[col] or 0) for r in rows)

        def _strike_pct(strikes, pitches):
            return round(strikes / pitches * 100) if strikes is not None and pitches else None

        batting_totals = {c: _sum(batting_log, c) for c in
                           ("ab", "r", "h", "doubles", "triples", "hr", "rbi", "bb", "so", "sb")}
        pitching_ip_outs = _sum(pitching_log, "ip_outs")
        total_pitches = _sum(pitching_log, "pitches")
        total_strikes = _sum(pitching_log, "strikes") if any(r["strikes"] is not None for r in pitching_log) else None
        pitching_totals = {
            "ip": _format_ip(pitching_ip_outs),
            "h": _sum(pitching_log, "h"), "r": _sum(pitching_log, "r"), "er": _sum(pitching_log, "er"),
            "bb": _sum(pitching_log, "bb"), "so": _sum(pitching_log, "so"), "pitches": total_pitches,
            "strikes": total_strikes, "strike_pct": _strike_pct(total_strikes, total_pitches),
        }

        return {
            "player_name": player_name,
            "jersey_number": jersey_number,
            "batting_log": [
                {"game_date": r["game_date"], "opponent": r["opponent_name"],
                 "ab": r["ab"] or 0, "r": r["r"] or 0, "h": r["h"] or 0,
                 "doubles": r["doubles"] or 0, "triples": r["triples"] or 0, "hr": r["hr"] or 0,
                 "rbi": r["rbi"] or 0, "bb": r["bb"] or 0, "so": r["so"] or 0, "sb": r["sb"] or 0}
                for r in batting_log
            ],
            "batting_totals": batting_totals,
            "pitching_log": [
                {"game_date": r["game_date"], "opponent": r["opponent_name"], "ip": _format_ip(r["ip_outs"]),
                 "h": r["h"] or 0, "r": r["r"] or 0, "er": r["er"] or 0, "bb": r["bb"] or 0,
                 "so": r["so"] or 0, "pitches": r["pitches"], "strikes": r["strikes"],
                 "strike_pct": _strike_pct(r["strikes"], r["pitches"])}
                for r in pitching_log
            ],
            "pitching_totals": pitching_totals,
            "fielding_log": [
                {"game_date": r["game_date"], "opponent": r["opponent_name"], "errors": r["errors"] or 0}
                for r in fielding_log
            ],
        }
    finally:
        conn.close()


def map_gc_team(pg_team_key: int, gc_team_id: str = None, gc_url: str = None) -> dict:
    if not gc_team_id and not gc_url:
        raise ValueError("Provide gc_team_id or gc_url")

    conn = _get_connection()
    try:
        if gc_team_id:
            row = conn.execute("SELECT gc_team_id FROM gc_teams WHERE gc_team_id = ?", (gc_team_id,)).fetchone()
            if row is None:
                raise ValueError(f"GameChanger team '{gc_team_id}' not found -- scrape it first")
            resolved_id = gc_team_id
        else:
            row = conn.execute("SELECT gc_team_id FROM gc_teams WHERE gc_url = ?", (gc_url,)).fetchone()
            if row is None:
                raise ValueError(f"No GameChanger team found with URL '{gc_url}' -- scrape it first")
            resolved_id = row["gc_team_id"]

        conn.execute("UPDATE gc_teams SET pg_team_key = ? WHERE gc_team_id = ?", (pg_team_key, resolved_id))
        conn.commit()
    finally:
        conn.close()
    return {"gc_team_id": resolved_id, "pg_team_key": pg_team_key}


def list_gc_team_mappings() -> list[dict]:
    """For the admin page's team-mapping card: every scraped GC team, with
    its current PG mapping (if any)."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            """SELECT t.gc_team_id, t.gc_team_name, t.gc_url, t.pg_team_key, t.last_scraped, pg.team_name AS pg_team_name
               FROM gc_teams t LEFT JOIN teams pg ON pg.id = t.pg_team_key
               ORDER BY t.gc_team_name"""
        ).fetchall()
        return [
            {
                "gc_team_id": r["gc_team_id"],
                "gc_team_name": r["gc_team_name"],
                "gc_url": r["gc_url"],
                "pg_team_key": r["pg_team_key"],
                "pg_team_name": r["pg_team_name"],
                "last_scraped": r["last_scraped"],
            }
            for r in rows
        ]
    finally:
        conn.close()
