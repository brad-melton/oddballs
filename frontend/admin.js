// Same backend as the main app -- see app.js for local-dev notes.
const API_BASE = 'https://oddballs.onrender.com';

const ALL_STATUSES = ['complete', 'upcoming', 'ongoing', 'scheduled', 'cancelled'];
const STATUS_CLASS = {
  complete: 'status-complete',
  upcoming: 'status-upcoming',
  ongoing: 'status-ongoing',
  scheduled: 'status-scheduled',
  cancelled: 'status-cancelled',
};

let allEvents = [];
let activeStatuses = new Set(ALL_STATUSES); // all on by default -- no filtering until the user toggles one
let selectedEventId = null;

/* ---------- load + render ---------- */
async function loadAdminStatus() {
  const summaryEl = document.getElementById('adminSummary');
  const listEl = document.getElementById('adminEventList');
  summaryEl.innerHTML = `<p class="placeholder-note">Loading…</p>`;
  listEl.innerHTML = `<p class="placeholder-note">Loading…</p>`;

  try {
    const res = await fetch(`${API_BASE}/api/admin/status`);
    if (!res.ok) throw new Error(`Server responded ${res.status}`);
    const data = await res.json();
    allEvents = data.events;
    renderSummary(data);
    renderStatusFilterPills();
    applyFiltersAndRender();
  } catch (err) {
    summaryEl.innerHTML = '';
    listEl.innerHTML = `<p class="placeholder-note">Couldn't load status (${err.message}). Is the API running at <code>${API_BASE}</code>?</p>`;
  }
}

function renderSummary(data) {
  const pendingGames = data.total_games - data.total_completed_games;
  document.getElementById('adminSummary').innerHTML = `
    <div class="admin-stat-grid">
      <div class="admin-stat"><span class="admin-stat-num">${data.total_events}</span><span class="admin-stat-label">Events</span></div>
      <div class="admin-stat"><span class="admin-stat-num">${data.total_games}</span><span class="admin-stat-label">Games</span></div>
      <div class="admin-stat"><span class="admin-stat-num">${data.total_completed_games}</span><span class="admin-stat-label">Completed</span></div>
      <div class="admin-stat"><span class="admin-stat-num">${pendingGames}</span><span class="admin-stat-label">Pending</span></div>
      <div class="admin-stat"><span class="admin-stat-num">${data.total_teams}</span><span class="admin-stat-label">Teams</span></div>
    </div>
  `;
}

/* ---------- filters ---------- */
function renderStatusFilterPills() {
  const el = document.getElementById('statusFilters');
  el.innerHTML = ALL_STATUSES.map(s => `
    <button class="admin-status-pill ${STATUS_CLASS[s]}${activeStatuses.has(s) ? ' active' : ''}" data-status="${s}">${s}</button>
  `).join('');
  el.querySelectorAll('.admin-status-pill').forEach(btn => {
    btn.addEventListener('click', () => {
      const s = btn.dataset.status;
      if (activeStatuses.has(s)) activeStatuses.delete(s);
      else activeStatuses.add(s);
      renderStatusFilterPills();
      applyFiltersAndRender();
    });
  });
}

function pctScraped(e) {
  return e.total_games > 0 ? (e.completed_games / e.total_games) * 100 : 0;
}

function applyFiltersAndRender() {
  const dateFrom = document.getElementById('dateFrom').value;
  const dateTo = document.getElementById('dateTo').value;
  const pctFromRaw = document.getElementById('pctFrom').value;
  const pctToRaw = document.getElementById('pctTo').value;
  const pctFrom = pctFromRaw === '' ? 0 : Number(pctFromRaw);
  const pctTo = pctToRaw === '' ? 100 : Number(pctToRaw);

  const filtered = allEvents.filter(e => {
    if (!activeStatuses.has(e.status)) return false;

    if (dateFrom || dateTo) {
      const eventDate = (e.start_date || '').split(' ')[0]; // "YYYY-MM-DD HH:MM:SS" -> "YYYY-MM-DD"
      if (dateFrom && (!eventDate || eventDate < dateFrom)) return false;
      if (dateTo && (!eventDate || eventDate > dateTo)) return false;
    }

    const pct = pctScraped(e);
    if (pct < pctFrom || pct > pctTo) return false;

    return true;
  });

  document.getElementById('eventCountLabel').textContent =
    filtered.length === allEvents.length ? '' : `(${filtered.length} of ${allEvents.length})`;
  renderEventList(filtered);
}

['dateFrom', 'dateTo', 'pctFrom', 'pctTo'].forEach(id => {
  document.getElementById(id).addEventListener('input', applyFiltersAndRender);
});
document.getElementById('clearFiltersBtn').addEventListener('click', () => {
  activeStatuses = new Set(ALL_STATUSES);
  document.getElementById('dateFrom').value = '';
  document.getElementById('dateTo').value = '';
  document.getElementById('pctFrom').value = '';
  document.getElementById('pctTo').value = '';
  renderStatusFilterPills();
  applyFiltersAndRender();
});

