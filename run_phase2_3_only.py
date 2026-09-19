"""
One-off runner: executes only Phase 2 (bracket enrichment) and Phase 3 (team
enrichment / team-key population) against the existing games table, without
re-running Phase 1's scrape. Phase 1 already produced correct rows for this
pipeline's events in the prior run; re-running it here would duplicate them
since insert_games_to_db has no dedup.
"""

import fall2026catchupscoring as pipeline

try:
    pipeline.phase_2_enrich_brackets()
    pipeline.phase_3a_enrich_teams()
    pipeline.phase_3b_populate_team_keys()
finally:
    try:
        pipeline.page.context.close()
    except Exception as e:
        print(f"Error closing page context: {e}")
    try:
        if pipeline.browser is not None:
            pipeline.browser.close()
    except Exception as e:
        print(f"Error closing browser: {e}")
    try:
        pipeline.playwright.stop()
    except Exception as e:
        print(f"Error stopping Playwright: {e}")
