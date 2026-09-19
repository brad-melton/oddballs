/* ============================================================
   Tournament data loads from the backend (GET /api/tournaments).
   Pool play / bracket play games, predictions, and score updates
   all hit real endpoints too — see populate/predict/saveScores
   below.
   ============================================================ */
// Local dev: run backend/app.py locally and point this at
// http://localhost:8000 instead.
const API_BASE = 'https://oddballs.onrender.com';

let TOURNAMENTS = [];
let selectedTournament = null;
let phase = 'pool-play'; // 'pool-play' | 'bracket-play'
let currentGames = [];   // games returned by the most recent populate/predict call, used by the scores form
let selectedStandingsTeam = null; // team the current Pool Standings result is for, used by the placement-scenario modal

/* ---------- generic dropdown ---------- */
function setupDropdown(inputEl, listEl, items, renderItem, onSelect) {
  function render(filter) {
    const f = filter.trim().toLowerCase();
    const matches = items.filter(it => it._search.includes(f));
    listEl.innerHTML = '';
    if (matches.length === 0) {
      listEl.innerHTML = '<div class="dropdown-empty">No matches</div>';
    } else {
      matches.forEach(it => {
        const row = document.createElement('div');
        row.className = 'dropdown-item';
        row.innerHTML = renderItem(it);
        row.addEventListener('click', () => {
          inputEl.value = it._label;
          listEl.classList.remove('open');
          onSelect(it);
        });
        listEl.appendChild(row);
      });
    }
    listEl.classList.add('open');
  }
  inputEl.addEventListener('focus', () => render(inputEl.value));
  inputEl.addEventListener('input', () => render(inputEl.value));
  document.addEventListener('click', (e) => {
    if (!inputEl.contains(e.target) && !listEl.contains(e.target)) listEl.classList.remove('open');
  });
}

/* ---------- tournament dropdown ---------- */
let tournamentItems = [];

function setupTournamentDropdown() {
  tournamentItems = TOURNAMENTS.map(t => ({ ...t, _label: t.name, _search: (t.name + ' ' + (t.location || '')).toLowerCase() }));
  setupDropdown(
    document.getElementById('tournamentInput'), document.getElementById('tournamentList'), tournamentItems,
    (t) => `<span>${t.name}</span>${t.location ? `<span class="meta">${t.location}</span>` : ''}`,
    (t) => {
      selectedTournament = t;
      document.getElementById('phaseCard').style.display = 'block';
      document.getElementById('actionCard').style.display = 'block';
      resetResults();
    }
  );
}

async function loadTournaments() {
  const input = document.getElementById('tournamentInput');
  input.disabled = true;
  input.placeholder = 'Loading tournaments…';
  try {
    const res = await fetch(`${API_BASE}/api/tournaments`);
    if (!res.ok) throw new Error(`Server responded ${res.status}`);
    const data = await res.json();
    TOURNAMENTS = data.tournaments;
    input.disabled = false;
    input.placeholder = 'Search tournaments…';
    setupTournamentDropdown();
  } catch (err) {
    input.placeholder = 'Failed to load tournaments';
    document.getElementById('emptyStateText').innerHTML =
      `Couldn't load tournaments from the backend (${err.message}). Is the API running at <code>${API_BASE}</code>?`;
    console.error('Failed to load tournaments:', err);
  }
}

/* ---------- phase tabs ---------- */
document.getElementById('phaseTabs').addEventListener('click', (e) => {
  const tab = e.target.closest('.mode-tab');
  if (!tab) return;
  document.querySelectorAll('#phaseTabs .mode-tab').forEach(t => t.classList.remove('active'));
  tab.classList.add('active');
  phase = tab.dataset.phase;
  document.querySelectorAll('.action-btn').forEach(b => b.classList.remove('active'));
  updateStandingsVisibility();
  resetResults();
});

// Standings only make sense for pool play (bracket play doesn't have a
// round-robin table to rank), so hide that action entirely otherwise.
function updateStandingsVisibility() {
  document.getElementById('standingsBtn').style.display = phase === 'pool-play' ? '' : 'none';
}

