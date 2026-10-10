// RailSync — F-07 & F-08 Operations Cockpit JavaScript

let cockpitData = null;
let currentSelectedBlock = null;
let cockpitIdentity = null;

document.addEventListener("DOMContentLoaded", () => {
  const blockModal = document.getElementById("blockModalBackdrop");
  if (blockModal) {
    blockModal.hidden = true;
    blockModal.classList.remove("is-open");
    blockModal.style.display = "none";
  }
  initEventListeners();
  loadCockpitIdentity();
  initPolicyTuning();
  loadCockpitSummary();
});

function initEventListeners() {
  // Officer Role changes
  const roleSelect = document.getElementById("controllerRoleSelect");
  const roleDisplay = document.getElementById("headerRoleDisplay");
  if (roleSelect && roleDisplay) {
    roleSelect.addEventListener("change", (e) => {
      roleDisplay.textContent = e.target.value;
    });
  }

  // Filters and refresh
  const sectionFilter = document.getElementById("sectionFilter");
  if (sectionFilter) {
    sectionFilter.addEventListener("change", () => renderGanttTimeline());
  }

  const statusFilter = document.getElementById("statusFilter");
  if (statusFilter) {
    statusFilter.addEventListener("change", () => renderGanttTimeline());
  }

  const refreshBtn = document.getElementById("refreshCockpitBtn");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => loadCockpitSummary());
  }

  // Modal close buttons
  const closeModalBtn = document.getElementById("closeModalBtn");
  const closeModalFooterBtn = document.getElementById("closeModalFooterBtn");
  const modalBackdrop = document.getElementById("blockModalBackdrop");
  if (closeModalBtn) closeModalBtn.addEventListener("click", closeModal);
  if (closeModalFooterBtn) closeModalFooterBtn.addEventListener("click", closeModal);
  if (modalBackdrop) {
    modalBackdrop.addEventListener("click", (e) => {
      if (e.target === modalBackdrop) closeModal();
    });
  }

  // Action Radio Buttons toggle override fields
  const actionRadios = document.querySelectorAll('input[name="blockActionChoice"]');
  const overrideBox = document.getElementById("overrideInputsBox");
  actionRadios.forEach((radio) => {
    radio.addEventListener("change", (e) => {
      if (overrideBox) {
        overrideBox.hidden = !["OVERRIDE", "EXTEND"].includes(e.target.value);
      }
    });
  });

  // Justification character counter
  const justInput = document.getElementById("justificationNotesInput");
  const justCount = document.getElementById("justificationCharCount");
  if (justInput && justCount) {
    justInput.addEventListener("input", (e) => {
      const len = e.target.value.trim().length;
      justCount.textContent = `(${len} chars, min 5 required)`;
      justCount.style.color = len >= 5 ? "#34d399" : "#f87171";
    });
  }

  // Submit Action Button
  const submitActionBtn = document.getElementById("submitBlockActionBtn");
  if (submitActionBtn) {
    submitActionBtn.addEventListener("click", submitBlockAction);
  }

  // Export Buttons
  const exportPdfBtn = document.getElementById("exportPdfBtn");
  if (exportPdfBtn) {
    exportPdfBtn.addEventListener("click", () => {
      if (currentSelectedBlock) {
        window.open(`/api/v1/blocks/${encodeURIComponent(currentSelectedBlock.block_id)}/export-memo?format=pdf`, "_blank");
      }
    });
  }

  const exportHtmlBtn = document.getElementById("exportHtmlBtn");
  if (exportHtmlBtn) {
    exportHtmlBtn.addEventListener("click", () => {
      if (currentSelectedBlock) {
        window.open(`/api/v1/blocks/${encodeURIComponent(currentSelectedBlock.block_id)}/export-memo?format=html`, "_blank");
      }
    });
  }

  const exportJsonBtn = document.getElementById("exportJsonBtn");
  if (exportJsonBtn) {
    exportJsonBtn.addEventListener("click", () => {
      if (currentSelectedBlock) {
        exportCurrentBlockDecisionJson();
      }
    });
  }

  // Global Audit Drawer
  const openAuditBtn = document.getElementById("openAuditLedgerBtn");
  const closeAuditBtn = document.getElementById("closeAuditDrawerBtn");
  const auditDrawer = document.getElementById("auditDrawerBackdrop");
  if (openAuditBtn) {
    openAuditBtn.addEventListener("click", openAuditDrawer);
  }
  if (closeAuditBtn) {
    closeAuditBtn.addEventListener("click", () => {
      if (auditDrawer) auditDrawer.hidden = true;
    });
  }
  if (auditDrawer) {
    auditDrawer.addEventListener("click", (e) => {
      if (e.target === auditDrawer) auditDrawer.hidden = true;
    });
  }

  // What-If Drawer
  const openWhatIfBtn = document.getElementById("openWhatIfDrawerBtn");
  const closeWhatIfBtn = document.getElementById("closeWhatIfDrawerBtn");
  const whatIfDrawer = document.getElementById("whatIfDrawerBackdrop");
  const modalWhatIfBtn = document.getElementById("modalWhatIfBtn");
  if (openWhatIfBtn) {
    openWhatIfBtn.addEventListener("click", () => openWhatIfDrawer());
  }
  if (modalWhatIfBtn) {
    modalWhatIfBtn.addEventListener("click", () => openWhatIfDrawer());
  }
  if (closeWhatIfBtn) {
    closeWhatIfBtn.addEventListener("click", () => {
      if (whatIfDrawer) whatIfDrawer.hidden = true;
    });
  }
  if (whatIfDrawer) {
    whatIfDrawer.addEventListener("click", (e) => {
      if (e.target === whatIfDrawer) whatIfDrawer.hidden = true;
    });
  }

  // What-If Form Submission
  const whatIfForm = document.getElementById("cockpitWhatIfForm");
  if (whatIfForm) {
    whatIfForm.addEventListener("submit", handleWhatIfSubmit);
  }
}

async function loadCockpitSummary() {
  try {
    const res = await fetch("/api/v1/cockpit/summary");
    if (!res.ok) {
      throw new Error(`Failed to load summary: ${res.statusText}`);
    }
    cockpitData = await res.json();
    renderKPIs(cockpitData.kpis);
    populateSectionFilter(cockpitData.sections);
    renderGanttTimeline();
    renderDeferredWork(cockpitData.unscheduled_tasks);
  } catch (err) {
    console.error("Cockpit summary error:", err);
  }
}

