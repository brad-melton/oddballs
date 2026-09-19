import numpy as np
import pandas as pd

from load_models import (
    load_home_score_model,
    load_away_score_model,
    load_win_probability_model,
    load_column_transformer
)

def prepare_features(game_dict: dict) -> pd.DataFrame:
    return pd.DataFrame([game_dict])


def predict_single_game(game_features: dict) -> dict:
    """
    Predict home/away scores and win probability for a single game.
    Scores are returned as integers.
    Bracket games cannot end in ties.
    """

    # Load trained models and transformer
    home_model = load_home_score_model()
    away_model = load_away_score_model()
    win_model = load_win_probability_model()
    transformer = load_column_transformer()

    # Prepare DataFrame
    df = prepare_features(game_features)

    # Apply preprocessing
    X = transformer.transform(df)

    # Predict scores (floats)
    pred_home = home_model.predict(X)[0]
    pred_away = away_model.predict(X)[0]

    # Convert to integers
    pred_home_int = int(round(pred_home))
    pred_away_int = int(round(pred_away))

    # Score differential (float)
    pred_diff = pred_home - pred_away

    # Win probability
    pred_win_prob = win_model.predict_proba(X)[0][1]

    # Prevent ties in bracket games
    bracket_round = game_features.get("bracket_round", "").lower()

    if bracket_round != "pool":
        # Bracket game → no ties allowed
        if pred_home_int == pred_away_int:
            if pred_win_prob >= 0.5:
                pred_home_int += 1
            else:
                pred_away_int += 1

    return {
        "pred_home_score": pred_home_int,
        "pred_away_score": pred_away_int,
        "pred_score_diff": float(pred_diff),
        "pred_win_probability": float(pred_win_prob)
    }


if __name__ == "__main__":
    sample_game = {
        "home_avg_runs_scored": 6.2,
        "home_avg_runs_allowed": 4.1,
        "away_avg_runs_scored": 5.8,
        "away_avg_runs_allowed": 3.9,
        "home_seed": 3,
        "visitor_seed": 7,
        "age_group_diff": 0,
        "level_diff": 1,
        "ballpark": "Premier",
        "classification": "AA",
        "bracket_round": "Bracket"
    }

    preds = predict_single_game(sample_game)
    print(preds)