/* ---------- event list ---------- */
function renderEventList(events) {
  const listEl = document.getElementById('adminEventList');
  if (events.length === 0) {
    listEl.innerHTML = `<p class="placeholder-note">No events match this filter.</p>`;
    return;
  }

  listEl.innerHTML = events.map(e => {
    const pct = e.total_games > 0 ? Math.round(pctScraped(e)) : 0;
    const statusClass = STATUS_CLASS[e.status] || 'status-scheduled';
    const isSelected = e.eventid === selectedEventId;
    return `
      <div class="admin-event-row admin-event-clickable${isSelected ? ' admin-event-selected' : ''}" data-eventid="${e.eventid}">
        <div class="admin-event-top">
          <span class="admin-event-name">${e.name}</span>
          <span class="admin-status-badge ${statusClass}">${e.status || 'unknown'}</span>
        </div>
        <div class="admin-event-meta">
          ${e.classification || ''}${e.classification && e.start_date ? ' · ' : ''}${(e.start_date || '').split(' ')[0]}
        </div>
        ${e.total_games > 0 ? `
          <div class="admin-progress-track">
            <div class="admin-progress-fill" style="width:${pct}%"></div>
          </div>
          <div class="admin-event-counts">
            ${e.completed_games}/${e.total_games} final (${pct}%) · ${e.pool_games} pool · ${e.bracket_games} bracket
          </div>
        ` : `
          <div class="admin-event-counts">No games scraped yet (0%)</div>
        `}
      </div>
    `;
  }).join('');

  listEl.querySelectorAll('.admin-event-clickable').forEach(row => {
    row.addEventListener('click', () => selectEvent(row.dataset.eventid));
  });
}

/* ---------- selected event: change status + scrape command ---------- */
function selectEvent(eventid) {
  selectedEventId = eventid === selectedEventId ? null : eventid; // click again to deselect
  applyFiltersAndRender();
  renderSelectedEventPanel();
}

function renderSelectedEventPanel() {
  const card = document.getElementById('selectedEventCard');
  const content = document.getElementById('selectedEventContent');

  if (!selectedEventId) {
    card.style.display = 'none';
    return;
  }

  const e = allEvents.find(ev => ev.eventid === selectedEventId);
  if (!e) { card.style.display = 'none'; return; }

  card.style.display = 'block';
  content.innerHTML = `
    <p class="admin-selected-name">${e.name}</p>
    <p class="admin-filter-label" style="margin-top:14px;">Change status</p>
    <div class="admin-filter-row">
      <select class="select-input" id="statusSelect" style="padding:10px 12px; font-size:14px;">
        ${ALL_STATUSES.map(s => `<option value="${s}"${s === e.status ? ' selected' : ''}>${s}</option>`).join('')}
      </select>
      <button class="mode-tab" id="saveStatusBtn" style="padding:8px 16px;">Save</button>
    </div>
    <p class="placeholder-note" id="statusSaveNote" style="text-align:left; padding:6px 0 0; font-size:11px;"></p>

    <p class="admin-filter-label" style="margin-top:14px;">Scrape just this event</p>
    <p class="placeholder-note" style="text-align:left; padding:0; font-size:11px;">Run from your laptop:</p>
    <pre class="admin-code" id="scrapeCommand">python fall2026catchupscoring.py --event ${e.eventid}</pre>
    <button class="mode-tab" id="copyCommandBtn" style="padding:6px 14px;">Copy command</button>
  `;

  document.getElementById('saveStatusBtn').addEventListener('click', () => saveEventStatus(e.eventid));
  document.getElementById('copyCommandBtn').addEventListener('click', () => {
    navigator.clipboard.writeText(`python fall2026catchupscoring.py --event ${e.eventid}`)
      .then(() => {
        const btn = document.getElementById('copyCommandBtn');
        const original = btn.textContent;
        btn.textContent = 'Copied!';
        setTimeout(() => { btn.textContent = original; }, 1500);
      })
      .catch(() => {});
  });
}

