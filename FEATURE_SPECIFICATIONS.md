# Automatic Block Planning System

## Purpose and governing scope

This document defines eight features for an Automatic Block Planning system. It follows the official problem statement: integrate maintenance defects and overdue work from TMS, SMMS and TDMS with Control Office Application (COA) corridor/block availability, the Train Time Table, and goods-train forecasts; prioritize work; create coordinated weekly and monthly plans; and maximize fixed-infrastructure availability without compromising safe, reliable train operation.

The system is a decision-support and planning tool. Railway authorities retain control of block sanction, extension, and restoration decisions.

## F-01 — Multi-department data integration and unified task model

**Objective.** Bring Engineering, Signal & Telecommunication (S&T), and Traction Distribution maintenance records into one reliable planning dataset, together with COA corridor/block availability, timetable constraints, and goods-train forecasts.

**Inputs.**

- TMS: defects, overdue preventive work, kilometre range, restrictions, due dates, duration and required resources.
- SMMS: failures/overdue work, station or asset reference, urgency, affected route and required isolation.
- TDMS: OHE/traction defects, mast or substation references, planned work, required traction disconnection and duration.
- COA: corridor identities, available block windows, permitted capacity, existing requests and operational constraints.
- Train Time Table and goods forecast: planned occupancy, traffic demand and route criticality.

**Behaviour.** Validate source payloads; retain source-system identifiers and timestamps; translate asset/station/mast/KM references to a controlled network section and kilometre range; flag incomplete or ambiguous records for planner review rather than silently guessing; and maintain traceability to the original record.

**Unified task record.** At minimum: task ID, source system/reference, department, asset type/reference, section ID, start/end KM (where applicable), defect/maintenance type, severity, overdue status/due date, estimated duration, required crew/equipment, traffic-block requirement, traction-disconnection requirement, co-working compatibility, and data-quality status.

**Acceptance outcome.** A planner can compare all eligible departmental work on the same corridor and time horizon, with source traceability and visible exceptions.

## F-02 — Operating-condition feasibility and risk assessment

**Objective.** Assess whether a candidate block is safe and practical under forecast operating conditions, without replacing the applicable railway safety rules or controller judgment.

**Inputs.** Section location, proposed window, task method and resources, weather/environment forecast where available, and railway-approved operating limits.

**Behaviour.** Evaluate candidate windows against configurable, authority-approved rules (for example, weather, visibility, wind, flood exposure or heat constraints relevant to the task). Mark a task/window as suitable, caution-required, or not suitable; record the reason and risk adjustment; and pass only suitable/caution-qualified options to the scheduler. Missing forecast data must be surfaced as uncertainty, not treated as safe.

**Outputs.** Feasibility status, risk factor, warning reasons, data timestamp, and the rule version used.

**Acceptance outcome.** Unsafe or uncertain work windows are visible and are not automatically selected as normal candidates.

## F-03 — Explainable cross-department prioritization

**Objective.** Rank work consistently across departments so that safety, urgency and network impact drive planning rather than separate departmental queues.

**Priority factors.** Configurable factors include defect severity/safety consequence, overdue age, active speed or operational restriction, asset and route criticality, passenger timetable demand, goods forecast demand, estimated downtime, dependencies, and F-02 risk/feasibility.

**Behaviour.** Produce a normalized priority score and tier for each eligible task. A rules-based weighted model is the required transparent baseline; an ML model may be added only when validated historical data is available. Any model must be versioned, monitored, and fall back to the baseline if confidence or data quality is inadequate.

**Explanation.** For every score, show the leading factors, their direction and relative contribution in clear operational language. Explanations must derive from the scoring result; generative text, if used, may improve wording but must not alter scores or fabricate reasons.

**Acceptance outcome.** Controllers can see why one task outranks another and can trace the result to its inputs and model/rule version.

## F-04 — Weekly and monthly coordinated block optimizer

**Objective.** Generate feasible weekly (tactical) and monthly (strategic) block plans that combine compatible Engineering, S&T and Traction Distribution work into shared corridor windows wherever this safely reduces repeat disruption.

**Inputs.** Unified tasks, priority/feasibility results, COA block availability, timetable and goods-demand constraints, crew/equipment availability, traction-disconnection needs, spatial ranges, and co-working rules.

**Hard constraints.** A task must fit an eligible COA block on its section; required traffic block and traction disconnection must be available; crew/equipment and corridor capacities cannot be exceeded; incompatible work cannot co-occur; spatial separation and safety rules must be respected; and scheduled work must meet its due-date and dependency constraints where feasible.

**Optimization goals.** First protect safety and operational feasibility, then maximize priority-weighted work completion and asset availability, minimize repeated/dispersed blocks and train-operation impact, and prefer safe multi-department consolidation. The planner must disclose trade-offs, including work deferred or not scheduled with reasons.

**Outputs.** Proposed block windows, consolidated task lists/departments, required isolations/resources/precautions, expected availability benefit, operational-impact indicators, and unscheduled/deferred tasks with explanation. Generate both weekly and monthly views from the same governed data.

**Acceptance outcome.** The proposed plan demonstrably reduces avoidable separate blocks while respecting COA availability and all planning constraints.

