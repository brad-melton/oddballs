"""
Comprehensive Baseball Data Pipeline (Playwright edition)
======================================
Phase 1: Scrape games from Perfect Game tournament schedule pages
Phase 2: Enrich bracket data (determine pool vs bracket, populate seeds/rounds)
Phase 3: Enrich teams table and populate team keys in games

Ported from Selenium/chromedriver to Playwright: chromedriver.exe was crashing with
STATUS_STACK_OVERFLOW (0xC00000FD) on every launch on this ARM64 Windows machine,
confirmed via Event Viewer across every chromedriver build compatible with the
installed Chrome (153.0.8010.36/.47/.52). Playwright talks to Chromium directly
over CDP with no separate driver-server process, which avoids that crash class.
"""

import pandas as pd
import sqlite3
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from datetime import datetime, timedelta
import time
import logging
import re

# ---------------------------
# LOGGING SETUP
# ---------------------------
logging.basicConfig(
    filename="baseball_pipeline.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

SQLITE_DB = r"C:\Users\bsmel\OneDrive\Documents\Baseball_data\10u data.db"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"

# ---------------------------
# PLAYWRIGHT SETUP
# ---------------------------
playwright = sync_playwright().start()
browser = None


def create_page():
    """Launch the shared browser if needed, and return a fresh page in a new context."""
    global browser
    if browser is None or not browser.is_connected():
        browser = playwright.chromium.launch(headless=True)

    context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1920, "height": 1080})
    new_page = context.new_page()
    new_page.set_default_navigation_timeout(45000)
    new_page.set_default_timeout(45000)
    return new_page


def restart_page():
    """Tear down the current page/context (and relaunch the browser if it died), start fresh."""
    global page
    try:
        page.context.close()
    except Exception as e:
        logging.warning(f"Error closing page context during restart: {e}")

    try:
        page = create_page()
        logging.info("Playwright page restarted successfully")
        return True
    except Exception as e:
        logging.error(f"Failed to restart Playwright page: {e}")
        return False


page = create_page()


# ==============================================================================
# PHASE 1: GAME SCRAPING FUNCTIONS
# ==============================================================================

def generate_scrape_dates(start_date, end_date):
    """
    Generate list of dates to scrape based on tournament span:
    - 1-3 days: Every day in range (inclusive)
    - 4+ days: Every other Sunday (double-header tournament)

    Returns list of date strings in MM/DD/YYYY format
    """
    start = datetime.strptime(start_date, '%Y-%m-%d %H:%M:%S')
    end = datetime.strptime(end_date, '%Y-%m-%d %H:%M:%S')
    span = (end - start).days

    dates = []

    if span <= 3:
        # Daily tournament: include every date in range
        current = start
        while current <= end:
            dates.append(current.strftime('%m/%d/%Y'))
            current += timedelta(days=1)
    else:
        # Double-header tournament: every other Sunday
        current = start
        while current <= end:
            dates.append(current.strftime('%m/%d/%Y'))
            current += timedelta(days=14)

    return dates


def build_source_url(event_id, game_date):
    """Construct Perfect Game tournament schedule URL."""
    return f"https://www.perfectgame.org/events/TournamentSchedule.aspx?event={event_id}&Date={game_date}"


def build_bracket_url(event_id):
    """Construct Perfect Game bracket page URL."""
    return f"https://www.perfectgame.org/events/Brackets.aspx?event={event_id}"


def safe_get(url, retries=3):
    """Safely navigate to a URL with retry logic. Restarts the Playwright page/context
    if it appears to have died."""
    for attempt in range(retries):
        try:
            page.goto(url, wait_until="domcontentloaded")
            return True
        except Exception as e:
            logging.warning(f"Attempt {attempt+1} failed for {url}: {e}")
            time.sleep(2)

    logging.warning(f"All {retries} attempts failed for {url}; restarting Playwright page")
    if restart_page():
        try:
            page.goto(url, wait_until="domcontentloaded")
            return True
        except Exception as e:
            logging.error(f"Post-restart attempt failed for {url}: {e}")

    return False


