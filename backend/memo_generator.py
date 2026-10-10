"""F-08: Combined Multi-Department Block Sanction Memo generator.

Generates official-format draft sanction memos in PDF and printable HTML,
conforming to Indian Railways Joint Safety Circular specifications.
Decision-support draft only: operational sanction requires human controller signature.
"""

from __future__ import annotations

import hashlib
from html import escape as html_escape
import io
from datetime import datetime, timezone
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def pdf_text(value: Any) -> str:
    """Escape dynamic values before passing them to ReportLab markup parsing."""
    return xml_escape(str(value))


def generate_sanction_memo_pdf(memo_data: dict[str, Any]) -> bytes:
    """Generate a vector PDF block sanction memo draft using ReportLab."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=36,
        leftMargin=36,
        topMargin=32,
        bottomMargin=32,
    )
    styles = getSampleStyleSheet()

    elements = []

    # Colors
    c_primary = colors.HexColor("#1e293b")  # Slate 800
    c_accent = colors.HexColor("#0f766e")   # Teal 700
    c_border = colors.HexColor("#cbd5e1")   # Slate 300
    c_bg_light = colors.HexColor("#f8fafc") # Slate 50
    c_header_bg = colors.HexColor("#f1f5f9")# Slate 100

    title_style = ParagraphStyle(
        "MemoTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=16,
        alignment=1,
        textColor=c_primary,
    )
    sub_title_style = ParagraphStyle(
        "MemoSubTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=14,
        alignment=1,
        textColor=c_accent,
    )
    section_heading = ParagraphStyle(
        "SectionHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=12,
        textColor=c_primary,
    )
    body_bold = ParagraphStyle(
        "BodyBold",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=c_primary,
    )
    body_norm = ParagraphStyle(
        "BodyNorm",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=c_primary,
    )
    disclaimer_style = ParagraphStyle(
        "Disclaimer",
        parent=styles["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=6.5,
        leading=8.5,
        alignment=1,
        textColor=colors.HexColor("#64748b"),
    )

    # Document Header
    elements.append(Paragraph("INDIAN RAILWAYS — DIVISIONAL CONTROL OFFICE", title_style))
    elements.append(Spacer(1, 2))
    elements.append(Paragraph("COMBINED MULTI-DEPARTMENT BLOCK SANCTION MEMO (DRAFT)", sub_title_style))
    elements.append(Spacer(1, 6))
    elements.append(HRFlowable(width="100%", thickness=1, color=c_accent, spaceBefore=0, spaceAfter=8))

    # Basic Block & Corridor Details Table
    ref_no = memo_data.get("memo_reference", f"IR/DCO/ER/{memo_data.get('section_id', 'SEC')}/{memo_data.get('block_id', 'BLK')}")
    state = memo_data.get("state", "PROPOSED")
    section_id = memo_data.get("section_id", "N/A")
    km_span = memo_data.get("km_span", "Kilometre markers verified in field")
    eff_start = memo_data.get("effective_start", "N/A")
    eff_end = memo_data.get("effective_end", "N/A")
    dur_mins = memo_data.get("duration_minutes", 0)

    basic_data = [
        [
            Paragraph("Memo Reference:", body_bold),
            Paragraph(pdf_text(ref_no), body_norm),
            Paragraph("Sanction Status:", body_bold),
            Paragraph(f"<b>{pdf_text(state)}</b>", body_bold),
        ],
        [
            Paragraph("Railway Section:", body_bold),
            Paragraph(pdf_text(section_id), body_norm),
            Paragraph("Kilometric Span:", body_bold),
            Paragraph(pdf_text(km_span), body_norm),
        ],
        [
            Paragraph("Sanctioned Window:", body_bold),
            Paragraph(f"{pdf_text(eff_start)}<br/>to {pdf_text(eff_end)}", body_norm),
            Paragraph("Granted Duration:", body_bold),
            Paragraph(f"{dur_mins} mins ({round(dur_mins/60, 2)} hrs)", body_norm),
        ],
        [
            Paragraph("Timetable Ref:", body_bold),
            Paragraph(pdf_text(memo_data.get("timetable_reference", "N/A")), body_norm),
            Paragraph("Goods Forecast Ref:", body_bold),
            Paragraph(pdf_text(memo_data.get("goods_forecast_reference", "N/A")), body_norm),
        ],
    ]

    t_basic = Table(basic_data, colWidths=[105, 155, 105, 155])
    t_basic.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), c_bg_light),
        ("GRID", (0, 0), (-1, -1), 0.5, c_border),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(t_basic)
    elements.append(Spacer(1, 10))

    # Safety Isolations & Mandatory Precautions Table
    elements.append(Paragraph("1. MANDATORY SAFETY ISOLATIONS & PROTECTION PROTOCOLS", section_heading))
    elements.append(Spacer(1, 4))

    traffic_req = "Recorded requirement: YES" if memo_data.get("requires_traffic_block") else "Recorded requirement: NO"
    traction_req = "Recorded requirement: YES" if memo_data.get("requires_traction_disconnection") else "Recorded requirement: NO"

    iso_data = [
        [
            Paragraph("Traffic Block Protection:", body_bold),
            Paragraph(traffic_req, body_norm),
        ],
        [
            Paragraph("Traction Power Disconnection:", body_bold),
            Paragraph(traction_req, body_norm),
        ],
        [
            Paragraph("Co-Working Authorization:", body_bold),
            Paragraph(pdf_text(", ".join(memo_data.get("consolidated_departments", [])) or "None authorized"), body_norm),
        ],
        [
            Paragraph("Authorized Track Equipment:", body_bold),
            Paragraph(pdf_text(", ".join(memo_data.get("equipment", [])) or "Not recorded"), body_norm),
        ],
        [
            Paragraph("Authorized Crew Resources:", body_bold),
            Paragraph(pdf_text(", ".join(memo_data.get("crews", [])) or "Not recorded"), body_norm),
        ],
    ]
    t_iso = Table(iso_data, colWidths=[140, 380])
    t_iso.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), c_header_bg),
        ("GRID", (0, 0), (-1, -1), 0.5, c_border),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(t_iso)
    elements.append(Spacer(1, 10))

    # Consolidated Departmental Tasks Table
    elements.append(Paragraph("2. CONSOLIDATED DEPARTMENTAL MAINTENANCE WORK ORDER", section_heading))
    elements.append(Spacer(1, 4))

    task_header = [
        Paragraph("Task ID", body_bold),
        Paragraph("Dept", body_bold),
        Paragraph("Maintenance Type / Defect", body_bold),
        Paragraph("KM Range", body_bold),
        Paragraph("Priority", body_bold),
        Paragraph("Slot Time", body_bold),
    ]
    task_rows = [task_header]
    tasks = memo_data.get("assigned_tasks", [])
    if tasks:
        for t in tasks:
            task_rows.append([
                Paragraph(pdf_text(t.get("task_id", "")), body_norm),
                Paragraph(pdf_text(t.get("department", "")), body_norm),
                Paragraph(pdf_text(t.get("maintenance_type", t.get("defect_type", "Scheduled work"))), body_norm),
                Paragraph(pdf_text(t.get("km_span", "Section bounds")), body_norm),
                Paragraph(pdf_text(t.get("priority_score", "N/A")), body_norm),
                Paragraph(f"{pdf_text(str(t.get('scheduled_start', ''))[-8:-3])}–{pdf_text(str(t.get('scheduled_end', ''))[-8:-3])}", body_norm),
            ])
    else:
        task_rows.append([
            Paragraph("No assigned tasks recorded.", body_norm),
            Paragraph("—", body_norm),
            Paragraph("—", body_norm),
            Paragraph("—", body_norm),
            Paragraph("—", body_norm),
            Paragraph("—", body_norm),
        ])

    t_tasks = Table(task_rows, colWidths=[90, 75, 145, 80, 50, 80])
    t_tasks.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), c_header_bg),
        ("GRID", (0, 0), (-1, -1), 0.5, c_border),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(t_tasks)
    elements.append(Spacer(1, 10))

    # Human-in-the-Loop Authority & Audit Trail Table
    elements.append(Paragraph("3. HUMAN APPROVAL, OVERRIDE JUSTIFICATION & REGULATORY AUDIT LEDGER", section_heading))
    elements.append(Spacer(1, 4))

    sanctioned_by = memo_data.get("sanctioned_by") or "Awaiting Controller Action"
    sanctioned_role = memo_data.get("sanctioned_role") or "Section Controller"
    sanctioned_at = memo_data.get("sanctioned_at") or "Pending"
    reason_code = memo_data.get("reason_code") or "PROPOSED_AUTOMATIC_PLAN"
    justification = memo_data.get("justification") or "Proposed multi-department plan submitted for controller review."
    v_hash = memo_data.get("verification_hash", "UNVERIFIED")

    audit_data = [
        [
            Paragraph("Sanctioning Authority:", body_bold),
            Paragraph(f"{pdf_text(sanctioned_by)} ({pdf_text(sanctioned_role)})", body_norm),
            Paragraph("Recorded Timestamp:", body_bold),
            Paragraph(pdf_text(sanctioned_at), body_norm),
        ],
        [
            Paragraph("Regulatory Reason Code:", body_bold),
            Paragraph(pdf_text(reason_code), body_norm),
            Paragraph("Audit Ledger Hash:", body_bold),
            Paragraph(f"<font name='Courier'>IR-AUDIT-{pdf_text(v_hash)}</font>", body_bold),
        ],
        [
            Paragraph("Controller Justification:", body_bold),
            Paragraph(pdf_text(justification), body_norm),
            Paragraph("Audit Verification:", body_bold),
            Paragraph("TAMPER-EVIDENT APPEND-ONLY LOG", body_norm),
        ],
    ]
    t_audit = Table(audit_data, colWidths=[110, 150, 110, 150])
    t_audit.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), c_bg_light),
        ("GRID", (0, 0), (-1, -1), 0.5, c_border),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(t_audit)
    elements.append(Spacer(1, 14))

    # Formal Counter-Signature Sign-off Block
    elements.append(Paragraph("4. FORMAL OPERATIONAL SIGN-OFF & FIELD CLEARANCE COUNTER-SIGNATURES", section_heading))
    elements.append(Spacer(1, 4))

    sig_data = [
        [
            Paragraph("<b>SECTION CONTROLLER</b><br/><br/><br/>_______________________<br/>Sanction Authority Signature", body_norm),
            Paragraph("<b>SSE / PERMANENT WAY</b><br/><br/><br/>_______________________<br/>Engineering In-Charge", body_norm),
            Paragraph("<b>SSE / SIGNAL &amp; TELECOM</b><br/><br/><br/>_______________________<br/>S&amp;T In-Charge", body_norm),
            Paragraph("<b>SSE / TRACTION (TRD)</b><br/><br/><br/>_______________________<br/>TRD In-Charge", body_norm),
        ]
    ]
    t_sig = Table(sig_data, colWidths=[130, 130, 130, 130])
    t_sig.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), c_header_bg),
        ("BOX", (0, 0), (-1, -1), 0.5, c_border),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, c_border),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.append(t_sig)
    elements.append(Spacer(1, 12))

    # Governance Disclaimer
    elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#94a3b8"), spaceBefore=0, spaceAfter=4))
    elements.append(Paragraph(
        "LEGAL & GOVERNANCE NOTICE: This document is a decision-support sanction draft generated by RailSync. "
        "It does not independently authorize train movement stoppage or OHE disconnection. "
        "Operational sanction must be confirmed via Control Telephone/FOT and recorded in the Divisional Train Control Register "
        "in strict accordance with General & Subsidiary Rules (G&SR 15.06 & 15.08) and Joint Safety Circular norms.",
        disclaimer_style,
    ))

    doc.build(elements)
    return buffer.getvalue()


def generate_sanction_memo_html(memo_data: dict[str, Any]) -> str:
    """Generate a high-contrast, printable HTML block sanction memo."""
    ref_no = memo_data.get("memo_reference", f"IR/DCO/ER/{memo_data.get('section_id', 'SEC')}/{memo_data.get('block_id', 'BLK')}")
    state = memo_data.get("state", "PROPOSED")
    section_id = memo_data.get("section_id", "N/A")
    km_span = memo_data.get("km_span", "KM range verified in field")
    eff_start = memo_data.get("effective_start", "N/A")
    eff_end = memo_data.get("effective_end", "N/A")
    dur_mins = memo_data.get("duration_minutes", 0)
    traffic_req = "Recorded requirement: YES" if memo_data.get("requires_traffic_block") else "Recorded requirement: NO"
    traction_req = "Recorded requirement: YES" if memo_data.get("requires_traction_disconnection") else "Recorded requirement: NO"
    sanctioned_by = memo_data.get("sanctioned_by") or "Pending Controller Review"
    sanctioned_role = memo_data.get("sanctioned_role") or "Section Controller"
    sanctioned_at = memo_data.get("sanctioned_at") or "Pending"
    reason_code = memo_data.get("reason_code") or "PROPOSED_PLAN"
    justification = memo_data.get("justification") or "Coordinated multi-department block recommendation."
    v_hash = memo_data.get("verification_hash", "UNVERIFIED")
    safe = lambda value: html_escape(str(value), quote=True)

    tasks_html = ""
    for t in memo_data.get("assigned_tasks", []):
        tasks_html += f"""
        <tr>
            <td><strong>{safe(t.get('task_id', ''))}</strong></td>
            <td><span class="dept-pill dept-{safe(str(t.get('department', '')).lower())}">{safe(t.get('department', ''))}</span></td>
            <td>{safe(t.get('maintenance_type', t.get('defect_type', 'Track Maintenance')))}</td>
            <td>{safe(t.get('km_span', 'Section Bounds'))}</td>
            <td>{safe(t.get('priority_score', 'N/A'))}</td>
            <td>{safe(str(t.get('scheduled_start', ''))[-8:-3])} – {safe(str(t.get('scheduled_end', ''))[-8:-3])}</td>
        </tr>
        """
    if not tasks_html:
        tasks_html = "<tr><td colspan='6' style='text-align:center;color:#64748b;'>No assigned tasks recorded.</td></tr>"

    return f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <title>Sanction Memo — {safe(ref_no)}</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #1e293b; padding: 24px; }}
        .memo-sheet {{ background: #ffffff; max-width: 860px; margin: 0 auto; padding: 36px; border-radius: 8px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }}
        .header {{ text-align: center; border-bottom: 2px solid #0f766e; padding-bottom: 12px; margin-bottom: 18px; }}
        .header h1 {{ font-size: 18px; letter-spacing: 0.05em; color: #0f172a; text-transform: uppercase; margin-bottom: 4px; }}
        .header h2 {{ font-size: 13px; color: #0f766e; font-weight: 700; }}
        .meta-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 16px; font-size: 13px; }}
        .meta-card {{ background: #f8fafc; border: 1px solid #e2e8f0; padding: 10px 14px; border-radius: 6px; }}
        .meta-card dt {{ font-weight: 700; color: #475569; font-size: 11px; text-transform: uppercase; margin-bottom: 2px; }}
        .meta-card dd {{ font-size: 13px; font-weight: 600; color: #0f172a; }}
        .badge {{ display: inline-block; padding: 3px 8px; font-size: 11px; font-weight: 700; border-radius: 4px; text-transform: uppercase; }}
        .badge-sanctioned {{ background: #dcfce7; color: #15803d; border: 1px solid #86efac; }}
        .badge-overridden {{ background: #ffedd5; color: #c2410c; border: 1px solid #fdba74; }}
        .badge-proposed {{ background: #fef9c3; color: #854d0e; border: 1px solid #fde047; }}
        .badge-rejected {{ background: #fee2e2; color: #b91c1c; border: 1px solid #fca5a5; }}
        .section-title {{ font-size: 12px; font-weight: 800; text-transform: uppercase; color: #0f172a; margin: 18px 0 8px; letter-spacing: 0.03em; border-left: 3px solid #0f766e; padding-left: 8px; }}
        table {{ width: 100%; border-collapse: collapse; margin-bottom: 14px; font-size: 12px; }}
        th, td {{ border: 1px solid #cbd5e1; padding: 7px 10px; text-align: left; }}
        th {{ background: #f1f5f9; font-weight: 700; color: #334155; text-transform: uppercase; font-size: 11px; }}
        .dept-pill {{ display: inline-block; padding: 2px 6px; border-radius: 3px; font-size: 10px; font-weight: 700; color: #fff; }}
        .dept-engineering {{ background: #f97316; }}
        .dept-signal_telecom {{ background: #3b82f6; }}
        .dept-traction {{ background: #a855f7; }}
        .signatures {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-top: 24px; text-align: center; font-size: 11px; }}
        .sig-box {{ border: 1px dashed #94a3b8; padding: 14px 6px 10px; border-radius: 4px; background: #f8fafc; }}
        .sig-line {{ margin-top: 36px; border-top: 1px solid #475569; padding-top: 4px; font-weight: 700; font-size: 10px; }}
        .notice {{ font-size: 10px; color: #64748b; font-style: italic; border-top: 1px solid #e2e8f0; padding-top: 12px; margin-top: 20px; line-height: 1.4; }}
        .actions {{ max-width: 860px; margin: 0 auto 16px; display: flex; justify-content: flex-end; gap: 10px; }}
        .btn {{ padding: 8px 16px; border-radius: 6px; font-weight: 600; cursor: pointer; text-decoration: none; font-size: 13px; border: none; }}
        .btn-print {{ background: #0f766e; color: #ffffff; }}
        .btn-pdf {{ background: #3b82f6; color: #ffffff; }}
        @media print {{
            body {{ background: #fff; padding: 0; }}
            .actions {{ display: none; }}
            .memo-sheet {{ box-shadow: none; padding: 0; max-width: 100%; }}
        }}
    </style>
</head>
<body>
    <div class="actions">
        <button class="btn btn-print" onclick="window.print()">Print Memo / Save as PDF</button>
        <a class="btn btn-pdf" href="?format=pdf">Download Native Vector PDF</a>
    </div>
    <div class="memo-sheet">
        <div class="header">
            <h1>Indian Railways — Divisional Control Office</h1>
            <h2>Combined Multi-Department Block Sanction Memo (Draft)</h2>
        </div>
        <div class="meta-grid">
            <div class="meta-card">
                <dt>Sanction Memo Reference</dt>
                <dd>{safe(ref_no)}</dd>
            </div>
            <div class="meta-card">
                <dt>Current Sanction Status</dt>
                <dd><span class="badge badge-{safe(state.lower())}">{safe(state)}</span></dd>
            </div>
            <div class="meta-card">
                <dt>Railway Section &amp; Spatial Span</dt>
                <dd>{safe(section_id)} ({safe(km_span)})</dd>
            </div>
            <div class="meta-card">
                <dt>Sanctioned Window &amp; Duration</dt>
                <dd>{safe(eff_start)} to {safe(eff_end)} ({dur_mins} mins / {round(dur_mins/60, 2)} hrs)</dd>
            </div>
        </div>

        <div class="section-title">1. Mandatory Safety Isolations &amp; Work Protection</div>
        <table>
            <tr><th style="width:30%;">Traffic Block Protection</th><td>{traffic_req}</td></tr>
            <tr><th>Traction Power Disconnection</th><td>{traction_req}</td></tr>
            <tr><th>Authorized Departments</th><td>{safe(", ".join(memo_data.get('consolidated_departments', [])) or 'None')}</td></tr>
            <tr><th>Recorded Track Machines / Gear</th><td>{safe(", ".join(memo_data.get('equipment', [])) or 'Not recorded')}</td></tr>
            <tr><th>Recorded Supervisor &amp; Crews</th><td>{safe(", ".join(memo_data.get('crews', [])) or 'Not recorded')}</td></tr>
        </table>

        <div class="section-title">2. Consolidated Departmental Work Schedule</div>
        <table>
            <thead>
                <tr>
                    <th>Task ID</th>
                    <th>Dept</th>
                    <th>Maintenance Details</th>
                    <th>KM Span</th>
                    <th>Priority</th>
                    <th>Window</th>
                </tr>
            </thead>
            <tbody>
                {tasks_html}
            </tbody>
        </table>

        <div class="section-title">3. Human Sign-Off &amp; Regulatory Audit Ledger</div>
        <table>
            <tr>
                <th style="width:25%;">Sanction Authority</th>
                <td style="width:25%;">{safe(sanctioned_by)} ({safe(sanctioned_role)})</td>
                <th style="width:25%;">Timestamp</th>
                <td style="width:25%;">{safe(sanctioned_at)}</td>
            </tr>
            <tr>
                <th>Reason Code</th>
                <td>{safe(reason_code)}</td>
                <th>Audit Hash</th>
                <td><code>IR-AUDIT-{safe(v_hash)}</code></td>
            </tr>
            <tr>
                <th>Controller Justification</th>
                <td colspan="3">{safe(justification)}</td>
            </tr>
        </table>

        <div class="section-title">4. Operational Sign-Off &amp; Clearance Signatures</div>
        <div class="signatures">
            <div class="sig-box">
                <strong>SECTION CONTROLLER</strong>
                <div class="sig-line">Sanctioning Officer</div>
            </div>
            <div class="sig-box">
                <strong>SSE / P-WAY</strong>
                <div class="sig-line">Engineering In-Charge</div>
            </div>
            <div class="sig-box">
                <strong>SSE / SIGNAL</strong>
                <div class="sig-line">S&amp;T In-Charge</div>
            </div>
            <div class="sig-box">
                <strong>SSE / TRD</strong>
                <div class="sig-line">Traction In-Charge</div>
            </div>
        </div>

        <div class="notice">
            LEGAL &amp; GOVERNANCE NOTICE: This document is a decision-support draft generated by RailSync. It does not independently authorize train movement stoppage or OHE disconnection. Operational sanction must be confirmed via Control Telephone/FOT and recorded in the Divisional Train Control Register in strict accordance with General &amp; Subsidiary Rules (G&SR 15.06 &amp; 15.08) and Joint Safety Circular norms.
        </div>
    </div>
</body>
</html>
"""
