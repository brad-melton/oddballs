from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
options = Options()
options.add_argument("--log-level=3")
service = Service()

from bs4 import BeautifulSoup as Soup
import requests
import pandas as pd
from pandas import DataFrame
import time
import numpy as np
from collections import defaultdict

# get raw html
pg_address = 'https://www.perfectgame.org/Events/TournamentSchedule.aspx?event=105275&Date=11/08/2025'
pg_response = requests.get(pg_address)

driver = webdriver.Chrome(service=service,options=options)
driver.get(pg_address)

time.sleep(5)

pg_html = driver.page_source
driver.quit()

pg_soup = Soup(pg_html, "html.parser")

games_source = pg_soup.find_all("div", class_="row mt-2")

games_data = []

for game in games_source:
#    game_num = game.find("span",id=lambda x: x and "lblGameNumber" in x).get_text(strip=True)
    teamVisitor = game.find("a",id=lambda x: x and "hlVisitorTeamName" in x).get_text(strip=True)
    teamHome = game.find("a",id=lambda x: x and "hlHomeTeam" in x).get_text(strip=True)
    scoreVisitor_raw = game.find("div",id=lambda x: x and "VisitorPGScore" in x).get_text(strip=True)
    scoreHome_raw = game.find("div",id=lambda x: x and "HomeScoreFinal" in x).get_text(strip=True)

    try:
        ScoreVisitor = int(scoreVisitor_raw)
    except:
        ScoreVisitor = None
    
    try:
        ScoreHome = int(scoreHome_raw)
    except:
        ScoreHome = None

    games_data.append({
#        "GameNumber": game_num,
        "VisitorTeam": teamVisitor,
        "VisitorScore": ScoreVisitor,
        "HomeTeam": teamHome,
        "HomeScore": ScoreHome
    })

df = pd.DataFrame(games_data)
df.to_csv('games_data_2025 15U Turkey Gobbler.csv',index=True)