async function loadCockpitIdentity() {
  const actorInput = document.getElementById("controllerNameInput");
  const roleSelect = document.getElementById("controllerRoleSelect");
  const roleDisplay = document.getElementById("headerRoleDisplay");
  if (!actorInput || !roleSelect || !roleDisplay) return;

  try {
    const response = await fetch("/auth/identity");
    if (!response.ok) throw new Error("Configured operator identity is unavailable.");
    const identity = await response.json();
    cockpitIdentity = {
      ...identity,
      actor: identity.officer_name,
      role: identity.authorized_role,
    };
    actorInput.value = identity.officer_name;
    actorInput.readOnly = false;
    actorInput.placeholder = "Enter officer name";
    roleSelect.value = identity.authorized_role;
    roleSelect.disabled = false;
    roleDisplay.textContent = `${identity.officer_name} · COA Active`;
    roleDisplay.closest(".user-role-badge")?.classList.add("identity-active");
    const hint = document.getElementById("authorityIdentityHint");
    if (hint) hint.textContent = "You may edit these fields; submitted decisions must match the configured server identity.";
  } catch (error) {
    cockpitIdentity = null;
    actorInput.value = "";
    actorInput.placeholder = "Server identity unavailable";
    actorInput.readOnly = false;
    roleSelect.disabled = false;
    roleDisplay.textContent = "Identity unavailable";
    roleDisplay.closest(".user-role-badge")?.classList.remove("identity-active");
    const hint = document.getElementById("authorityIdentityHint");
    if (hint) hint.textContent = "Server identity is unavailable; decisions cannot be submitted until it is configured.";
    console.error("Cockpit identity error:", error);
  }
}

function policyInputs() {
  return {
    passenger_00_06: document.getElementById("policy-passenger-00-06"),
    passenger_06_12: document.getElementById("policy-passenger-06-12"),
    passenger_12_18: document.getElementById("policy-passenger-12-18"),
    passenger_18_24: document.getElementById("policy-passenger-18-24"),
    goods_00_06: document.getElementById("policy-goods-00-06"),
    goods_06_12: document.getElementById("policy-goods-06-12"),
    goods_12_18: document.getElementById("policy-goods-12-18"),
    goods_18_24: document.getElementById("policy-goods-18-24"),
    section_traffic: document.getElementById("policy-section-traffic"),
    route_criticality: document.getElementById("policy-route-criticality"),
    weather_risk_weight: document.getElementById("policy-weather-weight"),
    max_overdue_tolerance_days: document.getElementById("policy-overdue-tolerance"),
    tsr_penalty_score: document.getElementById("policy-tsr-penalty"),
    freight_revenue_weight: document.getElementById("policy-freight-weight"),
    policy_version: document.getElementById("policy-version"),
  };
}

function setPolicyInputs(policy) {
  const inputs = policyInputs();
  Object.entries(inputs).forEach(([key, input]) => {
    if (input && policy[key] !== undefined) input.value = policy[key];
  });
}

async function loadPolicyTuning() {
  const response = await fetch("/api/policy");
  if (!response.ok) throw new Error("COA policy could not be loaded.");
  setPolicyInputs(await response.json());
}

function showPolicyMessage(message, isError = false) {
  const messageElement = document.getElementById("policy-tuning-message");
  if (!messageElement) return;
  messageElement.textContent = message;
  messageElement.style.color = isError ? "#b42318" : "#0f766e";
}

