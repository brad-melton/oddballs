"""
Pydantic models for request/response bodies.

These shapes intentionally match what frontend/app.js's mock functions
already return (runMockTournamentSim, runMockTeamFocus, runMockMatchup,
generateScoutingReport) — so swapping the frontend from mock data to
real fetch() calls requires no changes to app.js's rendering logic.
"""
from pydantic import BaseModel
from typing import List, Optional


# ---------- Shared ----------

class Team(BaseModel):
    name: str
    c1: str  # primary color, hex
    c2: str  # secondary color, hex


class Tournament(BaseModel):
    id: str
    name: str
    location: Optional[str] = None  # not tracked in the DB yet — safe to leave unset
    teams: List[Team] = []  # not wired up yet — defaults to empty until the teams table exists


# ---------- GET /api/tournaments ----------

class TournamentListResponse(BaseModel):
    tournaments: List[Tournament]


# ---------- POST /api/simulate-tournament (used by the old "whole tournament" view — kept for reference) ----------

class SimulateTournamentRequest(BaseModel):
    tournament_id: str


class StandingRow(BaseModel):
    name: str
    c1: str
    c2: str
    winPct: int
    record: str


class SimulateTournamentResponse(BaseModel):
    simsRun: int
    standings: List[StandingRow]


# ---------- Pool play / bracket play workflow ----------
# Same three request/response shapes are reused for both phases —
# app.py exposes them at /api/pool-play/... and /api/bracket-play/...

class Game(BaseModel):
    id: str
    team_a: str
    team_b: str
    field: Optional[str] = None
    scheduled_time: Optional[str] = None
    status: str = "scheduled"  # "scheduled" | "completed"
    score_a: Optional[int] = None
    score_b: Optional[int] = None
    bracket_name: Optional[str] = None  # e.g. "Gold Bracket" -- only set for bracket-play games
    round_name: Optional[str] = None    # e.g. "Quarterfinals" -- only set for bracket-play games


class PopulateGamesRequest(BaseModel):
    tournament_id: str
    date: Optional[str] = None  # only meaningful once needs_date_selection has been returned once; pass the user's chosen date back here


class PopulateGamesResponse(BaseModel):
    already_populated: bool  # True if games existed already and nothing new was created
    games: List[Game]
    needs_date_selection: bool = False  # True means: don't scrape yet, ask the user which date, then call again with `date` set
    available_dates: List[str] = []  # populated only when needs_date_selection is True


class PredictGamesRequest(BaseModel):
    tournament_id: str


class GamePrediction(BaseModel):
    game_id: str
    team_a: str
    team_b: str
    win_pct_a: int
    win_pct_b: int
    is_final: bool = False           # True if this game already has a real score
    score_a: Optional[int] = None    # set only when is_final
    score_b: Optional[int] = None    # set only when is_final


class PredictGamesResponse(BaseModel):
    sims_run: int
    predictions: List[GamePrediction]


class ScoreEntry(BaseModel):
    game_id: str
    score_a: int
    score_b: int


class UpdateScoresRequest(BaseModel):
    tournament_id: str
    scores: List[ScoreEntry]


class UpdateScoresResponse(BaseModel):
    updated: int


# ---------- Current (actual) pool standings ----------

class CurrentStandingsRequest(BaseModel):
    tournament_id: str


class TeamStanding(BaseModel):
    rank: int
    team: str
    wins: int
    losses: int
    ties: int
    runs_scored: int
    runs_allowed: int
    win_pct: float


class CurrentStandingsResponse(BaseModel):
    pool_size: int
    standings: List[TeamStanding]


# ---------- Pool standings odds (simulated) ----------

class PoolStandingsRequest(BaseModel):
    tournament_id: str
    team_name: str


class PlacementOdds(BaseModel):
    place: int  # 1 = first place, 2 = second, ...
    pct: int    # percent of simulated pools where the team finished here
    # Field/time of that seed's first bracket-play game, when known (the
    # schedule still shows a raw "Seed #N" placeholder there) -- None once
    # that slot's game has actually been played and resolved to a team name.
    # next_bracket_name distinguishes which flight (e.g. "Gold Bracket" vs
    # "Silver Bracket") that seed lands in, for tournaments that split into
    # more than one bracket out of the same pool.
    next_bracket_name: Optional[str] = None
    next_ballpark: Optional[str] = None
    next_field: Optional[str] = None
    next_date: Optional[str] = None
    next_time: Optional[str] = None


