const form = document.querySelector('#whatif-form');
const simHeading = document.querySelector('#sim-heading');
const simCaption = document.querySelector('#sim-caption');
const simResult = document.querySelector('#sim-result');
const simDetail = document.querySelector('#sim-detail');
const simTrains = document.querySelector('#sim-trains');
const simWarnings = document.querySelector('#sim-warnings');
const handoff = new URLSearchParams(window.location.search);

['corridor_id', 'block_start_time', 'block_end_time'].forEach((name) => {
  const value = handoff.get(name);
  const field = form.elements[name];
  if (value && field) field.value = value;
});
const sectionId = handoff.get('section_id');
if (sectionId) {
  form.elements.section_from.value = sectionId;
  form.elements.section_to.value = sectionId;
  simCaption.textContent = `F-04 block ${handoff.get('block_id') || ''} is loaded. Confirm the section references before simulation.`;
}

const esc = v => String(v).replace(/[&<>'"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#039;','"':'&quot;'}[c]));
const dt = v => v ? new Date(v).toLocaleString() : '—';

function renderResult(report) {
  const delayed = report.regulated_trains.length;
  simHeading.textContent = `${delayed} train${delayed !== 1 ? 's' : ''} regulated`;
  simCaption.textContent = `Section: ${esc(report.section_impacted)} · Punctuality impact: ${esc(report.network_punctuality_impact_pct)}%`;

  simResult.className = 'result-data';
  simResult.innerHTML = `
    <span class="result-status ${delayed > 0 ? 'warn' : 'complete'}">${delayed > 0 ? 'IMPACT' : 'CLEAR'}</span>
    <dl>
      <div><dt>Passenger delay</dt><dd>${esc(report.total_passenger_delay_minutes)} min</dd></div>
      <div><dt>Freight delay</dt><dd>${esc(report.total_freight_delay_minutes)} min</dd></div>
      <div><dt>Trains regulated</dt><dd>${esc(delayed)}</dd></div>
      <div><dt>Punctuality impact</dt><dd>${esc(report.network_punctuality_impact_pct)}%</dd></div>
    </dl>
    <p class="muted-line">Simulation ID: ${esc(report.block_id_simulated)}</p>`;

  simDetail.hidden = false;

  if (report.regulated_trains.length) {
    simTrains.innerHTML = `
      <div class="candidate-table-wrap">
        <table class="candidate-table">
          <thead><tr>
            <th>Train</th><th>Type</th><th>Held at</th>
            <th>Delay (min)</th><th>Original arrival</th><th>Simulated arrival</th>
          </tr></thead>
          <tbody>${report.regulated_trains.map(t => `
            <tr>
              <td><strong>${esc(t.train_no)}</strong></td>
              <td>${esc(t.train_type)}</td>
              <td>${esc(t.held_at_station)}</td>
              <td class="${t.delay_minutes >= 30 ? 'ineligible-text' : 'eligible-text'}">${esc(t.delay_minutes)}</td>
              <td>${esc(dt(t.original_arrival))}</td>
              <td>${esc(dt(t.simulated_arrival))}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>`;
  } else {
    simTrains.innerHTML = '<p class="muted-line">No trains are regulated under this proposed block window.</p>';
  }

  if (report.headway_conflict_warnings && report.headway_conflict_warnings.length) {
    simWarnings.innerHTML = `<div class="review-list"><strong>Headway conflict warnings:</strong><ul>${
      report.headway_conflict_warnings.map(w => `<li>${esc(w)}</li>`).join('')}</ul></div>`;
  } else {
    simWarnings.innerHTML = '';
  }
}

form.addEventListener('submit', async ev => {
  ev.preventDefault();
  const d = new FormData(form);
  const payload = {
    corridor_id:      d.get('corridor_id').trim(),
    section_from:     d.get('section_from').trim(),
    section_to:       d.get('section_to').trim(),
    block_start_time: d.get('block_start_time'),
    block_end_time:   d.get('block_end_time'),
  };

  const trainsRaw = d.get('trains').trim();
  if (trainsRaw) {
    try {
      const parsed = JSON.parse(trainsRaw);
      if (!Array.isArray(parsed)) throw new Error('Train schedules must be a JSON array.');
      payload.trains = parsed;
    } catch (err) {
      simHeading.textContent = 'Input needs correction';
      simCaption.textContent = err.message;
      return;
    }
  }

  simHeading.textContent = 'Running simulation…';
  simCaption.textContent = 'Querying imported timetable data and applying delay model.';
  simResult.className = 'empty-result';
  simResult.innerHTML = '<div class="empty-rail"><i></i><i></i></div><p>Processing…</p>';
  simDetail.hidden = true;

  try {
    const r = await fetch('/api/v1/simulate/what-if', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const x = await r.json();
    if (!r.ok) throw new Error(Array.isArray(x.detail)
      ? x.detail.map(i => i.msg).join('; ')
      : x.detail || 'Simulation failed.');
    renderResult(x);
  } catch (err) {
    simHeading.textContent = 'Input needs correction';
    simCaption.textContent = err.message;
    simResult.className = 'empty-result';
    simResult.innerHTML = '<p>No simulation result. Review the error above.</p>';
    simDetail.hidden = true;
  }
});