async function savePolicyTuning(event) {
  event.preventDefault();
  const inputs = policyInputs();
  const payload = Object.fromEntries(Object.entries(inputs).map(([key, input]) => [
    key,
    key === "policy_version" ? input.value.trim() : Number(input.value),
  ]));

  try {
    const response = await fetch("/api/policy", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const policy = await response.json();
    if (!response.ok) throw new Error(policy.detail || "COA policy update failed.");
    setPolicyInputs(policy);
    showPolicyMessage("Policy updated successfully.");
  } catch (error) {
    showPolicyMessage(error.message, true);
  }
}

function initPolicyTuning() {
  const form = document.getElementById("policy-tuning-form");
  if (!form) return;
  form.addEventListener("submit", savePolicyTuning);
  loadPolicyTuning().catch((error) => showPolicyMessage(error.message, true));
}

function renderKPIs(kpis) {
  if (!kpis) return;
  document.getElementById("kpiHoursSaved").innerHTML = `${kpis.total_block_hours_saved ?? "—"}<small> hrs</small>`;
  document.getElementById("kpiAvailability").innerHTML = `${kpis.planned_availability_pct ?? "—"}<small>%</small>`;
  document.getElementById("kpiHoursUsed").textContent = `${kpis.total_block_hours_used ?? "—"} hrs corridor maintenance`;
  document.getElementById("kpiBlockCount").textContent = kpis.total_scheduled_blocks || 0;
  document.getElementById("kpiTaskCount").textContent = `${kpis.total_scheduled_tasks || 0} maintenance tasks`;
  document.getElementById("kpiDeferredCount").textContent = kpis.total_unscheduled_tasks || 0;

  const counts = kpis.sanction_counts || {};
  document.getElementById("chipCountProposed").textContent = counts.PROPOSED || 0;
  document.getElementById("chipCountSanctioned").textContent = counts.SANCTIONED || 0;
  document.getElementById("chipCountOverridden").textContent = counts.OVERRIDDEN || 0;
}

function populateSectionFilter(sections) {
  const filter = document.getElementById("sectionFilter");
  if (!filter || !sections) return;
  const currentVal = filter.value;
  filter.innerHTML = '<option value="ALL">All Registered Sections</option>';
  sections.forEach((s) => {
    const opt = document.createElement("option");
    opt.value = s;
    opt.textContent = s;
    filter.appendChild(opt);
  });
  if (currentVal) filter.value = currentVal;
}

function renderGanttTimeline() {
  const emptyState = document.getElementById("timelineEmptyState");
  const ganttBoard = document.getElementById("ganttBoard");
  if (!emptyState || !ganttBoard) return;

  const blocks = cockpitData?.blocks || [];
  if (blocks.length === 0) {
    emptyState.hidden = false;
    ganttBoard.hidden = true;
    return;
  }

  emptyState.hidden = true;
  ganttBoard.hidden = false;

  const sectionFilter = document.getElementById("sectionFilter").value;
  const statusFilter = document.getElementById("statusFilter").value;

  const filtered = blocks.filter((b) => {
    if (sectionFilter !== "ALL" && b.section_id !== sectionFilter) return false;
    if (statusFilter !== "ALL" && b.state !== statusFilter) return false;
    return true;
  });

  if (filtered.length === 0) {
    ganttBoard.innerHTML = `
      <div class="timeline-empty-state">
        <div class="empty-icon">🔍</div>
        <h3>No matching blocks</h3>
        <p>No blocks matched the selected section or sanction state filter.</p>
      </div>
    `;
    return;
  }

  // Group by section
  const grouped = {};
  filtered.forEach((b) => {
    const sec = b.section_id || "UNASSIGNED";
    if (!grouped[sec]) grouped[sec] = [];
    grouped[sec].push(b);
  });

  ganttBoard.innerHTML = "";

  const timestamps = filtered.flatMap((block) => [Date.parse(block.effective_start || block.scheduled_start), Date.parse(block.effective_end || block.scheduled_end)]).filter(Number.isFinite);
  const domainStart = Math.min(...timestamps);
  const domainEnd = Math.max(...timestamps);
  const domainLength = Math.max(domainEnd - domainStart, 60 * 60 * 1000);
  const axis = document.createElement("div");
  axis.className = "gantt-time-axis";
  for (let index = 0; index <= 4; index += 1) {
    const marker = document.createElement("span");
    const timestamp = domainStart + (domainLength * index / 4);
    marker.style.left = `${index * 25}%`;
    marker.textContent = new Date(timestamp).toISOString().slice(0, 16).replace("T", " ") + " UTC";
    axis.appendChild(marker);
  }
  ganttBoard.appendChild(axis);

  Object.entries(grouped).forEach(([secId, secBlocks]) => {
    const rowEl = document.createElement("div");
    rowEl.className = "gantt-section-row";

    const headerEl = document.createElement("div");
    headerEl.className = "section-row-header";
    headerEl.innerHTML = `
      <span class="section-row-title">${escapeHtml(secId)}</span>
      <span class="badge badge-proposed">${secBlocks.length} Window${secBlocks.length > 1 ? "s" : ""}</span>
    `;

    const laneEl = document.createElement("div");
    laneEl.className = "section-row-blocks-lane";

    secBlocks.forEach((b) => {
      const pill = createBlockPill(b, domainStart, domainLength);
      laneEl.appendChild(pill);
    });

    rowEl.appendChild(headerEl);
    rowEl.appendChild(laneEl);
    ganttBoard.appendChild(rowEl);
  });
}

function createBlockPill(block, domainStart, domainLength) {
  const pill = document.createElement("div");
  const stateCls = `state-${(block.state || "proposed").toLowerCase()}`;
  pill.className = `block-pill ${stateCls}`;
  const blockStart = Date.parse(block.effective_start || block.scheduled_start);
  const blockEnd = Date.parse(block.effective_end || block.scheduled_end);
  const left = Number.isFinite(blockStart) ? Math.max(0, ((blockStart - domainStart) / domainLength) * 100) : 0;
  const width = Number.isFinite(blockStart) && Number.isFinite(blockEnd) ? Math.max(4, ((blockEnd - blockStart) / domainLength) * 100) : 8;
  pill.style.left = `${left}%`;
  pill.style.width = `${width}%`;
  pill.setAttribute("role", "button");
  pill.setAttribute("tabindex", "0");

  const depts = block.consolidated_departments || [];
  let segmentsHtml = "";
  if (depts.includes("ENGINEERING")) segmentsHtml += '<div class="seg-eng" title="Engineering"></div>';
  if (depts.includes("SIGNAL_TELECOM")) segmentsHtml += '<div class="seg-st" title="Signal & Telecom"></div>';
  if (depts.includes("TRACTION")) segmentsHtml += '<div class="seg-trd" title="Traction"></div>';
  if (!segmentsHtml) segmentsHtml = '<div class="seg-eng" style="background:#475569;"></div>';

  const badgeCls = `badge-${(block.state || "proposed").toLowerCase()}`;
  const startStr = block.effective_start ? formatIsoTime(block.effective_start) : "Not recorded";
  const endStr = block.effective_end ? formatIsoTime(block.effective_end) : "Not recorded";

  pill.innerHTML = `
    <div class="block-pill-top">
      <span class="pill-id">${escapeHtml(block.block_id)}</span>
      <span class="pill-state-badge ${badgeCls}">${escapeHtml(block.state || "PROPOSED")}</span>
    </div>
    <div class="pill-dept-segments">${segmentsHtml}</div>
    <div class="pill-time-slot">
      ${startStr} – ${endStr} UTC (${block.duration_minutes || 0}m)
    </div>
    <div class="pill-meta-foot">
      <span>${(block.assigned_tasks || []).length} tasks</span>
      <div class="precautions-icons">
        ${block.requires_traffic_block ? '<span title="Traffic Block Required">🛑</span>' : ""}
        ${block.requires_traction_disconnection ? '<span title="Traction Disconnection Required">⚡</span>' : ""}
      </div>
    </div>
  `;

  pill.addEventListener("click", () => openBlockModal(block));
  pill.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openBlockModal(block);
    }
  });

  return pill;
}