function resetResults() {
  document.getElementById('results').style.display = 'none';
  document.getElementById('emptyState').style.display = 'block';
  const phaseLabel = phase === 'pool-play' ? 'pool play' : 'bracket play';
  document.getElementById('emptyStateText').textContent = `Choose an action below to work with ${phaseLabel} for ${selectedTournament.name}.`;
  document.getElementById('simStatus').textContent = '';
  currentGames = [];
  activeBracketName = null;
}

/* ---------- action buttons ---------- */
const simStatus = document.getElementById('simStatus');
const STATUS_MESSAGES = {
  populate: ['Checking existing schedule…', 'Generating matchups…'],
  predict: ['Running Monte Carlo trials…', 'Scoring game outcomes…'],
  standings: ['Simulating remaining pool games…', 'Tallying final standings…'],
};

document.querySelectorAll('.action-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    if (!selectedTournament) return;
    document.querySelectorAll('.action-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const action = btn.dataset.action;

    if (action === 'populate') await runPopulate();
    else if (action === 'predict') await runPredict();
    else if (action === 'scores') await showScoreForm();
    else if (action === 'standings') await runStandings();
  });
});

function withStatusCycle(messages, fn) {
  let i = 0;
  simStatus.textContent = messages[0];
  const interval = setInterval(() => { i = (i + 1) % messages.length; simStatus.textContent = messages[i]; }, 400);
  return fn().finally(() => clearInterval(interval));
}

async function apiPost(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `Server responded ${res.status}`;
    try { const errBody = await res.json(); if (errBody.detail) detail = errBody.detail; } catch {}
    throw new Error(detail);
  }
  return res.json();
}

function showError(message) {
  document.getElementById('emptyState').style.display = 'block';
  document.getElementById('results').style.display = 'none';
  document.getElementById('emptyStateText').textContent = message;
}

/* ---------- Populate ---------- */
async function runPopulate(chosenDate = null) {
  try {
    const body = { tournament_id: selectedTournament.id };
    if (chosenDate) body.date = chosenDate;
    const data = await withStatusCycle(STATUS_MESSAGES.populate, () =>
      apiPost(`/api/${phase}/populate`, body)
    );

    if (data.needs_date_selection) {
      simStatus.textContent = '';
      renderDatePicker(data.available_dates);
      return;
    }

    currentGames = data.games;

    if (phase === 'bracket-play') {
      simStatus.textContent = data.games.length
        ? `Showing ${data.games.length} bracket game${data.games.length === 1 ? '' : 's'}.`
        : 'No bracket games found yet for this tournament.';
      renderBracketView(data.games);
      return;
    }

    simStatus.textContent = data.already_populated
      ? `Already populated — showing ${data.games.length} existing games.`
      : `Created ${data.games.length} games.`;
    renderGameList(data.games, { showMeta: true });
  } catch (err) {
    simStatus.textContent = '';
    showError(`Couldn't populate games (${err.message}).`);
  }
}

/* ---------- Bracket Play visual bracket ---------- */
let activeBracketName = null;

function groupBracketGames(games) {
  // bracketName -> (roundName -> games[]); games arrive from the backend
  // already sorted by game_date/game_time, so round order falls out of
  // insertion order for free -- no date parsing needed here.
  const brackets = new Map();
  for (const g of games) {
    const bracketKey = g.bracket_name || 'Bracket';
    const roundKey = g.round_name || 'Round';
    if (!brackets.has(bracketKey)) brackets.set(bracketKey, new Map());
    const rounds = brackets.get(bracketKey);
    if (!rounds.has(roundKey)) rounds.set(roundKey, []);
    rounds.get(roundKey).push(g);
  }
  return brackets;
}