def get_games_from_url(url, event_id, game_date, game_format):
    """Scrapes a single Perfect Game tournament schedule URL."""
    if not url:
        print(f"   [Warning] Invalid URL")
        return []

    try:
        print(f"   Scraping: {url}")
        if not safe_get(url):
            print(f"   [Warning] Failed to load page after retries/restart")
            return []
        time.sleep(5)  # Wait for JavaScript to render

        pg_soup = BeautifulSoup(page.content(), "html.parser")
        games_source = pg_soup.find_all("div", class_="mb-3")
        extracted_games = []

        if not games_source:
            print(f"   [Notice] No games found on page")
            return []

        for game in games_source:
            try:
                # Find Raw Elements
                team_v_raw = game.find("a", id=lambda x: x and "hlVisitorTeamName" in x)
                team_h_raw = game.find("a", id=lambda x: x and "hlHomeTeam" in x)
                score_v_raw = game.find("div", id=lambda x: x and "VisitorPGScore" in x)
                score_h_raw = game.find("div", id=lambda x: x and "HomeScoreFinal" in x)
                time_tag = game.find("span", id=lambda x: x and "GameTime" in x)

                # GameID (drives game_num)
                game_id_raw = game.find("div", string=lambda x: x and "GameID:" in x)

                # Field/Ballpark
                ballpark_container = game.find("div", id=lambda x: x and "pnlBallparkKnown" in x)

                if not team_v_raw or not team_h_raw:
                    continue

                # CLEANING/PARSING DATA
                def safe_int(val):
                    try:
                        text = val.get_text(strip=True)
                        return int(text)
                    except:
                        return None

                # Extract game number from "GameID: 1539564"
                game_num = None
                if game_id_raw:
                    m = re.search(r"GameID:\s*(\d+)", game_id_raw.get_text(strip=True))
                    if m:
                        game_num = int(m.group(1))

                # Extract field: "Field 1" (field_num) and "The Rac Waller" (ballpark) separately
                field_num = None
                field_full = None
                if ballpark_container:
                    field_div = ballpark_container.find("div")
                    if field_div:
                        anchor = field_div.find("a")
                        if anchor:
                            field_full = anchor.get_text(strip=True)
                        for content in field_div.contents:
                            if isinstance(content, str) and content.strip():
                                field_num = re.sub(r"@\s*$", "", content.strip()).strip()
                                break

                # Extract time
                game_time = time_tag.get_text(strip=True) if time_tag else None

                # BUILD RECORD
                game_record = {
                    "eventid": event_id,
                    "game_date": game_date,
                    "game_time": game_time,
                    "game_num": game_num,
                    "format": game_format,
                    "ballpark": field_full,
                    "field_num": field_num,
                    "away_team": team_v_raw.get_text(strip=True),
                    "away_team_key": None,
                    "away_score": safe_int(score_v_raw),
                    "home_team": team_h_raw.get_text(strip=True),
                    "home_team_key": None,
                    "home_score": safe_int(score_h_raw),
                    "bracket": None,
                    "bracket_round": None,
                    "seed": None,
                    "sourceurl": url,
                    "bracketurl": build_bracket_url(event_id) if game_format == "bracket" else None,
                    "classification": None,
                    "home_seed": None,
                    "visitor_seed": None,
                }

                extracted_games.append(game_record)

            except Exception as e:
                logging.error(f"Error parsing game row: {e}")

        return extracted_games

    except Exception as e:
        logging.error(f"Failed to load URL {url}: {e}")
        return []


def insert_games_to_db(games_list, db_path):
    """Inserts game records into the 'games' table."""
    if not games_list:
        return

    try:
        df_games = pd.DataFrame(games_list)

        with sqlite3.connect(db_path) as conn:
            df_games.to_sql('games', conn, if_exists='append', index=False)

        print(f"      [Success] Inserted {len(games_list)} games")

    except Exception as e:
        logging.error(f"Failed to insert games: {e}")


