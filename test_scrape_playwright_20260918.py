"""
Playwright prototype: scrapes only the FIRST event and FIRST day from the
Perfect Game tournament schedule. Read-only: does not write to the database.

Exists to test whether Playwright avoids the STATUS_STACK_OVERFLOW crash seen
in chromedriver.exe (Selenium) on this ARM64 Windows machine -- Playwright talks
to the browser directly over CDP with no separate driver-server process.
"""

import sqlite3
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from datetime import datetime, timedelta
import time
import logging

logging.basicConfig(
    filename="test_scrape_playwright_20260918.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

SQLITE_DB = r"C:\Users\bsmel\OneDrive\Documents\Baseball_data\10u data.db"


def build_source_url(event_id, game_date):
    return f"https://www.perfectgame.org/events/TournamentSchedule.aspx?event={event_id}&Date={game_date}"


def generate_scrape_dates(start_date, end_date):
    start = datetime.strptime(start_date, '%Y-%m-%d %H:%M:%S')
    end = datetime.strptime(end_date, '%Y-%m-%d %H:%M:%S')
    span = (end - start).days

    dates = []
    if span <= 3:
        current = start
        while current <= end:
            dates.append(current.strftime('%m/%d/%Y'))
            current += timedelta(days=1)
    else:
        current = start
        while current <= end:
            dates.append(current.strftime('%m/%d/%Y'))
            current += timedelta(days=14)
    return dates


def get_games_from_url(page, url, event_id, game_date):
    print(f"   Scraping: {url}")
    try:
        page.goto(url, timeout=45000, wait_until="domcontentloaded")
    except Exception as e:
        logging.error(f"Failed to load URL {url}: {e}")
        print(f"   [Warning] Failed to load page: {e}")
        return []

    time.sleep(5)  # Wait for JavaScript to render

    soup = BeautifulSoup(page.content(), "html.parser")
    games_source = soup.find_all("div", class_="mb-3")
    extracted_games = []

    if not games_source:
        print(f"   [Notice] No games found on page")
        return []

    for game in games_source:
        try:
            team_v_raw = game.find("a", id=lambda x: x and "hlVisitorTeamName" in x)
            team_h_raw = game.find("a", id=lambda x: x and "hlHomeTeam" in x)
            score_v_raw = game.find("div", id=lambda x: x and "VisitorPGScore" in x)
            score_h_raw = game.find("div", id=lambda x: x and "HomeScoreFinal" in x)
            time_tag = game.find("span", id=lambda x: x and "GameTime" in x)

            if not team_v_raw or not team_h_raw:
                continue

            def safe_int(val):
                try:
                    return int(val.get_text(strip=True))
                except Exception:
                    return None

            game_record = {
                "eventid": event_id,
                "game_date": game_date,
                "game_time": time_tag.get_text(strip=True) if time_tag else None,
                "away_team": team_v_raw.get_text(strip=True),
                "away_score": safe_int(score_v_raw),
                "home_team": team_h_raw.get_text(strip=True),
                "home_score": safe_int(score_h_raw),
                "sourceurl": url,
            }
            extracted_games.append(game_record)
        except Exception as e:
            logging.error(f"Error parsing game row: {e}")

    return extracted_games


def main():
    print("=" * 70)
    print("PLAYWRIGHT TEST SCRAPE: first event, first day only (read-only)")
    print("=" * 70)

    with sqlite3.connect(SQLITE_DB) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT eventid, name, start_date, end_date, classification
            FROM events
            WHERE status in ('complete', 'upcoming')
            ORDER BY eventid
            LIMIT 1
        """)
        event = cur.fetchone()

    if not event:
        print("No complete/upcoming events found.")
        return

    event_id = event["eventid"]
    event_name = event["name"]
    start_date = event["start_date"]
    end_date = event["end_date"]

    print(f"First event: {event_name} (ID: {event_id})")
    print(f"Dates: {start_date} to {end_date}")

    scrape_dates = generate_scrape_dates(start_date, end_date)
    first_date = scrape_dates[0]
    print(f"Testing first day only: {first_date}")

    url = build_source_url(event_id, first_date)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        try:
            games = get_games_from_url(page, url, event_id, first_date)
        finally:
            browser.close()

    print(f"\nGames found: {len(games)}")
    for g in games:
        print(f"  {g['away_team']} ({g['away_score']}) @ {g['home_team']} ({g['home_score']}) - {g['game_time']}")

    print("=" * 70)
    print("Test scrape complete")
    print("=" * 70)


if __name__ == "__main__":
    main()
