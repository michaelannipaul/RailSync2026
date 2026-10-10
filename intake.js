const tabs = document.querySelectorAll('.tab');
const panels = { reference: document.querySelector('#reference-panel'), task: document.querySelector('#task-panel') };
const resultTitle = document.querySelector('.result-heading h2');
const resultCaption = document.querySelector('#result-caption');
const resultOutput = document.querySelector('#result-output');
const sourceGuide = document.querySelector('#source-guide');

document.querySelector('input[name="due_date"]')?.closest('label')?.remove();
const coWorkingToggle = document.querySelector('input[name="co_working_compatible"]');
const coWorkingLabel = coWorkingToggle?.closest('label');
let coWorkingDepartments = document.querySelector('#co-working-departments');
let coWorkingError;
const coWorkingSystems = new Set(['TMS', 'SMMS', 'TDMS']);
const validateCoWorkingDepartments = () => {
  if (!coWorkingToggle || !coWorkingDepartments) return true;
  const selectedSystems = [...coWorkingDepartments.querySelectorAll('input[name="co_working_departments"]:checked')]
    .map((input) => input.value)
    .filter((value) => coWorkingSystems.has(value));
  const hasDepartment = selectedSystems.length > 0;
  if (coWorkingToggle.checked && !hasDepartment) {
    if (coWorkingError) coWorkingError.hidden = false;
    return false;
  }
  if (coWorkingError) coWorkingError.hidden = true;
  return true;
};
if (coWorkingToggle && coWorkingLabel) {
  coWorkingLabel.lastChild.textContent = ' Requires assistance from another department?';
  if (!coWorkingDepartments) {
    coWorkingDepartments = document.createElement('div');
    coWorkingDepartments.id = 'co-working-departments';
    coWorkingDepartments.className = 'co-working-departments';
    coWorkingDepartments.hidden = true;
    coWorkingDepartments.style.cssText = 'display:grid;gap:8px;margin:4px 0 0 21px;padding:9px 12px;border-left:3px solid #a8d5c2;background:#f5faf7;';
    coWorkingDepartments.innerHTML = '<span class="co-working-label">Select required co-working departments</span><label class="check"><input type="checkbox" name="co_working_departments" value="TMS" /> TMS (Track)</label><label class="check"><input type="checkbox" name="co_working_departments" value="SMMS" /> SMMS (Signal &amp; Telecom)</label><label class="check"><input type="checkbox" name="co_working_departments" value="TDMS" /> TDMS (Traction)</label>';
    coWorkingDepartments.querySelector('.co-working-label').style.cssText = 'font-size:11px;font-weight:700;color:#40534b;';
    coWorkingLabel.insertAdjacentElement('afterend', coWorkingDepartments);
  }
  coWorkingError = document.createElement('p');
  coWorkingError.textContent = 'Please select at least one co-working department.';
  coWorkingError.setAttribute('role', 'alert');
  coWorkingError.hidden = true;
  coWorkingError.style.cssText = 'margin:6px 0 0 21px;color:#b42318;font-size:11px;font-weight:600;';
  coWorkingDepartments.insertAdjacentElement('afterend', coWorkingError);
  coWorkingToggle.addEventListener('change', () => {
    coWorkingDepartments.hidden = !coWorkingToggle.checked;
    if (!coWorkingToggle.checked) {
      coWorkingDepartments.querySelectorAll('input[type="checkbox"]').forEach((input) => { input.checked = false; });
    }
    validateCoWorkingDepartments();
  });
  coWorkingDepartments.addEventListener('change', validateCoWorkingDepartments);
}

const fieldGuides = {
  TMS: 'Required source fields: ticket_id, track_id, km_start, km_end, defect_class, date_detected. Register the track_id as the source reference.',
  SMMS: 'Required source fields: fault_id, station_code, gear_type, failure_category, reported_ts, urgency_code. Register the station_code as the source reference.',
  TDMS: 'Required source fields: defect_no, ohe_substation, mast_from, mast_to, issue_type, scheduled_date. Register mast_from:mast_to as the source reference.'
};