def phase_1_scrape_games(event_id=None):
    """
    PHASE 1: Scrape games from Perfect Game tournament pages.

    event_id: if given, scrapes only that one event regardless of its
    status (used for the admin page's "scrape this event" action, which
    targets a single event rather than the whole complete/upcoming set).
    """
    print("\n" + "="*70)
    print("PHASE 1: SCRAPING GAMES FROM PERFECT GAME")
    print("="*70 + "\n")

    try:
        with sqlite3.connect(SQLITE_DB) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            if event_id:
                query = """
                    SELECT eventid, name, age, start_date, end_date, classification
                    FROM events
                    WHERE eventid = ?
                """
                cursor.execute(query, (event_id,))
            else:
                query = """
                    SELECT eventid, name, age, start_date, end_date, classification
                    FROM events
                    WHERE status in ('complete', 'upcoming')
                    ORDER BY eventid
                """
                cursor.execute(query)

            events = cursor.fetchall()

            print(f"Found {len(events)} event(s) to process.\n")

            if not events:
                print("No matching events found.")
                return

            # Process each event
            for i, event in enumerate(events, 1):
                event_id = event['eventid']
                event_name = event['name']
                classification = event['classification']
                start_date = event['start_date']
                end_date = event['end_date']

                print(f"[{i}/{len(events)}] Event: {event_name} (ID: {event_id})")
                print(f"  Dates: {start_date} to {end_date}")

                # Generate dates to scrape
                scrape_dates = generate_scrape_dates(start_date, end_date)
                print(f"  Scraping {len(scrape_dates)} date(s): {scrape_dates}")
                first_day = scrape_dates[0] if scrape_dates else None

                # Scrape each date
                total_games = 0
                for game_date in scrape_dates:
                    # Build URL
                    url = build_source_url(event_id, game_date)

                    # First day of the event -> pool play, otherwise bracket play
                    game_format = "pool" if game_date == first_day else "bracket"

                    # Scrape games
                    games = get_games_from_url(url, event_id, game_date, game_format)

                    if games:
                        # Add classification if available
                        for game in games:
                            if game['classification'] is None:
                                game['classification'] = classification

                        # Insert into database
                        insert_games_to_db(games, SQLITE_DB)
                        total_games += len(games)

                    # Polite delay between requests
                    time.sleep(2)

                print(f"  Total games for this event: {total_games}\n")

        print("="*70)
        print("Phase 1 Complete: Games Scraped")
        print("="*70)
        logging.info("Phase 1 completed successfully")

    except Exception as e:
        logging.error(f"Phase 1 failed: {e}")
        print(f"\n!!! Fatal Error in Phase 1: {e}")


# ==============================================================================
# PHASE 2: BRACKET ENRICHMENT FUNCTIONS
# ==============================================================================

def extract_bracket_round_from_source(source_url, game_num):
    """Extract bracket round from source URL"""
    if not safe_get(source_url):
        return None

    soup = BeautifulSoup(page.content(), "html.parser")
    spans = soup.find_all("span", id=lambda x: x and "lblPool" in x)

    for span in spans:
        row = span.find_parent("div", class_="row")
        if not row:
            continue

        text = row.get_text(" ", strip=True)

        if re.search(rf"(GameID\s*:\s*{game_num}\b|Game\s*#?\s*{game_num}\b)", text):
            return span.get_text(strip=True)

    return None


def find_bracket_for_game(soup, home_team, away_team):
    """Find bracket name from bracket page"""
    headers = soup.find_all("span", class_="h3 font-bentonsans text-uppercase")

    for header in headers:
        bracket_name = header.get_text(strip=True)
        table = header.find_next("table")

        if not table:
            continue

        table_text = table.get_text(" ", strip=True)

        if home_team in table_text or away_team in table_text:
            return bracket_name, table

    return None, None


def extract_seeds_from_table(table, home_team, away_team):
    """Extract seed information from bracket table"""
    home_seed = None
    visitor_seed = None

    for td in table.find_all("td", class_=["HomeTeamBox", "VisitorTeamBox"]):
        cell_text = td.get_text(" ", strip=True)

        # HOME TEAM
        if home_team and home_team in cell_text and "HomeTeamBox" in td.get("class", []):
            home_input = td.find("input", id=lambda x: x and "hfHomeSeed" in x)
            if home_input and home_input.has_attr("value"):
                try:
                    home_seed = int(home_input["value"])
                except ValueError:
                    pass

        # VISITOR TEAM
        if away_team and away_team in cell_text and "VisitorTeamBox" in td.get("class", []):
            visitor_input = td.find("input", id=lambda x: x and "hfVisitorSeed" in x)
            if visitor_input and visitor_input.has_attr("value"):
                try:
                    visitor_seed = int(visitor_input["value"])
                except ValueError:
                    pass

    return home_seed, visitor_seed