async function saveEventStatus(eventid) {
  const select = document.getElementById('statusSelect');
  const note = document.getElementById('statusSaveNote');
  const btn = document.getElementById('saveStatusBtn');
  const newStatus = select.value;

  btn.disabled = true;
  note.textContent = 'Saving…';
  try {
    const res = await fetch(`${API_BASE}/api/admin/events/${eventid}/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: newStatus }),
    });
    if (!res.ok) {
      let detail = `Server responded ${res.status}`;
      try { const errBody = await res.json(); if (errBody.detail) detail = errBody.detail; } catch {}
      throw new Error(detail);
    }
    // Update local copy so the list/badge reflect it without a full refetch.
    const e = allEvents.find(ev => ev.eventid === eventid);
    if (e) e.status = newStatus;
    note.textContent = 'Saved.';
    applyFiltersAndRender();
  } catch (err) {
    note.textContent = `Couldn't save (${err.message}).`;
  } finally {
    btn.disabled = false;
  }
}

document.getElementById('refreshBtn').addEventListener('click', loadAdminStatus);
loadAdminStatus();

/* ---------- GameChanger team links ---------- */
async function loadGcMappings() {
  const el = document.getElementById('gcMappingList');
  el.innerHTML = `<p class="placeholder-note">Loading…</p>`;
  try {
    const res = await fetch(`${API_BASE}/api/admin/scouting/team-mappings`);
    if (!res.ok) throw new Error(`Server responded ${res.status}`);
    const data = await res.json();
    renderGcMappings(data.mappings);
  } catch (err) {
    el.innerHTML = `<p class="placeholder-note">Couldn't load (${err.message}). Is the API running at <code>${API_BASE}</code>?</p>`;
  }
}

function renderGcMappings(mappings) {
  const el = document.getElementById('gcMappingList');
  if (mappings.length === 0) {
    el.innerHTML = `<p class="placeholder-note">No GameChanger teams scraped yet.</p>`;
    return;
  }

  el.innerHTML = mappings.map((m, i) => `
    <div class="admin-event-row">
      <div class="admin-event-top">
        <span class="admin-event-name">${m.gc_team_name || m.gc_team_id}</span>
        ${m.pg_team_name
          ? `<span class="admin-status-badge status-complete">→ ${m.pg_team_name}</span>`
          : `<span class="admin-status-badge status-scheduled">unlinked</span>`}
      </div>
      ${m.last_scraped ? `<div class="admin-event-meta">Last scraped: ${m.last_scraped.split('T')[0]}</div>` : ''}
      ${!m.pg_team_name ? `
        <div class="admin-filter-row" style="margin-top:8px;">
          <input type="text" class="select-input" id="gcMapSearch${i}" placeholder="Search PG team to link…" style="flex:1; padding:8px 10px; font-size:12px;">
        </div>
        <div id="gcMapResults${i}"></div>
        <p class="placeholder-note" id="gcMapNote${i}" style="text-align:left; padding:4px 0 0; font-size:11px;"></p>
      ` : ''}
    </div>
  `).join('');

  mappings.forEach((m, i) => {
    if (m.pg_team_name) return;
    const input = document.getElementById(`gcMapSearch${i}`);
    let debounceTimer;
    input.addEventListener('input', () => {
      clearTimeout(debounceTimer);
      const q = input.value.trim();
      debounceTimer = setTimeout(() => searchPgTeamsForMapping(q, m.gc_team_id, i), 250);
    });
  });
}

async function searchPgTeamsForMapping(query, gcTeamId, i) {
  const resultsEl = document.getElementById(`gcMapResults${i}`);
  if (!query) { resultsEl.innerHTML = ''; return; }
  try {
    const res = await fetch(`${API_BASE}/api/scouting/teams?query=${encodeURIComponent(query)}`);
    if (!res.ok) throw new Error(`Server responded ${res.status}`);
    const teams = await res.json();
    resultsEl.innerHTML = teams.slice(0, 8).map(t =>
      `<button class="mode-tab" data-team-key="${t.team_key}" style="padding:6px 12px; margin:6px 6px 0 0; font-size:12px;">${t.team_name}</button>`
    ).join('') || `<p class="placeholder-note" style="padding:4px 0; text-align:left;">No matches.</p>`;
    resultsEl.querySelectorAll('button[data-team-key]').forEach(btn => {
      btn.addEventListener('click', () => linkGcTeam(gcTeamId, Number(btn.dataset.teamKey), i));
    });
  } catch (err) {
    resultsEl.innerHTML = `<p class="placeholder-note" style="text-align:left;">Search failed (${err.message}).</p>`;
  }
}

async function linkGcTeam(gcTeamId, pgTeamKey, i) {
  const note = document.getElementById(`gcMapNote${i}`);
  note.textContent = 'Linking…';
  try {
    const res = await fetch(`${API_BASE}/api/admin/scouting/team-mappings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pg_team_key: pgTeamKey, gc_team_id: gcTeamId }),
    });
    if (!res.ok) {
      let detail = `Server responded ${res.status}`;
      try { const errBody = await res.json(); if (errBody.detail) detail = errBody.detail; } catch {}
      throw new Error(detail);
    }
    loadGcMappings();
  } catch (err) {
    note.textContent = `Couldn't link (${err.message}).`;
  }
}

document.getElementById('refreshMappingsBtn').addEventListener('click', loadGcMappings);
loadGcMappings();