## F-05 — Controller what-if operational-impact simulation

**Objective.** Let planners test a proposed block, changed window, or extension before sanctioning it and understand the resulting effect on train operations.

**Inputs.** Candidate block and corridor, timetable, goods-train forecast, route/section capacity and headway rules, and any applicable traffic-priority policies.

**Behaviour.** Compare the candidate against a no-change or currently approved plan. Estimate conflicts, regulated trains, delay minutes, punctuality/service impact, capacity impact and assumptions. Results are planning estimates, not live movement authority or a substitute for control-office operating procedures.

**Outputs.** Before/after impact summary, affected trains/services, high-risk conflicts, uncertainty/assumptions, and alternatives such as an adjacent viable COA window.

**Acceptance outcome.** A planner can assess the operational consequence of a block change and use it as evidence in approval decisions.

## F-06 — Live execution monitoring, overrun and early-restoration support

**Objective.** Monitor sanctioned blocks using authorized field/control updates and help the control office respond when work is progressing late or finishes early.

**States.** Planned, sanctioned, active, extension requested, restoration-ready, restored, completed, cancelled. State transitions must record actor, time and reason.

**Behaviour.** Ingest authenticated progress updates; compare estimated completion with the sanctioned end time; identify potential overruns; re-run the impact assessment and, where appropriate, propose revised options. On early completion, calculate recoverable capacity and recommend an earlier restoration review.

**Control safeguard.** The engine issues alerts and recommendations only. It must not autonomously extend a block, restore a line, issue signals, or direct station staff; an authorized controller performs and records the decision.

**Acceptance outcome.** Authorized users receive timely, traceable alerts and an updated impact view for deviations from the plan.

## F-07 — Planner and dispatcher operations cockpit

**Objective.** Provide a clear operational interface for planning, review, approval and monitoring of coordinated blocks.

**Core views.**

- Corridor timeline showing existing COA availability and proposed/sanctioned blocks by section and time.
- Consolidated task view with department, asset/KM range, required traffic block/disconnection, resources and safety constraints.
- Priority explanation and data-quality/feasibility warnings.
- Weekly and monthly plan views, plus a what-if impact panel.
- KPIs such as planned block utilization, coordinated-task count, estimated avoided repeat-block hours, deferred critical work and projected asset availability.

**Interaction principles.** Clearly distinguish proposed, sanctioned and live states; make assumptions and stale data visible; use accessible high-contrast visual encoding; and ensure no presentation implies that an AI recommendation has been approved.

**Acceptance outcome.** A controller can understand, compare and act on a plan without moving between departmental systems for the core planning decision.

## F-08 — Human approval, overrides, audit trail and formal reporting

**Objective.** Preserve accountable human authority over AI recommendations and maintain a complete record suitable for operational review and governance.

**Access control.** Role-based permissions distinguish data contributors, planners, reviewers, sanctioning authorities and auditors. Only authorized roles may approve, modify, reject, request extension, or record restoration.

**Audit behaviour.** Record each recommendation, input/model version, schedule revision, approval/override/rejection, reason code, justification, actor and timestamp in an append-only or tamper-evident audit trail. Preserve both original and modified plan versions. Require a reason and justification for any deviation from the recommendation.

**Reporting.** Generate a printable/exportable block-plan or sanction-memo draft containing the approved operational data: date/window, corridor and KM range, departments/tasks, required blocks/disconnections, resources, precautions, approval status, signatures/approvals and audit reference. Its template and any digital-signature workflow must be approved by the relevant railway authority; the system must not claim an unofficial template is an official sanction.

**Acceptance outcome.** Every plan and decision can be reconstructed, attributed and reviewed, while final operational authority remains human.

## Alignment notes and resolved conflicts

| Original proposal | Resolution under the official problem statement |
| --- | --- |
| The feature set treated weather as a compulsory core input. | Weather is a valuable feasibility input, but the mandated integration is TMS, SMMS, TDMS, COA, timetable and goods forecast. It is retained as an optional/configurable support feature. |
| Fixed weather thresholds and named external APIs were presented as railway safety rules. | Thresholds must be configurable and approved by railway authorities; external forecasts are advisory and must carry provenance/uncertainty. |
| A synthetic-data LightGBM model was positioned as the prioritization solution. | Explainable, governed prioritization is required; a transparent rules baseline is primary until validated real historical data supports ML. |
| The optimizer focused mostly on available corridors and task priority. | It now explicitly incorporates COA availability, Train Time Table, goods forecast, safety/isolation, dependencies and the required weekly/monthly horizons. |
| Live overrun handling proposed issuing warnings/actions to station masters. | The system may alert and recommend. It cannot autonomously control signals, grant/extend blocks, or restore a line. |
| A generated PDF was described as an official Indian Railways format with digital signatures. | The output is an approved-template draft/export; official format and signature workflow require railway-authority approval. |
| Team names, prescribed frameworks and unverified data sources were embedded in functional requirements. | They are implementation choices, not product requirements. Use authorized interfaces/data; mock or synthetic data may be used only for demonstrations and testing. |