function renderBracketGameCard(g) {
  const sides = ['a', 'b'].map(side => {
    const name = g[`team_${side}`];
    const score = g[`score_${side}`];
    const isWinner = g.status === 'completed' && (
      side === 'a' ? g.score_a > g.score_b : g.score_b > g.score_a
    );
    return `
      <div class="bracket-game-team${isWinner ? ' bracket-team-winner' : ''}">
        <span class="bracket-team-name">${name}</span>
        ${g.status === 'completed' ? `<span class="bracket-team-score">${score}</span>` : ''}
      </div>
    `;
  }).join('');
  const meta = [g.field, g.scheduled_time].filter(Boolean).join(' · ');
  return `
    <div class="bracket-game-card${g.status === 'completed' ? ' bracket-game-final' : ''}">
      ${sides}
      ${meta ? `<div class="bracket-game-meta">${meta}</div>` : ''}
    </div>
  `;
}

function renderBracketView(games) {
  if (games.length === 0) {
    showResults(`<p class="placeholder-note">No bracket games yet — bracket play is normally scraped alongside pool play once the event's underway.</p>`);
    return;
  }

  const brackets = groupBracketGames(games);
  const bracketNames = [...brackets.keys()];
  if (!activeBracketName || !brackets.has(activeBracketName)) {
    activeBracketName = bracketNames[0];
  }
  const rounds = brackets.get(activeBracketName);
  const roundNames = [...rounds.keys()];

  showResults(`
    <p class="card-label">${selectedTournament.name} — Bracket Play</p>
    ${bracketNames.length > 1 ? `
      <div class="bracket-tabs">
        ${bracketNames.map(name => `
          <button class="bracket-tab${name === activeBracketName ? ' active' : ''}" data-bracket="${name}">${name}</button>
        `).join('')}
      </div>
    ` : ''}
    <div class="bracket-scroll">
      ${roundNames.map(roundName => `
        <div class="bracket-round-col">
          <p class="bracket-round-title">${roundName}</p>
          ${rounds.get(roundName).map(renderBracketGameCard).join('')}
        </div>
      `).join('')}
    </div>
  `);

  document.querySelectorAll('.bracket-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      activeBracketName = tab.dataset.bracket;
      renderBracketView(games);
    });
  });
}

function renderDatePicker(dates) {
  showResults(`
    <p class="card-label">Which date is Pool Play?</p>
    <p class="placeholder-note" style="text-align:left; padding:0 0 14px;">${selectedTournament.name} spans more than two days — pick the date pool play games should be scraped from.</p>
    <div class="game-list" id="dateChoices">
      ${dates.map(d => `
        <label class="game-row" style="cursor:pointer;">
          <input type="radio" name="poolPlayDate" value="${d}" style="margin-right:10px;">
          ${d}
        </label>
      `).join('')}
    </div>
    <button class="save-scores-btn" id="confirmDateBtn">Populate for Selected Date</button>
  `);
  document.getElementById('confirmDateBtn').addEventListener('click', () => {
    const chosen = document.querySelector('input[name="poolPlayDate"]:checked');
    if (!chosen) { simStatus.textContent = 'Pick a date first.'; return; }
    runPopulate(chosen.value);
  });
}

/* ---------- Predict ---------- */
async function runPredict() {
  try {
    const data = await withStatusCycle(STATUS_MESSAGES.predict, () =>
      apiPost(`/api/${phase}/predict`, { tournament_id: selectedTournament.id })
    );
    const finalCount = data.predictions.filter(p => p.is_final).length;
    const pendingCount = data.predictions.length - finalCount;
    simStatus.textContent = `Done — ${data.sims_run.toLocaleString()} simulations across ${pendingCount} pending game${pendingCount === 1 ? '' : 's'}` +
      (finalCount ? ` (${finalCount} final).` : '.');
    renderPredictionList(data.predictions);
  } catch (err) {
    simStatus.textContent = '';
    showError(`Couldn't run predictions (${err.message}).`);
  }
}

/* ---------- Pool Standings ---------- */
async function runStandings() {
  try {
    simStatus.textContent = 'Loading current standings…';
    const data = await apiPost(`/api/${phase}/standings/current`, { tournament_id: selectedTournament.id });
    simStatus.textContent = '';
    renderCurrentStandings(data);
  } catch (err) {
    simStatus.textContent = '';
    showError(`Couldn't load standings (${err.message}).`);
  }
}

