import pandas as pd
import numpy as np

game_csv = "9U Hurricane Harvey NIT.csv"
num_sims = 2000

pg_schedule = pd.read_csv(game_csv)

pg_teams = pd.concat([pg_schedule["VisitorTeam"], pg_schedule["HomeTeam"]]).unique()

trial_summaries = []

for trial in range(1, num_sims + 1):
    trial_stats = pd.DataFrame({
        "Team": pg_teams,
        "Wins": 0,
        "Losses": 0,
        "Ties": 0,
        "RunsAllowed": 0,
        "RunsScored": 0
    }).set_index("Team")
    for _, row in pg_schedule.iterrows():
        visitor = row["VisitorTeam"]
        visitor_score = np.random.poisson(9)
        home = row["HomeTeam"]
        home_score = np.random.poisson(10)
        trial_stats.loc[visitor, "RunsScored"] += visitor_score
        trial_stats.loc[visitor, "RunsAllowed"] += home_score
        trial_stats.loc[home, "RunsScored"] += home_score
        trial_stats.loc[home, "RunsAllowed"] += visitor_score

        if visitor_score > home_score:
            trial_stats.loc[visitor, "Wins"] += 1
            trial_stats.loc[home, "Losses"] += 1
        elif home_score > visitor_score:
            trial_stats.loc[visitor, "Losses"] += 1
            trial_stats.loc[home, "Wins"] += 1
        else:
            trial_stats.loc[visitor, "Ties"] += 1
            trial_stats.loc[home, "Ties"] += 1
        
        trial_stats["WinPct"] = (trial_stats["Wins"] + 0.5 * trial_stats["Ties"]) / (
            trial_stats["Wins"] + trial_stats["Losses"] + trial_stats["Ties"])
        
        trial_stats["Trial"] = trial
        trial_stats["Team"] = trial_stats.index

        trial_stats = trial_stats.sort_values(
            by=["WinPct", "RunsAllowed", "RunsScored"],
            ascending=[False, True, False]
        )

        trial_stats["Seed"] = range(1, len(trial_stats) +1)
        
    trial_summaries.append(trial_stats.reset_index(drop=True))

full_summary = pd.concat(trial_summaries)
full_summary.set_index(["Trial","Team"], inplace=True)
full_summary.to_csv("9U Hurricane Harvey NIT simulation summary.csv")