tabs.forEach((tab) => tab.addEventListener('click', () => {
  tabs.forEach((item) => { item.classList.toggle('active', item === tab); item.setAttribute('aria-selected', String(item === tab)); });
  Object.entries(panels).forEach(([name, panel]) => { panel.hidden = name !== tab.dataset.tab; });
}));

document.querySelector('#task-source').addEventListener('change', (event) => {
  sourceGuide.textContent = fieldGuides[event.target.value] || 'Select a source system to view its required source fields.';
});

function lines(value) { return value.split('\n').map((item) => item.trim()).filter(Boolean); }
function formatValue(value) { return Array.isArray(value) ? value.join(', ') : String(value ?? '—'); }
function escapeHtml(value) { return String(value).replace(/[&<>'"]/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#039;', '"': '&quot;' })[character]); }

function showResult(response) {
  const complete = response.data_quality_status === 'COMPLETE';
  resultTitle.textContent = complete ? 'Normalized task ready' : 'Planner review required';
  resultCaption.textContent = complete ? 'This task has a controlled spatial reference and is ready for later planning stages.' : 'The original record is retained, but no section or kilometre value was inferred.';
  if (!complete) {
    resultOutput.className = 'result-data';
    resultOutput.innerHTML = `<span class="result-status needs-review">NEEDS REVIEW</span><div class="review-list">${response.review_reasons.map(escapeHtml).join('<br />')}</div><dl><div><dt>Ingestion ID</dt><dd>${escapeHtml(response.ingestion_id)}</dd></div><div><dt>Source system</dt><dd>${escapeHtml(response.source_system || 'Submitted record')}</dd></div></dl>`;
    return;
  }
  const task = response.normalized_task;
  const details = [['Task ID', task.id], ['Department', task.department], ['Section', task.section_id], ['KM range', `${task.start_km} – ${task.end_km}`], ['Asset', `${task.asset_type} · ${task.asset_reference}`], ['Maintenance type', task.maintenance_type], ['Duration', `${task.estimated_duration_minutes} min`], ['Coordinates', (task.section_latitude != null && task.section_longitude != null) ? `${task.section_latitude}, ${task.section_longitude}` : 'Not provided'], ['Proposed time', task.proposed_time ? new Date(task.proposed_time).toLocaleString() : 'Not provided'], ['Required crews', task.required_crews], ['Equipment', task.required_equipment], ['Traffic block', task.requires_traffic_block ? 'Required' : 'Not required'], ['Traction disconnection', task.requires_traction_disconnection ? 'Required' : 'Not required']];
  if (response.feasibility) {
    details.push(['Feasibility status', response.feasibility.status.replaceAll('_', ' ')]);
  }
  resultOutput.className = 'result-data';
  const feasibilityUrl = `/feasibility?task_id=${encodeURIComponent(task.id)}`;
  resultOutput.innerHTML = `<span class="result-status complete">COMPLETE</span><dl>${details.map(([label, value], index) => `<div class="${index > 9 ? 'full' : ''}"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(formatValue(value))}</dd></div>`).join('')}</dl><div class="form-submit"><p>Ticket ${escapeHtml(task.id)} is ready for the F-02 safety check.</p><a class="primary-button" href="${feasibilityUrl}">Validate feasibility <span>→</span></a></div>`;
}

async function request(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({ detail: 'The service returned an unreadable response.' }));
  if (!response.ok) {
    const error = new Error(Array.isArray(payload.detail) ? payload.detail.map((item) => item.msg).join('; ') : payload.detail || 'Request failed.');
    error.status = response.status;
    throw error;
  }
  return payload;
}

function showError(message) { resultTitle.textContent = 'Input needs correction'; resultCaption.textContent = message; resultOutput.className = 'empty-result'; resultOutput.innerHTML = '<p>No record was normalized. Correct the supplied input and submit again.</p>'; }