function formatWinPct(p) {
  // classic baseball style: ".833", not "0.833"
  const s = p.toFixed(3);
  return s.startsWith('0.') ? s.slice(1) : (s.startsWith('-0.') ? '-' + s.slice(2) : s);
}

function renderCurrentStandings(data) {
  if (data.standings.length === 0) {
    showResults(`<p class="placeholder-note">No teams yet for this pool — try Populate Games first.</p>`);
    return;
  }
  showResults(`
    <p class="card-label">${selectedTournament.name} — Current Standings</p>
    <p class="placeholder-note" style="text-align:left; padding:0 0 14px;">
      Ranked by win% → fewest runs allowed → most runs scored. Games not yet played count as 0-0.
      Tap a team to see its simulated finish-place odds.
    </p>
    <div class="cs-table">
      <div class="cs-row cs-head">
        <span class="cs-rank"></span>
        <span class="cs-team">Team</span>
        <span class="cs-rec">W-L-T</span>
        <span class="cs-runs">RS-RA</span>
        <span class="cs-pct">Pct</span>
      </div>
      ${data.standings.map(s => `
        <div class="cs-row cs-clickable" data-team="${s.team}">
          <span class="cs-rank">${s.rank}</span>
          <span class="cs-team">${s.team}</span>
          <span class="cs-rec">${s.wins}-${s.losses}-${s.ties}</span>
          <span class="cs-runs">${s.runs_scored}-${s.runs_allowed}</span>
          <span class="cs-pct">${formatWinPct(s.win_pct)}</span>
        </div>
      `).join('')}
    </div>
  `);
}

async function runStandingsForTeam(teamName) {
  selectedStandingsTeam = teamName;
  try {
    const data = await withStatusCycle(STATUS_MESSAGES.standings, () =>
      apiPost(`/api/${phase}/standings`, { tournament_id: selectedTournament.id, team_name: teamName })
    );
    simStatus.textContent = `Done — ${data.sims_run.toLocaleString()} simulated pools.`;
    renderStandingsResult(data);
  } catch (err) {
    simStatus.textContent = '';
    showError(`Couldn't simulate standings (${err.message}).`);
  }
}

function ordinal(n) {
  const suffixes = ['th', 'st', 'nd', 'rd'];
  const v = n % 100;
  return n + (suffixes[(v - 20) % 10] || suffixes[v] || suffixes[0]);
}

function bracketTagClass(name) {
  if (!name) return '';
  const lower = name.toLowerCase();
  if (lower.includes('gold')) return ' bracket-tag-gold';
  if (lower.includes('silver')) return ' bracket-tag-silver';
  return '';
}

function renderStandingsResult(data) {
  const maxPct = Math.max(...data.placements.map(p => p.pct), 1);
  const hasNextGameInfo = data.placements.some(p => p.next_date);
  showResults(`
    <p class="card-label">${data.team} — Finish Odds</p>
    <p class="placeholder-note" style="text-align:left; padding:0 0 14px;">
      ${data.pool_size}-team pool, ranked by win% → fewest runs allowed → most runs scored,
      across ${data.sims_run.toLocaleString()} simulated outcomes of the remaining games.
      ${hasNextGameInfo ? ' Where each seed plays next is shown below, while that slot is still open.' : ''}
      Tap a finish to see what it would take to get there.
    </p>
    <div class="standings-list">
      ${data.placements.map(p => `
        <div class="standings-row${p.next_date ? ' standings-row-has-next' : ''}" data-place="${p.place}">
          <span class="standings-place">${ordinal(p.place)}</span>
          <div class="standings-mid">
            <div class="standings-bar-track">
              <div class="standings-bar-fill" style="width:${maxPct > 0 ? (p.pct / maxPct) * 100 : 0}%"></div>
            </div>
            ${p.next_date ? `
              <div class="standings-next">
                ${p.next_bracket_name ? `<span class="bracket-tag${bracketTagClass(p.next_bracket_name)}">${p.next_bracket_name}</span> ` : ''}
                📍 ${p.next_ballpark || ''}${p.next_ballpark && p.next_field ? ' · ' : ''}${p.next_field || ''}
                — ${p.next_date} ${p.next_time || ''}
              </div>
            ` : ''}
          </div>
          <span class="standings-pct">${p.pct}%</span>
        </div>
      `).join('')}
    </div>
  `);
}

