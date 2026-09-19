import pandas as pd
from bs4 import BeautifulSoup
import sqlite3
import time
import re
from urllib.parse import urlparse, parse_qs
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

# -- CONFIGURATION --
EXCEL_NAME = 'C:/Users/bsmel/OneDrive/PG 9U Data Directory.xlsx'
DAYS = ['Day1', 'Day2', 'Day3', 'Day4', 'Day5', 'Day6']
DB_GAMES = 'C:/Users/bsmel/OneDrive/9U games.db'

# -- CHROME SETUP --
chrome_options = Options()
chrome_options.add_argument("--headless") 
chrome_options.add_argument("--log-level=3")
# Adding real browser headers to prevent hanging/blocking
chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")
chrome_options.add_argument("--window-size=1920,1080")
chrome_options.add_argument("--disable-blink-features=AutomationControlled")

def get_data(url, day_label):
    """
    Scrapes a single URL and returns a list of dictionaries (one per game).
    """
    if pd.isna(url) or not str(url).startswith('http'):
        return []
    
    # --- Extract Event ID and Date from URL for database columns ---
    parsed_url = urlparse(url)
    query_params = parse_qs(parsed_url.query)
    url_event_id = query_params.get('event', ['N/A'])[0]
    url_date = query_params.get('Date', ['N/A'])[0]

    driver = webdriver.Chrome(options=chrome_options)
    try:
        driver.get(url)
        # Give the page time to execute JavaScript
        time.sleep(5)
        
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
                time_tag = game.find("span", id=lambda x: x and "GameTime" in x)
                
                # GameID (numeric) and Gm# (label)
                game_id_raw = game.find("div", string=lambda x: x and "GameID:" in x)
                gm_label_tag = game.find("span", id=lambda x: x and "lblGameNumber" in x)
                
                # Field
                ballpark_container = game.find("div", id=lambda x: x and "pnlBallparkKnown" in x)

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
                
                gm_label = gm_label_tag.get_text(strip=True) if gm_label_tag else "N/A"

                field_full = "N/A"
                if ballpark_container:
                    field_div = ballpark_container.find("div")
                    if field_div:
                        field_full = field_div.get_text(" ", strip=True)

                # Append record
                extracted_games.append({
                    "Day": day_label,
                    "EventID": url_event_id,
                    "Date": url_date,
                    "GameNum": gm_label,
                    "GameID": numeric_id,
                    "Field": field_full,
                    "Time": (time_tag.get_text(strip=True) if time_tag else "N/A"),
                    "VisitorTeam": team_v_raw.get_text(strip=True),
                    "VisitorScore": safe_int(score_v_raw),
                    "HomeTeam": team_h_raw.get_text(strip=True),
                    "HomeScore": safe_int(score_h_raw),
                    "SourceURL": url
                })
            except Exception as e:
                print(f"   [Error] Parsing individual game row: {e}")

        return extracted_games
    finally:
        driver.quit() 

def run_process(column_name, db_file):
    """
    Reads the Excel, loops through URLs, and saves to DB incrementally.
    """
    print(f"\n>>> Starting Process for Column: {column_name}")
    try:
        # Load URLs from Excel
        df_input = pd.read_excel(EXCEL_NAME, sheet_name='data', header=0, usecols=[column_name])
        
        for url in df_input[column_name]:
            if pd.notna(url):
                print(f"Retrieving Data: {url}")
                
                # Scrape this specific URL
                url_data = get_data(url, column_name)

                if url_data:
                    # Convert this URL's data to a DataFrame
                    results_df = pd.DataFrame(url_data)
                    
                    # Write to SQLite immediately (incremental)
                    with sqlite3.connect(db_file) as conn:
                        results_df.to_sql('AllGames', conn, if_exists='append', index=False)
                    
                    print(f"   [Success] Saved {len(url_data)} games to database.")
                else:
                    print(f"   [Notice] No games found or page failed to load for this URL.")
                
                # Polite delay
                time.sleep(1)

    except Exception as e:
        print(f"!!! Column {column_name} failed: {e}")

def main():
    print("Initializing Data Collection...")
    
    # We use 'append' in run_process, so we only run Day 1 here per your request.
    # To run all days, uncomment the loop below.
    
    # for day in DAYS:
    #     run_process(day, DB_GAMES)
    
    run_process('Day1', DB_GAMES)
    
    print("\nProcess Complete.")
    
if __name__ == "__main__":
    main()