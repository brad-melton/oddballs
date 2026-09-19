"""
Trains the home/away score models and the win-probability model from the
current contents of the games/teams tables, and saves them wherever
load_models.py will look for them (MODEL_DIR) for predict_single_game.py /
monte_carlo_game.py / the backend to load.

Re-run this any time new results have been added to the database (e.g.
after a scrape) to refresh the models with the latest data:

    backend\\.venv\\Scripts\\python.exe "prediction model.py"
"""

import os
import joblib
import numpy as np
import pandas as pd

from sklearn.model_selection import train_test_split
from sklearn.preprocessing import OneHotEncoder
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingRegressor, GradientBoostingClassifier
from sklearn.metrics import mean_absolute_error, roc_auc_score

import team_stats
from team_stats import NUMERIC_FEATURES, CATEGORICAL_FEATURES
from load_models import MODEL_DIR

# Save exactly where load_models.py will look (respects the MODEL_DIR env
# var if set -- confirmed set on this machine to the project root, not a
# models/ subfolder) so training and loading can never point at different
# places.
MODELS_DIR = MODEL_DIR

# -----------------------------
# 1. Load + clean data
# -----------------------------
games, teams = team_stats.load_games_and_teams()

if len(games) < 20:
    raise SystemExit(
        f"Only {len(games)} completed games in the database -- not enough to train on. "
        "Run the scraping pipeline first."
    )

games["month"] = games["game_date"].dt.month
games["season"] = games["month"].apply(team_stats.season_from_month)

games["game_time"] = pd.to_datetime(games["game_time"], errors="coerce")
games["hour"] = games["game_time"].dt.hour
games["time_bucket"] = games["hour"].apply(team_stats.time_bucket_label)

games["weekday"] = games["game_date"].dt.day_name()
games["is_weekend"] = games["weekday"].isin(["Saturday", "Sunday"]).astype(int)

# -----------------------------
# 2. Team-level stats (shared with live prediction path)
# -----------------------------
stats = team_stats.build_team_stats(games, teams)

games = games.merge(
    stats.add_prefix("home_"), left_on="home_team", right_index=True, how="left"
)
games = games.merge(
    stats.add_prefix("away_"), left_on="away_team", right_index=True, how="left"
)

games["age_group_diff"] = games["home_age_group_num"] - games["away_age_group_num"]
games["level_diff"] = games["home_level_num"] - games["away_level_num"]

games = games.rename(columns={
    "home_age_group_num": "home_age_group",
    "away_age_group_num": "away_age_group",
    "home_level_num": "home_level",
    "away_level_num": "away_level",
})

# -----------------------------
# 3. Targets + feature matrix
# -----------------------------
y_home = games["home_score"].astype(float)
y_away = games["away_score"].astype(float)
y_win = (games["home_score"] > games["away_score"]).astype(int)

for col in NUMERIC_FEATURES:
    games[col] = pd.to_numeric(games[col], errors="coerce")
    games[col] = games[col].fillna(games[col].mean())

X = games[NUMERIC_FEATURES + CATEGORICAL_FEATURES]

# -----------------------------
# 4. Preprocessing
# -----------------------------
preprocessor = ColumnTransformer(
    transformers=[
        ("num", "passthrough", NUMERIC_FEATURES),
        ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
    ]
)

# Fit on the full dataset so the deployed transformer knows every category
# seen so far; evaluation below uses a held-out split purely for reporting.
X_transformed = preprocessor.fit_transform(X)

# -----------------------------
# 5. Home / away score models
# -----------------------------
Xh_train, Xh_test, yh_train, yh_test = train_test_split(X_transformed, y_home, test_size=0.2, random_state=42)
home_score_model = GradientBoostingRegressor(random_state=42)
home_score_model.fit(Xh_train, yh_train)
print("Home score MAE (holdout):", mean_absolute_error(yh_test, home_score_model.predict(Xh_test)))
home_score_model.fit(X_transformed, y_home)  # refit on all data for the deployed model

Xa_train, Xa_test, ya_train, ya_test = train_test_split(X_transformed, y_away, test_size=0.2, random_state=42)
away_score_model = GradientBoostingRegressor(random_state=42)
away_score_model.fit(Xa_train, ya_train)
print("Away score MAE (holdout):", mean_absolute_error(ya_test, away_score_model.predict(Xa_test)))
away_score_model.fit(X_transformed, y_away)

# -----------------------------
# 6. Win probability model
# -----------------------------
# Same transformed features as the score models -- predict_single_game.py
# calls win_model.predict_proba() on that same X, so the feature sets must
# match (an earlier version of this script trained win_model on a separate,
# smaller feature set, which would break at inference time).
Xw_train, Xw_test, yw_train, yw_test = train_test_split(X_transformed, y_win, test_size=0.2, random_state=42)
win_model = GradientBoostingClassifier(random_state=42)
win_model.fit(Xw_train, yw_train)
print("Win prob ROC-AUC (holdout):", roc_auc_score(yw_test, win_model.predict_proba(Xw_test)[:, 1]))
win_model.fit(X_transformed, y_win)

# -----------------------------
# 7. Persist for predict_single_game.py / monte_carlo_game.py / the backend
# -----------------------------
os.makedirs(MODELS_DIR, exist_ok=True)
joblib.dump(preprocessor, os.path.join(MODELS_DIR, "column_transformer.pkl"))
joblib.dump(home_score_model, os.path.join(MODELS_DIR, "home_score_model.pkl"))
joblib.dump(away_score_model, os.path.join(MODELS_DIR, "away_score_model.pkl"))
joblib.dump(win_model, os.path.join(MODELS_DIR, "win_probability_model.pkl"))
print(f"\nSaved models to {MODELS_DIR}")

# -----------------------------
# 8. Sanity check -- predict one real upcoming matchup
# -----------------------------
if __name__ == "__main__":
    from predict_single_game import predict_single_game

    league_avg_rs = stats["avg_runs_scored"].mean()
    league_avg_ra = stats["avg_runs_allowed"].mean()

    sample_home = games["home_team"].iloc[-1]
    sample_away = games["away_team"].iloc[-1]

    sample_features = team_stats.build_game_features_row(
        home_team=sample_home,
        away_team=sample_away,
        team_stats=stats,
        ballpark="Premier Baseball of Texas",
        format="pool",
        classification="AA",
        bracket_round="pool",
        home_seed=0,
        visitor_seed=0,
        game_date="2026-09-20",
        game_time="6:30 PM",
        league_avg_rs=league_avg_rs,
        league_avg_ra=league_avg_ra,
    )

    print(f"\nSanity check: {sample_home} (home) vs {sample_away} (away)")
    print(predict_single_game(sample_features))
