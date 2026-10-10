const form = document.querySelector('#priority-form');
const taskSelect = document.querySelector('#priority-task');
const policyContainer = document.querySelector('#policy-config');
const resultHeading = document.querySelector('.result-heading h2');
const resultCaption = document.querySelector('#priority-caption');
const resultBox = document.querySelector('#priority-result');
const requestedTaskId = new URLSearchParams(window.location.search).get('task_id');

const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (character) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
}[character]));

async function loadTasks() {
  try {
    const response = await fetch('/api/v1/ingestion/tasks');
    if (!response.ok) throw new Error('Unable to load tasks.');
    const tasks = await response.json();
    taskSelect.innerHTML = '<option value="" selected disabled>Select normalized task</option>';
    tasks.filter((item) => item.data_quality_status === 'COMPLETE').forEach((item) => {
      const task = item.normalized_task;
      const option = document.createElement('option');
      option.value = task.id;
      option.textContent = `${task.id} · ${task.department} · ${task.section_id}`;
      taskSelect.appendChild(option);
    });
    if (requestedTaskId && [...taskSelect.options].some((option) => option.value === requestedTaskId)) {
      taskSelect.value = requestedTaskId;
      resultCaption.textContent = `Ticket ${requestedTaskId} passed from F-02. Confirm any active restriction, then calculate its priority.`;
    }
  } catch (error) {
    taskSelect.innerHTML = '<option value="" selected disabled>Could not load tasks</option>';
    resultCaption.textContent = error.message;
  }
}

async function loadPolicyConfig() {
  try {
    const response = await fetch('/api/policy');
    if (!response.ok) throw new Error('Unable to load COA policy.');
    const policy = await response.json();
    const slotEntries = [
      ['display-passenger-00-06', policy.passenger_00_06],
      ['display-passenger-06-12', policy.passenger_06_12],
      ['display-passenger-12-18', policy.passenger_12_18],
      ['display-passenger-18-24', policy.passenger_18_24],
      ['display-goods-00-06', policy.goods_00_06],
      ['display-goods-06-12', policy.goods_06_12],
      ['display-goods-12-18', policy.goods_12_18],
      ['display-goods-18-24', policy.goods_18_24],
    ];
    slotEntries.forEach(([id, value]) => {
      const node = document.getElementById(id);
      if (node) node.textContent = value ?? '—';
    });
    document.querySelector('#display-section-traffic').textContent = policy.section_traffic;
    document.querySelector('#display-route-criticality').textContent = policy.route_criticality;
    document.querySelector('#display-weather-weight').textContent = policy.weather_risk_weight;
    document.querySelector('#display-overdue-tolerance').textContent = policy.max_overdue_tolerance_days;
    document.querySelector('#display-tsr-penalty').textContent = policy.tsr_penalty_score;
    document.querySelector('#display-freight-weight').textContent = policy.freight_revenue_weight;
    document.querySelector('#display-policy-version').textContent = policy.policy_version;
  } catch (error) {
    policyContainer.querySelectorAll('strong, span').forEach((value) => { value.textContent = 'Unavailable'; });
    resultCaption.textContent = error.message;
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const taskId = taskSelect.value;
  const activeRestriction = document.querySelector('#restriction-toggle').checked;
  if (!taskId) {
    resultHeading.textContent = 'Select a task';
    resultCaption.textContent = 'An eligible task is required.';
    return;
  }

  try {
    const response = await fetch('/calculate-priority', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: taskId, active_restriction: activeRestriction })
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Priority calculation failed.');
    resultHeading.textContent = `${result.tier} · ${result.score}/100`;
    resultCaption.textContent = `Policy ${result.policy_version} · transparent weighted calculation`;
    resultBox.className = 'result-data';
    resultBox.innerHTML = `<span class="result-status">${escapeHtml(result.tier)}</span><dl><div><dt>Priority score</dt><dd>${escapeHtml(result.score)} / 100</dd></div><div><dt>Task</dt><dd>${escapeHtml(result.task_id)}</dd></div></dl><div class="review-list">${result.top_contributing_factors.map((factor) => `${escapeHtml(factor.factor)}: +${escapeHtml(factor.score_contribution)} points (input ${escapeHtml(factor.input_score)}, weight ${escapeHtml(factor.weight)})`).join('<br />')}</div><div class="form-submit"><p>F-03 is complete. This ticket is now eligible for the F-04 optimizer.</p><a class="primary-button" href="/planner?task_id=${encodeURIComponent(result.task_id)}">Build coordinated block plan <span>→</span></a></div>`;
  } catch (error) {
    resultHeading.textContent = 'Input needs correction';
    resultCaption.textContent = error.message;
    resultBox.className = 'empty-result';
    resultBox.innerHTML = '<p>No priority score was generated.</p>';
  }
});

loadTasks();
loadPolicyConfig();
