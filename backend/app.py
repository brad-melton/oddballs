from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import simulation
from models import (
    TournamentListResponse,
    PopulateGamesRequest, PopulateGamesResponse,
    PredictGamesRequest, PredictGamesResponse,
    UpdateScoresRequest, UpdateScoresResponse,
    CurrentStandingsRequest, CurrentStandingsResponse,
    PoolStandingsRequest, PoolStandingsResponse,
    ExplainGameRequest, ExplainGameResponse,
    ExplainPlacementRequest, ExplainPlacementResponse,
    AdminStatusResponse,
    UpdateEventStatusRequest, UpdateEventStatusResponse,
)

app = FastAPI(title="Diamond Odds API")

VALID_PHASES = ("pool-play", "bracket-play")

# CORS only checks scheme+host+port, not path, so this covers GitHub Pages
# whether it ends up serving from the root or a /oddballs subpath.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://brad-melton.github.io",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _check_phase(phase: str):
    if phase not in VALID_PHASES:
        raise HTTPException(status_code=404, detail=f"Unknown phase '{phase}', expected one of {VALID_PHASES}")


@app.get("/health")
def health():
    """Render (and you, while testing) can hit this to confirm the API is up."""
    return {"status": "ok"}


@app.get("/api/tournaments", response_model=TournamentListResponse)
def get_tournaments():
    try:
        tournaments = simulation.load_tournaments()
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    return {"tournaments": tournaments}


@app.post("/api/{phase}/populate", response_model=PopulateGamesResponse)
def populate_games(phase: str, req: PopulateGamesRequest):
    _check_phase(phase)
    try:
        return simulation.populate_games(phase, req.tournament_id, req.date)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))


@app.post("/api/{phase}/predict", response_model=PredictGamesResponse)
def predict_games(phase: str, req: PredictGamesRequest):
    _check_phase(phase)
    try:
        return simulation.predict_games(phase, req.tournament_id)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))


@app.post("/api/{phase}/scores", response_model=UpdateScoresResponse)
def update_scores(phase: str, req: UpdateScoresRequest):
    _check_phase(phase)
    try:
        scores = [s.model_dump() for s in req.scores]
        return simulation.update_scores(phase, req.tournament_id, scores)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))


@app.post("/api/{phase}/standings/current", response_model=CurrentStandingsResponse)
def current_standings(phase: str, req: CurrentStandingsRequest):
    _check_phase(phase)
    try:
        return simulation.get_current_pool_standings(phase, req.tournament_id)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/{phase}/standings", response_model=PoolStandingsResponse)
def pool_standings(phase: str, req: PoolStandingsRequest):
    _check_phase(phase)
    try:
        return simulation.predict_pool_standings(phase, req.tournament_id, req.team_name)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/{phase}/explain", response_model=ExplainGameResponse)
def explain_game(phase: str, req: ExplainGameRequest):
    _check_phase(phase)
    try:
        return simulation.explain_game(phase, req.tournament_id, req.game_id)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/{phase}/standings/explain", response_model=ExplainPlacementResponse)
def explain_placement(phase: str, req: ExplainPlacementRequest):
    _check_phase(phase)
    try:
        return simulation.explain_pool_placement(phase, req.tournament_id, req.team_name, req.place)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/admin/status", response_model=AdminStatusResponse)
def admin_status():
    return simulation.get_admin_status()


@app.post("/api/admin/events/{eventid}/status", response_model=UpdateEventStatusResponse)
def admin_update_event_status(eventid: str, req: UpdateEventStatusRequest):
    try:
        return simulation.update_event_status(eventid, req.status)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))