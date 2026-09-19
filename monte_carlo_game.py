import numpy as np
import pandas as pd

from predict_single_game import predict_single_game


# -------------------------------------------------------------------
# Monte Carlo Simulation for a Single Game
# -------------------------------------------------------------------
# This module:
# - Calls the single-game prediction engine
# - Adds realistic variance around predicted scores
# - Runs N simulations
# - Ensures integer scores
# - Prevents ties in bracket games
# - Returns distributions and probabilities
# -------------------------------------------------------------------


def monte_carlo_single_game(game_features: dict, n_sims: int = 10000) -> dict:
    """
    Run Monte Carlo simulations for a single game.

    Parameters
    ----------
    game_features : dict
        Same feature dictionary used by predict_single_game().
    n_sims : int
        Number of Monte Carlo simulations.

    Returns
    -------
    dict
        {
            "home_win_probability": float,
            "away_win_probability": float,
            "tie_probability": float (pool only),
            "home_score_distribution": list,
            "away_score_distribution": list,
            "score_diff_distribution": list
        }
    """

    # Get base predictions
    base_preds = predict_single_game(game_features)

    base_home = base_preds["pred_home_score"]
    base_away = base_preds["pred_away_score"]
    base_win_prob = base_preds["pred_win_probability"]

    bracket_round = game_features.get("bracket_round", "").lower()
    bracket_game = bracket_round != "pool"

    # Variance assumptions (tunable)
    # Youth baseball scoring variance is high — Poisson-like but noisy
    home_std = max(1.0, base_home * 0.35)
    away_std = max(1.0, base_away * 0.35)

    home_scores = []
    away_scores = []
    score_diffs = []

    home_wins = 0
    away_wins = 0
    ties = 0

    for _ in range(n_sims):

        # Draw random scores from normal distribution
        sim_home = int(round(np.random.normal(base_home, home_std)))
        sim_away = int(round(np.random.normal(base_away, away_std)))

        # Prevent negative scores
        sim_home = max(sim_home, 0)
        sim_away = max(sim_away, 0)

        # Prevent ties in bracket games
        if bracket_game and sim_home == sim_away:
            if base_win_prob >= 0.5:
                sim_home += 1
            else:
                sim_away += 1

        # Track outcomes
        if sim_home > sim_away:
            home_wins += 1
        elif sim_away > sim_home:
            away_wins += 1
        else:
            ties += 1  # only possible in pool play

        home_scores.append(sim_home)
        away_scores.append(sim_away)
        score_diffs.append(sim_home - sim_away)

    # Compute probabilities
    home_win_prob = home_wins / n_sims
    away_win_prob = away_wins / n_sims
    tie_prob = ties / n_sims if not bracket_game else 0.0

    return {
        "home_win_probability": home_win_prob,
        "away_win_probability": away_win_prob,
        "tie_probability": tie_prob,
        "home_score_distribution": home_scores,
        "away_score_distribution": away_scores,
        "score_diff_distribution": score_diffs,
        "base_predictions": base_preds
    }


# -------------------------------------------------------------------
# Optional quick test
# -------------------------------------------------------------------
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
        "bracket_round": "Pool"
    }

    results = monte_carlo_single_game(sample_game, n_sims=5000)
    print(results)
