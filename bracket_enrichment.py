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
    filename="bracket_enrichment.log",
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

def find_bracket_for_game(soup, home_team, away_team):
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
    home_seed = None
    visitor_seed = None

    # Look at each cell, not just the whole row
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
        if re.search(rf"Game\s*#?\s*{game_num}\b", text):
            return span.get_text(strip=True)

    return None


def main():
    conn = sqlite3.connect(SQLITE_DB)
    cur = conn.cursor()

    # TEMP UPDATE for id > 3600 for rerun
    cur.execute("""
        SELECT id, game_num, sourceurl, bracketurl, home_team, away_team
        FROM games
        WHERE bracketurl IS NOT NULL AND bracketurl <> '' AND id >=3600 
    """)



    games = cur.fetchall()
    print(f"Found {len(games)} bracket games.")

    for game_id, game_num, sourceurl, bracketurl, home_team, away_team in games:
        print(f"Processing game {game_num}: {home_team} vs {away_team}")

        # Load bracket page
        if not safe_get(bracketurl):
            continue

        bracket_soup = BeautifulSoup(driver.page_source, "html.parser")

        bracket_name, bracket_table = find_bracket_for_game(bracket_soup, home_team, away_team)

        if not bracket_name:
            bracket_name = "Single bracket"

        if bracket_table:
            home_seed, visitor_seed = extract_seeds_from_table(bracket_table, home_team, away_team)
        else:
            home_seed = visitor_seed = None

        # Load source page for bracket round
        bracket_round = extract_bracket_round_from_source(sourceurl, game_num)

        # Update DB
        cur.execute("""
            UPDATE games
            SET bracket = ?, home_seed = ?, visitor_seed = ?, bracket_round = ?
            WHERE id = ?
        """, (bracket_name, home_seed, visitor_seed, bracket_round, game_id))

        conn.commit()

        print(f"Updated game {game_num}: bracket={bracket_name}, home_seed={home_seed}, visitor_seed={visitor_seed}, round={bracket_round}")

        time.sleep(1)

    conn.close()
    driver.quit()
    print("Done enriching bracket data.")

if __name__ == "__main__":
    main()
