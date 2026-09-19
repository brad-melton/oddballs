import sqlite3
import pandas as pd
from datetime import datetime, timedelta

# -------------------------------------------------------------------
# Utility: Determine upcoming weekend
# -------------------------------------------------------------------

def get_upcoming_weekend():
    today = datetime.today()
    # Find next Saturday
    days_until_sat = (5 - today.weekday()) % 7
    saturday = today + timedelta(days=days_until_sat)
    sunday = saturday + timedelta(days=1)
    return saturday.date(), sunday.date()


# -------------------------------------------------------------------
# Scraper stub (replace with your actual scraping logic)
# -------------------------------------------------------------------

def scrape_event_schedule(eventid):
    """
    Replace this stub with your actual scraping logic.
    Should return a list of dicts like:

    [
        {
            "gameid": "12345",
            "eventid": eventid,
            "datetime": "2026-07-18 09:00",
            "field": "Field 3",
            "home_team": "Texas Heat",
            "away_team": "Houston Eagles",
            "bracket_round": "Pool"
        },
        ...
    ]
    """
    raise NotImplementedError("Scraper not implemented yet.")


# -------------------------------------------------------------------
# Insert team if not exists
# -------------------------------------------------------------------

def ensure_team_exists(conn, team_name):
    cur = conn.cursor()
    cur.execute("SELECT teamid FROM teams WHERE name = ?", (team_name,))
    row = cur.fetchone()

    if row:
        return row[0]  # existing teamid

    # Insert new team
    cur.execute("INSERT INTO teams (name) VALUES (?)", (team_name,))
    conn.commit()
    return cur.lastrowid


# -------------------------------------------------------------------
# Insert game into database
# -------------------------------------------------------------------

def insert_game(conn, game):
    cur = conn.cursor()

    # Ensure teams exist
    home_team_id = ensure_team_exists(conn, game["home_team"])
    away_team_id = ensure_team_exists(conn, game["away_team"])

    cur.execute("""
        INSERT OR IGNORE INTO games (
            gameid, eventid, datetime, field,
            home_team_id, away_team_id, bracket_round
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        game["gameid"],
        game["eventid"],
        game["datetime"],
        game["field"],
        home_team_id,
        away_team_id,
        game["bracket_round"]
    ))

    conn.commit()


# -------------------------------------------------------------------
# Main function: Update schedules for upcoming weekend
# -------------------------------------------------------------------

def update_schedules_for_upcoming_weekend(db_path="games.db"):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    saturday, sunday = get_upcoming_weekend()

    print(f"Checking events scheduled for {saturday}–{sunday}")

    # Query events with status = 'Scheduled' and occurring this weekend
    events_df = pd.read_sql_query("""
        SELECT *
        FROM events
        WHERE status = 'Scheduled'
        AND start_date <= ?
        AND end_date >= ?
    """, conn, params=(sunday.isoformat(), saturday.isoformat()))

    if events_df.empty:
        print("No scheduled events for the upcoming weekend.")
        conn.close()
        return

    for _, event in events_df.iterrows():
        eventid = event["eventid"]
        print(f"Scraping schedule for event {eventid}...")

        try:
            schedule = scrape_event_schedule(eventid)
        except NotImplementedError:
            print("Scraper not implemented. Skipping.")
            continue

        for game in schedule:
            insert_game(conn, game)

        print(f"Inserted {len(schedule)} games for event {eventid}.")

    conn.close()
    print("Schedule update complete.")