document.querySelector('#reference-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  const payload = Object.fromEntries(form.entries());
  payload.start_km = Number(payload.start_km); payload.end_km = Number(payload.end_km);
  try {
    const result = await request('/api/v1/network-references', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    resultTitle.textContent = 'Reference registered'; resultCaption.textContent = 'The controlled mapping can now be used for a matching source record. No maintenance task was created.';
    resultOutput.className = 'result-data'; resultOutput.innerHTML = `<span class="result-status complete">REGISTERED</span><dl><div><dt>Reference ID</dt><dd>${escapeHtml(result.reference_id)}</dd></div><div><dt>Section</dt><dd>${escapeHtml(result.reference.section_id)}</dd></div><div class="full"><dt>Source reference</dt><dd>${escapeHtml(result.reference.source_reference)}</dd></div></dl>`;
    formElement.reset();
  } catch (error) { showError(error.message); }
});

document.querySelector('#task-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const formElement = event.currentTarget;
  if (!validateCoWorkingDepartments()) return;
  const form = new FormData(formElement);
  let record;
  try { record = JSON.parse(form.get('record')); } catch { showError('Source record must be valid JSON. No data was submitted.'); return; }
  const payload = {
    source_system: form.get('source_system'),
    record,
    planning_context: {
      severity: form.get('severity'),
      estimated_duration_minutes: Number(form.get('estimated_duration_minutes')),
      proposed_time: form.get('proposed_time') || null,
      section_latitude: form.get('section_latitude') ? Number(form.get('section_latitude')) : null,
      section_longitude: form.get('section_longitude') ? Number(form.get('section_longitude')) : null,
      required_crews: lines(form.get('required_crews')),
      required_equipment: lines(form.get('required_equipment')),
      requires_traffic_block: form.get('requires_traffic_block') === 'on',
      requires_traction_disconnection: form.get('requires_traction_disconnection') === 'on',
      co_working_compatible: form.get('co_working_compatible') === 'on',
      co_working_departments: form.getAll('co_working_departments').filter((value) => coWorkingSystems.has(value))
    }
  };
  try { const result = await request('/api/v1/ingestion/tasks', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }); showResult(result); formElement.reset(); sourceGuide.textContent = 'Select a source system to view its required source fields.'; await loadRecords(); } catch (error) {
    const sourceId = record.ticket_id || record.fault_id || record.defect_no;
    if (error.status === 409 && sourceId) {
      const existing = (await request('/api/v1/ingestion/tasks')).find((item) => item.source_system === payload.source_system && item.source_reference === sourceId);
      if (existing) { showResult(existing); resultCaption.textContent = 'This source record was already normalized. Its existing traceable task is shown below.'; await loadRecords(); return; }
    }
    showError(error.message);
  }
});


async function loadRecords() {
  const body = document.querySelector('#records-body');
  try {
    const records = await request('/api/v1/ingestion/tasks');
    if (!records.length) { body.innerHTML = '<tr><td colspan="5" class="empty-table">No persisted records found. The system does not create sample records.</td></tr>'; return; }
    body.innerHTML = records.map((record) => `<tr><td>${escapeHtml(new Date(record.received_at).toLocaleString())}</td><td>${escapeHtml(record.source_system)}</td><td>${escapeHtml(record.source_reference || 'Not available')}</td><td class="quality-cell ${record.data_quality_status === 'COMPLETE' ? 'complete-text' : 'review-text'}">${escapeHtml(record.data_quality_status)}</td><td>${escapeHtml(record.review_reasons.join('; ') || '—')}</td></tr>`).join('');
  } catch (error) { body.innerHTML = `<tr><td colspan="5" class="empty-table">Could not load persisted records: ${escapeHtml(error.message)}</td></tr>`; }
}

document.querySelector('#refresh-records').addEventListener('click', loadRecords);
loadRecords();
