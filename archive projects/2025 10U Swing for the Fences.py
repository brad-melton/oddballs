import streamlit as st
import pandas as pd
from bs4 import BeautifulSoup
import requests

pg_address = 'https://www.perfectgame.org/Events/TournamentSchedule.aspx?event=105275&Date=11/08/2025'
pg_response = requests.get(pg_address)

pg_soup = BeautifulSoup(pg_response,"html.parser")

games_source = pg_soup.find_all("div",class_ = "p-2 border border-1 border-secondary-subtle shadom-sm rounded-3 mb-3")

games_data = []

# 1. Core Scraping Funtion
def extract_game_data(game_row):
    try:
        game_num_text = game_row.find('span',id=lambda x: x and 'lblGameNumber' in x).text
        game_time = game_row.find('span',id=lambda x: x and 'lblGameTime' in x).text

        game_id_div = game_row.find('div',style='font-size: 9px', class_ = 'text-secondary')
        game_id = game_id_div.text.replace('GameID:','').strip() if game_id_div else 'N/A'

        pool = game_row.find('span',id=lambda x: x and 'lblPool' in x).text

        ballpark_link = game_row.find('a',id=lambda x: x and 'hlBallPark' in x)
        ballpark_name = ballpark_link.text if ballpark_link else 'N/A'

        visitor_name_link = game_row.find('a',id=lambda x: x and 'hlVisitorTeamName' in x)
        visitor_name = visitor_name_link.text if visitor_name else 'N/A'

        visitor_score_div = game_row.find('div', id=lambda x: x and 'pnlVisitorPGScore' in x)
        visitor_score = visitor_score_div.text.strip() if visitor_score_div else 'N/A'

        home_name_link = game_row.find('a', id=lambda x: x and 'hlHomeTeam' in x)
        home_name = home_name_link.text if home_name_link else 'N/A'

        home_score_div = game_row.find('div', id=lambda x: x and 'pnlHomeScoreFinal' in x)
        home_score = home_score_div.text.strip() if home_score_div else 'N/A'

        return {
            'Game #': game_num_text,
            'Pool': pool,
            'Time': game_time,
            'Field': f"{game_row.text.split('@')[0].split('Field')[1].strip()} @ {ballpark_name}",
            'Visitor Team': visitor_name,
            'Visitor Score': visitor_score,
            'Home Team': home_name,
            'Home Score': home_score,
            'GameID': game_id
        }
    except Exception as e:
        st.error(f"Error parsing game row: {e}")
        return None
    
