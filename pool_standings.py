"""
Monte Carlo simulation of full pool-play standings.

Unlike monte_carlo_game.py (which simulates ONE game in isolation many
times), this simulates an entire pool's remaining schedule jointly: each
simulation draws one random outcome per not-yet-played game, computes
standings for every team using the tournament's real tie-break order, and
records where the team of interest finished. Repeating that N times turns
into a distribution over final placements.
"""

import numpy as np

from predict_single_game import predict_single_game

# Same variance assumption as monte_carlo_game.py's single-game simulation.
_SCORE_STD_FACTOR = 0.35


def _score_std(base_score: float) -> float:
    return max(1.0, base_score * _SCORE_STD_FACTOR)


def _record_result(stats: dict, home: str, away: str, home_score: int, away_score: int):
    stats[home]["runs_scored"] += home_score
    stats[home]["runs_allowed"] += away_score
    stats[away]["runs_scored"] += away_score
    stats[away]["runs_allowed"] += home_score

    if home_score > away_score:
        stats[home]["wins"] += 1
        stats[away]["losses"] += 1
    elif away_score > home_score:
        stats[away]["wins"] += 1
        stats[home]["losses"] += 1
    else:
        stats[home]["ties"] += 1
        stats[away]["ties"] += 1


def _win_pct(s: dict) -> float:
    games = s["wins"] + s["losses"] + s["ties"]
    if games == 0:
        return 0.0
    return (s["wins"] + 0.5 * s["ties"]) / games


def compute_current_standings(pool_games: list[dict]) -> dict:
    """
    Actual standings as of right now -- no simulation involved. Completed
    games count for real; every not-yet-played game is treated as a 0-0
    tie rather than predicted/simulated, since this is meant to reflect
    only what's actually happened so far. Same ranking as everywhere else:
    win% (ties count as half a win) -> fewest runs allowed -> most runs
    scored.

    Returns:
    {
      "pool_size": 6,
      "standings": [
        {"rank": 1, "team": "...", "wins": 3, "losses": 0, "ties": 1,
         "runs_scored": 24, "runs_allowed": 9, "win_pct": 0.875},
        ...
      ]
    }
    """
    teams = sorted({g["home_team"] for g in pool_games} | {g["away_team"] for g in pool_games})
    stats = {t: {"wins": 0, "losses": 0, "ties": 0, "runs_scored": 0, "runs_allowed": 0} for t in teams}

    for g in pool_games:
        if g.get("completed"):
            _record_result(stats, g["home_team"], g["away_team"], g["home_score"], g["away_score"])
        else:
            _record_result(stats, g["home_team"], g["away_team"], 0, 0)

    ranked = sorted(
        teams,
        key=lambda t: (-_win_pct(stats[t]), stats[t]["runs_allowed"], -stats[t]["runs_scored"]),
    )

    standings = [
        {
            "rank": i,
            "team": t,
            "wins": stats[t]["wins"],
            "losses": stats[t]["losses"],
            "ties": stats[t]["ties"],
            "runs_scored": stats[t]["runs_scored"],
            "runs_allowed": stats[t]["runs_allowed"],
            "win_pct": round(_win_pct(stats[t]), 3),
        }
        for i, t in enumerate(ranked, 1)
    ]

    return {"pool_size": len(teams), "standings": standings}


def _prepare(pool_games: list[dict], team_of_interest: str):
    """
    Common setup for both simulate_pool_standings and
    explain_placement_scenario: validates the team is in this pool and
    precomputes each pending game's base (mean) prediction once -- the
    random draw around that mean is the only thing that changes per
    simulation, so there's no need to re-run predict_single_game() inside
    the simulation loop.
    """
    teams = sorted({g["home_team"] for g in pool_games} | {g["away_team"] for g in pool_games})

    if team_of_interest not in teams:
        raise ValueError(f"'{team_of_interest}' is not one of this pool's teams: {teams}")

    completed = [g for g in pool_games if g.get("completed")]

    pending = []
    for g in pool_games:
        if g.get("completed"):
            continue
        preds = predict_single_game(g)
        pending.append({
            "home_team": g["home_team"],
            "away_team": g["away_team"],
            "base_home": preds["pred_home_score"],
            "base_away": preds["pred_away_score"],
        })

    return teams, completed, pending


def simulate_pool_standings(pool_games: list[dict], team_of_interest: str, n_sims: int = 2000) -> dict:
    """
    pool_games: one dict per pool-play game --
      completed: {"home_team", "away_team", "home_score", "away_score", "completed": True}
      pending:   {"home_team", "away_team", "completed": False, **feature dict for predict_single_game}

    Ranking, in order: 1) win percentage (ties count as half a win)
    2) fewest runs allowed  3) most runs scored.

    Returns:
    {
      "sims_run": 2000, "team": "...", "pool_size": 6,
      "placements": [{"place": 1, "pct": 34}, {"place": 2, "pct": 28}, ...]
    }
    """
    teams, completed, pending = _prepare(pool_games, team_of_interest)
    pool_size = len(teams)

    placement_counts = {}

    for _ in range(n_sims):
        stats = {t: {"wins": 0, "losses": 0, "ties": 0, "runs_scored": 0, "runs_allowed": 0} for t in teams}

        for g in completed:
            _record_result(stats, g["home_team"], g["away_team"], g["home_score"], g["away_score"])

        for g in pending:
            home_std = _score_std(g["base_home"])
            away_std = _score_std(g["base_away"])
            sim_home = max(0, int(round(np.random.normal(g["base_home"], home_std))))
            sim_away = max(0, int(round(np.random.normal(g["base_away"], away_std))))
            _record_result(stats, g["home_team"], g["away_team"], sim_home, sim_away)

        ranked = sorted(
            teams,
            key=lambda t: (-_win_pct(stats[t]), stats[t]["runs_allowed"], -stats[t]["runs_scored"]),
        )

        place = ranked.index(team_of_interest) + 1
        placement_counts[place] = placement_counts.get(place, 0) + 1

    placements = [
        {"place": p, "pct": round(placement_counts.get(p, 0) / n_sims * 100)}
        for p in range(1, pool_size + 1)
    ]

    return {
        "sims_run": n_sims,
        "team": team_of_interest,
        "pool_size": pool_size,
        "placements": placements,
    }