class PoolStandingsResponse(BaseModel):
    sims_run: int
    team: str
    pool_size: int
    placements: List[PlacementOdds]


# ---------- "What has to happen for this placement?" ----------

class ExplainPlacementRequest(BaseModel):
    tournament_id: str
    team_name: str
    place: int  # which finish to explain, e.g. 3 for "3rd place"


class RequiredResult(BaseModel):
    winner: str
    loser: str
    pct: int              # how often the winner won this game, among sims reaching the target place
    involves_team: bool    # True if team_name is one of the two sides


class RunTarget(BaseModel):
    opponent: str
    min_runs_scored: int
    max_runs_allowed: int


class ExplainPlacementResponse(BaseModel):
    place: int
    reachable: bool
    sims_run: int
    sims_matching: int
    match_pct: int
    low_confidence: bool
    required_results: List[RequiredResult]
    run_targets: List[RunTarget]


# ---------- "Why this prediction?" ----------

class ExplainGameRequest(BaseModel):
    tournament_id: str
    game_id: str


class PredictionFactor(BaseModel):
    feature: str      # human-readable feature name, e.g. "Home avg. runs scored"
    value: str         # this game's actual value for that feature, e.g. "6.2"
    weight_pct: int    # relative importance among the shown factors (sums to ~100)


class ExplainGameResponse(BaseModel):
    game_id: str
    team_a: str
    team_b: str
    pred_home_score: int
    pred_away_score: int
    pred_win_probability: int  # team_a's win %, for context
    factors: List[PredictionFactor]


# ---------- Admin monitoring ----------

class EventStatus(BaseModel):
    eventid: str
    name: str
    status: Optional[str] = None
    classification: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    total_games: int
    completed_games: int
    pending_games: int
    pool_games: int
    bracket_games: int


class AdminStatusResponse(BaseModel):
    events: List[EventStatus]
    total_events: int
    total_games: int
    total_completed_games: int
    total_teams: int


class UpdateEventStatusRequest(BaseModel):
    status: str


class UpdateEventStatusResponse(BaseModel):
    eventid: str
    status: str


# ---------- Scouting report (Phase A -- see gamechanger_scrape.py) ----------

class ScoutingTeamSearchResult(BaseModel):
    team_key: int
    team_name: str
    gc_linked: bool
    last_scraped: Optional[str] = None


class TeamScoutingRosterRow(BaseModel):
    player_name: str
    games_played: int
    avg: Optional[float] = None
    hr: int
    rbi: int
    ip: Optional[str] = None
    era: Optional[float] = None
    so_pitching: int


class TeamScoutingReportResponse(BaseModel):
    team_key: int
    team_name: str
    last_scraped: Optional[str] = None
    roster: List[TeamScoutingRosterRow]


class PlayerBattingLine(BaseModel):
    game_date: Optional[str] = None
    opponent: Optional[str] = None
    ab: int
    r: int
    h: int
    doubles: int
    triples: int
    hr: int
    rbi: int
    bb: int
    so: int
    sb: int


class PlayerPitchingLine(BaseModel):
    game_date: Optional[str] = None
    opponent: Optional[str] = None
    ip: str
    h: int
    r: int
    er: int
    bb: int
    so: int
    pitches: Optional[int] = None


class PlayerFieldingLine(BaseModel):
    game_date: Optional[str] = None
    opponent: Optional[str] = None
    errors: int


class PlayerScoutingProfileResponse(BaseModel):
    player_name: str
    jersey_number: Optional[str] = None
    batting_log: List[PlayerBattingLine]
    batting_totals: dict
    pitching_log: List[PlayerPitchingLine]
    pitching_totals: dict
    fielding_log: List[PlayerFieldingLine]


class MapGcTeamRequest(BaseModel):
    pg_team_key: int
    gc_team_id: Optional[str] = None
    gc_url: Optional[str] = None


class MapGcTeamResponse(BaseModel):
    gc_team_id: str
    pg_team_key: int


class GcTeamMapping(BaseModel):
    gc_team_id: str
    gc_team_name: Optional[str] = None
    gc_url: Optional[str] = None
    pg_team_key: Optional[int] = None
    pg_team_name: Optional[str] = None
    last_scraped: Optional[str] = None


class GcTeamMappingListResponse(BaseModel):
    mappings: List[GcTeamMapping]