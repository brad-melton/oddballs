import streamlit as st
import pandas as pd

dfSimulations = pd.read_csv('9U Hurricane Harvey NIT simulation summary.csv')
dfTeams = dfSimulations['Team'].unique()


tournaments = ['9U Hurricane Harvey NIT']

selecttournament = st.selectbox(label="Select tournament for viewing:", options=tournaments)
selectteams = st.selectbox(label="Select team:", options=dfTeams)