# A pending game counts as a hard requirement ("Team X must beat Team Y")
# only when one side won it in at least this fraction of the simulation
# runs that actually reached the target placement. Games that split more
# evenly than this aren't decisive on their own and are left out.
_REQUIRED_WIN_THRESHOLD = 0.85

# Below this many matching runs, the conditional stats are too thin to be
# reliable -- still returned, but flagged so the caller can warn about it.
_LOW_CONFIDENCE_MATCHES = 30


def explain_placement_scenario(pool_games: list[dict], team_of_interest: str, target_place: int, n_sims: int = 4000) -> dict:
    """
    "What has to happen" for team_of_interest to land on target_place:
    reruns the same joint pool simulation as simulate_pool_standings, but
    this time keeps only the runs where the team actually finished there,
    and looks at how each pending game went in THOSE runs specifically.

    This is empirically derived from the simulation, not a formal
    guarantee -- it's "what usually has to happen for this outcome",
    based on however many of the n_sims runs actually landed on that
    placement (see low_confidence below).

    Returns:
    {
      "place": 3, "reachable": True, "sims_run": 4000, "sims_matching": 812,
      "match_pct": 20, "low_confidence": False,
      "required_results": [
        {"winner": "Team A", "loser": "Team B", "pct": 94, "involves_team": True},
        ...
      ],
      "run_targets": [
        {"opponent": "Team B", "min_runs_scored": 6, "max_runs_allowed": 4},
        ...
      ]
    }
    """
    teams, completed, pending = _prepare(pool_games, team_of_interest)
    pool_size = len(teams)

    if not (1 <= target_place <= pool_size):
        raise ValueError(f"place must be between 1 and {pool_size}")

    own_game_idx = [i for i, g in enumerate(pending) if team_of_interest in (g["home_team"], g["away_team"])]

    game_home_wins = [0] * len(pending)
    game_totals = [0] * len(pending)
    own_scored = {i: [] for i in own_game_idx}
    own_allowed = {i: [] for i in own_game_idx}
    matching = 0

    for _ in range(n_sims):
        stats = {t: {"wins": 0, "losses": 0, "ties": 0, "runs_scored": 0, "runs_allowed": 0} for t in teams}
        for g in completed:
            _record_result(stats, g["home_team"], g["away_team"], g["home_score"], g["away_score"])

        sim_scores = []
        for g in pending:
            home_std = _score_std(g["base_home"])
            away_std = _score_std(g["base_away"])
            sh = max(0, int(round(np.random.normal(g["base_home"], home_std))))
            sa = max(0, int(round(np.random.normal(g["base_away"], away_std))))
            sim_scores.append((sh, sa))
            _record_result(stats, g["home_team"], g["away_team"], sh, sa)

        ranked = sorted(
            teams,
            key=lambda t: (-_win_pct(stats[t]), stats[t]["runs_allowed"], -stats[t]["runs_scored"]),
        )
        place = ranked.index(team_of_interest) + 1

        if place != target_place:
            continue

        matching += 1
        for i, (sh, sa) in enumerate(sim_scores):
            game_totals[i] += 1
            if sh > sa:
                game_home_wins[i] += 1
            if i in own_game_idx:
                g = pending[i]
                if g["home_team"] == team_of_interest:
                    own_scored[i].append(sh)
                    own_allowed[i].append(sa)
                else:
                    own_scored[i].append(sa)
                    own_allowed[i].append(sh)

    if matching == 0:
        return {
            "place": target_place, "reachable": False, "sims_run": n_sims,
            "sims_matching": 0, "match_pct": 0, "low_confidence": True,
            "required_results": [], "run_targets": [],
        }

    required_results = []
    for i, g in enumerate(pending):
        if game_totals[i] == 0:
            continue
        home_win_rate = game_home_wins[i] / game_totals[i]
        involves_team = team_of_interest in (g["home_team"], g["away_team"])
        if home_win_rate >= _REQUIRED_WIN_THRESHOLD:
            required_results.append({
                "winner": g["home_team"], "loser": g["away_team"],
                "pct": round(home_win_rate * 100), "involves_team": involves_team,
            })
        elif home_win_rate <= 1 - _REQUIRED_WIN_THRESHOLD:
            required_results.append({
                "winner": g["away_team"], "loser": g["home_team"],
                "pct": round((1 - home_win_rate) * 100), "involves_team": involves_team,
            })
    required_results.sort(key=lambda r: (not r["involves_team"], -r["pct"]))

    run_targets = []
    for i in own_game_idx:
        scored, allowed = own_scored[i], own_allowed[i]
        if not scored:
            continue
        g = pending[i]
        opponent = g["away_team"] if g["home_team"] == team_of_interest else g["home_team"]
        run_targets.append({
            "opponent": opponent,
            "min_runs_scored": int(np.percentile(scored, 25)),
            "max_runs_allowed": int(np.percentile(allowed, 75)),
        })

    return {
        "place": target_place,
        "reachable": True,
        "sims_run": n_sims,
        "sims_matching": matching,
        "match_pct": round(matching / n_sims * 100),
        "low_confidence": matching < _LOW_CONFIDENCE_MATCHES,
        "required_results": required_results,
        "run_targets": run_targets,
    }
