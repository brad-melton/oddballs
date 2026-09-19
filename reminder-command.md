# Running the app locally

Backend and frontend are two separate pieces that both need to be running at
the same time. They use **separate virtual environments** — don't run the
backend out of the root `.venv` (that one's for the Playwright scraping
pipeline, `fall2026catchupscoring.py`).

## Backend (FastAPI, port 8000)

```
cd backend
.venv\Scripts\python.exe -m uvicorn app:app --reload
```

First time only (already done once):
```
cd backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Frontend (static site, port 5500)

```
cd frontend
python -m http.server 5500
```
Then open http://localhost:5500 in a browser. It calls the backend at
`http://localhost:8000` (see `API_BASE` in `frontend/app.js`), so the backend
must already be running.

## Why "No module named 'fastapi'" happened

`uvicorn app:app --reload` was run with whatever Python was on PATH, which
never had fastapi/uvicorn/pandas installed. Fix: always run uvicorn through
`backend\.venv\Scripts\python.exe -m uvicorn ...` (or activate that venv
first with `backend\.venv\Scripts\activate`), not a bare `uvicorn` command.

## Retraining the prediction models

`predict_single_game.py` / `monte_carlo_game.py` load 4 files (score models,
win-probability model, column transformer) from wherever `MODEL_DIR` points —
on this machine that's a Windows **user environment variable** set to the
project root itself (`C:\Users\bsmel\.vscode\PerfectGame`), not a `models\`
subfolder. `prediction model.py` (the trainer) reads that same `MODEL_DIR`
via `load_models.py`, so training and loading always agree on where the
files live without either one needing to be edited.

Re-run this any time new results have been scraped in, to pick up the
latest data (uses scikit-learn, which lives in the backend venv):

```
backend\.venv\Scripts\python.exe "prediction model.py"
```

It prints holdout MAE/ROC-AUC for a sanity check, then overwrites the 4
`.pkl` files in place. The running backend doesn't need a restart — each
`/api/{phase}/predict` call loads the models fresh.
