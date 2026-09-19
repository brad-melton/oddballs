import os
import joblib

# -------------------------------------------------------------------
# Model Loader Module
# -------------------------------------------------------------------
# This module centralizes loading of all trained ML assets:
# - Home score model
# - Away score model
# - Win probability model
# - Column transformer / encoder
#
# These models are trained offline and saved as .pkl files.
# This loader is used by prediction and Monte Carlo modules.
# -------------------------------------------------------------------

# Default model directory (can be overridden by environment variable).
# Resolved relative to this file, not the process's cwd, so it works the
# same whether this is imported from the project root or from backend/.
_DEFAULT_MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
MODEL_DIR = os.getenv("MODEL_DIR", _DEFAULT_MODEL_DIR)


def _load(path: str):
    """Internal helper to load a joblib file."""
    full_path = os.path.join(MODEL_DIR, path)
    if not os.path.exists(full_path):
        raise FileNotFoundError(f"Model file not found: {full_path}")
    return joblib.load(full_path)


def load_home_score_model():
    """Load trained model predicting home team runs."""
    return _load("home_score_model.pkl")


def load_away_score_model():
    """Load trained model predicting away team runs."""
    return _load("away_score_model.pkl")


def load_win_probability_model():
    """Load trained model predicting win probability."""
    return _load("win_probability_model.pkl")


def load_column_transformer():
    """Load the fitted ColumnTransformer used for preprocessing."""
    return _load("column_transformer.pkl")


def load_encoder():
    """Load the fitted OneHotEncoder (if saved separately)."""
    return _load("encoder.pkl")


def load_all_models():
    """
    Convenience function to load everything at once.
    Useful for Monte Carlo modules that need all assets.
    """
    return {
        "home_model": load_home_score_model(),
        "away_model": load_away_score_model(),
        "win_model": load_win_probability_model(),
        "transformer": load_column_transformer(),
        "encoder": load_encoder(),
    }
