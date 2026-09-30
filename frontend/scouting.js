// Same backend as the main app -- see app.js for local-dev notes.
const API_BASE = 'https://oddballs.onrender.com';

let selectedTeamKey = null;
let selectedTeamName = null;
let activePlayerTab = 'batting';

/* ---------- team search ---------- */
let _searchDebounce = null;
document.getElementById('teamSearchInput').addEventListener('input', (e) => {
  clearTimeout(_searchDebounce);
  const query = e.target.value.trim();
  _searchDebounce = setTimeout(() => runTeamSearch(query), 250);
});

async function runTeamSearch(query) {
  const resultsEl = document.getElementById('teamSearchResults');
  if (!query) {
    resultsEl.innerHTML = '';
    return;
  }
  resultsEl.innerHTML = `<p class="placeholder-note">Searching…</p>`;
  try {
    const res = await fetch(`${API_BASE}/api/scouting/teams?query=${encodeURIComponent(query)}`);
    if (!res.ok) throw new Error(`Server responded ${res.status}`);
    const teams = await res.json();
    renderTeamSearchResults(teams);
  } catch (err) {
    resultsEl.innerHTML = `<p class="placeholder-note">Couldn't search (${err.message}).</p>`;
  }
}

function renderTeamSearchResults(teams) {
  const resultsEl = document.getElementById('teamSearchResults');
  if (teams.length === 0) {
    resultsEl.innerHTML = `<p class="placeholder-note">No teams match.</p>`;
    return;
  }
  resultsEl.innerHTML = teams.map(t => `
    <div class="admin-event-row admin-event-clickable" data-team-key="${t.team_key}" data-team-name="${t.team_name}">
      <div class="admin-event-top">
        <span class="admin-event-name">${t.team_name}</span>
        <span class="admin-status-badge ${t.gc_linked ? 'status-complete' : 'status-scheduled'}">
          ${t.gc_linked ? 'scouted' : 'no data yet'}
        </span>
      </div>
      ${t.last_scraped ? `<div class="admin-event-meta">Last scraped: ${t.last_scraped.split('T')[0]}</div>` : ''}
    </div>
  `).join('');
  resultsEl.querySelectorAll('.admin-event-clickable').forEach(row => {
    row.addEventListener('click', () => selectTeam(Number(row.dataset.teamKey), row.dataset.teamName));
  });
}

/* ---------- team report ---------- */
async function selectTeam(teamKey, teamName) {
  selectedTeamKey = teamKey;
  selectedTeamName = teamName;
  document.getElementById('playerCard').style.display = 'none';

  const card = document.getElementById('teamReportCard');
  const content = document.getElementById('teamReportContent');
  card.style.display = 'block';
  content.innerHTML = `<p class="placeholder-note">Loading…</p>`;

  try {
    const res = await fetch(`${API_BASE}/api/scouting/team/${teamKey}`);
    if (!res.ok) throw new Error(`Server responded ${res.status}`);
    const data = await res.json();
    renderTeamReport(data);
  } catch (err) {
    content.innerHTML = `<p class="placeholder-note">Couldn't load team (${err.message}).</p>`;
  }
}

function renderTeamReport(data) {
  const content = document.getElementById('teamReportContent');
  if (data.roster.length === 0) {
    content.innerHTML = `
      <p class="admin-selected-name">${data.team_name}</p>
      <p class="placeholder-note" style="text-align:left; padding:10px 0 0;">
        No GameChanger data linked to this team yet. Scrape it with
        <code>gamechanger_scrape.py</code> and link it on the
        <a href="admin.html" class="admin-link" style="position:static; display:inline;">Admin</a> page.
      </p>`;
    return;
  }

  const rows = data.roster.map(p => `
    <tr class="scouting-row" data-player-name="${p.player_name}">
      <td class="name-cell">${p.player_name}</td>
      <td>${p.games_played}</td>
      <td>${p.avg !== null ? p.avg.toFixed(3).replace(/^0/, '') : '—'}</td>
      <td>${p.hr}</td>
      <td>${p.rbi}</td>
      <td>${p.ip || '—'}</td>
      <td>${p.era !== null ? p.era.toFixed(2) : '—'}</td>
      <td>${p.so_pitching}</td>
    </tr>
  `).join('');

  content.innerHTML = `
    <p class="admin-selected-name">${data.team_name}</p>
    ${data.last_scraped ? `<p class="placeholder-note" style="text-align:left; padding:2px 0 12px; font-size:11px;">Last scraped: ${data.last_scraped.split('T')[0]}</p>` : ''}
    <table class="roster">
      <thead><tr><th>Player</th><th>GP</th><th>AVG</th><th>HR</th><th>RBI</th><th>IP</th><th>ERA</th><th>SO (P)</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;

  content.querySelectorAll('.scouting-row').forEach(row => {
    row.addEventListener('click', () => selectPlayer(row.dataset.playerName));
  });
}

/* ---------- player drill-down ---------- */
async function selectPlayer(playerName) {
  const card = document.getElementById('playerCard');
  const content = document.getElementById('playerContent');
  card.style.display = 'block';
  content.innerHTML = `<p class="placeholder-note">Loading…</p>`;
  card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

  try {
    const res = await fetch(`${API_BASE}/api/scouting/team/${selectedTeamKey}/player/${encodeURIComponent(playerName)}`);
    if (!res.ok) throw new Error(`Server responded ${res.status}`);
    const data = await res.json();
    activePlayerTab = 'batting';
    renderPlayerProfile(data);
  } catch (err) {
    content.innerHTML = `<p class="placeholder-note">Couldn't load player (${err.message}).</p>`;
  }
}

