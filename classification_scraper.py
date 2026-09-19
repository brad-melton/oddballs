import sqlite3
from urllib.parse import urlparse, parse_qs
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
    filename="classification_scraper.log",
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
# Helper: Navigate safely
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
# Extract classification from event main page
# ---------------------------
def get_event_classification(event_url):
    if not safe_get(event_url):
        logging.error(f"Could not load event page: {event_url}")
        return None

    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
    except:
        logging.warning(f"Page did not fully load: {event_url}")

    soup = BeautifulSoup(driver.page_source, "html.parser")

    title_tag = soup.find("a", id=lambda x: x and "lblEventNameNew" in x)
    if not title_tag:
        return None

    title_text = title_tag.get_text(strip=True)

    # Regex to capture the last (...) group
    m = re.search(r"\(([^()]*)\)\s*$", title_text)
    if m:
        return m.group(1).strip()

    return None



# ---------------------------
# Main Script
# ---------------------------
def main():
    conn = sqlite3.connect(SQLITE_DB)
    cur = conn.cursor()

    # Get all distinct source URLs from games table
    cur.execute("SELECT DISTINCT sourceurl FROM games")
    urls = [row[0] for row in cur.fetchall()]

    print(f"Found {len(urls)} URLs to classify.")

    for url in urls:
        print(f"Scraping classification for event: {url}")
        logging.info(f"Scraping classification for event: {url}")

        classification = get_event_classification(url)

        if classification:
            # Update ALL games for this event
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            event_id = qs.get("event", ["N/A"])[0]

            cur.execute("""
                UPDATE games
                SET classification = ?
                WHERE sourceurl = ?
            """, (classification, url))

            conn.commit()
            print(f"Updated classification '{classification}' for url {url}")
            logging.info(f"Updated classification '{classification}' for event {url}")

        time.sleep(1)

    conn.close()
    driver.quit()
    print("Done updating classifications.")

if __name__ == "__main__":
    main()
