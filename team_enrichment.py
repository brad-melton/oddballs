import sqlite3
from bs4 import BeautifulSoup
import undetected_chromedriver as uc
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import logging
import re

logging.basicConfig(
    filename="team_enrichment.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

SQLITE_DB = "C:/Users/bsmel/OneDrive/Documents/Baseball_data/10u data.db"

# ---------------------------
# Selenium Setup
# ---------------------------
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

driver = uc.Chrome(options=chrome_options)
driver.set_page_load_timeout(25)

# ---------------------------
# Helper: Safe navigation
# ---------------------------
def safe_get(url, retries=3):
    for attempt in range(retries):
        try:
            driver.get(url)
            return True
        except Exception as e:
            logging.warning(f"Attempt {attempt+1} failed for {url}: {e}")
            time.sleep(2)
    return False

# ---------------------------
# Extract team_index from the game page
# ---------------------------
def extract_team_index_from_game_page(team_name, game_url):
    if not safe_get(game_url):
        logging.error(f"Could not load game page: {game_url}")
        return None

    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
    except:
        logging.warning(f"Page did not fully load: {game_url}")

    soup = BeautifulSoup(driver.page_source, "html.parser")

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

# ---------------------------
# Scrape Perfect Game team page
# ---------------------------
def scrape_pg_team_page(team_index):
    url = f"https://www.perfectgame.org/Events/Tournaments/Teams/Default.aspx?team={team_index}"

    if not safe_get(url):
        logging.error(f"Could not load PG team page: {url}")
        return None

    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
    except:
        logging.warning(f"Page did not fully load: {url}")

    soup = BeautifulSoup(driver.page_source, "html.parser")

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

# ---------------------------
# Main Script
# ---------------------------
def main():
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
    print(f"Found {len(teams)} teams to enrich.")

    for team_name in teams:
        if not team_name:
            continue

        # Skip placeholder teams like "Winner"
        if "winner" in team_name.lower():
            print(f"Skipping placeholder team: {team_name}")
            logging.info(f"Skipping placeholder team: {team_name}")
            continue

        print(f"Processing team: {team_name}")
        logging.info(f"Processing team: {team_name}")

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
            continue

        last_game_url = row[0]

        # Extract team_index from the game page
        team_index = extract_team_index_from_game_page(team_name, last_game_url)
        if not team_index:
            logging.warning(f"No team_index found for {team_name}")
            continue

        # Scrape PG team page
        metadata = scrape_pg_team_page(team_index)

        # Insert or update team record
        cur.execute("""
            INSERT INTO teams (team_name, team_index, age_group, level, last_updated)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(team_name) DO UPDATE SET
                team_index=excluded.team_index,
                age_group=excluded.age_group,
                level=excluded.level,
                last_updated=datetime('now')
        """, (
            team_name,
            team_index,
            metadata["age_group"] if metadata else None,
            metadata["level"] if metadata else None
        ))

        conn.commit()

        # Retrieve assigned team_key (auto-increment ID)
        cur.execute("SELECT id FROM teams WHERE team_name = ?", (team_name,))
        team_key = cur.fetchone()[0]

        # Update games table with team_key
        cur.execute("""
            UPDATE games
            SET away_team_key = ?
            WHERE away_team = ?
        """, (team_key, team_name))

        cur.execute("""
            UPDATE games
            SET home_team_key = ?
            WHERE home_team = ?
        """, (team_key, team_name))

        conn.commit()

        print(f"Updated team '{team_name}' → team_index={team_index}, key={team_key}")
        logging.info(f"Updated team '{team_name}' → team_index={team_index}, key={team_key}")

        time.sleep(1)

    conn.close()
    driver.quit()
    print("Done enriching teams.")

if __name__ == "__main__":
    main()