/* ---------- Update Scores ---------- */
async function showScoreForm() {
  simStatus.textContent = '';
  // Reuse whatever games we already have (from a populate/predict call);
  // if we don't have any yet, populate first so there's something to score.
  if (currentGames.length === 0) {
    try {
      simStatus.textContent = 'Loading games…';
      const data = await apiPost(`/api/${phase}/populate`, { tournament_id: selectedTournament.id });
      currentGames = data.games;
      simStatus.textContent = '';
    } catch (err) {
      simStatus.textContent = '';
      showError(`Couldn't load games to score (${err.message}).`);
      return;
    }
  }
  renderScoreForm(currentGames);
}

/* ---------- renderers ---------- */
function showResults(html) {
  document.getElementById('emptyState').style.display = 'none';
  const el = document.getElementById('results');
  el.style.display = 'block';
  el.innerHTML = html;
}

function renderGameList(games, opts = {}) {
  if (games.length === 0) {
    showResults(`<p class="placeholder-note">No games yet for this phase.</p>`);
    return;
  }
  showResults(`
    <p class="card-label">${selectedTournament.name} — ${phase === 'pool-play' ? 'Pool Play' : 'Bracket Play'} Schedule</p>
    <div class="game-list">
      ${games.map(g => `
        <div class="game-row">
          <div class="game-teams">${g.team_a} <span class="vs">vs</span> ${g.team_b}</div>
          ${opts.showMeta ? `<div class="game-meta">${g.field || ''}${g.field && g.scheduled_time ? ' · ' : ''}${g.scheduled_time || ''}</div>` : ''}
        </div>
      `).join('')}
    </div>
  `);
}

function renderPredictionRow(p) {
  // Bar segments are normalized to fill the full width even when
  // win_pct_a + win_pct_b < 100 (the remainder is tie probability, only
  // possible in pool play) — the exact model output is still shown as text.
  // For final games win_pct_a/win_pct_b are already 100/0/50, so the same
  // bar logic works unchanged — only the displayed number switches to the
  // real score.
  const total = p.win_pct_a + p.win_pct_b;
  const widthA = total > 0 ? (p.win_pct_a / total) * 100 : 50;
  const widthB = total > 0 ? (p.win_pct_b / total) * 100 : 50;
  const valueA = p.is_final ? p.score_a : `${p.win_pct_a}%`;
  const valueB = p.is_final ? p.score_b : `${p.win_pct_b}%`;
  return `
    <div class="predict-row${p.is_final ? ' predict-row-final' : ''}" data-game-id="${p.game_id}">
      <div class="predict-header">
        <span class="predict-team">
          <span class="team-dot dot-a"></span>
          <span class="predict-team-name">${p.team_a}</span>
          <span class="predict-pct">${valueA}</span>
        </span>
        ${p.is_final ? '<span class="final-badge">Final</span>' : ''}
        <span class="predict-team">
          <span class="team-dot dot-b"></span>
          <span class="predict-team-name">${p.team_b}</span>
          <span class="predict-pct">${valueB}</span>
        </span>
      </div>
      <div class="game-predict-bar">
        <div class="side-a" style="width:${widthA}%"></div>
        <div class="side-b" style="width:${widthB}%"></div>
      </div>
    </div>
  `;
}

