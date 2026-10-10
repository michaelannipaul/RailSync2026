const telemetryForm = document.querySelector('#telemetry-form');
const corridorInput = document.querySelector('#corridor-id-input');
const telemetryStatus = document.querySelector('#telemetry-status');
const wsHeading = document.querySelector('#ws-heading');
const wsCaption = document.querySelector('#ws-caption');
const wsStatusEl = document.querySelector('#ws-status');
const alertsFeed = document.querySelector('#alerts-feed');

const esc = v => String(v).replace(/[&<>'"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#039;','"':'&quot;'}[c]));
const dt = v => v ? new Date(v).toLocaleString() : '—';

let socket = null;
let currentCorridorId = null;
let alertCount = 0;
const monitorParams = new URLSearchParams(window.location.search);
['block_id', 'corridor_id'].forEach((name) => {
  const value = monitorParams.get(name);
  const field = telemetryForm.elements[name];
  if (value && field) field.value = value;
});
if (monitorParams.get('block_id')) {
  wsCaption.textContent = `Sanctioned block ${monitorParams.get('block_id')} was passed from the cockpit. Enter the authenticated supervisor update to begin monitoring.`;
}

function connectWebSocket(corridorId) {
  if (socket) {
    socket.close();
    socket = null;
  }
  currentCorridorId = corridorId;
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
  const url = `${protocol}://${location.host}/ws/live-blocks/${encodeURIComponent(corridorId)}`;
  socket = new WebSocket(url);

  wsStatusEl.textContent = '● Connecting…';
  wsStatusEl.className = 'ws-indicator connecting';

  socket.addEventListener('open', () => {
    wsHeading.textContent = `Live feed: ${esc(corridorId)}`;
    wsCaption.textContent = 'Receiving authenticated field updates for this corridor.';
    wsStatusEl.textContent = '● Connected';
    wsStatusEl.className = 'ws-indicator connected';
  });

  socket.addEventListener('message', ev => {
    try {
      const msg = JSON.parse(ev.data);
      appendAlert(msg);
    } catch {
      appendRawAlert(ev.data);
    }
  });

  socket.addEventListener('close', () => {
    wsStatusEl.textContent = '● Disconnected';
    wsStatusEl.className = 'ws-indicator disconnected';
  });

  socket.addEventListener('error', () => {
    wsStatusEl.textContent = '● Connection error';
    wsStatusEl.className = 'ws-indicator disconnected';
  });
}

function stateClass(state) {
  const map = {
    ACTIVE: 'state-active',
    EXTENSION_REQUESTED: 'state-overrun',
    CLEARED_EARLY: 'state-early',
    COMPLETED: 'state-complete',
    PENDING_START: 'state-pending',
  };
  return map[state] || '';
}

function appendAlert(msg) {
  alertCount++;
  if (alertCount === 1) alertsFeed.innerHTML = '';
  const alert = msg.active_alert;
  const alertHtml = alert
    ? (alert.overrun_minutes !== undefined
        ? `<div class="alert-card alert-overrun">
             <div class="alert-badge">OVERRUN WARNING</div>
             <p><strong>Overrun by ${esc(alert.overrun_minutes)} min.</strong> ${esc(alert.message)}</p>
             <dl>
               <div><dt>Revised handover</dt><dd>${esc(dt(alert.revised_handover_time))}</dd></div>
               <div><dt>First impacted</dt><dd>${renderFirstTrain(alert.first_impacted_train)}</dd></div>
             </dl>
           </div>`
        : `<div class="alert-card alert-early">
             <div class="alert-badge">EARLY RESTORATION AVAILABLE</div>
             <p><strong>${esc(alert.slack_capacity_recovered_minutes)} min recoverable.</strong> ${esc(alert.recommended_action)}</p>
             <p class="muted-line">Controller review required before any restoration.</p>
           </div>`)
    : '';

  const card = document.createElement('article');
  card.className = 'feed-item';
  card.innerHTML = `
    <div class="feed-header">
      <span class="block-id-label">${esc(msg.block_id)}</span>
      <span class="feed-state ${stateClass(msg.state)}">${esc(msg.state)}</span>
      <span class="feed-ts">${esc(dt(msg.telemetry_timestamp))}</span>
    </div>
    <div class="progress-bar-wrap">
      <div class="progress"><span style="width:${Math.min(100, msg.percent_completed)}%"></span></div>
      <span class="progress-label">${esc(msg.percent_completed)}% complete</span>
    </div>
    ${alertHtml}`;
  alertsFeed.prepend(card);
}

function renderFirstTrain(info) {
  if (!info) return '—';
  if (info.timetable_record_id) {
    return `Record ${esc(info.timetable_record_id)} · ${esc(info.passenger_trains_affected)} passenger trains affected`;
  }
  return `<em>${esc(info.note || 'Identify manually from timetable.')}</em>`;
}

function appendRawAlert(text) {
  alertCount++;
  if (alertCount === 1) alertsFeed.innerHTML = '';
  const item = document.createElement('article');
  item.className = 'feed-item';
  item.innerHTML = `<pre class="raw-alert">${esc(text)}</pre>`;
  alertsFeed.prepend(item);
}

telemetryForm.addEventListener('submit', async ev => {
  ev.preventDefault();
  const d = new FormData(telemetryForm);
  const corridorId = d.get('corridor_id').trim();
  const tsRaw = d.get('timestamp').trim();
  const payload = {
    block_id:                    d.get('block_id').trim(),
    corridor_id:                 corridorId,
    supervisor_id:               d.get('supervisor_id').trim(),
    actual_progress_pct:         parseFloat(d.get('actual_progress_pct')),
    estimated_minutes_remaining: parseInt(d.get('estimated_minutes_remaining'), 10),
    timestamp:                   tsRaw || new Date().toISOString(),
  };

  // Connect (or reconnect) the WebSocket for this corridor before posting.
  if (corridorId !== currentCorridorId) {
    connectWebSocket(corridorId);
  }

  telemetryStatus.textContent = 'Submitting update…';
  telemetryStatus.className = 'telemetry-status';

  try {
    const r = await fetch('/api/v1/telemetry/progress-update', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const x = await r.json();
    if (!r.ok) throw new Error(Array.isArray(x.detail)
      ? x.detail.map(i => i.msg).join('; ')
      : x.detail || 'Telemetry update failed.');
    telemetryStatus.textContent = `Update accepted: block ${esc(x.block_id)} · state ${esc(x.state)}`;
    telemetryStatus.className = 'telemetry-status status-ok';
    // Also render the returned message directly (in case WS has lag).
    appendAlert(x);
  } catch (err) {
    telemetryStatus.textContent = err.message;
    telemetryStatus.className = 'telemetry-status status-err';
  }
});
