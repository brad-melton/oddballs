import pandas as pd
import sqlite3
from bs4 import BeautifulSoup
import time
from urllib.parse import urlparse, parse_qs
from selenium import webdriver
# from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
import undetected_chromedriver as uc
import random




import logging

logging.basicConfig(
    filename="scraper.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# -- CHROME SETUP --
chrome_options = Options()
chrome_options.add_argument("--headless") 
chrome_options.add_argument("--log-level=3")
# Adding real browser headers to prevent hanging/blocking
chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")
chrome_options.add_argument("--window-size=1920,1080")
chrome_options.add_argument("--disable-blink-features=AutomationControlled")

driver = uc.Chrome(options=chrome_options)

EXCEL_FILE = "C:/Users/bsmel/OneDrive/Documents/Baseball_data/Data for 10U 2025-2026 Season.xlsx"
SQLITE_DB  = "C:/Users/bsmel/OneDrive/Documents/Baseball_data/10u data.db"

def load_urls_from_excel(path):
    df = pd.read_excel(path)
    link_columns = ["Link1", "Link2", "Link3", "Link4", "Link5", "Link6", "Link7"]
    format_columns = ["Format1", "Format2", "Format3", "Format4", "Format5", "Format6", "Format7"]
    urls=[]
    for _, row in df.iterrows():
        bracket_value = row.get("Bracket", None)

        for link_col, fmt_col in zip(link_columns, format_columns):
            url = row.get(link_col)
            fmt = row.get(fmt_col)

            if pd.notna(url) and isinstance(url,str) and url.strip():
                url = url.strip()

                bracket_for_url = None
                if isinstance(fmt,str) and "bracket" in fmt.lower():
                    bracket_for_url = bracket_value
                
                urls.append({
                    "url": url,
                    "format": fmt,
                    "bracket": bracket_for_url
                })
        
    return urls

def init_db(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            eventid TEXT,
            game_date TEXT,
            game_time TEXT,
            game_num INTEGER,
            format TEXT,
            ballpark TEXT,
            field_num TEXT,
            away_team TEXT,
            away_team_key INTEGER,
            away_score INTEGER,
            home_team TEXT,
            home_team_key INTEGER,
            home_score INTEGER,
            bracket TEXT,
            bracket_round TEXT,
            seed INTEGER,
            sourceurl TEXT,
            bracketurl TEXT            
        )
    """)
    conn.commit()
    return conn

def url_already_scraped(conn, url):
    cur = conn.cursor()
    cur.execute("""
        SELECT 1 FROM games
        WHERE sourceurl = ?
        LIMIT 1
    """, (url,))
    return cur.fetchone() is not None

def get_data(url, fmt, brk_url):
    if pd.isna(url) or not str(url).startswith('http'):
        return []
    
    # --- Extract Event ID and Date from URL for database columns ---
    parsed_url = urlparse(url)
    query_params = parse_qs(parsed_url.query)
    url_event_id = query_params.get('event', ['N/A'])[0]
    url_date = query_params.get('Date', ['N/A'])[0]

    try:
        time.sleep(2+ random.random() * 2)
        try:
            driver.get(url)
        except Exception as e:
            logging.warning(f"First attempt failed, retrying: {e}")
            time.sleep(3)
            driver.get(url)
       

        # Give the page time to execute JavaScript
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CLASS_NAME, "mb-3"))
        )
        logging.info(f"Fetching URL: {url}")
        
        pg_soup = BeautifulSoup(driver.page_source, "html.parser")
        games_source = pg_soup.find_all("div", class_="mb-3")
        extracted_games = []

        for game in games_source:
            try:
                # Find Raw Elements
                team_v_raw = game.find("a", id=lambda x: x and "hlVisitorTeamName" in x)
                team_h_raw = game.find("a", id=lambda x: x and "hlHomeTeam" in x)
                score_v_raw = game.find("div", id=lambda x: x and "VisitorPGScore" in x)
                score_h_raw = game.find("div", id=lambda x: x and "HomeScoreFinal" in x)
                time_tag = game.find("span", id=lambda x: x and "lblGameTime" in x)
                
                # GameID (numeric) and Gm# (label)
                game_id_raw = game.find("div", string=lambda x: x and "GameID:" in x)
                
                
                # Field
                ballpark_container = game.find("div", id=lambda x: x and "pnlBallparkKnown" in x)
                
                def extract_field_num(container):
                    text = container.get_text(" ", strip=True)
                    if "Field" in text:
                        return text.split("@")[0].strip()
                    return None
                
                def extract_ballpark(container):
                    a_tag = container.find("a")
                    if a_tag:
                        return a_tag.get_text(strip=True)
                    return None
                

                if not team_v_raw or not team_h_raw:
                    continue

                # --- CLEANING DATA ---
                def safe_int(val):
                    try: 
                        text = val.get_text(strip=True)
                        return int(text)
                    except: 
                        return None

                numeric_id = "N/A"
                if game_id_raw:
                    numeric_id = game_id_raw.get_text(strip=True).replace("GameID:", "").strip()
                
                # Append record
                extracted_games.append({
                    "eventid": url_event_id,
                    "game_date": url_date,
                    "game_time": (time_tag.get_text(strip=True) if time_tag else "N/A"),
                    "game_num": numeric_id,
                    "format": fmt,
                    "ballpark": extract_ballpark(ballpark_container),
                    "field_num": extract_field_num(ballpark_container),
                    "away_team": team_v_raw.get_text(strip=True),
                    "away_score": safe_int(score_v_raw),
                    "home_team": team_h_raw.get_text(strip=True),
                    "home_score": safe_int(score_h_raw),
                    "sourceURL": url,
                    "bracketurl": brk_url
                })
            except Exception as e:
                print(f"   [Error] Parsing individual game row: {e}")
                logging.error(f"Error parsing game row: {e}")

        return extracted_games
    finally:
        print("finally")        


def game_exists(conn, eventid, game_num):
    cur = conn.cursor()
    cur.execute("""
        SELECT 1 FROM games
        WHERE eventid = ? AND game_num = ?
        LIMIT 1
    """, (eventid, game_num))
    return cur.fetchone() is not None

def save_website_data(conn, data):
    if game_exists(conn, data["eventid"], data["game_num"]):
        print(f"   Skipped duplicate game {data['eventid']} / {data['game_num']}")
        logging.info(f"Duplicate skipped: {data['eventid']} / {data['game_num']}")
        return

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO games (
            eventid, game_date, game_time, game_num, format,
            ballpark, field_num,
            away_team, away_score,
            home_team, home_score,
            sourceurl, bracketurl
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data["eventid"],
        data["game_date"],
        data["game_time"],
        data["game_num"],
        data["format"],
        data["ballpark"],
        data["field_num"],
        data["away_team"],
        data["away_score"],
        data["home_team"],
        data["home_score"],
        data["sourceURL"],
        data["bracketurl"]
    ))
    logging.info(f"Saved game {data['eventid']} / {data['game_num']}")
    conn.commit()
    cur.close()



def main():
    urls = load_urls_from_excel(EXCEL_FILE)
    conn = init_db(SQLITE_DB)

    for rec in urls:
        url = rec["url"]
        fmt = rec["format"]
        brk = rec["bracket"]

        if url_already_scraped(conn, url):
            print(f"Skipping already-scraped URL: {url}")
            logging.info(f"Skipping already-scraped URL: {url}")
            continue
        
        print(f"Fetching: {url}")
        logging.info(f"Fetching: {url}")

        data = get_data(url, fmt, brk)

        for game in data:
            save_website_data(conn, game)

        print(f"    Saved: {url}")
        logging.info(f"Saved URL: {url}")

    driver.quit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