def phase_2_enrich_brackets(event_id=None):
    """
    PHASE 2: Enrich bracket data (pool vs bracket determination).

    event_id: if given, scopes to just that event's bracket games instead
    of the whole complete/upcoming set (paired with phase_1's same param
    for the admin page's single-event scrape).
    """
    print("\n" + "="*70)
    print("PHASE 2: ENRICHING BRACKET DATA")
    print("="*70 + "\n")

    try:
        conn = sqlite3.connect(SQLITE_DB)
        cur = conn.cursor()

        # format is already set in Phase 1 (first day of event = pool, otherwise bracket);
        # bracketurl is only ever populated for bracket-format games, so every row returned
        # here is already known to be a bracket game. Scoped to this pipeline's own events
        # (status complete/upcoming, or just event_id when given) -- games.bracketurl is not
        # unique to this pipeline, and an unscoped query here will also pick up unrelated
        # rows from other data sources.
        if event_id:
            scope_sql = "g.eventid = ?"
            scope_params = (event_id,)
        else:
            scope_sql = "g.eventid IN (SELECT eventid FROM events WHERE status IN ('complete', 'upcoming'))"
            scope_params = ()

        cur.execute(f"""
            SELECT
                g.id,
                g.game_num,
                g.sourceurl,
                g.bracketurl,
                g.home_team,
                g.away_team,
                g.game_date
            FROM games g
            WHERE g.bracketurl IS NOT NULL AND g.bracketurl <> ''
            AND {scope_sql}
            ORDER BY g.id
        """, scope_params)

        games = cur.fetchall()
        print(f"Found {len(games)} games with bracket URLs.\n")

        for i, (game_id, game_num, sourceurl, bracketurl, home_team, away_team, game_date) in enumerate(games, 1):

            print(f"[{i}/{len(games)}] Game {game_id} ({game_num}): {home_team} vs {away_team} on {game_date}")

            bracket_name = None
            home_seed = None
            visitor_seed = None
            bracket_round = None

            # Load bracket page
            if safe_get(bracketurl):
                bracket_soup = BeautifulSoup(page.content(), "html.parser")
                bracket_name, bracket_table = find_bracket_for_game(bracket_soup, home_team, away_team)

                if not bracket_name:
                    bracket_name = "Single bracket"

                if bracket_table:
                    home_seed, visitor_seed = extract_seeds_from_table(bracket_table, home_team, away_team)
                    print(f"    Bracket: {bracket_name}, Seeds: Home={home_seed}, Away={visitor_seed}")
            else:
                print(f"    Failed to load bracket URL")

            # Extract bracket round from source page
            bracket_round = extract_bracket_round_from_source(sourceurl, game_num)
            if bracket_round:
                print(f"    Round: {bracket_round}")

            # Update database
            cur.execute("""
                UPDATE games
                SET bracket = ?, home_seed = ?, visitor_seed = ?, bracket_round = ?
                WHERE id = ?
            """, (bracket_name, home_seed, visitor_seed, bracket_round, game_id))

            conn.commit()
            print(f"  → Updated database")

            time.sleep(1)

        conn.close()
        print("\n" + "="*70)
        print("Phase 2 Complete: Bracket Data Enriched")
        print("="*70)
        logging.info("Phase 2 completed successfully")

    except Exception as e:
        logging.error(f"Phase 2 failed: {e}")
        print(f"\n!!! Fatal Error in Phase 2: {e}")


# ==============================================================================
# PHASE 3: TEAM ENRICHMENT FUNCTIONS
# ==============================================================================

def extract_team_index_from_game_page(team_name, game_url):
    """Extract team_index from game page"""
    if not safe_get(game_url):
        logging.error(f"Could not load game page: {game_url}")
        return None

    try:
        page.wait_for_selector("body", timeout=10000)
    except Exception:
        logging.warning(f"Page did not fully load: {game_url}")

    soup = BeautifulSoup(page.content(), "html.parser")

    # Find anchor tag matching the team name
    team_anchor = soup.find("a", string=lambda x: x and x.strip() == team_name)
    if not team_anchor:
        logging.warning(f"No anchor tag found for team {team_name} on {game_url}")
        return None

    href = team_anchor.get("href", "")
    m = re.search(r"team=(\d+)", href)
    if m:
        return int(m.group(1))

    return None