function renderPredictionList(predictions) {
  if (predictions.length === 0) {
    showResults(`<p class="placeholder-note">No games yet for this phase — try Populate Games first.</p>`);
    return;
  }
  showResults(`
    <p class="card-label">${selectedTournament.name} — ${phase === 'pool-play' ? 'Pool Play' : 'Bracket Play'} Predictions</p>
    <p class="predict-legend"><span class="team-dot dot-a"></span>Home team (left)<span class="predict-legend-sep">·</span><span class="team-dot dot-b"></span>Away team (right)</p>
    <p class="placeholder-note" style="text-align:left; padding:0 0 14px; font-size:11px;">Games marked <span class="final-badge" style="position:static; display:inline-block; vertical-align:middle;">Final</span> already have a real score. Tap any game for the factors behind its prediction.</p>
    <div class="game-list">
      ${predictions.map(renderPredictionRow).join('')}
    </div>
  `);
}

/* ---------- "Why this prediction?" modal ---------- */
const explainModal = document.getElementById('explainModal');
document.getElementById('explainClose').addEventListener('click', closeExplainModal);
explainModal.addEventListener('click', (e) => {
  if (e.target === explainModal) closeExplainModal();
});
document.getElementById('results').addEventListener('click', (e) => {
  const gameRow = e.target.closest('.predict-row');
  if (gameRow && gameRow.dataset.gameId) { showGameExplanation(gameRow.dataset.gameId); return; }
  const placementRow = e.target.closest('.standings-row');
  if (placementRow && placementRow.dataset.place) { showPlacementScenario(Number(placementRow.dataset.place)); return; }
  const csRow = e.target.closest('.cs-clickable');
  if (csRow && csRow.dataset.team) runStandingsForTeam(csRow.dataset.team);
});

function closeExplainModal() {
  explainModal.style.display = 'none';
}

async function showGameExplanation(gameId) {
  const content = document.getElementById('explainContent');
  content.innerHTML = `<p class="placeholder-note">Loading…</p>`;
  explainModal.style.display = 'flex';
  try {
    const data = await apiPost(`/api/${phase}/explain`, { tournament_id: selectedTournament.id, game_id: gameId });
    renderExplainContent(data);
  } catch (err) {
    content.innerHTML = `<p class="placeholder-note">Couldn't load explanation (${err.message}).</p>`;
  }
}

function renderExplainContent(data) {
  document.getElementById('explainContent').innerHTML = `
    <p class="card-label">${data.team_a} vs ${data.team_b}</p>
    <p class="explain-headline">
      Predicted ${data.pred_home_score}–${data.pred_away_score}
      <span class="explain-sub">(${data.pred_win_probability}% ${data.team_a})</span>
    </p>
    <p class="explain-note">Key factors behind this prediction, by how much weight the model gives each:</p>
    <div class="factor-list">
      ${data.factors.map(f => `
        <div class="factor-row">
          <div class="factor-top">
            <span class="factor-name">${f.feature}</span>
            <span class="factor-value">${f.value}</span>
          </div>
          <div class="factor-bar-track">
            <div class="factor-bar-fill" style="width:${f.weight_pct}%"></div>
          </div>
        </div>
      `).join('')}
    </div>
  `;
}

async function showPlacementScenario(place) {
  const content = document.getElementById('explainContent');
  content.innerHTML = `<p class="placeholder-note">Loading…</p>`;
  explainModal.style.display = 'flex';
  try {
    const data = await apiPost(`/api/${phase}/standings/explain`, {
      tournament_id: selectedTournament.id, team_name: selectedStandingsTeam, place,
    });
    renderPlacementScenario(data);
  } catch (err) {
    content.innerHTML = `<p class="placeholder-note">Couldn't load scenario (${err.message}).</p>`;
  }
}