function _gameLogTable(rows, columns) {
  if (rows.length === 0) return `<p class="placeholder-note">No games logged yet.</p>`;
  const head = columns.map(c => `<th>${c.label}</th>`).join('');
  const body = rows.map(r => `
    <tr>
      <td class="name-cell">${r.game_date ? r.game_date.split('T')[0] : '—'}</td>
      <td>${r.opponent || '—'}</td>
      ${columns.slice(2).map(c => `<td>${r[c.key] ?? '—'}</td>`).join('')}
    </tr>
  `).join('');
  return `<table class="roster"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderPlayerProfile(data) {
  const content = document.getElementById('playerContent');

  const battingCols = [
    { key: 'game_date', label: 'Date' }, { key: 'opponent', label: 'Opp' },
    { key: 'ab', label: 'AB' }, { key: 'r', label: 'R' }, { key: 'h', label: 'H' },
    { key: 'doubles', label: '2B' }, { key: 'triples', label: '3B' }, { key: 'hr', label: 'HR' },
    { key: 'rbi', label: 'RBI' }, { key: 'bb', label: 'BB' }, { key: 'so', label: 'SO' }, { key: 'sb', label: 'SB' },
  ];
  const pitchingCols = [
    { key: 'game_date', label: 'Date' }, { key: 'opponent', label: 'Opp' },
    { key: 'ip', label: 'IP' }, { key: 'h', label: 'H' }, { key: 'r', label: 'R' }, { key: 'er', label: 'ER' },
    { key: 'bb', label: 'BB' }, { key: 'so', label: 'SO' }, { key: 'pitches', label: 'Pitches' },
  ];
  const fieldingCols = [
    { key: 'game_date', label: 'Date' }, { key: 'opponent', label: 'Opp' }, { key: 'errors', label: 'E' },
  ];

  content.innerHTML = `
    <p class="admin-selected-name">${data.player_name}${data.jersey_number ? ` <span style="color:var(--chalk-dim); font-weight:400;">#${data.jersey_number}</span>` : ''}</p>
    <div class="admin-status-pills" style="margin:10px 0 14px;">
      <button class="mode-tab scouting-tab ${activePlayerTab === 'batting' ? 'active' : ''}" data-tab="batting">Batting</button>
      <button class="mode-tab scouting-tab ${activePlayerTab === 'pitching' ? 'active' : ''}" data-tab="pitching">Pitching</button>
      <button class="mode-tab scouting-tab ${activePlayerTab === 'fielding' ? 'active' : ''}" data-tab="fielding">Fielding</button>
    </div>
    <div id="scoutingTabBatting" style="${activePlayerTab === 'batting' ? '' : 'display:none;'}">
      ${_gameLogTable(data.batting_log, battingCols)}
    </div>
    <div id="scoutingTabPitching" style="${activePlayerTab === 'pitching' ? '' : 'display:none;'}">
      ${_gameLogTable(data.pitching_log, pitchingCols)}
    </div>
    <div id="scoutingTabFielding" style="${activePlayerTab === 'fielding' ? '' : 'display:none;'}">
      ${_gameLogTable(data.fielding_log, fieldingCols)}
    </div>
  `;

  content.querySelectorAll('.scouting-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      activePlayerTab = btn.dataset.tab;
      content.querySelectorAll('.scouting-tab').forEach(b => b.classList.toggle('active', b === btn));
      ['Batting', 'Pitching', 'Fielding'].forEach(name => {
        document.getElementById(`scoutingTab${name}`).style.display = name.toLowerCase() === activePlayerTab ? '' : 'none';
      });
    });
  });
}
