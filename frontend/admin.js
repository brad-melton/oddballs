// Same backend as the main app -- see app.js for local-dev notes.
const API_BASE = 'https://oddballs.onrender.com';

const STATUS_CLASS = {
  complete: 'status-complete',
  upcoming: 'status-upcoming',
  ongoing: 'status-ongoing',
  scheduled: 'status-scheduled',
  cancelled: 'status-cancelled',
};

async function loadAdminStatus() {
  const summaryEl = document.getElementById('adminSummary');
  const listEl = document.getElementById('adminEventList');
  summaryEl.innerHTML = `<p class="placeholder-note">Loading…</p>`;
  listEl.innerHTML = `<p class="placeholder-note">Loading…</p>`;

  try {
    const res = await fetch(`${API_BASE}/api/admin/status`);
    if (!res.ok) throw new Error(`Server responded ${res.status}`);
    const data = await res.json();
    renderSummary(data);
    renderEventList(data.events);
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

function renderEventList(events) {
  const listEl = document.getElementById('adminEventList');
  if (events.length === 0) {
    listEl.innerHTML = `<p class="placeholder-note">No events in the database yet.</p>`;
    return;
  }

  listEl.innerHTML = events.map(e => {
    const pct = e.total_games > 0 ? Math.round((e.completed_games / e.total_games) * 100) : 0;
    const statusClass = STATUS_CLASS[e.status] || 'status-scheduled';
    return `
      <div class="admin-event-row">
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
            ${e.completed_games}/${e.total_games} final · ${e.pool_games} pool · ${e.bracket_games} bracket
          </div>
        ` : `
          <div class="admin-event-counts">No games scraped yet</div>
        `}
      </div>
    `;
  }).join('');
}

document.getElementById('refreshBtn').addEventListener('click', loadAdminStatus);
loadAdminStatus();
