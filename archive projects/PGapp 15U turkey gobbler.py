import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt

dfSimulations = pd.read_csv('15U Turkey Gobbler simulation summary.csv')
dfTeams = dfSimulations['Team'].unique()


tournaments = ['15U Turkey Gobbler']

selecttournament = st.selectbox(label="Select tournament for viewing:", options=tournaments)
selectteams = st.selectbox(label="Select team:", options=dfTeams)

dfTeamSimulations = dfSimulations[dfSimulations['Team'] == selectteams]

# create histogram
fig, ax = plt.subplots()
ax.hist(dfTeamSimulations['Seed'], bins=30, color='skyblue',edgecolor='black')
plt.xlabel('Seed')
plt.ylabel('Frequency')
plt.title(f'Distribution of seeds for {selectteams}')

st.pyplot(fig)