function openBlockModal(block) {
  currentSelectedBlock = block;
  const modal = document.getElementById("blockModalBackdrop");
  if (!modal) return;
  let liveMonitorButton = document.getElementById("openLiveMonitorBtn");
  if (!liveMonitorButton) {
    const whatIfButton = document.getElementById("modalWhatIfBtn");
    liveMonitorButton = document.createElement("button");
    liveMonitorButton.id = "openLiveMonitorBtn";
    liveMonitorButton.type = "button";
    liveMonitorButton.className = "outline-button compact";
    liveMonitorButton.textContent = "Open F-06 live monitor";
    whatIfButton?.insertAdjacentElement("afterend", liveMonitorButton);
    liveMonitorButton.addEventListener("click", () => {
      const query = new URLSearchParams({ block_id: currentSelectedBlock.block_id, corridor_id: currentSelectedBlock.corridor_id || currentSelectedBlock.section_id });
      window.location.href = `/live-monitor?${query.toString()}`;
    });
  }
  const executable = ["SANCTIONED", "OVERRIDDEN", "ACTIVE"].includes(block.state);
  liveMonitorButton.disabled = !executable;
  liveMonitorButton.title = executable ? "Open this controller-approved block in F-06." : "F-06 is enabled after controller sanction or activation.";

  document.getElementById("modalBlockTitle").textContent = `Block ${block.block_id}`;
  document.getElementById("mSection").textContent = block.section_id;

  const stateBadge = document.getElementById("mStateBadge");
  stateBadge.className = `status-badge badge-${(block.state || "proposed").toLowerCase()}`;
  stateBadge.textContent = block.state || "PROPOSED";

  document.getElementById("mScheduledWindow").textContent = `${formatIsoDateTime(block.scheduled_start)} to ${formatIsoDateTime(block.scheduled_end)}`;
  document.getElementById("mEffectiveWindow").textContent = `${formatIsoDateTime(block.effective_start)} to ${formatIsoDateTime(block.effective_end)}`;
  document.getElementById("mDuration").textContent = `${block.duration_minutes} mins (${(block.duration_minutes / 60).toFixed(2)} hrs)`;
  document.getElementById("mTrafficBlock").textContent = block.requires_traffic_block ? "YES (Absolute Traffic Protection)" : "NO";
  document.getElementById("mTractionDisconnection").textContent = block.requires_traction_disconnection ? "YES (25kV Power Disconnection)" : "NO";
  document.getElementById("mTrainImpact").textContent = `${block.passenger_trains_affected || 0} passenger · ${block.goods_trains_affected || 0} goods affected`;

  // Render Tasks
  const tbody = document.getElementById("mTasksTableBody");
  tbody.innerHTML = "";
  (block.assigned_tasks || []).forEach((t) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><strong>${escapeHtml(t.task_id || "")}</strong></td>
      <td><span class="dept-pill dept-${escapeHtml(String(t.department || "").toLowerCase())}">${escapeHtml(t.department || "")}</span></td>
      <td>${escapeHtml(t.maintenance_type || "Not recorded")}</td>
      <td>${escapeHtml(t.km_span || "Not recorded")}</td>
      <td><strong>${t.priority_score != null ? t.priority_score : "N/A"}</strong></td>
      <td>${t.scheduled_start ? formatIsoTime(t.scheduled_start) : "Not recorded"}–${t.scheduled_end ? formatIsoTime(t.scheduled_end) : "Not recorded"}</td>
      <td><strong>${escapeHtml(t.feasibility?.status || "NOT_ASSESSED")}</strong><br/><small>${escapeHtml((t.feasibility?.warning_reasons || []).join(" ") || `Data quality: ${t.data_quality_status || "UNKNOWN"}`)}</small></td>
    `;
    tbody.appendChild(tr);
  });

  // F-07: Render Priority Factor Contribution Breakdown
  const breakdownHeading = document.getElementById("mPriorityBreakdownHeading");
  const breakdownBox = document.getElementById("mPriorityBreakdown");
  const tasksWithPriority = (block.assigned_tasks || []).filter((t) => t.priority_evaluation);
  if (tasksWithPriority.length > 0) {
    breakdownHeading.hidden = false;
    breakdownBox.hidden = false;
    breakdownBox.innerHTML = "";
    tasksWithPriority.forEach((t) => {
      const pe = t.priority_evaluation;
      const factors = pe.top_contributing_factors || [];
      const maxContrib = Math.max(...factors.map((f) => f.score_contribution || 0), 1);
      const tierClass = (pe.tier || "medium").toLowerCase();
      const taskBlock = document.createElement("div");
      taskBlock.className = "priority-task-breakdown";
      taskBlock.innerHTML = `
        <div class="priority-task-header">
          <span class="priority-task-id">${escapeHtml(t.task_id || "")}</span>
          <span class="priority-score-badge tier-${tierClass}">Score: ${pe.score != null ? pe.score.toFixed(1) : "N/A"} — ${escapeHtml(pe.tier || "UNKNOWN")}</span>
          <span class="priority-policy-ref">Policy: ${escapeHtml(pe.policy_version || "—")}</span>
        </div>
        <div class="factor-bars">
          ${factors.map((f) => {
            const barPct = maxContrib > 0 ? Math.round((f.score_contribution / maxContrib) * 100) : 0;
            const deptColor = t.department === "ENGINEERING" ? "#f97316" : t.department === "SIGNAL_TELECOM" ? "#3b82f6" : "#a855f7";
            return `
              <div class="factor-row">
                <span class="factor-label" title="${escapeHtml(f.factor)}">${escapeHtml(f.factor)}</span>
                <div class="factor-bar-track">
                  <div class="factor-bar-fill" style="width:${barPct}%;background:${deptColor};" title="${f.score_contribution != null ? f.score_contribution.toFixed(2) : "0"} pts"></div>
                </div>
                <span class="factor-score">${f.score_contribution != null ? "+" + f.score_contribution.toFixed(2) : "0"}</span>
              </div>`;
          }).join("")}
        </div>
        ${pe.explanation ? `<p class="priority-explanation">${escapeHtml(pe.explanation)}</p>` : ""}
      `;
      breakdownBox.appendChild(taskBlock);
    });
  } else {
    breakdownHeading.hidden = true;
    breakdownBox.hidden = true;
    breakdownBox.innerHTML = "";
  }

  // Action Panel reset
  document.querySelector('input[name="blockActionChoice"][value="APPROVE"]').checked = true;
  document.getElementById("overrideInputsBox").hidden = true;
  document.getElementById("actionStatusMsg").textContent = "";

  // Pre-fill override date inputs
  const startIso = block.effective_start ? block.effective_start.substring(0, 16) : "";
  const endIso = block.effective_end ? block.effective_end.substring(0, 16) : "";
  document.getElementById("overrideStartInput").value = startIso;
  document.getElementById("overrideEndInput").value = endIso;

  const justInput = document.getElementById("justificationNotesInput");
  justInput.value = "";
  document.getElementById("justificationCharCount").textContent = "(0 chars, min 5 required)";
  document.getElementById("justificationCharCount").style.color = "#f87171";

  // Load audit history for this block
  loadBlockAuditHistory(block.block_id);

  modal.hidden = false;
  modal.classList.add("is-open");
  modal.style.display = "grid";
}

function closeModal() {
  const modal = document.getElementById("blockModalBackdrop");
  if (modal) {
    modal.hidden = true;
    modal.classList.remove("is-open");
    modal.style.display = "none";
  }
  currentSelectedBlock = null;
}

async function submitBlockAction() {
  if (!currentSelectedBlock) return;

  const actor = document.getElementById("controllerNameInput").value.trim();
  const role = document.getElementById("controllerRoleSelect").value;
  const actionChoice = document.querySelector('input[name="blockActionChoice"]:checked')?.value || "APPROVE";
  const reasonCode = document.getElementById("reasonCodeSelect").value;
  const notes = document.getElementById("justificationNotesInput").value.trim();
  const statusMsg = document.getElementById("actionStatusMsg");

  if (!actor) {
    statusMsg.className = "status-msg error";
    statusMsg.textContent = "Please enter officer/controller name.";
    return;
  }

  if (notes.length < 5) {
    statusMsg.className = "status-msg error";
    statusMsg.textContent = "Justification must be at least 5 characters.";
    return;
  }

  if (!cockpitIdentity) {
    statusMsg.className = "status-msg error";
    statusMsg.textContent = "Server identity is unavailable; refresh after configuring the authority.";
    return;
  }

  if (actor !== cockpitIdentity.actor || role !== cockpitIdentity.role) {
    statusMsg.className = "status-msg error";
    statusMsg.textContent = "Officer name and role must match the configured server identity.";
    return;
  }

  const payload = {
    plan_id: currentSelectedBlock.plan_id,
    actor: actor,
    role: role,
    action: actionChoice,
    reason_code: reasonCode,
    justification_notes: notes,
  };

  if (["OVERRIDE", "EXTEND"].includes(actionChoice)) {
    const modStart = document.getElementById("overrideStartInput").value;
    const modEnd = document.getElementById("overrideEndInput").value;
    if (modStart) payload.modified_start = datetimeLocalToUtcIso(modStart);
    if (modEnd) payload.modified_end = datetimeLocalToUtcIso(modEnd);
  }

  statusMsg.className = "status-msg";
  statusMsg.textContent = "Submitting decision to regulatory ledger...";

  try {
    const res = await fetch(`/api/v1/blocks/${encodeURIComponent(currentSelectedBlock.block_id)}/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || res.statusText);
    }

    const data = await res.json();

    // F-08: Automatically generate and transfer JSON decision records to the respective department(s) who raised input work tickets
    const transferredFiles = dispatchDepartmentalTransferJsonFiles(
      currentSelectedBlock,
      actionChoice,
      reasonCode,
      notes,
      actor,
      role,
      data
    );

    const deptsSummary = transferredFiles
      .map((f) => `<strong>${escapeHtml(f.department)}</strong> (${f.ticketCount} ticket${f.ticketCount === 1 ? "" : "s"})`)
      .join(", ");

    statusMsg.className = "status-msg success";
    statusMsg.innerHTML = `Decision logged! State is now <strong>${data.state}</strong>. Audit reference: <code>${data.audit_id.substring(0, 8)}</code>.<br/><span style="display:inline-block;margin-top:5px;color:#0d9488;font-weight:600;">📤 Generated &amp; transferred decision JSON to originating department(s): ${deptsSummary}</span>`;

    // Refresh block data
    currentSelectedBlock.state = data.state;
    if (data.effective_start) currentSelectedBlock.effective_start = data.effective_start;
    if (data.effective_end) currentSelectedBlock.effective_end = data.effective_end;

    // Update modal badge
    const stateBadge = document.getElementById("mStateBadge");
    stateBadge.className = `status-badge badge-${data.state.toLowerCase()}`;
    stateBadge.textContent = data.state;
    document.getElementById("mEffectiveWindow").textContent = `${formatIsoDateTime(data.effective_start)} to ${formatIsoDateTime(data.effective_end)}`;

    // Refresh audit history in modal
    loadBlockAuditHistory(currentSelectedBlock.block_id);

    // Refresh cockpit summary in background
    loadCockpitSummary();
  } catch (err) {
    statusMsg.className = "status-msg error";
    statusMsg.textContent = `Error: ${err.message}`;
  }
}

