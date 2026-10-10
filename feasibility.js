const form = document.querySelector('#assessment-form');
const select = document.querySelector('#task-select');
const heading = document.querySelector('.result-heading h2');
const caption = document.querySelector('#f02-caption');
const output = document.querySelector('#f02-result');
const esc = (value) => String(value).replace(/[&<>'"]/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#039;','"':'&quot;'}[character]));
const requestedTaskId = new URLSearchParams(window.location.search).get('task_id');

async function loadTasks() {
  const response = await fetch('/api/v1/ingestion/tasks');
  const records = await response.json();
  const tasks = records.filter(record => record.data_quality_status === 'COMPLETE' && record.normalized_task);
  select.innerHTML = '<option value="" selected disabled>Select normalized task</option>';
  for (const record of tasks) { const task = record.normalized_task; const option = document.createElement('option'); option.value = task.id; option.textContent = `${task.id} · ${task.department} · ${task.section_id} · KM ${task.start_km}–${task.end_km}`; select.append(option); }
  if (!tasks.length) select.innerHTML = '<option value="" selected disabled>No complete F-01 tasks found</option>';
  if (requestedTaskId && tasks.some((record) => record.normalized_task.id === requestedTaskId)) {
    select.value = requestedTaskId;
    caption.textContent = `Ticket ${requestedTaskId} was selected from data intake. Validate its candidate window to continue.`;
  }
}

function showResult(result) {
  heading.textContent = result.status.replaceAll('_', ' ');
  const statusClass = result.status === 'SUITABLE' ? 'complete' : 'needs-review';
  caption.textContent = result.status === 'NEEDS_REVIEW' ? 'Forecast data was unavailable for the stored section and proposed time.' : 'Review this advisory assessment before progressing it to planning.';
  const forecast = result.forecast ? `<dl><div><dt>Forecast hour</dt><dd>${esc(new Date(result.forecast.forecast_timestamp).toLocaleString())}</dd></div><div><dt>Temperature</dt><dd>${esc(result.forecast.temperature_c)} °C</dd></div><div><dt>Wind speed</dt><dd>${esc(result.forecast.wind_speed_kmh)} km/h</dd></div><div><dt>Visibility</dt><dd>${esc(result.forecast.visibility_m)} m</dd></div><div><dt>Risk multiplier</dt><dd>${esc(result.risk_multiplier ?? 'Not calculated')}</dd></div></dl>` : `<p class="review-list">Forecast values are unavailable; no substitute values were used.</p>`;
  const canContinue = ['SUITABLE', 'CAUTION_REQUIRED'].includes(result.status);
  const continueAction = canContinue ? `<div class="form-submit"><p>F-02 is eligible for priority scoring.</p><a class="primary-button" href="/priority?task_id=${encodeURIComponent(result.task_id)}">Continue to priority <span>→</span></a></div>` : '';
  output.className = 'result-data'; output.innerHTML = `<span class="result-status ${statusClass}">${esc(result.status)}</span>${forecast}<div class="review-list">${result.warning_reasons.map(esc).join('<br/>')}</div>${continueAction}`;
}

form.addEventListener('submit', async (event) => { event.preventDefault(); const values = new FormData(form); const payload = {task_id: values.get('task_id')}; try { const response = await fetch('/api/v1/feasibility/assessments',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'Assessment request failed.'); showResult(data); } catch (error) { heading.textContent = 'Input needs correction'; caption.textContent = error.message; output.className = 'empty-result'; output.innerHTML = '<p>No feasibility conclusion was created.</p>'; } });
loadTasks().catch(() => { select.innerHTML = '<option value="" selected disabled>Could not load F-01 tasks</option>'; });
