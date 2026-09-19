"""
Standalone diagnostic scraper.
Scrapes only the FIRST event and FIRST day from the Perfect Game tournament
schedule, using the same driver-restart/cleanup logic as the main pipeline.
Read-only: does not write anything to the database.
"""

import sqlite3
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from datetime import datetime, timedelta
import time
import logging
import subprocess

logging.basicConfig(
    filename="test_scrape_20260918.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

SQLITE_DB = r"C:\Users\bsmel\OneDrive\Documents\Baseball_data\10u data.db"

# 153.0.8010.47 and .52 both crash with STATUS_STACK_OVERFLOW (0xC00000FD) per Event
# Viewer, and .52 is confirmed the newest available patch for this build (no newer
# fix to try). 149.x rejected outright on version mismatch (Chrome is 153.x), so that
# wasn't a real test. Trying .36 -- the exact same patch as the installed Chrome browser.
CHROMEDRIVER_PATH = r"C:\Users\bsmel\.cache\selenium\chromedriver\win64\153.0.8010.36\chromedriver.exe"

chrome_options = Options()
chrome_options.add_argument("--headless")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--disable-blink-features=AutomationControlled")
chrome_options.add_argument("--window-size=1920,1080")
chrome_options.add_argument(
    "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
)


def create_driver():
    service = Service(executable_path=CHROMEDRIVER_PATH)
    drv = webdriver.Chrome(service=service, options=chrome_options)
    drv.set_page_load_timeout(45)
    return drv


def force_kill_driver_tree(drv):
    """Force-kill a driver's chromedriver process tree, even if chromedriver
    has already exited -- Windows can still tree-kill its orphaned Chrome
    children via taskkill /T as long as the PID hasn't been reused."""
    try:
        proc = drv.service.process
        if proc:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    except Exception as e:
        logging.warning(f"Error force-killing driver process tree: {e}")


def restart_driver(drv):
    try:
        drv.quit()
    except Exception as e:
        logging.warning(f"Error quitting driver during restart: {e}")
        force_kill_driver_tree(drv)
    return create_driver()


def safe_get(drv, url, retries=3):
    """Returns (driver, success). driver may be a new instance if a restart happened."""
    for attempt in range(retries):
        try:
            drv.get(url)
            return drv, True
        except Exception as e:
            logging.warning(f"Attempt {attempt+1} failed for {url}: {e}")
            time.sleep(2)

    logging.warning(f"All {retries} attempts failed for {url}; restarting WebDriver session")
    drv = restart_driver(drv)
    try:
        drv.get(url)
        return drv, True
    except Exception as e:
        logging.error(f"Post-restart attempt failed for {url}: {e}")

    return drv, False


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


def get_games_from_url(drv, url, event_id, game_date):
    print(f"   Scraping: {url}")
    drv, ok = safe_get(drv, url)
    if not ok:
        print(f"   [Warning] Failed to load page after retries/restart")
        return drv, []

    time.sleep(5)  # Wait for JavaScript to render

    soup = BeautifulSoup(drv.page_source, "html.parser")
    games_source = soup.find_all("div", class_="mb-3")
    extracted_games = []

    if not games_source:
        print(f"   [Notice] No games found on page")
        return drv, []

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

    return drv, extracted_games


def main():
    print("=" * 70)
    print("TEST SCRAPE: first event, first day only (read-only, no DB writes)")
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

    driver = create_driver()
    try:
        driver, games = get_games_from_url(driver, url, event_id, first_date)
    finally:
        try:
            driver.quit()
        except Exception as e:
            logging.warning(f"Error during final driver quit: {e}")
            force_kill_driver_tree(driver)

    print(f"\nGames found: {len(games)}")
    for g in games:
        print(f"  {g['away_team']} ({g['away_score']}) @ {g['home_team']} ({g['home_score']}) - {g['game_time']}")

    print("=" * 70)
    print("Test scrape complete")
    print("=" * 70)


if __name__ == "__main__":
    main()