/**
 * Downloads arbitrary JSON data as a formatted .json file in the user's browser.
 */
function downloadJsonFile(filename, jsonData) {
  const jsonStr = typeof jsonData === "string" ? jsonData : JSON.stringify(jsonData, null, 2);
  const blob = new Blob([jsonStr], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  setTimeout(() => URL.revokeObjectURL(url), 3000);
}

/**
 * Department metadata lookup for Indian Railways maintenance systems (TMS, SMMS, TDMS).
 */
function getDepartmentMetadata(deptCode) {
  const code = (deptCode || "").toUpperCase();
  switch (code) {
    case "ENGINEERING":
      return {
        code: "ENGINEERING",
        name: "Civil Engineering (Permanent Way & Track Maintenance)",
        target_system: "TMS (Track Management System)",
        responsible_roles: ["Section Engineer (P-Way)", "Assistant Divisional Engineer", "Permanent Way Inspector"],
        contact_channel: "tms-dispatch@railnet.gov.in",
      };
    case "SIGNAL_TELECOM":
      return {
        code: "SIGNAL_TELECOM",
        name: "Signal & Telecommunication Department (S&T)",
        target_system: "SMMS (Signal Maintenance Management System)",
        responsible_roles: ["Section Engineer (Signals)", "Signal Inspector", "Telecom Maintenance In-Charge"],
        contact_channel: "smms-dispatch@railnet.gov.in",
      };
    case "TRACTION":
      return {
        code: "TRACTION",
        name: "Electrical / Traction Distribution Department (TRD)",
        target_system: "TDMS (Traction Distribution Management System)",
        responsible_roles: ["Traction Power Controller (TPC)", "Section Engineer (OHE)", "Sub-Station Operator"],
        contact_channel: "tdms-dispatch@railnet.gov.in",
      };
    default:
      return {
        code: code || "GENERAL",
        name: code ? `${code} Maintenance Department` : "General Operations",
        target_system: `${code || "DEPT"}-Work-Management-System`,
        responsible_roles: ["Departmental Maintenance Supervisor", "Section Controller"],
        contact_channel: "control-dispatch@railnet.gov.in",
      };
  }
}

/**
 * Builds a formal departmental transfer & decision dispatch record specifically addressed
 * to the department that originated the work ticket(s).
 */
function buildDepartmentTransferJson(deptCode, deptTasks, block, actionChoice, reasonCode, notes, actor, role, serverData) {
  const deptMeta = getDepartmentMetadata(deptCode);
  const effStart = serverData?.effective_start || block.effective_start;
  const effEnd = serverData?.effective_end || block.effective_end;
  const state = serverData?.state || block.state || "PROPOSED";
  const isShifted = ["OVERRIDE", "EXTEND"].includes(actionChoice);

  let directive = "";
  if (actionChoice === "APPROVE" || state === "SANCTIONED") {
    directive = "SANCTION GRANTED: Department field maintenance party is authorized to occupy designated track section within the effective window under Section Controller clearance.";
  } else if (actionChoice === "OVERRIDE" || state === "OVERRIDDEN") {
    directive = "REGULATORY SCHEDULE SHIFT: Possession window has been modified by Section Controller due to traffic/operational constraints. Work must be executed strictly within the updated effective window.";
  } else if (actionChoice === "REJECT" || state === "REJECTED") {
    directive = "SANCTION REJECTED: Track possession denied due to critical corridor constraints. Do not occupy track. Work ticket remains unfulfilled and must be resubmitted.";
  } else if (actionChoice === "ACTIVATE" || state === "ACTIVE") {
    directive = "BLOCK ACTIVE: Track possession is in effect. Field supervisor must maintain continuous VHF/telecom contact with Section Controller.";
  } else if (actionChoice === "RESTORE") {
    directive = "RESTORATION REPORTED: Track section reported cleared and normalized by field party. Awaiting formal ledger completion.";
  } else if (actionChoice === "COMPLETE" || state === "COMPLETED") {
    directive = "BLOCK COMPLETED: Possession normalized in tamper-evident ledger and section reopened for commercial operations.";
  } else {
    directive = `DECISION APPLIED (${actionChoice}): Updated state is ${state}. Review attached regulatory justification.`;
  }

  return {
    dispatch_header: {
      document_type: "DEPARTMENT_WORK_TICKET_SANCTION_TRANSFER",
      version: "1.1",
      transfer_protocol: "RAILSYNC_F08_REGULATORY_DECISION_DISPATCH",
      dispatch_timestamp_utc: new Date().toISOString(),
      handover_status: "DISPATCHED_TO_DEPARTMENT",
    },
    audit_ledger_proof: {
      audit_id: serverData?.audit_id || null,
      entry_hash: serverData?.entry_hash || null,
      timestamp_utc: serverData?.timestamp || new Date().toISOString(),
      ledger_authenticity: "COMMITTED_IMMUTABLE",
    },
    transferred_to_department: {
      department_code: deptMeta.code,
      department_name: deptMeta.name,
      originating_work_system: deptMeta.target_system,
      authorized_recipient_roles: deptMeta.responsible_roles,
      dispatch_channel: deptMeta.contact_channel,
    },
    regulatory_decision: {
      action_taken: actionChoice,
      block_status: state,
      regulatory_reason_code: reasonCode,
      controller_justification_notes: notes,
      sanctioning_authority: {
        officer_name: actor,
        role: role,
        server_verified_identity: cockpitIdentity ? { actor: cockpitIdentity.actor, role: cockpitIdentity.role } : null,
        issuing_control_office: "Section Operating Control Center (DRM Office)",
      },
    },
    authorized_possession_window: {
      block_id: block.block_id,
      plan_id: block.plan_id,
      section_id: block.section_id,
      scheduled_start: block.scheduled_start,
      scheduled_end: block.scheduled_end,
      effective_start: effStart,
      effective_end: effEnd,
      duration_minutes: block.duration_minutes,
      schedule_shifted_by_controller: isShifted,
      absolute_traffic_block_granted: Boolean(block.requires_traffic_block),
      traction_power_cut_granted: Boolean(block.requires_traction_disconnection),
      timetable_reference: block.timetable_reference || null,
      goods_forecast_reference: block.goods_forecast_reference || null,
    },
    transferred_work_tickets: (deptTasks || []).map((t) => ({
      work_ticket_id: t.task_id,
      department: t.department,
      originating_system: deptMeta.target_system,
      maintenance_type: t.maintenance_type,
      priority_score: t.priority_score,
      kilometre_span: t.km_span,
      start_km: t.start_km,
      end_km: t.end_km,
      asset_reference: t.asset_reference,
      required_crews: t.required_crews || [],
      data_quality_status: t.data_quality_status,
      ticket_sanction_outcome: state,
      operational_directive: directive,
    })),
    joint_corridor_departments: block.consolidated_departments || [deptMeta.code],
    field_safety_mandates: [
      "Field supervisor must confirm banner flag / detonator protection before placing machines or crew on track.",
      block.requires_traction_disconnection
        ? "MANDATORY: Confirm 25kV OHE isolation and earthing permit from TPC before beginning work."
        : "CAUTION: 25kV overhead line remains ENERGIZED. Maintain strict 2.0-meter safety clearance.",
      "Any required time extension must be formally requested to the Section Controller minimum 15 minutes before window expiry.",
      "Upon completion, site supervisor must formally issue Track Fit / Restoration Certificate to Operating Control.",
    ],
  };
}

/**
 * Generates and downloads departmental JSON transfer files for each department
 * that submitted work tickets in the given block.
 */
function dispatchDepartmentalTransferJsonFiles(block, actionChoice, reasonCode, notes, actor, role, serverData) {
  const tasks = block.assigned_tasks || [];
  const deptMap = {};

  if (tasks.length === 0) {
    const fallbackDept = (block.consolidated_departments && block.consolidated_departments[0]) || "ENGINEERING";
    deptMap[fallbackDept] = [];
  } else {
    tasks.forEach((t) => {
      const dept = t.department || "ENGINEERING";
      if (!deptMap[dept]) deptMap[dept] = [];
      deptMap[dept].push(t);
    });
  }

  const generatedFiles = [];
  const sanitizedBlockId = (block.block_id || "BLOCK").replace(/[^a-zA-Z0-9_-]/g, "_");
  const timestampStr = new Date().toISOString().replace(/[:.]/g, "-");

  const deptEntries = Object.entries(deptMap);
  deptEntries.forEach(([dept, deptTasks], idx) => {
    const transferData = buildDepartmentTransferJson(
      dept,
      deptTasks,
      block,
      actionChoice,
      reasonCode,
      notes,
      actor,
      role,
      serverData
    );
    const filename = `department_transfer_${dept}_${sanitizedBlockId}_${actionChoice.toLowerCase()}_${timestampStr}.json`;

    // Stagger slightly if multiple departments to avoid browser download blocking
    setTimeout(() => {
      downloadJsonFile(filename, transferData);
    }, idx * 200);

    generatedFiles.push({
      department: dept,
      ticketCount: deptTasks.length,
      filename: filename,
    });
  });

  return generatedFiles;
}

/**
 * On-demand export of the current selected block's departmental decision transfers.
 */
function exportCurrentBlockDecisionJson() {
  if (!currentSelectedBlock) return;
  const actor = document.getElementById("controllerNameInput")?.value.trim() || cockpitIdentity?.actor || "";
  const role = document.getElementById("controllerRoleSelect")?.value || cockpitIdentity?.role || "";
  const actionChoice = document.querySelector('input[name="blockActionChoice"]:checked')?.value || "APPROVE";
  const reasonCode = document.getElementById("reasonCodeSelect")?.value || "ROUTINE_SANCTION";
  const notes = document.getElementById("justificationNotesInput")?.value.trim() || "Snapshot transferred to originating department(s)";

  dispatchDepartmentalTransferJsonFiles(currentSelectedBlock, actionChoice, reasonCode, notes, actor, role, {
    state: currentSelectedBlock.state,
    effective_start: currentSelectedBlock.effective_start,
    effective_end: currentSelectedBlock.effective_end,
    timestamp: new Date().toISOString(),
  });
}

async function loadBlockAuditHistory(blockId) {
  const container = document.getElementById("blockAuditTimeline");
  if (!container) return;
  container.innerHTML = '<div class="audit-loading">Fetching audit history...</div>';

  try {
    const res = await fetch(`/api/v1/blocks/${encodeURIComponent(blockId)}/audit-logs`);
    if (!res.ok) throw new Error("Failed to load audit logs.");
    const logs = await res.json();
    if (logs.length === 0) {
      container.innerHTML = '<div class="audit-loading">No audit decisions recorded yet.</div>';
      return;
    }
    container.innerHTML = "";
    logs.forEach((log) => {
      const item = document.createElement("div");
      item.className = "audit-timeline-item";
      item.innerHTML = `
        <div class="audit-item-top">
          <span>${escapeHtml(log.action)} by ${escapeHtml(log.actor)} (${escapeHtml(log.role)})</span>
          <span>${formatIsoDateTime(log.created_at)}</span>
        </div>
        <div class="audit-item-notes">
          <strong>[${escapeHtml(log.reason_code)}]</strong> ${escapeHtml(log.justification_notes)}
        </div>
      `;
      container.appendChild(item);
    });
  } catch (err) {
    container.innerHTML = `<div class="audit-loading" style="color:#f87171;">Audit log unavailable: ${escapeHtml(err.message)}</div>`;
  }
}

async function openAuditDrawer() {
  const drawer = document.getElementById("auditDrawerBackdrop");
  const tbody = document.getElementById("globalAuditTableBody");
  if (!drawer || !tbody) return;

  drawer.hidden = false;
  tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;">Loading audit entries...</td></tr>';

  try {
    const res = await fetch("/api/v1/audit/logs?limit=50");
    if (!res.ok) throw new Error("Failed to load audit ledger.");
    const logs = await res.json();
    if (logs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#94a3b8;">No regulatory entries logged yet.</td></tr>';
      return;
    }
    tbody.innerHTML = "";
    logs.forEach((l) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td class="mono-txt">${formatIsoDateTime(l.created_at)}</td>
        <td><strong>${escapeHtml(l.block_id)}</strong></td>
        <td>${escapeHtml(l.actor)}<br/><small style="color:#94a3b8;">${escapeHtml(l.role)}</small></td>
        <td><span class="status-chip chip-${escapeHtml(String(l.action).toLowerCase())}">${escapeHtml(l.action)}</span></td>
        <td><code>${escapeHtml(l.reason_code)}</code></td>
        <td>${escapeHtml(l.justification_notes)}</td>
      `;
      tbody.appendChild(tr);
    });
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="6" style="color:#f87171;text-align:center;">Error: ${escapeHtml(err.message)}</td></tr>`;
  }
}

function openWhatIfDrawer() {
  const drawer = document.getElementById("whatIfDrawerBackdrop");
  if (!drawer) return;

  const form = document.getElementById("cockpitWhatIfForm");
  if (form) form.reset();

  const corridorInput = document.getElementById("wiCorridorId");
  if (corridorInput) corridorInput.value = "";
  const secFromInput = document.getElementById("wiSectionFrom");
  if (secFromInput) secFromInput.value = "";
  const secToInput = document.getElementById("wiSectionTo");
  if (secToInput) secToInput.value = "";
  const startInput = document.getElementById("wiStartTime");
  if (startInput) startInput.value = "";
  const endInput = document.getElementById("wiEndTime");
  if (endInput) endInput.value = "";
  const trainsInput = document.getElementById("wiTrainSchedules");
  if (trainsInput) trainsInput.value = "";

  // Clear banners and results
  wiClearError();
  const resultsArea = document.getElementById("wiResultsArea");
  if (resultsArea) resultsArea.hidden = true;
  const warnBox = document.getElementById("wiWarningsBox");
  if (warnBox) {
    warnBox.innerHTML = "";
    warnBox.hidden = true;
  }
  const trainsWrap = document.getElementById("wiTrainsWrap");
  if (trainsWrap) trainsWrap.hidden = true;

  drawer.hidden = false;

  // Load DB hints (non-blocking)
  loadWiHints();
}

function wiShowError(msg, isInfo) {
  const banner = document.getElementById("wiErrorBanner");
  if (!banner) return;
  banner.innerHTML = `<span class="wi-error-icon">${isInfo ? 'ℹ️' : '⚠️'}</span><span class="wi-error-text">${escapeHtml(msg)}</span>`;
  banner.className = isInfo ? "wi-error-banner wi-info" : "wi-error-banner wi-error";
  banner.hidden = false;
  banner.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function wiClearError() {
  const banner = document.getElementById("wiErrorBanner");
  if (banner) {
    banner.hidden = true;
    banner.innerHTML = "";
  }
}

async function loadWiHints() {
  const content = document.getElementById("wiHintsContent");
  if (!content) return;
  try {
    const res = await fetch("/api/v1/simulate/what-if/hints");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!data.records || data.records.length === 0) {
      content.innerHTML = `<p style="color:#94a3b8;font-size:0.8rem;">No timetable-occupancy records found in database. You must supply train schedules in the JSON field.</p>`;
      return;
    }
    content.innerHTML = `<p style="font-size:0.78rem;color:#64748b;margin:0 0 6px;">These records exist in the DB and can be simulated without JSON input:</p>` +
      `<table class="cockpit-table" style="font-size:0.76rem;"><thead><tr><th>Corridor ID</th><th>Section ID</th><th>Window Start (UTC)</th><th>Window End (UTC)</th><th>Passenger Trains</th></tr></thead><tbody>` +
      data.records.map(r => `<tr>
        <td><code>${escapeHtml(r.corridor_id || '—')}</code></td>
        <td><code>${escapeHtml(r.section_id)}</code></td>
        <td>${escapeHtml(r.window_start)}</td>
        <td>${escapeHtml(r.window_end)}</td>
        <td>${r.passenger_trains_affected}</td>
      </tr>`).join("") +
      `</tbody></table>
      <p style="font-size:0.76rem;color:#94a3b8;margin:6px 0 0;">Tip: enter Section Origin = first token before "-" in Section ID, e.g. <code>HWH</code> for <code>HWH-BDC-UP-MAIN</code>.</p>`;
  } catch (err) {
    content.innerHTML = `<p style="color:#f87171;font-size:0.78rem;">Could not load hints: ${escapeHtml(err.message)}</p>`;
  }
}

async function handleWhatIfSubmit(e) {
  e.preventDefault();
  wiClearError();
  const btn = document.getElementById("runWiSimBtn");
  const resultsArea = document.getElementById("wiResultsArea");
  btn.disabled = true;
  btn.textContent = "Simulating delay propagation...";

  const startVal = document.getElementById("wiStartTime").value;
  const endVal = document.getElementById("wiEndTime").value;

  const startIso = datetimeLocalToUtcIso(startVal);
  const endIso = datetimeLocalToUtcIso(endVal);

  if (!startIso || !endIso) {
    wiShowError("Invalid date/time format. Please use the date picker to select start and end times.");
    btn.disabled = false;
    btn.textContent = "Run Operational Delay Simulation";
    return;
  }

  if (new Date(endIso) <= new Date(startIso)) {
    wiShowError("Proposed End time must be after Proposed Start time.");
    btn.disabled = false;
    btn.textContent = "Run Operational Delay Simulation";
    return;
  }

  const payload = {
    corridor_id: document.getElementById("wiCorridorId").value.trim(),
    section_from: document.getElementById("wiSectionFrom").value.trim(),
    section_to: document.getElementById("wiSectionTo").value.trim(),
    block_start_time: startIso,
    block_end_time: endIso,
  };

  const schedulesRaw = document.getElementById("wiTrainSchedules").value.trim();
  if (schedulesRaw) {
    try {
      const schedules = JSON.parse(schedulesRaw);
      if (!Array.isArray(schedules)) throw new Error("Train schedules must be a JSON array.");
      payload.trains = schedules;
    } catch (error) {
      wiShowError(`Train schedule JSON validation failed: ${error.message}`);
      btn.disabled = false;
      btn.textContent = "Run Operational Delay Simulation";
      return;
    }
  }

  try {
    const res = await fetch("/api/v1/simulate/what-if", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      let detailStr = `Server returned ${res.status}: ${res.statusText}`;
      try {
        const err = await res.json();
        if (typeof err.detail === "string") {
          detailStr = err.detail;
        } else if (Array.isArray(err.detail)) {
          detailStr = err.detail.map(d => `${d.loc ? d.loc.join(".") + ": " : ""}${d.msg}`).join("\n");
        } else if (err.detail) {
          detailStr = JSON.stringify(err.detail);
        }
      } catch {}
      throw new Error(detailStr);
    }

    const report = await res.json();
    document.getElementById("wiPassDelay").innerHTML = `${report.total_passenger_delay_minutes || 0}<small> mins</small>`;
    document.getElementById("wiRegTrains").textContent = (report.regulated_trains || []).length;
    document.getElementById("wiPunctuality").textContent = `${report.network_punctuality_impact_pct ?? 0}%`;

    const warnBox = document.getElementById("wiWarningsBox");
    const warnings = report.headway_conflict_warnings || [];
    if (warnings.length > 0) {
      warnBox.innerHTML = warnings.map((w) => `<div>⚠ ${escapeHtml(w)}</div>`).join("");
      warnBox.hidden = false;
    } else {
      warnBox.innerHTML = "<div>✓ No safety headway conflicts identified on this simulated run.</div>";
      warnBox.hidden = false;
    }

    const trainsWrap = document.getElementById("wiTrainsWrap");
    const trainsTbody = document.getElementById("wiTrainsTableBody");
    if (trainsWrap && trainsTbody) {
      const regTrains = report.regulated_trains || [];
      if (regTrains.length > 0) {
        trainsTbody.innerHTML = regTrains.map(t => `
          <tr>
            <td><strong>${escapeHtml(t.train_no)}</strong></td>
            <td><span class="dept-pill" style="background:#e0f2fe;color:#0369a1;">${escapeHtml(t.train_type)}</span></td>
            <td>${escapeHtml(t.held_at_station)}</td>
            <td style="color:${t.delay_minutes >= 30 ? '#dc2626' : '#ea580c'};font-weight:700;">+${t.delay_minutes} min</td>
            <td>${formatIsoTime(t.original_arrival)}</td>
            <td><strong>${formatIsoTime(t.simulated_arrival)}</strong></td>
          </tr>
        `).join("");
        trainsWrap.hidden = false;
      } else {
        trainsWrap.hidden = true;
      }
    }

    resultsArea.hidden = false;
  } catch (err) {
    wiShowError(err.message);
    // Open the hints if it's a 409 (no timetable match) so user can see what's available
    if (err.message.includes("No imported timetable") || err.message.includes("No train schedules")) {
      const details = document.getElementById("wiHintsDetails");
      if (details) details.open = true;
    }
  } finally {
    btn.disabled = false;
    btn.textContent = "Run Operational Delay Simulation";
  }
}

function renderDeferredWork(tasks) {
  const section = document.getElementById("deferredSection");
  const tbody = document.getElementById("deferredTableBody");
  const badge = document.getElementById("deferredCountBadge");
  if (!section || !tbody || !badge) return;

  if (!tasks || tasks.length === 0) {
    section.hidden = true;
    return;
  }

  section.hidden = false;
  badge.textContent = `${tasks.length} Task${tasks.length > 1 ? "s" : ""}`;
  tbody.innerHTML = "";

  tasks.forEach((t) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><strong>${escapeHtml(t.task_id || "")}</strong></td>
      <td class="mono-txt">${escapeHtml(t.plan_id ? t.plan_id.substring(0, 8) + "..." : "—")}</td>
      <td style="color:#f87171;">${escapeHtml(t.reason || "Constraint unfulfilled")}</td>
    `;
    tbody.appendChild(tr);
  });
}

function formatIsoTime(isoStr) {
  if (!isoStr) return "";
  try {
    const parts = isoStr.split("T");
    if (parts.length > 1) {
      return parts[1].substring(0, 5);
    }
    const d = new Date(isoStr);
    return d.toISOString().substring(11, 16);
  } catch {
    return isoStr;
  }
}

function formatIsoDateTime(isoStr) {
  if (!isoStr) return "N/A";
  try {
    return isoStr.replace("T", " ").substring(0, 19);
  } catch {
    return isoStr;
  }
}

function datetimeLocalToUtcIso(value) {
  if (!value) return null;
  const normalized = value.length === 16 ? `${value}:00` : value;
  const parsed = new Date(`${normalized}Z`);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