def scrape_pg_team_page(team_index):
    """Scrape Perfect Game team page"""
    url = f"https://www.perfectgame.org/Events/Tournaments/Teams/Default.aspx?team={team_index}"

    if not safe_get(url):
        logging.error(f"Could not load PG team page: {url}")
        return None

    try:
        page.wait_for_selector("body", timeout=10000)
    except Exception:
        logging.warning(f"Page did not fully load: {url}")

    soup = BeautifulSoup(page.content(), "html.parser")

    # Age division
    age_tag = soup.find("span", id=lambda x: x and "lblAgeDivision" in x)
    age_group = age_tag.get_text(strip=True) if age_tag else None

    # Classification
    class_tag = soup.find("span", id=lambda x: x and "lblBaseballClassification" in x)
    level = class_tag.get_text(strip=True) if class_tag else None

    return {
        "age_group": age_group,
        "level": level
    }


def phase_3a_enrich_teams():
    """PHASE 3A: Enrich Teams Table"""
    print("\n" + "="*70)
    print("PHASE 3A: ENRICHING TEAMS TABLE")
    print("="*70 + "\n")

    try:
        conn = sqlite3.connect(SQLITE_DB)
        cur = conn.cursor()

        # Create teams table if not exists
        cur.execute("""
            CREATE TABLE IF NOT EXISTS teams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_name TEXT UNIQUE,
                team_index INTEGER,
                age_group TEXT,
                level TEXT,
                last_updated TEXT
            )
        """)
        conn.commit()

        # Extract distinct teams from games table
        cur.execute("""
            SELECT DISTINCT away_team FROM games
            UNION
            SELECT DISTINCT home_team FROM games
        """)

        teams = [row[0] for row in cur.fetchall()]
        print(f"Found {len(teams)} teams to process.\n")

        for i, team_name in enumerate(teams, 1):
            if not team_name:
                continue

            # Skip placeholder teams
            if "winner" in team_name.lower():
                print(f"[{i}] Skipping placeholder: {team_name}")
                logging.info(f"Skipping placeholder team: {team_name}")
                continue

            print(f"[{i}] Processing: {team_name}")
            logging.info(f"Processing team: {team_name}")

            # Check if team already exists
            cur.execute("""
                SELECT id FROM teams WHERE team_name = ?
            """, (team_name,))

            existing = cur.fetchone()
            if existing:
                print(f"     → Already in database (key={existing[0]})")
                logging.info(f"Team '{team_name}' already exists with key={existing[0]}")
                continue

            # Find most recent game for this team
            cur.execute("""
                SELECT sourceurl
                FROM games
                WHERE away_team = ? OR home_team = ?
                ORDER BY id DESC
                LIMIT 1
            """, (team_name, team_name))

            row = cur.fetchone()
            if not row:
                logging.warning(f"No games found for team {team_name}")
                print(f"     → No games found, skipping")
                continue

            last_game_url = row[0]

            # Extract team_index
            team_index = extract_team_index_from_game_page(team_name, last_game_url)
            if not team_index:
                logging.warning(f"No team_index found for {team_name}")
                print(f"     → Could not extract team_index, skipping")
                continue

            # Scrape PG team page
            metadata = scrape_pg_team_page(team_index)

            # Insert team record
            cur.execute("""
                INSERT INTO teams (team_name, team_index, age_group, level, last_updated)
                VALUES (?, ?, ?, ?, datetime('now'))
            """, (
                team_name,
                team_index,
                metadata["age_group"] if metadata else None,
                metadata["level"] if metadata else None
            ))

            conn.commit()

            # Get assigned team_key
            cur.execute("SELECT id FROM teams WHERE team_name = ?", (team_name,))
            team_key = cur.fetchone()[0]

            print(f"     → Added: team_index={team_index}, key={team_key}")
            logging.info(f"Added team '{team_name}' → team_index={team_index}, key={team_key}")

            time.sleep(1)

        conn.close()
        print("\n" + "="*70)
        print("Phase 3A Complete: Teams Table Enriched")
        print("="*70)
        logging.info("Phase 3A completed successfully")

    except Exception as e:
        logging.error(f"Phase 3A failed: {e}")
        print(f"\n!!! Fatal Error in Phase 3A: {e}")


