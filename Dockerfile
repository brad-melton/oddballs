# Diamond Odds backend -- FastAPI app in backend/, plus the sibling
# prediction/scraping modules it imports from the project root at runtime
# (see backend/simulation.py's sys.path insert, and backend/db.py's
# docstring for why Turso support lives behind an env-var switch rather
# than always being required).
FROM python:3.12-slim

# Chrome for Selenium (backend/simulation.py's on-demand pool-play scrape,
# _fetch_schedule_html). Render's containers are plain x86_64 Linux, so the
# amd64 .deb is the right one regardless of what architecture this image is
# built on. Selenium 4.6+'s Selenium Manager resolves a matching
# chromedriver automatically at runtime -- no separate chromedriver install.
RUN apt-get update && apt-get install -y --no-install-recommends wget ca-certificates \
    && wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get install -y --no-install-recommends /tmp/chrome.deb \
    && rm /tmp/chrome.deb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Sibling modules backend/simulation.py imports via sys.path.
COPY team_stats.py predict_single_game.py monte_carlo_game.py load_models.py pool_standings.py ./
COPY *.pkl ./models/
COPY backend/ backend/

# Render sets PORT itself at runtime; the default here only matters for
# running the image locally/elsewhere.
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "cd backend && uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
