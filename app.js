const modal = document.querySelector('.modal-backdrop');
const title = document.querySelector('#modal-title');
const body = document.querySelector('#modal-body');

function navigateTo(path) {
  window.location.href = path;
}

function formatTime(value) {
  if (!value) return 'time unavailable';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function formatDateLabel(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Date unavailable' : date.toLocaleDateString([], { weekday: 'short', day: '2-digit' });
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[character]));
}

function renderOverview(data, identity) {
  const kpis = data.kpis || {};
  const availability = document.querySelector('#metric-availability');
  availability.innerHTML = `${kpis.planned_availability_pct ?? '—'}<small>%</small>`;
  document.querySelector('#metric-availability-note').textContent = kpis.availability_status || 'No approved baseline recorded';
  document.querySelector('#metric-hours').innerHTML = `${kpis.total_block_hours_used ?? '—'}<small> h</small>`;
  document.querySelector('#metric-tasks').textContent = kpis.total_scheduled_tasks ?? 0;
  document.querySelector('#metric-unscheduled').textContent = `${kpis.total_unscheduled_tasks ?? 0} unscheduled`;
  document.querySelector('#metric-sanctioned').textContent = kpis.sanction_counts?.SANCTIONED ?? 0;
  document.querySelector('#metric-proposed').textContent = `${kpis.sanction_counts?.PROPOSED ?? 0} proposed`;
  document.querySelector('#overview-data-status').innerHTML = 'Data status: <i></i> Connected to stored records';
  const profile = document.querySelector('.profile-copy');
  if (profile && identity) {
    profile.innerHTML = `<strong>${escapeHtml(identity.actor)}</strong><small>${escapeHtml(identity.role)}</small>`;
    document.querySelector('#profile-avatar').textContent = identity.actor.split(/\s+/).map((part) => part[0]).join('').slice(0, 2).toUpperCase();
  }

  const timeline = document.querySelector('#overview-timeline-rows');
  const blocks = data.blocks || [];
  const sections = data.sections || [];
  document.querySelector('#overview-section-title').textContent = sections.length ? sections.join(' / ') : 'No registered sections';
  document.querySelector('#overview-route-status').innerHTML = `<i></i> ${blocks.length ? `${blocks.length} stored block${blocks.length === 1 ? '' : 's'}` : 'No stored plan'}`;
  const dates = [...new Set(blocks.flatMap((block) => [block.effective_start, block.effective_end]).filter(Boolean).map(formatDateLabel))];
  document.querySelector('#overview-time-head').innerHTML = dates.length ? dates.map((date) => `<b>${escapeHtml(date)}</b>`).join('') : '<b>No plan horizon recorded</b>';
  timeline.innerHTML = blocks.length ? blocks.map((block) => `<div class="timeline-row"><div class="route-name"><strong>${escapeHtml(block.section_id || 'Unidentified section')}</strong><small>${escapeHtml(block.state)}</small></div><div class="track-lane"><button class="block ${block.state === 'SANCTIONED' ? 'combo-block' : 'eng-block'}" data-route="/cockpit">${escapeHtml(formatTime(block.effective_start))}–${escapeHtml(formatTime(block.effective_end))}</button></div></div>`).join('') : '<div class="timeline-row"><div class="route-name"><strong>No stored blocks</strong><small>Generate a plan after completing the required workflow.</small></div></div>';
  document.querySelector('#overview-plan-status').innerHTML = `<i class="spark"></i> ${blocks.length} stored block${blocks.length === 1 ? '' : 's'}`;

  const tasks = data.unscheduled_tasks || [];
  document.querySelector('#overview-task-list').innerHTML = tasks.length ? tasks.slice(0, 3).map((task) => `<button class="task-row" data-route="/intake"><span class="priority high">Review</span><div><strong>${escapeHtml(task.task_id || 'Unknown task')}</strong><p>${escapeHtml(task.reason || 'Unscheduled task')}</p></div><span class="row-arrow">→</span></button>`).join('') : '<p class="muted-line">No unscheduled tasks are stored.</p>';
  const activeBlocks = blocks.filter((block) => ['ACTIVE', 'EXTENSION_REQUESTED'].includes(block.state));
  document.querySelector('#overview-live-state').innerHTML = activeBlocks.length ? activeBlocks.map((block) => `<strong>${escapeHtml(block.block_id)}</strong><p>${escapeHtml(block.section_id || 'Section unavailable')} · ${escapeHtml(block.state)}</p>`).join('') : '<p class="muted-line">No active blocks are recorded.</p>';
}

async function loadOverview() {
  try {
    const [summaryResponse, identityResponse] = await Promise.all([fetch('/api/v1/cockpit/summary'), fetch('/api/v1/cockpit/identity')]);
    if (!summaryResponse.ok || !identityResponse.ok) throw new Error('Overview data unavailable');
    renderOverview(await summaryResponse.json(), await identityResponse.json());
  } catch (error) {
    document.querySelector('#overview-data-status').textContent = 'Data status: Unable to load stored records';
    console.error('Overview data error:', error);
  }
}

loadOverview();

document.addEventListener('click', (event) => {
  const routeElement = event.target.closest('[data-route]');
  if (routeElement) navigateTo(routeElement.dataset.route);
});

const messages = {
  'generate-plan': ['Weekly plan ready for review', 'The optimizer has consolidated compatible work into COA-approved windows. Review operational impact before submitting for sanction.'],
  'what-if': ['What-if simulation', 'Choose a proposed block window to compare likely train regulation, freight impact and recoverable capacity before any change is submitted.'],
  'open-plan': ['Coordinated block plan', 'The detailed weekly view will include all candidate windows, constraints, required disconnections and approval status.'],
  'open-tasks': ['Maintenance priority queue', 'Tasks are ranked with safety, overdue age, route criticality and operational impact. Each recommendation remains explainable and reviewable.']
};

function showModal(heading, description) {
  title.textContent = heading;
  body.textContent = description;
  modal.hidden = false;
  document.querySelector('.modal-close').focus();
}

document.querySelectorAll('[data-action]').forEach((button) => {
  button.addEventListener('click', () => showModal(...messages[button.dataset.action]));
});

document.querySelectorAll('.block').forEach((block) => {
  block.addEventListener('click', () => showModal(`Block ${block.dataset.block}`, `This planned window is ready to show its consolidated department tasks, required disconnections, constraints and forecast operational impact.`));
});

document.querySelectorAll('.task-row').forEach((task) => {
  task.addEventListener('click', () => showModal(task.dataset.task, 'Open the task detail to review source data, priority factors, feasibility warnings and compatible shared block opportunities.'));
});

document.querySelectorAll('.modal-close, .modal-cancel').forEach((button) => button.addEventListener('click', () => { modal.hidden = true; }));
modal.addEventListener('click', (event) => { if (event.target === modal) modal.hidden = true; });
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') modal.hidden = true; });

document.querySelectorAll('.nav-link').forEach((link) => link.addEventListener('click', () => {
  document.querySelector('.nav-link.active').classList.remove('active');
  link.classList.add('active');
}));
