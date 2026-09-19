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
    filename="bracket_enrichment2.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

SQLITE_DB = "C:/Users/bsmel/OneDrive/Documents/Baseball_data/10u data.db"

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
driver.set_page_load_timeout(45)

def safe_get(url, retries=3):
    for attempt in range(retries):
        try:
            driver.get(url)
            return True
        except Exception as e:
            logging.warning(f"Attempt {attempt+1} failed for {url}: {e}")
            time.sleep(2)
    return False

def extract_bracket_round_from_source(source_url, game_num):
    if not safe_get(source_url):
        return None

    soup = BeautifulSoup(driver.page_source, "html.parser")

    # Find all pool/round labels
    spans = soup.find_all("span", id=lambda x: x and "lblPool" in x)

    for span in spans:
        # Go up to the row that contains this span
        row = span.find_parent("div", class_="row")
        if not row:
            continue

        text = row.get_text(" ", strip=True)

        # Match the correct game number in this row
        if re.search(rf"(GameID\s*:\s*{game_num}\b|Game\s*#?\s*{game_num}\b)", text):
            return span.get_text(strip=True)

    return None


def main():
    conn = sqlite3.connect(SQLITE_DB)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, game_num, sourceurl, bracketurl, home_team, away_team, game_date
        FROM games
        WHERE bracketurl IS NOT NULL AND bracketurl <> ''  
    """)

    games = cur.fetchall()
    print(f"Found {len(games)} bracket games.")

    for game_id, game_num, sourceurl, bracketurl, home_team, away_team, game_date in games:
        print(f"Processing game {game_num} on {game_date}: {home_team} vs {away_team}")

        # Load bracket page
        if not safe_get(bracketurl):
            continue

        # Load source page for bracket round
        bracket_round = extract_bracket_round_from_source(sourceurl, game_num)

        # Update DB
        cur.execute("""
            UPDATE games
            SET bracket_round = ?
            WHERE id = ?
        """, (bracket_round, game_id))

        conn.commit()

        print(f"Updated game {game_num} on {game_date}: round={bracket_round}")

        time.sleep(1)

    conn.close()
    driver.quit()
    print("Done enriching bracket data.")

if __name__ == "__main__":
    main()