def phase_3b_populate_team_keys():
    """PHASE 3B: Populate Team Keys in Games Table"""
    print("\n" + "="*70)
    print("PHASE 3B: POPULATING TEAM KEYS IN GAMES TABLE")
    print("="*70 + "\n")

    try:
        conn = sqlite3.connect(SQLITE_DB)
        cur = conn.cursor()

        # Get all games
        cur.execute("""
            SELECT id, home_team, away_team
            FROM games
            ORDER BY id
        """)

        games = cur.fetchall()
        print(f"Processing {len(games)} games for team key population...\n")

        home_updated = 0
        away_updated = 0

        for game_id, home_team, away_team in games:
            # Update home_team_key
            if home_team:
                cur.execute("""
                    SELECT id FROM teams WHERE team_name = ?
                """, (home_team,))

                result = cur.fetchone()
                if result:
                    home_team_key = result[0]
                    cur.execute("""
                        UPDATE games
                        SET home_team_key = ?
                        WHERE id = ?
                    """, (home_team_key, game_id))
                    home_updated += 1
                    logging.info(f"Game {game_id}: Updated home_team_key for '{home_team}' = {home_team_key}")

            # Update away_team_key
            if away_team:
                cur.execute("""
                    SELECT id FROM teams WHERE team_name = ?
                """, (away_team,))

                result = cur.fetchone()
                if result:
                    away_team_key = result[0]
                    cur.execute("""
                        UPDATE games
                        SET away_team_key = ?
                        WHERE id = ?
                    """, (away_team_key, game_id))
                    away_updated += 1
                    logging.info(f"Game {game_id}: Updated away_team_key for '{away_team}' = {away_team_key}")

        conn.commit()
        conn.close()

        print(f"Home team keys updated: {home_updated}")
        print(f"Away team keys updated: {away_updated}")
        print(f"Total team key updates: {home_updated + away_updated}")
        print("\n" + "="*70)
        print("Phase 3B Complete: Team Keys Populated")
        print("="*70)
        logging.info("Phase 3B completed successfully")

    except Exception as e:
        logging.error(f"Phase 3B failed: {e}")
        print(f"\n!!! Fatal Error in Phase 3B: {e}")


# ==============================================================================
# MAIN ORCHESTRATION
# ==============================================================================

def main(event_id=None):
    import turso_sync

    print("\n" + "="*70)
    print("COMPREHENSIVE BASEBALL DATA PIPELINE")
    print("="*70)
    if event_id:
        print(f"\nScraping ONLY event {event_id} (--event given)")
    else:
        print("\nPhase 1: Scrape games from Perfect Game")
    print("Phase 2: Enrich bracket data (pool vs bracket)")
    print("Phase 3A: Enrich teams table")
    print("Phase 3B: Populate team keys in games")
    print("Phase 4: Sync new data to Turso" + ("" if turso_sync.enabled() else " (skipped -- not configured)"))
    print("\n" + "="*70 + "\n")

    try:
        # Run all phases
        phase_1_scrape_games(event_id=event_id)
        phase_2_enrich_brackets(event_id=event_id)
        phase_3a_enrich_teams()
        phase_3b_populate_team_keys()

        print("\n" + "="*70)
        print("PHASE 4: SYNCING NEW DATA TO TURSO")
        print("="*70 + "\n")
        turso_sync.sync_to_turso(event_id=event_id)

        print("\n" + "="*70)
        print("ALL PHASES COMPLETE - DATA PIPELINE FINISHED SUCCESSFULLY")
        print("="*70 + "\n")
        logging.info("All phases completed successfully")

    except Exception as e:
        logging.error(f"Pipeline failed: {e}")
        print(f"\n!!! FATAL ERROR: {e}\n")

    finally:
        try:
            page.context.close()
        except Exception as e:
            logging.warning(f"Error closing page context: {e}")
        try:
            if browser is not None:
                browser.close()
        except Exception as e:
            logging.warning(f"Error closing browser: {e}")
        try:
            playwright.stop()
        except Exception as e:
            logging.warning(f"Error stopping Playwright: {e}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fall 2026 catchup scoring pipeline")
    parser.add_argument(
        "--event", type=str, default=None,
        help="Scrape only this eventid (any status) instead of every complete/upcoming event. "
             "Used by the admin page's 'scrape this event' action.",
    )
    args = parser.parse_args()

    main(event_id=args.event)