function renderPlacementScenario(data) {
  const content = document.getElementById('explainContent');

  if (!data.reachable) {
    content.innerHTML = `
      <p class="card-label">${selectedStandingsTeam} — ${ordinal(data.place)} Place</p>
      <p class="placeholder-note">Didn't happen in any of ${data.sims_run.toLocaleString()} simulated pools — effectively out of reach from here.</p>
    `;
    return;
  }

  const ownResults = data.required_results.filter(r => r.involves_team);
  const otherResults = data.required_results.filter(r => !r.involves_team);

  content.innerHTML = `
    <p class="card-label">${selectedStandingsTeam} — ${ordinal(data.place)} Place Scenario</p>
    <p class="explain-note">
      Based on the ${data.sims_matching.toLocaleString()} of ${data.sims_run.toLocaleString()} simulated pools (${data.match_pct}%) that landed here.
      ${data.low_confidence ? ' Small sample, so treat this as a rough read rather than a precise one.' : ''}
    </p>
    ${ownResults.length ? `
      <p class="explain-section-title">This team needs to:</p>
      <div class="scenario-list">
        ${ownResults.map(r => `
          <div class="scenario-row scenario-row-own">
            <span>${r.winner === selectedStandingsTeam ? `Beat <strong>${r.loser}</strong>` : `Lose to <strong>${r.winner}</strong>`}</span>
            <span class="scenario-pct">${r.pct}%</span>
          </div>
        `).join('')}
      </div>
    ` : ''}
    ${data.run_targets.length ? `
      <p class="explain-section-title">Run guidelines for those games:</p>
      <div class="scenario-list">
        ${data.run_targets.map(t => `
          <div class="scenario-row">
            <span>vs <strong>${t.opponent}</strong>: score ${t.min_runs_scored}+, allow ${t.max_runs_allowed} or fewer</span>
          </div>
        `).join('')}
      </div>
    ` : ''}
    ${otherResults.length ? `
      <p class="explain-section-title">Also needs to break this way:</p>
      <div class="scenario-list">
        ${otherResults.map(r => `
          <div class="scenario-row">
            <span><strong>${r.winner}</strong> beats <strong>${r.loser}</strong></span>
            <span class="scenario-pct">${r.pct}%</span>
          </div>
        `).join('')}
      </div>
    ` : ''}
    ${!ownResults.length && !otherResults.length ? `<p class="placeholder-note">No single result stands out as required — this finish comes down to how several close games land together.</p>` : ''}
  `;
}

function renderScoreForm(games) {
  if (games.length === 0) {
    showResults(`<p class="placeholder-note">No games to score yet — try Populate Games first.</p>`);
    return;
  }
  showResults(`
    <p class="card-label">${selectedTournament.name} — Update ${phase === 'pool-play' ? 'Pool Play' : 'Bracket Play'} Scores</p>
    <div id="scoreFormRows">
      ${games.map(g => `
        <div class="score-form-row" data-game-id="${g.id}">
          <span class="team-label">${g.team_a}</span>
          <input type="number" min="0" class="score-input score-a" value="${g.score_a ?? ''}" placeholder="–">
          <span class="score-vs">–</span>
          <input type="number" min="0" class="score-input score-b" value="${g.score_b ?? ''}" placeholder="–">
          <span class="team-label" style="text-align:right;">${g.team_b}</span>
        </div>
      `).join('')}
    </div>
    <button class="save-scores-btn" id="saveScoresBtn">Save Scores</button>
  `);
  document.getElementById('saveScoresBtn').addEventListener('click', saveScores);
}

async function saveScores() {
  const rows = document.querySelectorAll('#scoreFormRows .score-form-row');
  const scores = [];
  rows.forEach(row => {
    const a = row.querySelector('.score-a').value;
    const b = row.querySelector('.score-b').value;
    if (a !== '' && b !== '') {
      scores.push({ game_id: row.dataset.gameId, score_a: parseInt(a, 10), score_b: parseInt(b, 10) });
    }
  });
  if (scores.length === 0) {
    simStatus.textContent = 'Enter at least one score before saving.';
    return;
  }
  const btn = document.getElementById('saveScoresBtn');
  btn.disabled = true;
  btn.textContent = 'Saving…';
  try {
    const data = await apiPost(`/api/${phase}/scores`, { tournament_id: selectedTournament.id, scores });
    simStatus.textContent = `Saved ${data.updated} game${data.updated === 1 ? '' : 's'}.`;
    btn.disabled = false;
    btn.textContent = 'Save Scores';
  } catch (err) {
    simStatus.textContent = `Couldn't save scores (${err.message}).`;
    btn.disabled = false;
    btn.textContent = 'Save Scores';
  }
}

loadTournaments();