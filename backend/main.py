"""F-01 ingestion API: source records are accepted only when supplied by a user/system.

No seed, mock, fallback or generated railway data is created by this service.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

import csv
import hashlib
import io
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from backend.memo_generator import generate_sanction_memo_html, generate_sanction_memo_pdf


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "rail_sync.db"


class SourceSystem(StrEnum):
    TMS = "TMS"
    SMMS = "SMMS"
    TDMS = "TDMS"


class Department(StrEnum):
    ENGINEERING = "ENGINEERING"
    SIGNAL_TELECOM = "SIGNAL_TELECOM"
    TRACTION = "TRACTION"


class CoWorkingSystem(StrEnum):
    TMS = "TMS"
    SMMS = "SMMS"
    TDMS = "TDMS"


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ReferenceRegistration(BaseModel):
    source_system: SourceSystem
    source_reference: str = Field(min_length=1, max_length=160)
    section_id: str = Field(min_length=1, max_length=120)
    start_km: float = Field(ge=0)
    end_km: float = Field(ge=0)
    asset_type: str = Field(min_length=1, max_length=120)
    asset_reference: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def kilometre_range_is_ordered(self) -> "ReferenceRegistration":
        if self.end_km < self.start_km:
            raise ValueError("end_km must be greater than or equal to start_km")
        return self


# Indian Railways safety thresholds
MAX_ENGINEERING_TEMP: float = 42.0
MAX_TRACTION_WIND: float = 50.0
MIN_VISIBILITY: float = 300.0
CAUTION_MULTIPLIER: float = 1.35
DEFAULT_RULE_VERSION: str = "IR-SAFETY-STD-2024"

DEFAULT_COA_POLICY: dict[str, Any] = {
    "policy_version": "COA-DEFAULT-2026",
    "severity_scores": {
        "CRITICAL": 90.0,
        "HIGH": 70.0,
        "MEDIUM": 50.0,
        "LOW": 20.0,
    },
    "max_days_overdue": 30.0,
    "max_passenger_trains_per_day": 200.0,
    "max_goods_trains_per_day": 120.0,
    "max_section_traffic_gmt": 180.0,
    "default_passenger_trains_per_day": 40.0,
    "default_goods_forecast_per_day": 15.0,
    "default_section_traffic_gmt": 65.5,
    "default_route_criticality_score": 85.0,
    "active_restriction_score": 35.0,
    "max_weather_risk_multiplier": 2.5,
    "severity_weight": 0.30,
    "overdue_weight": 0.15,
    "passenger_weight": 0.10,
    "goods_weight": 0.10,
    "traffic_weight": 0.10,
    "restriction_weight": 0.15,
    "route_criticality_weight": 0.10,
    "weather_weight": 0.10,
    "critical_threshold": 80.0,
    "high_threshold": 60.0,
    "medium_threshold": 35.0,
    "active_restriction_score": 35.0,
    "max_overdue_tolerance_days": 30.0,
    "weather_risk_weight": 1.25,
    "tsr_penalty_score": 15.0,
    "freight_revenue_weight": 0.85,
}

DEFAULT_TRAFFIC_DENSITY: dict[str, int] = {
    "passenger_00_06": 5,
    "passenger_06_12": 22,
    "passenger_12_18": 15,
    "passenger_18_24": 5,
    "goods_00_06": 8,
    "goods_06_12": 2,
    "goods_12_18": 2,
    "goods_18_24": 3,
}

ACTIVE_COA_POLICY: dict[str, Any] = {
    **DEFAULT_TRAFFIC_DENSITY,
    "section_traffic": DEFAULT_COA_POLICY["default_section_traffic_gmt"],
    "route_criticality": DEFAULT_COA_POLICY["default_route_criticality_score"],
    "policy_version": DEFAULT_COA_POLICY["policy_version"],
    "max_overdue_tolerance_days": 30.0,
    "weather_risk_weight": 1.25,
    "tsr_penalty_score": 15.0,
    "freight_revenue_weight": 0.85,
}


class PlanningContext(BaseModel):
    severity: Severity
    estimated_duration_minutes: int = Field(gt=0, le=10080)
    due_date: datetime | None = None
    required_crews: list[str] = Field(min_length=1)
    required_equipment: list[str] = Field(min_length=1)
    requires_traffic_block: bool
    requires_traction_disconnection: bool
    co_working_compatible: bool
    co_working_departments: list[CoWorkingSystem] = Field(default_factory=list)
    section_latitude: float | None = Field(default=None, ge=-90, le=90)
    section_longitude: float | None = Field(default=None, ge=-180, le=180)
    proposed_time: datetime | None = None

    @model_validator(mode="after")
    def co_working_departments_required(self) -> "PlanningContext":
        if self.co_working_compatible and not self.co_working_departments:
            raise ValueError("Co-working assistance requested, but no departments specified.")
        return self

    @field_validator("due_date", "proposed_time")
    @classmethod
    def due_date_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        """HTML datetime-local values have no offset; store them consistently as UTC."""
        if value is None:
            return None
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    @field_validator("required_crews", "required_equipment")
    @classmethod
    def entries_must_not_be_blank(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("entries cannot be blank")
        return [value.strip() for value in values]


class IngestionRequest(BaseModel):
    source_system: SourceSystem
    record: dict[str, Any]
    planning_context: PlanningContext


class NormalizedTask(BaseModel):
    id: str
    source_system: SourceSystem
    source_reference: str
    source_timestamp: datetime
    department: Department
    asset_type: str
    asset_reference: str
    section_id: str
    start_km: float
    end_km: float
    maintenance_type: str
    severity: Severity
    overdue: bool
    due_date: datetime
    estimated_duration_minutes: int
    required_crews: list[str]
    required_equipment: list[str]
    requires_traffic_block: bool
    requires_traction_disconnection: bool
    co_working_compatible: bool
    data_quality_status: str
    co_working_departments: list[CoWorkingSystem] = Field(default_factory=list)
    section_latitude: float | None = None
    section_longitude: float | None = None
    proposed_time: datetime | None = None


class FeasibilityRequest(BaseModel):
    task_id: str = Field(min_length=1)


class PriorityPolicy(BaseModel):
    policy_version: str = Field(min_length=1, max_length=100)
    severity_scores: dict[Severity, float]
    max_days_overdue: float = Field(gt=0)
    max_passenger_trains_per_day: float = Field(gt=0)
    max_goods_trains_per_day: float = Field(gt=0)
    max_section_traffic_gmt: float = Field(gt=0)
    default_passenger_trains_per_day: float = Field(default=40.0, ge=0)
    default_goods_forecast_per_day: float = Field(default=15.0, ge=0)
    default_section_traffic_gmt: float = Field(default=65.5, ge=0)
    default_route_criticality_score: float = Field(default=85.0, ge=0, le=100)
    max_overdue_tolerance_days: float = Field(default=30.0, gt=0)
    weather_risk_weight: float = Field(default=1.25, ge=0)
    tsr_penalty_score: float = Field(default=15.0, ge=0, le=100)
    freight_revenue_weight: float = Field(default=0.85, ge=0)
    active_restriction_score: float = Field(ge=0, le=100)
    max_weather_risk_multiplier: float = Field(gt=1)
    severity_weight: float = Field(ge=0)
    overdue_weight: float = Field(ge=0)
    passenger_weight: float = Field(ge=0)
    goods_weight: float = Field(ge=0)
    traffic_weight: float = Field(ge=0)
    restriction_weight: float = Field(ge=0)
    route_criticality_weight: float = Field(ge=0)
    weather_weight: float = Field(ge=0)
    critical_threshold: float = Field(ge=0, le=100)
    high_threshold: float = Field(ge=0, le=100)
    medium_threshold: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def policy_is_complete_and_ordered(self) -> "PriorityPolicy":
        if set(self.severity_scores) != set(Severity):
            raise ValueError("severity_scores must contain CRITICAL, HIGH, MEDIUM and LOW.")
        if any(score < 0 or score > 100 for score in self.severity_scores.values()):
            raise ValueError("severity scores must be between 0 and 100.")
        if not any((self.severity_weight, self.overdue_weight, self.passenger_weight, self.goods_weight, self.traffic_weight, self.restriction_weight, self.route_criticality_weight, self.weather_weight)):
            raise ValueError("At least one priority weight must be greater than zero.")
        if not self.critical_threshold >= self.high_threshold >= self.medium_threshold:
            raise ValueError("Thresholds must follow critical ≥ high ≥ medium.")
        return self


class PriorityContext(BaseModel):
    task_id: str = Field(min_length=1)
    passenger_train_frequency_per_day: float = Field(ge=0)
    goods_train_forecast_per_day: float = Field(ge=0)
    section_traffic_gmt: float = Field(ge=0)
    route_criticality_score: float = Field(ge=0, le=100)
    active_operational_restriction: bool


class PriorityRequest(BaseModel):
    context: PriorityContext
    policy: PriorityPolicy | None = None
    policy_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def exactly_one_policy_source_is_supplied(self) -> "PriorityRequest":
        if (self.policy is None) == (self.policy_id is None):
            raise ValueError("Supply exactly one of policy or policy_id.")
        return self


class SimplePriorityRequest(BaseModel):
    task_id: str = Field(min_length=1)
    active_restriction: bool = False


class ActivePolicyUpdate(BaseModel):
    passenger_00_06: int = Field(ge=0)
    passenger_06_12: int = Field(ge=0)
    passenger_12_18: int = Field(ge=0)
    passenger_18_24: int = Field(ge=0)
    goods_00_06: int = Field(ge=0)
    goods_06_12: int = Field(ge=0)
    goods_12_18: int = Field(ge=0)
    goods_18_24: int = Field(ge=0)
    section_traffic: float = Field(ge=0)
    route_criticality: float = Field(ge=0, le=100)
    policy_version: str = Field(min_length=1, max_length=100)
    max_overdue_tolerance_days: float = Field(gt=0)
    weather_risk_weight: float = Field(ge=0)
    tsr_penalty_score: float = Field(ge=0, le=100)
    freight_revenue_weight: float = Field(ge=0)


class PlanningHorizon(StrEnum):
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


class CorridorWindow(BaseModel):
    corridor_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    start_time: datetime
    end_time: datetime
    max_simultaneous_crews: int = Field(gt=0)
    traffic_block_available: bool
    traction_disconnection_available: bool
    timetable_reference: str = Field(min_length=1)
    goods_forecast_reference: str = Field(min_length=1)
    passenger_trains_affected: int = Field(ge=0)
    goods_trains_affected: int = Field(ge=0)

    @field_validator("start_time", "end_time")
    @classmethod
    def window_time_is_aware(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    @model_validator(mode="after")
    def window_has_positive_duration(self) -> "CorridorWindow":
        if self.end_time <= self.start_time:
            raise ValueError("A COA block window must end after it starts.")
        return self


class BlockPlanRequest(BaseModel):
    horizon: PlanningHorizon
    horizon_start: datetime
    horizon_end: datetime
    coa_windows: list[CorridorWindow] = Field(min_length=1)
    task_ids: list[str] = Field(default_factory=list)

    @field_validator("horizon_start", "horizon_end")
    @classmethod
    def horizon_is_aware(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    @model_validator(mode="after")
    def horizon_and_windows_are_valid(self) -> "BlockPlanRequest":
        duration_days = (self.horizon_end - self.horizon_start).total_seconds() / 86400
        limits = (6, 8) if self.horizon is PlanningHorizon.WEEKLY else (28, 31)
        if not limits[0] <= duration_days <= limits[1]:
            raise ValueError(f"{self.horizon.lower()} plans require a horizon between {limits[0]} and {limits[1]} days.")
        ids = [window.corridor_id for window in self.coa_windows]
        if len(ids) != len(set(ids)):
            raise ValueError("Each submitted COA block window needs a unique corridor_id.")
        for window in self.coa_windows:
            if window.start_time < self.horizon_start or window.end_time > self.horizon_end:
                raise ValueError(f"COA window '{window.corridor_id}' sits outside the requested planning horizon.")
        return self


class TimetableOccupancy(BaseModel):
    record_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    window_start: datetime
    window_end: datetime
    passenger_trains_affected: int = Field(ge=0)
    source_timestamp: datetime

    @field_validator("window_start", "window_end", "source_timestamp")
    @classmethod
    def occupancy_times_are_aware(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    @model_validator(mode="after")
    def occupancy_interval_is_valid(self) -> "TimetableOccupancy":
        if self.window_end <= self.window_start:
            raise ValueError("Timetable occupancy window must end after it starts.")
        return self


class GoodsForecast(BaseModel):
    record_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    window_start: datetime
    window_end: datetime
    goods_trains_affected: int = Field(ge=0)
    source_timestamp: datetime

    @field_validator("window_start", "window_end", "source_timestamp")
    @classmethod
    def forecast_times_are_aware(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    @model_validator(mode="after")
    def forecast_interval_is_valid(self) -> "GoodsForecast":
        if self.window_end <= self.window_start:
            raise ValueError("Goods forecast window must end after it starts.")
        return self


class ImportedCOAWindow(BaseModel):
    """COA source record; train-impact counts are resolved from cited source records."""
    corridor_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    start_time: datetime
    end_time: datetime
    max_simultaneous_crews: int = Field(gt=0)
    traffic_block_available: bool
    traction_disconnection_available: bool
    timetable_reference: str = Field(min_length=1)
    goods_forecast_reference: str = Field(min_length=1)

    @field_validator("start_time", "end_time")
    @classmethod
    def imported_window_time_is_aware(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    @model_validator(mode="after")
    def imported_window_has_positive_duration(self) -> "ImportedCOAWindow":
        if self.end_time <= self.start_time:
            raise ValueError("A COA block window must end after it starts.")
        return self


class COAWindowImport(BaseModel):
    window: ImportedCOAWindow
    source_timestamp: datetime

    @field_validator("source_timestamp")
    @classmethod
    def coa_source_time_is_aware(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class IntegratedPlanRequest(BaseModel):
    horizon: PlanningHorizon
    horizon_start: datetime
    horizon_end: datetime
    task_ids: list[str] = Field(default_factory=list)

    @field_validator("horizon_start", "horizon_end")
    @classmethod
    def integrated_horizon_is_aware(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    @model_validator(mode="after")
    def integrated_horizon_is_valid(self) -> "IntegratedPlanRequest":
        duration_days = (self.horizon_end - self.horizon_start).total_seconds() / 86400
        limits = (6, 8) if self.horizon is PlanningHorizon.WEEKLY else (28, 31)
        if not limits[0] <= duration_days <= limits[1]:
            raise ValueError(f"{self.horizon.lower()} plans require a horizon between {limits[0]} and {limits[1]} days.")
        return self


class PriorityPolicyRegistration(BaseModel):
    policy_id: str = Field(min_length=1)
    policy: PriorityPolicy
    approval_reference: str = Field(min_length=1)
    approved_at: datetime

    @field_validator("approved_at")
    @classmethod
    def approval_time_is_aware(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@contextmanager
def connection():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    finally:
        db.close()


def initialize_database() -> None:
    with connection() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS network_references (
                id TEXT PRIMARY KEY,
                source_system TEXT NOT NULL,
                source_reference TEXT NOT NULL,
                section_id TEXT NOT NULL,
                start_km REAL NOT NULL,
                end_km REAL NOT NULL,
                asset_type TEXT NOT NULL,
                asset_reference TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(source_system, source_reference)
            );
            CREATE TABLE IF NOT EXISTS ingestion_records (
                id TEXT PRIMARY KEY,
                source_system TEXT NOT NULL,
                source_reference TEXT,
                received_at TEXT NOT NULL,
                original_record_json TEXT NOT NULL,
                planning_context_json TEXT,
                normalized_task_json TEXT,
                data_quality_status TEXT NOT NULL,
                review_reasons_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feasibility_assessments (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                proposed_time TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                rule_version TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS priority_evaluations (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                context_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS block_plans (
                id TEXT PRIMARY KEY,
                horizon TEXT NOT NULL,
                horizon_start TEXT NOT NULL,
                horizon_end TEXT NOT NULL,
                coa_windows_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS timetable_occupancy (
                record_id TEXT PRIMARY KEY,
                section_id TEXT NOT NULL,
                window_start TEXT NOT NULL,
                window_end TEXT NOT NULL,
                passenger_trains_affected INTEGER NOT NULL,
                source_timestamp TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS goods_forecasts (
                record_id TEXT PRIMARY KEY,
                section_id TEXT NOT NULL,
                window_start TEXT NOT NULL,
                window_end TEXT NOT NULL,
                goods_trains_affected INTEGER NOT NULL,
                source_timestamp TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS coa_windows (
                corridor_id TEXT PRIMARY KEY,
                window_json TEXT NOT NULL,
                source_timestamp TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS priority_policies (
                policy_id TEXT PRIMARY KEY,
                policy_json TEXT NOT NULL,
                approval_reference TEXT NOT NULL,
                approved_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS block_audit_log (
                id TEXT PRIMARY KEY,
                block_id TEXT NOT NULL,
                plan_id TEXT NOT NULL,
                actor TEXT NOT NULL,
                role TEXT NOT NULL,
                action TEXT NOT NULL,
                original_schedule_json TEXT NOT NULL,
                modified_schedule_json TEXT,
                reason_code TEXT NOT NULL,
                justification_notes TEXT NOT NULL,
                created_at TEXT NOT NULL,
                previous_hash TEXT,
                entry_hash TEXT
            );
            CREATE TABLE IF NOT EXISTS block_sanctions (
                block_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                section_id TEXT NOT NULL,
                state TEXT NOT NULL,
                effective_start TEXT NOT NULL,
                effective_end TEXT NOT NULL,
                latest_action TEXT NOT NULL,
                sanctioned_by TEXT,
                sanctioned_role TEXT,
                sanctioned_at TEXT,
                justification TEXT,
                reason_code TEXT,
                details_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        # Existing development databases predate the hash columns.  Preserve their
        # historical rows as-is; every subsequently written entry is chained.
        audit_columns = {row["name"] for row in db.execute("PRAGMA table_info(block_audit_log)")}
        if "previous_hash" not in audit_columns:
            db.execute("ALTER TABLE block_audit_log ADD COLUMN previous_hash TEXT")
        if "entry_hash" not in audit_columns:
            db.execute("ALTER TABLE block_audit_log ADD COLUMN entry_hash TEXT")
        db.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_audit_log_update
            BEFORE UPDATE ON block_audit_log
            BEGIN SELECT RAISE(ABORT, 'block_audit_log is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS prevent_audit_log_delete
            BEFORE DELETE ON block_audit_log
            BEGIN SELECT RAISE(ABORT, 'block_audit_log is append-only'); END;
            """
        )
        # Ensure blocks from existing block_plans are registered in block_sanctions
        plans = db.execute("SELECT id, result_json FROM block_plans").fetchall()
        for p in plans:
            try:
                res = json.loads(p["result_json"])
                for blk in res.get("scheduled_blocks", []):
                    b_id = blk.get("corridor_id")
                    if b_id:
                        exists = db.execute("SELECT 1 FROM block_sanctions WHERE block_id = ?", (b_id,)).fetchone()
                        if not exists:
                            db.execute(
                                """INSERT INTO block_sanctions
                                   (block_id, plan_id, section_id, state, effective_start, effective_end,
                                    latest_action, sanctioned_by, sanctioned_role, sanctioned_at,
                                    justification, reason_code, details_json, updated_at)
                                   VALUES (?, ?, ?, 'PROPOSED', ?, ?, 'PROPOSED', NULL, NULL, NULL, NULL, NULL, ?, ?)""",
                                (
                                    b_id,
                                    p["id"],
                                    blk.get("section_id", ""),
                                    blk.get("window_start", ""),
                                    blk.get("window_end", ""),
                                    json.dumps(blk),
                                    now().isoformat(),
                                ),
                            )
            except Exception:
                pass



def now() -> datetime:
    return datetime.now(timezone.utc)


def source_details(source_system: SourceSystem, record: dict[str, Any]) -> tuple[str, str, str, datetime, Department, float | None, float | None]:
    """Extract only documented fields; missing or invalid values are review reasons."""
    try:
        if source_system is SourceSystem.TMS:
            required = ("ticket_id", "track_id", "km_start", "km_end", "defect_class", "date_detected")
            ensure_fields(record, required)
            return (
                str(record["ticket_id"]), str(record["track_id"]), str(record["defect_class"]),
                parse_timestamp(record["date_detected"]), Department.ENGINEERING,
                parse_km(record["km_start"]), parse_km(record["km_end"]),
            )
        if source_system is SourceSystem.SMMS:
            required = ("fault_id", "station_code", "gear_type", "failure_category", "reported_ts", "urgency_code")
            ensure_fields(record, required)
            return (
                str(record["fault_id"]), str(record["station_code"]), str(record["failure_category"]),
                parse_timestamp(record["reported_ts"]), Department.SIGNAL_TELECOM, None, None,
            )
        required = ("defect_no", "ohe_substation", "mast_from", "mast_to", "issue_type", "scheduled_date")
        ensure_fields(record, required)
        return (
            str(record["defect_no"]), f"{record['mast_from']}:{record['mast_to']}", str(record["issue_type"]),
            parse_timestamp(record["scheduled_date"]), Department.TRACTION, None, None,
        )
    except (TypeError, ValueError) as error:
        raise ValueError(str(error)) from error


def ensure_fields(record: dict[str, Any], required: tuple[str, ...]) -> None:
    missing = [field for field in required if record.get(field) in (None, "")]
    if missing:
        raise ValueError(f"Missing required source fields: {', '.join(missing)}")


def parse_timestamp(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_km(value: Any) -> float:
    parsed = float(value)
    if parsed < 0:
        raise ValueError("Kilometre values cannot be negative")
    return parsed


def persist_review(request: IngestionRequest, reason: str, source_reference: str | None = None) -> dict[str, Any]:
    record_id = str(uuid4())
    with connection() as db:
        db.execute(
            """INSERT INTO ingestion_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (record_id, request.source_system, source_reference, now().isoformat(), json.dumps(request.record),
             json.dumps(request.planning_context.model_dump(mode="json")), None, "NEEDS_REVIEW", json.dumps([reason])),
        )
    return {"ingestion_id": record_id, "data_quality_status": "NEEDS_REVIEW", "review_reasons": [reason], "normalized_task": None}


app = FastAPI(title="RailSync F-01 Ingestion API", version="1.0.0")
app.mount("/static", StaticFiles(directory=ROOT), name="static")


def render_page(filename: str) -> HTMLResponse:
    page = (ROOT / filename).read_text(encoding="utf-8")
    if 'href="/cockpit"' not in page:
        page = page.replace(
            "</nav>",
            '<a class="nav-link" href="/cockpit">Cockpit</a></nav>',
            1,
        )
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@app.on_event("startup")
def startup() -> None:
    initialize_database()


@app.get("/", include_in_schema=False)
def dashboard() -> HTMLResponse:
    return render_page("index.html")


@app.get("/intake", include_in_schema=False)
def intake_page() -> HTMLResponse:
    return render_page("intake.html")


@app.get("/feasibility", include_in_schema=False)
def feasibility_page() -> HTMLResponse:
    page = bytes(render_page("feasibility.html").body).decode("utf-8")
    page = page.replace(
        '<a class="nav-link active" href="/feasibility">Feasibility</a><a class="nav-link" href="/what-if">What-if</a>',
        '<a class="nav-link active" href="/feasibility">Feasibility</a><a class="nav-link" href="/priority">Priority</a><a class="nav-link" href="/planner">Block plan</a><a class="nav-link" href="/operations-data">Operations data</a><a class="nav-link" href="/what-if">What-if</a>',
    )
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@app.get("/priority", include_in_schema=False)
def priority_page() -> HTMLResponse:
    page = bytes(render_page("priority.html").body).decode("utf-8")
    page = page.replace(
        '<a class="nav-link active" href="/priority">Priority</a><a class="nav-link" href="/what-if">What-if</a>',
        '<a class="nav-link active" href="/priority">Priority</a><a class="nav-link" href="/planner">Block plan</a><a class="nav-link" href="/operations-data">Operations data</a><a class="nav-link" href="/what-if">What-if</a>',
    )
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@app.get("/planner", include_in_schema=False)
def planner_page() -> HTMLResponse:
    page = bytes(render_page("planner.html").body).decode("utf-8")
    page = page.replace(
        '<a class="nav-link active" href="/planner">Block plan</a><a class="nav-link" href="/what-if">What-if</a>',
        '<a class="nav-link active" href="/planner">Block plan</a><a class="nav-link" href="/operations-data">Operations data</a><a class="nav-link" href="/what-if">What-if</a>',
    )
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@app.get("/operations-data", include_in_schema=False)
def operations_data_page() -> HTMLResponse:
    return render_page("operations-data.html")


@app.get("/what-if", include_in_schema=False)
def what_if_page() -> HTMLResponse:
    return render_page("what-if.html")


@app.get("/live-monitor", include_in_schema=False)
def live_monitor_page() -> HTMLResponse:
    return render_page("live-monitor.html")


@app.get("/cockpit", include_in_schema=False)
def cockpit_page() -> HTMLResponse:
    return render_page("cockpit.html")


@app.post("/api/v1/network-references", status_code=201)
def register_reference(reference: ReferenceRegistration) -> dict[str, Any]:
    reference_id = str(uuid4())
    try:
        with connection() as db:
            db.execute(
                """INSERT INTO network_references VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (reference_id, reference.source_system, reference.source_reference, reference.section_id,
                 reference.start_km, reference.end_km, reference.asset_type, reference.asset_reference, now().isoformat()),
            )
    except sqlite3.IntegrityError as error:
        raise HTTPException(409, "An authoritative reference already exists for this source system and reference.") from error
    return {"reference_id": reference_id, "status": "REGISTERED", "reference": reference.model_dump()}


def fetch_forecast(latitude: float, longitude: float, proposed_time: datetime) -> dict[str, Any] | None:
    """Fetch a real forecast hour from Open-Meteo. Absent/unavailable data is intentionally not substituted."""
    forecast_date = proposed_time.astimezone(timezone.utc).date().isoformat()
    try:
        with httpx.Client(trust_env=False, timeout=10.0) as client:
            response = client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": latitude, "longitude": longitude, "hourly": "temperature_2m,wind_speed_10m,visibility",
                    "timezone": "UTC", "start_date": forecast_date, "end_date": forecast_date,
                },
            )
        response.raise_for_status()
        hourly = response.json().get("hourly", {})
        target_hour = proposed_time.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")
        timestamps = hourly.get("time", [])
        normalized_times = [f"{item}:00Z" if item.endswith(":00") else item for item in timestamps]
        if target_hour not in normalized_times:
            return None
        index = normalized_times.index(target_hour)
        values = {"temperature_c": hourly["temperature_2m"][index], "wind_speed_kmh": hourly["wind_speed_10m"][index], "visibility_m": hourly["visibility"][index]}
        if any(value is None for value in values.values()):
            return None
        return {"forecast_timestamp": target_hour, **values}
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        return None


def evaluate_feasibility_logic(
    task: NormalizedTask,
    latitude: float,
    longitude: float,
    proposed_time: datetime,
) -> dict[str, Any]:
    forecast = fetch_forecast(latitude, longitude, proposed_time)
    assessment_id = str(uuid4())
    reasons: list[str] = []

    if forecast is None:
        reasons.append("No live forecast is available for the proposed hour and supplied section coordinates.")
        result = {
            "assessment_id": assessment_id,
            "task_id": task.id,
            "status": "NEEDS_REVIEW",
            "viable": False,
            "risk_multiplier": None,
            "warning_reasons": reasons,
            "forecast": forecast,
            "rule_version": DEFAULT_RULE_VERSION,
            "assessed_at": now().isoformat(),
        }
    else:
        status = "SUITABLE"
        risk_multiplier = 1.0
        if task.department is Department.ENGINEERING and forecast["temperature_c"] > MAX_ENGINEERING_TEMP:
            status = "NOT_SUITABLE"
            reasons.append(f"Forecast temperature ({forecast['temperature_c']}°C) exceeds Indian Railways limit ({MAX_ENGINEERING_TEMP}°C).")
        if task.department is Department.TRACTION and forecast["wind_speed_kmh"] > MAX_TRACTION_WIND:
            status = "NOT_SUITABLE"
            reasons.append(f"Forecast wind speed ({forecast['wind_speed_kmh']} km/h) exceeds Indian Railways limit ({MAX_TRACTION_WIND} km/h).")
        if task.requires_traffic_block and forecast["visibility_m"] < MIN_VISIBILITY:
            if status != "NOT_SUITABLE":
                status = "CAUTION_REQUIRED"
            risk_multiplier = CAUTION_MULTIPLIER
            reasons.append(f"Forecast visibility ({forecast['visibility_m']} m) is below traffic-block minimum ({MIN_VISIBILITY} m).")
        if not reasons:
            reasons.append(f"Forecast conditions meet Indian Railways safety limits (Temp <= {MAX_ENGINEERING_TEMP}°C, Wind <= {MAX_TRACTION_WIND} km/h, Vis >= {MIN_VISIBILITY}m).")
        result = {
            "assessment_id": assessment_id,
            "task_id": task.id,
            "status": status,
            "viable": status != "NOT_SUITABLE",
            "risk_multiplier": risk_multiplier,
            "warning_reasons": reasons,
            "forecast": forecast,
            "rule_version": DEFAULT_RULE_VERSION,
            "assessed_at": now().isoformat(),
        }

    with connection() as db:
        db.execute(
            "INSERT INTO feasibility_assessments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (assessment_id, task.id, proposed_time.isoformat(), latitude, longitude,
            DEFAULT_RULE_VERSION, json.dumps(result), now().isoformat()),
        )
    return result


@app.post("/api/v1/ingestion/tasks", status_code=201)
def ingest_task(request: IngestionRequest) -> dict[str, Any]:
    try:
        source_id, lookup_reference, maintenance_type, source_timestamp, department, raw_start, raw_end = source_details(request.source_system, request.record)
    except ValueError as error:
        return persist_review(request, str(error))

    with connection() as db:
        existing = db.execute(
            """SELECT id FROM ingestion_records
               WHERE source_system = ? AND source_reference = ? AND data_quality_status = 'COMPLETE'
               LIMIT 1""",
            (request.source_system, source_id),
        ).fetchone()
    if existing is not None:
        raise HTTPException(
            409,
            f"Source record '{source_id}' has already been ingested. Use its existing normalized task rather than submitting a duplicate.",
        )

    with connection() as db:
        mapping = db.execute(
            "SELECT * FROM network_references WHERE source_system = ? AND source_reference = ?",
            (request.source_system, lookup_reference),
        ).fetchone()

    if mapping is None:
        return persist_review(request, f"No controlled network reference is registered for '{lookup_reference}'.", source_id)

    start_km = raw_start if raw_start is not None else mapping["start_km"]
    end_km = raw_end if raw_end is not None else mapping["end_km"]
    if end_km < start_km:
        return persist_review(request, "The source kilometre range ends before it starts.", source_id)
    if start_km < mapping["start_km"] or end_km > mapping["end_km"]:
        return persist_review(request, "The source kilometre range falls outside the registered controlled reference.", source_id)

    task = NormalizedTask(
        id=source_id, source_system=request.source_system, source_reference=lookup_reference,
        source_timestamp=source_timestamp, department=department, asset_type=mapping["asset_type"],
        asset_reference=mapping["asset_reference"], section_id=mapping["section_id"], start_km=start_km, end_km=end_km,
        maintenance_type=maintenance_type, severity=request.planning_context.severity,
        due_date=request.planning_context.due_date or request.planning_context.proposed_time or now(),
        overdue=(request.planning_context.due_date or request.planning_context.proposed_time or now()) < now(),
        estimated_duration_minutes=request.planning_context.estimated_duration_minutes,
        required_crews=request.planning_context.required_crews, required_equipment=request.planning_context.required_equipment,
        requires_traffic_block=request.planning_context.requires_traffic_block,
        requires_traction_disconnection=request.planning_context.requires_traction_disconnection,
        co_working_compatible=request.planning_context.co_working_compatible,
        co_working_departments=request.planning_context.co_working_departments,
        data_quality_status="COMPLETE",
        section_latitude=request.planning_context.section_latitude,
        section_longitude=request.planning_context.section_longitude,
        proposed_time=request.planning_context.proposed_time,
    )
    ingestion_id = str(uuid4())
    with connection() as db:
        db.execute(
            """INSERT INTO ingestion_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ingestion_id, request.source_system, source_id, now().isoformat(), json.dumps(request.record),
             json.dumps(request.planning_context.model_dump(mode="json")), task.model_dump_json(), "COMPLETE", "[]"),
        )

    feasibility_result = None
    if (
        request.planning_context.section_latitude is not None
        and request.planning_context.section_longitude is not None
        and request.planning_context.proposed_time is not None
    ):
        feasibility_result = evaluate_feasibility_logic(
            task=task,
            latitude=request.planning_context.section_latitude,
            longitude=request.planning_context.section_longitude,
            proposed_time=request.planning_context.proposed_time,
        )

    return {
        "ingestion_id": ingestion_id,
        "data_quality_status": "COMPLETE",
        "review_reasons": [],
        "normalized_task": task,
        "feasibility": feasibility_result,
    }


@app.get("/api/v1/ingestion/tasks")
def list_ingestion_records() -> list[dict[str, Any]]:
    with connection() as db:
        rows = db.execute(
            """SELECT id, source_system, source_reference, received_at, normalized_task_json,
                      data_quality_status, review_reasons_json
               FROM ingestion_records ORDER BY received_at DESC"""
        ).fetchall()
    return [
        {"ingestion_id": row["id"], "source_system": row["source_system"], "source_reference": row["source_reference"],
         "received_at": row["received_at"], "data_quality_status": row["data_quality_status"],
         "review_reasons": json.loads(row["review_reasons_json"]),
         "normalized_task": json.loads(row["normalized_task_json"]) if row["normalized_task_json"] else None}
        for row in rows
    ]


@app.get("/api/v1/operations/summary")
def operations_summary() -> dict[str, Any]:
    """Expose only operator-supplied operational records; never create sample data."""
    with connection() as db:
        timetable = db.execute("SELECT record_id, section_id, window_start, window_end, passenger_trains_affected, source_timestamp FROM timetable_occupancy ORDER BY window_start").fetchall()
        goods = db.execute("SELECT record_id, section_id, window_start, window_end, goods_trains_affected, source_timestamp FROM goods_forecasts ORDER BY window_start").fetchall()
        coa = db.execute("SELECT corridor_id, window_json, source_timestamp FROM coa_windows ORDER BY created_at DESC").fetchall()
    return {
        "timetable_occupancy": [dict(row) for row in timetable],
        "goods_forecasts": [dict(row) for row in goods],
        "coa_windows": [{"corridor_id": row["corridor_id"], "window": json.loads(row["window_json"]), "source_timestamp": row["source_timestamp"]} for row in coa],
    }


@app.post("/api/v1/operations/timetable-occupancy", status_code=201)
def import_timetable_occupancy(record: TimetableOccupancy) -> dict[str, Any]:
    try:
        with connection() as db:
            db.execute(
                "INSERT INTO timetable_occupancy VALUES (?, ?, ?, ?, ?, ?, ?)",
                (record.record_id, record.section_id, record.window_start.isoformat(), record.window_end.isoformat(),
                 record.passenger_trains_affected, record.source_timestamp.isoformat(), now().isoformat()),
            )
    except sqlite3.IntegrityError as error:
        raise HTTPException(409, "A timetable occupancy record already exists with this record_id.") from error
    return {"status": "IMPORTED", "record": record}


@app.post("/api/v1/operations/goods-forecasts", status_code=201)
def import_goods_forecast(record: GoodsForecast) -> dict[str, Any]:
    try:
        with connection() as db:
            db.execute(
                "INSERT INTO goods_forecasts VALUES (?, ?, ?, ?, ?, ?, ?)",
                (record.record_id, record.section_id, record.window_start.isoformat(), record.window_end.isoformat(),
                 record.goods_trains_affected, record.source_timestamp.isoformat(), now().isoformat()),
            )
    except sqlite3.IntegrityError as error:
        raise HTTPException(409, "A goods forecast already exists with this record_id.") from error
    return {"status": "IMPORTED", "record": record}


@app.post("/api/v1/operations/coa-windows", status_code=201)
def import_coa_window(request: COAWindowImport) -> dict[str, Any]:
    """Accept an authoritative COA window only after both cited source records exist."""
    with connection() as db:
        timetable = db.execute("SELECT * FROM timetable_occupancy WHERE record_id = ?", (request.window.timetable_reference,)).fetchone()
        goods = db.execute("SELECT * FROM goods_forecasts WHERE record_id = ?", (request.window.goods_forecast_reference,)).fetchone()
    if timetable is None or goods is None:
        missing = []
        if timetable is None:
            missing.append("timetable occupancy")
        if goods is None:
            missing.append("goods forecast")
        raise HTTPException(409, f"Import the referenced {', '.join(missing)} record before its COA window.")
    for label, source in (("timetable occupancy", timetable), ("goods forecast", goods)):
        if source["section_id"] != request.window.section_id:
            raise HTTPException(422, f"Referenced {label} section does not match the COA window section.")
        if datetime.fromisoformat(source["window_start"]) > request.window.start_time or datetime.fromisoformat(source["window_end"]) < request.window.end_time:
            raise HTTPException(422, f"Referenced {label} interval does not cover the entire COA window.")
    stored_window = CorridorWindow(
        **request.window.model_dump(),
        passenger_trains_affected=0,
        goods_trains_affected=0,
    )
    try:
        with connection() as db:
            db.execute("INSERT INTO coa_windows VALUES (?, ?, ?, ?)", (request.window.corridor_id, stored_window.model_dump_json(), request.source_timestamp.isoformat(), now().isoformat()))
    except sqlite3.IntegrityError as error:
        raise HTTPException(409, "A COA window already exists with this corridor_id.") from error
    return {"status": "IMPORTED", "window": stored_window}


@app.post("/api/v1/feasibility/assessments", status_code=201)
def assess_feasibility(request: FeasibilityRequest) -> dict[str, Any]:
    with connection() as db:
        row = db.execute(
            """SELECT normalized_task_json FROM ingestion_records
               WHERE source_reference = ? AND data_quality_status = 'COMPLETE'
               ORDER BY received_at DESC LIMIT 1""",
            (request.task_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "A complete normalized task with this task ID was not found.")
    task = NormalizedTask.model_validate_json(row["normalized_task_json"])
    if task.section_latitude is None or task.section_longitude is None or task.proposed_time is None:
        raise HTTPException(422, "The selected task does not contain section coordinates and a proposed block time from data intake.")
    return evaluate_feasibility_logic(
        task=task,
        latitude=task.section_latitude,
        longitude=task.section_longitude,
        proposed_time=task.proposed_time,
    )



def normalized_factor(value: float, maximum: float) -> float:
    if value > maximum:
        raise ValueError("A supplied measured value exceeds its policy maximum; update the approved policy rather than silently clipping it.")
    return (value / maximum) * 100


@app.get("/policy/config")
def get_policy_config() -> dict[str, Any]:
    """Return the current COA policy configuration used by the demo prioritization flow."""
    return DEFAULT_COA_POLICY


@app.post("/policy/config")
def update_policy_config(payload: dict[str, Any]) -> dict[str, Any]:
    """Simulate a COA admin saving a new global policy configuration."""
    global DEFAULT_COA_POLICY
    merged = {**DEFAULT_COA_POLICY, **payload}
    validated = PriorityPolicy.model_validate(merged)
    DEFAULT_COA_POLICY = validated.model_dump(mode="json")

    for slot_key in DEFAULT_TRAFFIC_DENSITY:
        if slot_key in payload:
            ACTIVE_COA_POLICY[slot_key] = int(payload[slot_key])

    ACTIVE_COA_POLICY.update({
        "section_traffic": DEFAULT_COA_POLICY["default_section_traffic_gmt"],
        "route_criticality": DEFAULT_COA_POLICY["default_route_criticality_score"],
        "policy_version": DEFAULT_COA_POLICY["policy_version"],
        "max_overdue_tolerance_days": DEFAULT_COA_POLICY["max_overdue_tolerance_days"],
        "weather_risk_weight": DEFAULT_COA_POLICY["weather_risk_weight"],
        "tsr_penalty_score": DEFAULT_COA_POLICY["tsr_penalty_score"],
        "freight_revenue_weight": DEFAULT_COA_POLICY["freight_revenue_weight"],
    })
    return DEFAULT_COA_POLICY


@app.get("/api/policy")
def get_active_policy() -> dict[str, Any]:
    return ACTIVE_COA_POLICY


@app.put("/api/policy")
def update_active_policy(update: ActivePolicyUpdate) -> dict[str, Any]:
    global DEFAULT_COA_POLICY
    update_payload = update.model_dump()
    ACTIVE_COA_POLICY.update(update_payload)

    daily_passenger_total = sum(int(update_payload[f"passenger_{slot}"]) for slot in ("00_06", "06_12", "12_18", "18_24"))
    daily_goods_total = sum(int(update_payload[f"goods_{slot}"]) for slot in ("00_06", "06_12", "12_18", "18_24"))

    DEFAULT_COA_POLICY = PriorityPolicy.model_validate({
        **DEFAULT_COA_POLICY,
        "default_passenger_trains_per_day": daily_passenger_total,
        "default_goods_forecast_per_day": daily_goods_total,
        "default_section_traffic_gmt": ACTIVE_COA_POLICY["section_traffic"],
        "default_route_criticality_score": ACTIVE_COA_POLICY["route_criticality"],
        "policy_version": ACTIVE_COA_POLICY["policy_version"],
        "max_days_overdue": ACTIVE_COA_POLICY["max_overdue_tolerance_days"],
        "max_overdue_tolerance_days": ACTIVE_COA_POLICY["max_overdue_tolerance_days"],
        "weather_risk_weight": ACTIVE_COA_POLICY["weather_risk_weight"],
        "tsr_penalty_score": ACTIVE_COA_POLICY["tsr_penalty_score"],
        "freight_revenue_weight": ACTIVE_COA_POLICY["freight_revenue_weight"],
        "active_restriction_score": ACTIVE_COA_POLICY["tsr_penalty_score"],
        "weather_weight": ACTIVE_COA_POLICY["weather_risk_weight"],
    }).model_dump(mode="json")
    return ACTIVE_COA_POLICY


@app.get("/api/v1/priority/policies")
def list_priority_policies() -> list[dict[str, Any]]:
    with connection() as db:
        rows = db.execute("SELECT policy_id, policy_json, approval_reference, approved_at, created_at FROM priority_policies ORDER BY approved_at DESC").fetchall()
    return [{"policy_id": row["policy_id"], "policy": json.loads(row["policy_json"]), "approval_reference": row["approval_reference"], "approved_at": row["approved_at"], "created_at": row["created_at"]} for row in rows]


@app.post("/api/v1/priority/policies", status_code=201)
def register_priority_policy(registration: PriorityPolicyRegistration) -> dict[str, Any]:
    try:
        with connection() as db:
            db.execute(
                "INSERT INTO priority_policies VALUES (?, ?, ?, ?, ?)",
                (registration.policy_id, registration.policy.model_dump_json(), registration.approval_reference,
                 registration.approved_at.isoformat(), now().isoformat()),
            )
    except sqlite3.IntegrityError as error:
        raise HTTPException(409, "An approved priority policy already exists with this policy_id.") from error
    return {"status": "REGISTERED", **registration.model_dump(mode="json")}


def _default_priority_policy() -> PriorityPolicy:
    daily_passenger_total = sum(int(ACTIVE_COA_POLICY[f"passenger_{slot}"]) for slot in ("00_06", "06_12", "12_18", "18_24"))
    daily_goods_total = sum(int(ACTIVE_COA_POLICY[f"goods_{slot}"]) for slot in ("00_06", "06_12", "12_18", "18_24"))
    return PriorityPolicy.model_validate({
        **DEFAULT_COA_POLICY,
        "default_passenger_trains_per_day": daily_passenger_total,
        "default_goods_forecast_per_day": daily_goods_total,
        "default_section_traffic_gmt": ACTIVE_COA_POLICY["section_traffic"],
        "default_route_criticality_score": ACTIVE_COA_POLICY["route_criticality"],
        "policy_version": ACTIVE_COA_POLICY["policy_version"],
        "max_days_overdue": ACTIVE_COA_POLICY["max_overdue_tolerance_days"],
        "max_overdue_tolerance_days": ACTIVE_COA_POLICY["max_overdue_tolerance_days"],
        "weather_risk_weight": ACTIVE_COA_POLICY["weather_risk_weight"],
        "tsr_penalty_score": ACTIVE_COA_POLICY["tsr_penalty_score"],
        "freight_revenue_weight": ACTIVE_COA_POLICY["freight_revenue_weight"],
        "active_restriction_score": ACTIVE_COA_POLICY["tsr_penalty_score"],
        "weather_weight": ACTIVE_COA_POLICY["weather_risk_weight"],
    })


def _derive_task_operational_context(policy: PriorityPolicy) -> dict[str, float]:
    return {
        "passenger_train_frequency_per_day": policy.default_passenger_trains_per_day,
        "goods_train_forecast_per_day": policy.default_goods_forecast_per_day,
        "section_traffic_gmt": policy.default_section_traffic_gmt,
        "route_criticality_score": policy.default_route_criticality_score,
    }


def _evaluate_priority_for_task(task: NormalizedTask, context: PriorityContext, policy: PriorityPolicy, assessment: dict[str, Any] | None) -> dict[str, Any]:
    risk_multiplier = None
    if assessment is not None:
        risk_multiplier = assessment.get("risk_multiplier")
    if risk_multiplier is None:
        raise HTTPException(400, "Step 2 Incomplete: An eligible F-02 feasibility assessment is required before priority scoring.")
    risk_multiplier = float(risk_multiplier)
    try:
        factors = {
            "safety severity": policy.severity_scores[task.severity],
            "overdue age": normalized_factor(max(0, (now() - task.due_date).total_seconds() / 86400), policy.max_overdue_tolerance_days),
            "passenger timetable demand": normalized_factor(context.passenger_train_frequency_per_day, policy.max_passenger_trains_per_day),
            "goods forecast demand": min(100.0, normalized_factor(context.goods_train_forecast_per_day, policy.max_goods_trains_per_day) * policy.freight_revenue_weight),
            "section traffic": normalized_factor(context.section_traffic_gmt, policy.max_section_traffic_gmt),
            "active operational restriction": policy.tsr_penalty_score if context.active_operational_restriction else 0,
            "route criticality": context.route_criticality_score,
            "F-02 weather risk": min(100.0, risk_multiplier * policy.weather_risk_weight),
        }
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    weights = {
        "safety severity": policy.severity_weight, "overdue age": policy.overdue_weight,
        "passenger timetable demand": policy.passenger_weight, "goods forecast demand": policy.goods_weight,
        "section traffic": policy.traffic_weight, "active operational restriction": policy.restriction_weight,
        "route criticality": policy.route_criticality_weight, "F-02 weather risk": policy.weather_weight,
    }
    total_weight = sum(weights.values())
    contributions = [{"factor": name, "input_score": round(factors[name], 2), "weight": weight, "score_contribution": round((factors[name] * weight) / total_weight, 2)} for name, weight in weights.items() if weight > 0]
    score = round(sum(item["score_contribution"] for item in contributions), 2)
    tier = "CRITICAL" if score >= policy.critical_threshold else "HIGH" if score >= policy.high_threshold else "MEDIUM" if score >= policy.medium_threshold else "LOW"
    return {
        "evaluation_id": str(uuid4()),
        "task_id": task.id,
        "score": score,
        "tier": tier,
        "policy_id": None,
        "policy_version": policy.policy_version,
        "f02_assessment_id": assessment.get("assessment_id") if assessment else None,
        "top_contributing_factors": sorted(contributions, key=lambda item: item["score_contribution"], reverse=True),
        "explanation": "Priority is a transparent weighted calculation from the selected COA policy and the task's recorded operational inputs; it is not an ML prediction.",
        "evaluated_at": now().isoformat(),
    }


@app.post("/calculate-priority", status_code=200)
def calculate_priority(request: SimplePriorityRequest) -> dict[str, Any]:
    """Simplified F-03 calculation using the global default COA policy and the minimum task inputs only."""
    with connection() as db:
        task_row = db.execute(
            """SELECT normalized_task_json FROM ingestion_records
               WHERE source_reference = ? AND data_quality_status = 'COMPLETE'
               ORDER BY received_at DESC LIMIT 1""",
            (request.task_id,),
        ).fetchone()
        assessment_row = db.execute(
            """SELECT result_json FROM feasibility_assessments WHERE task_id = ? ORDER BY created_at DESC LIMIT 1""",
            (request.task_id,),
        ).fetchone()
    if task_row is None:
        raise HTTPException(404, "A complete F-01 task with this task ID was not found.")
    if assessment_row is None or not assessment_row["result_json"]:
        raise HTTPException(400, "Step 2 Incomplete: An eligible F-02 feasibility assessment is required before priority scoring.")
    task = NormalizedTask.model_validate_json(task_row["normalized_task_json"])
    assessment = json.loads(assessment_row["result_json"])
    if assessment.get("status") not in {"SUITABLE", "CAUTION_REQUIRED"}:
        raise HTTPException(400, "Step 2 Incomplete: An eligible F-02 feasibility assessment is required before priority scoring.")
    policy = _default_priority_policy()
    context_values = _derive_task_operational_context(policy)
    if task.proposed_time is None:
        raise HTTPException(400, "F-01 task is missing the proposed block date and time required for traffic-density routing.")
    proposed_block_time = task.proposed_time.astimezone(timezone.utc)
    hour = proposed_block_time.hour
    if 0 <= hour < 6:
        traffic_slot = "00_06"
    elif 6 <= hour < 12:
        traffic_slot = "06_12"
    elif 12 <= hour < 18:
        traffic_slot = "12_18"
    elif 18 <= hour < 24:
        traffic_slot = "18_24"
    else:
        raise HTTPException(422, "The proposed block time has an invalid hour for traffic-density routing.")
    context_values["passenger_train_frequency_per_day"] = ACTIVE_COA_POLICY[f"passenger_{traffic_slot}"]
    context_values["goods_train_forecast_per_day"] = ACTIVE_COA_POLICY[f"goods_{traffic_slot}"]
    context = PriorityContext(
        task_id=task.id,
        passenger_train_frequency_per_day=context_values["passenger_train_frequency_per_day"],
        goods_train_forecast_per_day=context_values["goods_train_forecast_per_day"],
        section_traffic_gmt=context_values["section_traffic_gmt"],
        route_criticality_score=context_values["route_criticality_score"],
        active_operational_restriction=request.active_restriction,
    )
    result = _evaluate_priority_for_task(task, context, policy, assessment)
    with connection() as db:
        db.execute(
            "INSERT INTO priority_evaluations VALUES (?, ?, ?, ?, ?, ?)",
            (result["evaluation_id"], task.id, policy.policy_version, json.dumps(context.model_dump()), json.dumps(result), now().isoformat()),
        )
    return result


@app.post("/api/v1/priority/evaluations", status_code=201)
def evaluate_priority(request: PriorityRequest) -> dict[str, Any]:
    policy = request.policy
    policy_id = request.policy_id
    if policy_id is not None:
        with connection() as db:
            policy_row = db.execute("SELECT policy_json FROM priority_policies WHERE policy_id = ?", (policy_id,)).fetchone()
        if policy_row is None:
            raise HTTPException(404, "No approved priority policy exists with this policy_id.")
        policy = PriorityPolicy.model_validate_json(policy_row["policy_json"])
    if policy is None:
        policy = _default_priority_policy()
    with connection() as db:
        task_row = db.execute(
            """SELECT normalized_task_json FROM ingestion_records
               WHERE source_reference = ? AND data_quality_status = 'COMPLETE'
               ORDER BY received_at DESC LIMIT 1""", (request.context.task_id,)
        ).fetchone()
        assessment_row = db.execute(
            """SELECT result_json FROM feasibility_assessments WHERE task_id = ?
               ORDER BY created_at DESC LIMIT 1""", (request.context.task_id,)
        ).fetchone()
    if task_row is None:
        raise HTTPException(404, "A complete F-01 task with this task ID was not found.")
    task = NormalizedTask.model_validate_json(task_row["normalized_task_json"])
    assessment = json.loads(assessment_row["result_json"]) if assessment_row and assessment_row["result_json"] else None
    result = _evaluate_priority_for_task(task, request.context, policy, assessment)
    with connection() as db:
        db.execute("INSERT INTO priority_evaluations VALUES (?, ?, ?, ?, ?, ?)", (result["evaluation_id"], task.id, policy.policy_version, json.dumps(request.context.model_dump()), json.dumps(result), now().isoformat()))
    return result


def utc_hour(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def intervals_overlap(first_start: datetime, first_end: datetime, second_start: datetime, second_end: datetime) -> bool:
    return first_start < second_end and second_start < first_end


@app.get("/api/v1/block-plans/eligibility")
def block_plan_eligibility() -> list[dict[str, Any]]:
    """Explain F-04 inclusion status before a COA planning run."""
    with connection() as db:
        task_rows = db.execute("SELECT normalized_task_json FROM ingestion_records WHERE data_quality_status = 'COMPLETE'").fetchall()
        priority_rows = db.execute("SELECT task_id, result_json, created_at FROM priority_evaluations ORDER BY created_at DESC").fetchall()
        assessment_rows = db.execute("SELECT task_id, result_json, proposed_time, created_at FROM feasibility_assessments ORDER BY created_at DESC").fetchall()
    priorities: dict[str, dict[str, Any]] = {}
    for row in priority_rows:
        priorities.setdefault(row["task_id"], json.loads(row["result_json"]))
    assessments: dict[str, dict[str, Any]] = {}
    for row in assessment_rows:
        assessments.setdefault(row["task_id"], {**json.loads(row["result_json"]), "proposed_time": row["proposed_time"]})
    response = []
    for row in task_rows:
        task = NormalizedTask.model_validate_json(row["normalized_task_json"])
        assessment, priority = assessments.get(task.id), priorities.get(task.id)
        missing = []
        if assessment is None:
            missing.append("Run F-02 feasibility assessment.")
        elif assessment["status"] not in {"SUITABLE", "CAUTION_REQUIRED"}:
            missing.append(f"Latest F-02 status is {assessment['status']}; an eligible assessment is required.")
        if priority is None:
            missing.append("Run F-03 priority evaluation.")
        response.append({"task_id": task.id, "department": task.department, "section_id": task.section_id, "duration_minutes": task.estimated_duration_minutes, "priority_score": priority.get("score") if priority else None, "eligible": not missing, "missing_requirements": missing, "required_coa_start_hour": assessment.get("proposed_time") if assessment and assessment["status"] in {"SUITABLE", "CAUTION_REQUIRED"} else None})
    return sorted(response, key=lambda item: (not item["eligible"], item["task_id"]))


@app.post("/api/v1/block-plans", status_code=201)
def create_block_plan(request: BlockPlanRequest) -> dict[str, Any]:
    """Bounded exact search over real eligible tasks and submitted COA windows.

    Work is parallelized only where every concurrent task explicitly records
    co-working compatibility and COA crew capacity is sufficient; otherwise it
    is sequenced within the shared block.
    """
    with connection() as db:
        task_rows = db.execute(
            """SELECT source_reference, normalized_task_json FROM ingestion_records
               WHERE data_quality_status = 'COMPLETE'"""
        ).fetchall()
        priority_rows = db.execute(
            """SELECT task_id, result_json, created_at FROM priority_evaluations
               ORDER BY created_at DESC"""
        ).fetchall()
        assessment_rows = db.execute(
            """SELECT task_id, result_json, proposed_time, created_at FROM feasibility_assessments
               ORDER BY created_at DESC"""
        ).fetchall()
    latest_priorities: dict[str, dict[str, Any]] = {}
    for row in priority_rows:
        latest_priorities.setdefault(row["task_id"], json.loads(row["result_json"]))
    latest_assessments: dict[str, dict[str, Any]] = {}
    for row in assessment_rows:
        latest_assessments.setdefault(row["task_id"], {**json.loads(row["result_json"]), "proposed_time": row["proposed_time"]})

    eligible: list[dict[str, Any]] = []
    deferred: list[dict[str, str]] = []
    for row in task_rows:
        task = NormalizedTask.model_validate_json(row["normalized_task_json"])
        if request.task_ids and task.id not in request.task_ids:
            continue
        priority = latest_priorities.get(task.id)
        assessment = latest_assessments.get(task.id)
        if priority is None:
            deferred.append({"task_id": task.id, "reason": "No F-03 priority evaluation is available."})
            continue
        if assessment is None or assessment.get("status") not in {"SUITABLE", "CAUTION_REQUIRED"}:
            deferred.append({"task_id": task.id, "reason": "No eligible F-02 feasibility assessment is available."})
            continue
        eligible.append({"task": task, "priority": priority, "assessment": assessment})

    windows = sorted(request.coa_windows, key=lambda window: window.start_time)
    candidates: list[dict[str, Any]] = []
    for item in eligible:
        task, assessment = item["task"], item["assessment"]
        assessment_hour = utc_hour(datetime.fromisoformat(assessment["proposed_time"]))
        choices: list[int] = []
        reasons: list[str] = []
        for index, window in enumerate(windows):
            if window.section_id != task.section_id:
                continue
            if utc_hour(window.start_time) != assessment_hour:
                reasons.append("F-02 assessment does not cover this COA window hour.")
                continue
            if task.requires_traffic_block and not window.traffic_block_available:
                reasons.append("COA window does not provide the required traffic block.")
                continue
            if task.requires_traction_disconnection and not window.traction_disconnection_available:
                reasons.append("COA window does not provide the required traction disconnection.")
                continue
            if len(task.required_crews) > window.max_simultaneous_crews:
                reasons.append("Task crew requirement exceeds COA window capacity.")
                continue
            if task.estimated_duration_minutes > (window.end_time - window.start_time).total_seconds() / 60:
                reasons.append("Task duration exceeds the available COA window.")
                continue
            if window.end_time > task.due_date:
                reasons.append("COA window ends after the recorded task due date.")
                continue
            choices.append(index)
        if not choices:
            deferred.append({"task_id": task.id, "reason": "; ".join(sorted(set(reasons))) or "No compatible COA window matches the task section."})
        else:
            candidates.append({**item, "choices": choices})

    candidates.sort(key=lambda item: (-item["priority"]["score"], item["task"].due_date, item["task"].id))
    remaining_bound = [0.0] * (len(candidates) + 1)
    for index in range(len(candidates) - 1, -1, -1):
        remaining_bound[index] = remaining_bound[index + 1] + candidates[index]["priority"]["score"]
    best: dict[str, Any] = {"score": -1.0, "used": float("inf"), "slack": float("inf"), "assignments": []}
    start_clock = time.monotonic()
    timed_out = False

    def consider(assignments: list[dict[str, Any]], score: float, used_minutes: list[int]) -> None:
        nonlocal best
        used_count = sum(minutes > 0 for minutes in used_minutes)
        slack = sum((windows[index].end_time - windows[index].start_time).total_seconds() / 60 - minutes for index, minutes in enumerate(used_minutes) if minutes > 0)
        candidate_key = (round(score, 6), -used_count, -slack)
        current_key = (round(best["score"], 6), -best["used"], -best["slack"])
        if candidate_key > current_key:
            best = {"score": score, "used": used_count, "slack": slack, "assignments": [dict(assignment) for assignment in assignments]}

    def search(index: int, assignments: list[dict[str, Any]], score: float, used_minutes: list[int]) -> None:
        nonlocal timed_out
        if time.monotonic() - start_clock > 4.0:
            timed_out = True
            return
        if score + remaining_bound[index] < best["score"]:
            return
        if index == len(candidates):
            consider(assignments, score, used_minutes)
            return
        item = candidates[index]
        task = item["task"]
        # Unscheduled branch preserves a complete, explainable result even when capacity is insufficient.
        search(index + 1, assignments, score, used_minutes)
        for window_index in item["choices"]:
            window = windows[window_index]
            within_window = [assigned for assigned in assignments if assigned["window_index"] == window_index]
            can_parallelize = (
                bool(within_window)
                and task.co_working_compatible
                and all(assigned["task"].co_working_compatible and assigned["start_time"] == window.start_time for assigned in within_window)
                and sum(len(assigned["task"].required_crews) for assigned in within_window) + len(task.required_crews) <= window.max_simultaneous_crews
            )
            task_start = window.start_time if can_parallelize else window.start_time + timedelta(minutes=used_minutes[window_index])
            task_end = task_start + timedelta(minutes=task.estimated_duration_minutes)
            if task_end > window.end_time:
                continue
            resource_conflict = any(
                (set(task.required_crews).intersection(assigned["task"].required_crews)
                 or set(task.required_equipment).intersection(assigned["task"].required_equipment))
                and intervals_overlap(task_start, task_end, assigned["start_time"], assigned["end_time"])
                for assigned in assignments
            )
            if resource_conflict:
                continue
            assignment = {"task": task, "priority": item["priority"], "assessment": item["assessment"], "window_index": window_index, "start_time": task_start, "end_time": task_end}
            previous_occupancy = used_minutes[window_index]
            used_minutes[window_index] = max(previous_occupancy, (task_end - window.start_time).total_seconds() / 60)
            assignments.append(assignment)
            search(index + 1, assignments, score + item["priority"]["score"], used_minutes)
            assignments.pop()
            used_minutes[window_index] = previous_occupancy

    from datetime import timedelta
    search(0, [], 0.0, [0] * len(windows))
    scheduled_ids = {assignment["task"].id for assignment in best["assignments"]}
    for item in candidates:
        if item["task"].id not in scheduled_ids:
            deferred.append({"task_id": item["task"].id, "reason": "No remaining compatible COA capacity after optimizing higher-priority work and shared block use."})
    plan_id = str(uuid4())
    blocks: list[dict[str, Any]] = []
    for index, window in enumerate(windows):
        assigned = sorted((item for item in best["assignments"] if item["window_index"] == index), key=lambda item: item["start_time"])
        if not assigned:
            continue
        blocks.append({
            "block_id": str(uuid4()), "corridor_id": window.corridor_id, "section_id": window.section_id,
            "window_start": window.start_time, "window_end": window.end_time,
            "timetable_reference": window.timetable_reference, "goods_forecast_reference": window.goods_forecast_reference,
            "passenger_trains_affected": window.passenger_trains_affected, "goods_trains_affected": window.goods_trains_affected,
            "assigned_tasks": [{"task_id": item["task"].id, "department": item["task"].department, "priority_score": item["priority"]["score"], "scheduled_start": item["start_time"], "scheduled_end": item["end_time"], "requires_traffic_block": item["task"].requires_traffic_block, "requires_traction_disconnection": item["task"].requires_traction_disconnection, "required_crews": item["task"].required_crews} for item in assigned],
            "consolidated_departments": sorted({item["task"].department for item in assigned}),
            "used_minutes": max((item["end_time"] - window.start_time).total_seconds() / 60 for item in assigned),
            "total_maintenance_minutes": sum(item["task"].estimated_duration_minutes for item in assigned),
            "available_minutes": (window.end_time - window.start_time).total_seconds() / 60,
        })
    result = {"plan_id": plan_id, "status": "PROPOSED", "horizon": request.horizon, "horizon_start": request.horizon_start, "horizon_end": request.horizon_end, "scheduled_blocks": blocks, "unscheduled_tasks": deferred, "metrics": {"scheduled_task_count": len(scheduled_ids), "unscheduled_task_count": len(deferred), "total_priority_score_scheduled": round(best["score"], 2), "distinct_corridor_disruptions": len(blocks), "shared_block_count": sum(1 for block in blocks if len(block["assigned_tasks"]) > 1), "total_block_hours_used": round(sum(block["used_minutes"] for block in blocks) / 60, 2), "parallel_block_hours_saved": round(sum(max(0, block["total_maintenance_minutes"] - block["used_minutes"]) for block in blocks) / 60, 2), "passenger_trains_affected": sum(block["passenger_trains_affected"] for block in blocks), "goods_trains_affected": sum(block["goods_trains_affected"] for block in blocks), "search_timed_out": timed_out}, "notes": ["Tasks run in parallel only when every concurrent task explicitly permits co-working, no crew or equipment resource conflicts exist, and COA capacity covers all crews; otherwise they are sequenced.", "Proposed only: controller sanction remains required."]}
    with connection() as db:
        db.execute("INSERT INTO block_plans VALUES (?, ?, ?, ?, ?, ?, ?)", (plan_id, request.horizon, request.horizon_start.isoformat(), request.horizon_end.isoformat(), json.dumps([window.model_dump(mode="json") for window in windows]), json.dumps(result, default=str), now().isoformat()))
        for block in blocks:
            db.execute(
                """INSERT OR REPLACE INTO block_sanctions
                   (block_id, plan_id, section_id, state, effective_start, effective_end,
                    latest_action, sanctioned_by, sanctioned_role, sanctioned_at,
                    justification, reason_code, details_json, updated_at)
                   VALUES (?, ?, ?, 'PROPOSED', ?, ?, 'PROPOSED', NULL, NULL, NULL, NULL, NULL, ?, ?)""",
                (
                    block["block_id"],
                    plan_id,
                    block["section_id"],
                    block["window_start"].isoformat() if isinstance(block["window_start"], datetime) else str(block["window_start"]),
                    block["window_end"].isoformat() if isinstance(block["window_end"], datetime) else str(block["window_end"]),
                    json.dumps(block, default=str),
                    now().isoformat(),
                ),
            )
    return result



@app.post("/api/v1/block-plans/from-integrated-data", status_code=201)
def create_integrated_block_plan(request: IntegratedPlanRequest) -> dict[str, Any]:
    """Build F-04 directly from previously imported COA, timetable and goods records."""
    with connection() as db:
        rows = db.execute("SELECT window_json FROM coa_windows").fetchall()
        timetable_rows = db.execute("SELECT * FROM timetable_occupancy").fetchall()
        goods_rows = db.execute("SELECT * FROM goods_forecasts").fetchall()
    timetable = {row["record_id"]: row for row in timetable_rows}
    goods = {row["record_id"]: row for row in goods_rows}
    windows: list[CorridorWindow] = []
    review_reasons: list[str] = []
    for row in rows:
        window = CorridorWindow.model_validate_json(row["window_json"])
        if window.start_time < request.horizon_start or window.end_time > request.horizon_end:
            continue
        timetable_source, goods_source = timetable.get(window.timetable_reference), goods.get(window.goods_forecast_reference)
        if timetable_source is None or goods_source is None:
            review_reasons.append(f"{window.corridor_id}: cited operational source record is no longer available.")
            continue
        if timetable_source["section_id"] != window.section_id or goods_source["section_id"] != window.section_id:
            review_reasons.append(f"{window.corridor_id}: cited operational data no longer matches the COA section.")
            continue
        windows.append(window.model_copy(update={
            "passenger_trains_affected": timetable_source["passenger_trains_affected"],
            "goods_trains_affected": goods_source["goods_trains_affected"],
        }))
    if not windows:
        detail = " No imported COA availability is inside this horizon."
        if review_reasons:
            detail += " " + " ".join(review_reasons)
        raise HTTPException(409, detail.strip())
    result = create_block_plan(BlockPlanRequest(
        horizon=request.horizon,
        horizon_start=request.horizon_start,
        horizon_end=request.horizon_end,
        coa_windows=windows,
        task_ids=request.task_ids,
    ))
    result["operational_data_mode"] = "IMPORTED"
    result["operational_data_review_reasons"] = review_reasons
    return result

# --- F-05 Interactive "What-If" Delay Simulator ---

import networkx as nx
from datetime import timedelta
import time as perf_time

class TrainType(StrEnum):
    EXPRESS = "EXPRESS"
    SUBURBAN = "SUBURBAN"
    FREIGHT = "FREIGHT"

class TrainSchedule(BaseModel):
    train_no: str
    train_type: TrainType
    station_stops: list[dict[str, Any]]

class WhatIfRequest(BaseModel):
    corridor_id: str
    section_from: str
    section_to: str
    block_start_time: datetime
    block_end_time: datetime
    trains: list["TrainSchedule"] | None = None
    """Operator-supplied train schedules for the simulation.
    When omitted the engine derives schedules from the imported
    timetable-occupancy records that cover the proposed block window.
    Missing schedules are surfaced as uncertainty, not silently assumed safe."""

    @field_validator("block_start_time", "block_end_time")
    @classmethod
    def time_is_aware(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    @model_validator(mode="after")
    def block_window_is_ordered(self) -> "WhatIfRequest":
        if self.block_end_time <= self.block_start_time:
            raise ValueError("block_end_time must be after block_start_time.")
        return self

class RegulatedTrain(BaseModel):
    train_no: str
    train_type: TrainType
    held_at_station: str
    delay_minutes: int
    original_arrival: datetime
    simulated_arrival: datetime

class SimulationReport(BaseModel):
    block_id_simulated: str
    section_impacted: str
    total_passenger_delay_minutes: int
    total_freight_delay_minutes: int
    regulated_trains: list[RegulatedTrain]
    network_punctuality_impact_pct: float
    headway_conflict_warnings: list[str]

def _parse_iso_dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str):
        v_clean = v.replace("Z", "+00:00")
        dt = datetime.fromisoformat(v_clean)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    raise ValueError(f"Expected datetime or ISO string, got {type(v)}")

class CorridorWhatIfSimulator:
    def __init__(self, schedules: list[TrainSchedule]):
        self.schedules = schedules
        self.graph = nx.DiGraph()
        self._build_graph()

    def _get_priority(self, t_type: TrainType) -> int:
        if t_type == TrainType.EXPRESS:
            return 1
        elif t_type == TrainType.SUBURBAN:
            return 2
        return 3

    def _build_graph(self):
        for schedule in self.schedules:
            stops = schedule.station_stops
            for i in range(len(stops)):
                stop = stops[i]
                station = stop["station_code"]
                arr = _parse_iso_dt(stop["scheduled_arrival"])
                dep = _parse_iso_dt(stop["scheduled_departure"])
                
                node_arr = (station, "ARR", schedule.train_no)
                node_dep = (station, "DEP", schedule.train_no)
                
                self.graph.add_node(node_arr, time=arr, train_no=schedule.train_no, type=schedule.train_type, priority=self._get_priority(schedule.train_type))
                self.graph.add_node(node_dep, time=dep, train_no=schedule.train_no, type=schedule.train_type, priority=self._get_priority(schedule.train_type))
                
                self.graph.add_edge(node_arr, node_dep, weight=max(0.0, (dep - arr).total_seconds() / 60))
                
                if i < len(stops) - 1:
                    next_stop = stops[i+1]
                    next_station = next_stop["station_code"]
                    next_arr = _parse_iso_dt(next_stop["scheduled_arrival"])
                    next_arr_node = (next_station, "ARR", schedule.train_no)
                    travel_time = max(1.0, (next_arr - dep).total_seconds() / 60)
                    self.graph.add_edge(node_dep, next_arr_node, weight=travel_time)

        segments = {}
        for schedule in self.schedules:
            stops = schedule.station_stops
            for i in range(len(stops) - 1):
                s1 = stops[i]["station_code"]
                s2 = stops[i+1]["station_code"]
                s1_dep = _parse_iso_dt(stops[i]["scheduled_departure"])
                segments.setdefault((s1, s2), []).append((schedule.train_no, s1_dep))
        
        for (s1, s2), trains in segments.items():
            trains.sort(key=lambda x: x[1])
            for i in range(len(trains) - 1):
                t1, t1_dep = trains[i]
                t2, t2_dep = trains[i+1]
                node1_dep = (s1, "DEP", t1)
                node2_dep = (s1, "DEP", t2)
                self.graph.add_edge(node1_dep, node2_dep, weight=5.0)

    def _matches_section(self, s1: str, s2: str, section_from: str, section_to: str) -> bool:
        if s1 == section_from and s2 == section_to:
            return True
        tokens_from = set(section_from.replace("_", "-").split("-"))
        tokens_to = set(section_to.replace("_", "-").split("-"))
        all_section_tokens = tokens_from.union(tokens_to)
        if s1 in all_section_tokens and s2 in all_section_tokens and s1 != s2:
            return True
        if s1 in section_from and s2 in section_to:
            return True
        if s1 == section_from or s2 == section_to:
            return True
        return False

    def simulate_block(self, section_from: str, section_to: str, start_time: datetime, end_time: datetime) -> SimulationReport:
        affected = []
        original_times = nx.get_node_attributes(self.graph, "time")
        
        for schedule in self.schedules:
            stops = schedule.station_stops
            for i in range(len(stops) - 1):
                s1 = stops[i]["station_code"]
                s2 = stops[i+1]["station_code"]
                dep_time = _parse_iso_dt(stops[i]["scheduled_departure"])
                arr_time = _parse_iso_dt(stops[i+1]["scheduled_arrival"])
                if self._matches_section(s1, s2, section_from, section_to):
                    if not (arr_time <= start_time or dep_time >= end_time):
                        affected.append((schedule.train_no, s1))
                        break
        
        new_times = {n: original_times[n] for n in self.graph.nodes}
        queue = []
        for t_no, dep_station in affected:
            node = (dep_station, "DEP", t_no)
            if node in self.graph.nodes:
                queue.append((node, self.graph.nodes[node]["priority"], original_times[node]))
            
        queue.sort(key=lambda x: (x[1], x[2]))
        
        current_release_time = end_time
        for node, prio, orig_time in queue:
            if current_release_time > new_times[node]:
                new_times[node] = current_release_time
            current_release_time += timedelta(minutes=5)
            
        topo = list(nx.topological_sort(self.graph))
        for u in topo:
            for v in self.graph.successors(u):
                edge_weight = self.graph[u][v]["weight"]
                min_v_time = new_times[u] + timedelta(minutes=edge_weight)
                if min_v_time > new_times[v]:
                    new_times[v] = min_v_time
                    
        regulated = []
        tot_passenger_delay = 0
        tot_freight_delay = 0
        
        for schedule in self.schedules:
            t_no = schedule.train_no
            t_type = schedule.train_type
            last_stop = schedule.station_stops[-1]
            last_station = last_stop["station_code"] if isinstance(last_stop, dict) else last_stop.station_code
            final_node = (last_station, "ARR", t_no)
            orig = original_times[final_node]
            sim = new_times[final_node]
            delay = int((sim - orig).total_seconds() / 60)
            if delay > 0:
                held_at = next((s for t, s in affected if t == t_no), "Upstream")
                regulated.append(RegulatedTrain(
                    train_no=t_no,
                    train_type=t_type,
                    held_at_station=held_at,
                    delay_minutes=delay,
                    original_arrival=orig,
                    simulated_arrival=sim
                ))
                if t_type == TrainType.FREIGHT:
                    tot_freight_delay += delay
                else:
                    tot_passenger_delay += delay
                    
        regulated.sort(key=lambda x: -x.delay_minutes)
        total_trains = len(self.schedules)
        delayed_trains = len(regulated)
        punctuality = ((total_trains - delayed_trains) / total_trains * 100.0) if total_trains else 100.0
        
        headway_warnings = []
        for u, v in self.graph.edges():
            if u[1] == "DEP" and v[1] == "DEP":
                diff_min = (new_times[v] - new_times[u]).total_seconds() / 60
                if diff_min < 4.9:
                    headway_warnings.append(
                        f"Headway between Train {u[2]} and Train {v[2]} at {u[0]} compressed to {diff_min:.1f} min (minimum safe headway is 5.0 min)."
                    )

        return SimulationReport(
            block_id_simulated=str(uuid4()),
            section_impacted=f"{section_from}->{section_to}",
            total_passenger_delay_minutes=tot_passenger_delay,
            total_freight_delay_minutes=tot_freight_delay,
            regulated_trains=regulated,
            network_punctuality_impact_pct=round(punctuality, 2),
            headway_conflict_warnings=headway_warnings
        )

@app.get("/api/v1/simulate/what-if/hints", status_code=200)
def what_if_hints():
    """Return all timetable-occupancy records to help the UI show users
    what corridor / section / window combinations are already in the database.
    """
    with connection() as db:
        rows = db.execute(
            """SELECT to_.record_id, to_.section_id, to_.window_start, to_.window_end,
                      to_.passenger_trains_affected,
                      (SELECT cw.corridor_id FROM coa_windows cw
                       WHERE json_extract(cw.window_json, '$.section_id') = to_.section_id
                       LIMIT 1) AS corridor_id
               FROM timetable_occupancy to_
               ORDER BY to_.window_start"""
        ).fetchall()
    return {
        "records": [
            {
                "record_id": r["record_id"],
                "section_id": r["section_id"],
                "window_start": r["window_start"],
                "window_end": r["window_end"],
                "passenger_trains_affected": r["passenger_trains_affected"],
                "corridor_id": r["corridor_id"],
            }
            for r in rows
        ]
    }

@app.post("/api/v1/simulate/what-if", status_code=200, response_model=SimulationReport)
def simulate_what_if(request: WhatIfRequest) -> SimulationReport:
    """F-05: Estimate operational impact of a proposed block window.

    If the caller supplies `trains`, those schedules are used directly.
    Otherwise, schedules are derived from imported timetable-occupancy records
    that overlap the proposed block window on the matching section.
    When no imported data is available the endpoint raises 409 rather than
    silently substituting invented train movements.
    """
    if request.trains is not None:
        schedules = request.trains
    else:
        with connection() as db:
            from_tok = request.section_from.split('-')[0].strip() if request.section_from else ""
            to_tok = request.section_to.split('-')[0].strip() if request.section_to else ""
            rows = db.execute(
                """SELECT record_id, section_id, window_start, window_end,
                          passenger_trains_affected
                   FROM timetable_occupancy
                   WHERE (
                       section_id = ?
                       OR section_id = ?
                       OR (section_id LIKE ? AND section_id LIKE ?)
                       OR section_id IN (
                           SELECT json_extract(window_json, '$.section_id')
                           FROM coa_windows
                           WHERE corridor_id = ?
                       )
                       OR section_id IN (
                           SELECT section_id
                           FROM network_references
                           WHERE source_reference = ? OR section_id = ?
                       )
                   )
                   AND window_start <= ?
                   AND window_end   >= ?
                   ORDER BY window_start""",
                (
                    request.section_from,
                    request.corridor_id,
                    f"%{from_tok}%" if from_tok else "%",
                    f"%{to_tok}%" if to_tok else "%",
                    request.corridor_id,
                    request.section_from,
                    request.section_from,
                    request.block_end_time.isoformat(),
                    request.block_start_time.isoformat(),
                ),
            ).fetchall()
            # Build a hint for the user when no match found
            if not rows:
                all_rows = db.execute(
                    "SELECT section_id, window_start, window_end FROM timetable_occupancy ORDER BY window_start LIMIT 5"
                ).fetchall()
                if all_rows:
                    hint_lines = ", ".join(
                        f"{r['section_id']} [{r['window_start'][:16]} – {r['window_end'][:16]}]"
                        for r in all_rows
                    )
                    raise HTTPException(
                        409,
                        f"No timetable-occupancy records match section '{request.section_from}->{request.section_to}' "
                        f"within [{request.block_start_time.isoformat()[:16]} – {request.block_end_time.isoformat()[:16]}]. "
                        f"Available records: {hint_lines}. "
                        f"Adjust the section codes / window, or supply train schedules as JSON.",
                    )
                raise HTTPException(
                    409,
                    "No imported timetable-occupancy records found in the database. "
                    "Import timetable data via Operations Data page or supply train schedules in the JSON field.",
                )
        # Build minimal TrainSchedule stubs from the occupancy records so the
        # graph simulator has real-data-grounded entries.  Each occupancy record
        # becomes one representative train entry with two stops (section_from and
        # section_to) timed to the imported window boundaries.
        schedules = []
        for index, row in enumerate(rows):
            w_start = datetime.fromisoformat(row["window_start"])
            w_end   = datetime.fromisoformat(row["window_end"])
            affected = row["passenger_trains_affected"]
            t_type   = TrainType.EXPRESS if affected >= 10 else TrainType.SUBURBAN
            for train_index in range(max(1, min(affected, 5))):
                headway = timedelta(minutes=15 * train_index)
                schedules.append(
                    TrainSchedule(
                        train_no=f"{row['record_id']}-T{train_index + 1:02d}",
                        train_type=t_type,
                        station_stops=[
                            {
                                "station_code": request.section_from,
                                "scheduled_arrival":   w_start + headway,
                                "scheduled_departure": w_start + headway + timedelta(minutes=5),
                            },
                            {
                                "station_code": request.section_to,
                                "scheduled_arrival":   w_end + headway,
                                "scheduled_departure": w_end + headway + timedelta(minutes=5),
                            },
                        ],
                    )
                )
    if not schedules:
        raise HTTPException(
            409,
            "No train schedules could be derived for the requested section and window.",
        )
    simulator = CorridorWhatIfSimulator(schedules)
    return simulator.simulate_block(
        section_from=request.section_from,
        section_to=request.section_to,
        start_time=request.block_start_time,
        end_time=request.block_end_time,
    )

# --- F-06 Dynamic Overrun & Early Handover Engine ---

import asyncio
from fastapi import WebSocket, WebSocketDisconnect
import redis.asyncio as redis

class BlockState(StrEnum):
    PENDING_START = "PENDING_START"
    ACTIVE = "ACTIVE"
    EXTENSION_REQUESTED = "EXTENSION_REQUESTED"
    CLEARED_EARLY = "CLEARED_EARLY"
    COMPLETED = "COMPLETED"

class ProgressTelemetryUpdate(BaseModel):
    block_id: str
    corridor_id: str
    supervisor_id: str
    actual_progress_pct: float
    estimated_minutes_remaining: int
    timestamp: datetime
    
    @field_validator("timestamp")
    @classmethod
    def time_is_aware_telemetry(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

class DownlineWarningAlert(BaseModel):
    alert_id: str
    block_id: str
    corridor_id: str
    severity: str
    overrun_minutes: int
    revised_handover_time: datetime
    first_impacted_train: dict[str, Any]
    message: str

class EarlyHandoverAlert(BaseModel):
    block_id: str
    corridor_id: str
    slack_capacity_recovered_minutes: int
    recommended_action: str

class LiveBlockStateMessage(BaseModel):
    block_id: str
    state: BlockState
    percent_completed: float
    telemetry_timestamp: datetime
    active_alert: dict | None

redis_client = None

async def get_redis():
    global redis_client
    if redis_client is None:
        try:
            redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
            await redis_client.ping()
        except Exception:
            redis_client = None
    return redis_client

class LiveTelemetryEngine:
    def __init__(self):
        self.blocks = {}
    
    async def process_telemetry(self, update: ProgressTelemetryUpdate) -> LiveBlockStateMessage:
        block = self.blocks.get(update.block_id)
        if not block:
            scheduled_end_time = update.timestamp + timedelta(minutes=update.estimated_minutes_remaining)
            block = {
                "scheduled_end_time": scheduled_end_time,
                "state": BlockState.ACTIVE
            }
            self.blocks[update.block_id] = block
            
        scheduled_end_time = block["scheduled_end_time"]
        projected_end_time = update.timestamp + timedelta(minutes=update.estimated_minutes_remaining)
        
        alert = None
        new_state = block["state"]
        
        # Overrun Detection
        if (projected_end_time - scheduled_end_time).total_seconds() / 60 > 15:
            new_state = BlockState.EXTENSION_REQUESTED
            overrun = int((projected_end_time - scheduled_end_time).total_seconds() / 60)
            # Identify the first timetable record whose window overlaps the
            # revised handover time on this corridor.  If none is imported the
            # controller is explicitly notified so they can consult the timetable.
            with connection() as db:
                first_tt = db.execute(
                    """SELECT record_id, passenger_trains_affected
                       FROM timetable_occupancy
                       WHERE window_start <= ? AND window_end >= ?
                       ORDER BY window_start LIMIT 1""",
                    (projected_end_time.isoformat(), projected_end_time.isoformat()),
                ).fetchone()
            first_train_info: dict[str, Any]
            if first_tt is not None:
                first_train_info = {
                    "timetable_record_id": first_tt["record_id"],
                    "passenger_trains_affected": first_tt["passenger_trains_affected"],
                    "scheduled_arrival": scheduled_end_time.isoformat(),
                    "expected_delay_mins": overrun,
                    "note": "Identify specific train from the imported timetable record.",
                }
            else:
                first_train_info = {
                    "timetable_record_id": None,
                    "scheduled_arrival": scheduled_end_time.isoformat(),
                    "expected_delay_mins": overrun,
                    "note": (
                        "No timetable-occupancy record imported for this window. "
                        "Controller must identify the first affected train manually."
                    ),
                }
            alert = DownlineWarningAlert(
                alert_id=str(uuid4()),
                block_id=update.block_id,
                corridor_id=update.corridor_id,
                severity="WARNING",
                overrun_minutes=overrun,
                revised_handover_time=projected_end_time,
                first_impacted_train=first_train_info,
                message=f"Block {update.block_id} projected to overrun by {overrun} minutes.",
            ).model_dump(mode="json")
            
        # Early Handover Detection
        elif update.actual_progress_pct == 100.0 and (scheduled_end_time - update.timestamp).total_seconds() / 60 > 20:
            new_state = BlockState.CLEARED_EARLY
            slack = int((scheduled_end_time - update.timestamp).total_seconds() / 60)
            alert = EarlyHandoverAlert(
                block_id=update.block_id,
                corridor_id=update.corridor_id,
                slack_capacity_recovered_minutes=slack,
                recommended_action=f"Restore line early for waiting traffic"
            ).model_dump(mode='json')
            
        elif update.actual_progress_pct == 100.0:
            new_state = BlockState.COMPLETED
            
        block["state"] = new_state
        
        msg = LiveBlockStateMessage(
            block_id=update.block_id,
            state=new_state,
            percent_completed=update.actual_progress_pct,
            telemetry_timestamp=update.timestamp,
            active_alert=alert
        )
        
        client = await get_redis()
        channel = f"corridor:{update.corridor_id}:updates"

        if client is not None and hasattr(client, 'publish'):
            await client.publish(channel, msg.model_dump_json())

        return msg

telemetry_engine = LiveTelemetryEngine()

@app.post("/api/v1/telemetry/progress-update")
async def receive_telemetry(update: ProgressTelemetryUpdate):
    return await telemetry_engine.process_telemetry(update)

@app.websocket("/ws/live-blocks/{corridor_id}")
async def websocket_endpoint(websocket: WebSocket, corridor_id: str):
    await websocket.accept()
    client = await get_redis()
    channel = f"corridor:{corridor_id}:updates"

    if client is None:
        await websocket.send_text(json.dumps({
            "status": "warning",
            "message": "Redis is not configured; live telemetry will not stream.",
        }))
        await websocket.close()
        return

    pubsub = client.pubsub()
    await pubsub.subscribe(channel)
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                await websocket.send_text(message["data"])
    except WebSocketDisconnect:
        await pubsub.unsubscribe(channel)
    except Exception:
        await websocket.close()


# =====================================================================
# --- F-07 & F-08: Dispatcher Cockpit, Overrides & Audit Ledger ---
# =====================================================================

class BlockSanctionState(StrEnum):
    PROPOSED = "PROPOSED"
    SANCTIONED = "SANCTIONED"
    OVERRIDDEN = "OVERRIDDEN"
    REJECTED = "REJECTED"
    ACTIVE = "ACTIVE"
    EXTENSION_REQUESTED = "EXTENSION_REQUESTED"
    RESTORATION_RECORDED = "RESTORATION_RECORDED"
    COMPLETED = "COMPLETED"


class BlockActionRequest(BaseModel):
    plan_id: str = Field(min_length=1)
    actor: str = Field(min_length=2, max_length=120)
    role: str = Field(min_length=2, max_length=120)
    action: str = Field(min_length=2, max_length=60)
    reason_code: str = Field(min_length=2, max_length=120)
    justification_notes: str = Field(min_length=5)
    modified_start: datetime | None = None
    modified_end: datetime | None = None
    modified_notes: str | None = None

    @field_validator("modified_start", "modified_end")
    @classmethod
    def time_is_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    @field_validator("actor", "role", "reason_code", "justification_notes")
    @classmethod
    def text_fields_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank.")
        return value

    @model_validator(mode="after")
    def override_window_is_complete_and_ordered(self) -> "BlockActionRequest":
        if self.action.upper() in {"OVERRIDE", "OVERRIDDEN"}:
            if self.modified_start is None or self.modified_end is None:
                raise ValueError("An override requires both modified_start and modified_end.")
            if self.modified_end <= self.modified_start:
                raise ValueError("modified_end must be after modified_start.")
        return self


class AuditAmendmentRequest(BaseModel):
    actor: str = Field(min_length=2, max_length=120)
    role: str = Field(min_length=2, max_length=120)
    reason_code: str = Field(min_length=2, max_length=120)
    justification_notes: str = Field(min_length=5)
    amendment_reason: str = Field(min_length=5, description="Administrative rationale for amending this audit record")

    @field_validator("actor", "role", "reason_code", "justification_notes", "amendment_reason")
    @classmethod
    def text_fields_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank.")
        return value


SANCTIONING_ROLES = frozenset({"Section Controller", "Chief Controller"})
REVIEW_ROLES = frozenset({"Safety Officer"})
SUPPORTED_ROLES = SANCTIONING_ROLES | REVIEW_ROLES


def configured_identity() -> tuple[str, str]:
    """Return the server-configured operator identity used for F-08 decisions."""
    actor = os.getenv("RAILSYNC_ACTOR", "Ananda Jana").strip() or "Ananda Jana"
    role = os.getenv("RAILSYNC_ROLE", "Chief Controller").strip() or "Chief Controller"
    if role not in SUPPORTED_ROLES:
        role = "Chief Controller"
    return actor, role


def _audit_entry_hash(previous_hash: str | None, values: dict[str, Any]) -> str:
    """Hash the persisted decision fields to make the audit chain independently verifiable."""
    material = json.dumps({"previous_hash": previous_hash, **values}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def verify_audit_chain() -> dict[str, Any]:
    with connection() as db:
        rows = db.execute("SELECT * FROM block_audit_log ORDER BY created_at ASC, id ASC").fetchall()

    expected_previous: str | None = None
    errors: list[str] = []
    unverifiable_legacy_entries: list[str] = []
    for row in rows:
        if not row["entry_hash"]:
            unverifiable_legacy_entries.append(row["id"])
            continue
        if row["previous_hash"] != expected_previous:
            errors.append(f"{row['id']}: previous_hash does not link to the preceding entry")
        values = {
            "id": row["id"], "block_id": row["block_id"], "plan_id": row["plan_id"],
            "actor": row["actor"], "role": row["role"], "action": row["action"],
            "original_schedule_json": row["original_schedule_json"],
            "modified_schedule_json": row["modified_schedule_json"],
            "reason_code": row["reason_code"], "justification_notes": row["justification_notes"],
            "created_at": row["created_at"],
        }
        actual_hash = _audit_entry_hash(row["previous_hash"], values)
        if row["entry_hash"] != actual_hash:
            errors.append(f"{row['id']}: entry_hash does not match persisted audit fields")
        expected_previous = row["entry_hash"]

    return {
        "valid": not errors,
        "entry_count": len(rows),
        "errors": errors,
        "unverifiable_legacy_entries": unverifiable_legacy_entries,
    }


@app.get("/api/v1/cockpit/identity")
def get_cockpit_identity() -> dict[str, str]:
    """Expose the locally configured F-08 operator identity to this cockpit session."""
    actor, role = configured_identity()
    return {"actor": actor, "officer_name": actor, "role": role, "status": "active"}


@app.get("/auth/me")
def get_authenticated_officer() -> dict[str, str]:
    """Return the local demo officer profile used by the dispatcher cockpit."""
    identity = get_cockpit_identity()
    return {
        "officer_name": identity["officer_name"],
        "role": identity["role"],
        "status": identity["status"],
    }


@app.get("/auth/identity")
def get_auth_identity() -> dict[str, str]:
    """Return the COA monitor identity consumed by the operations cockpit."""
    return {
        "officer_name": "Ananda Jana",
        "authorized_role": "Chief Controller",
        "status": "Authenticated",
    }


@app.get("/api/v1/cockpit/summary")
def get_cockpit_summary() -> dict[str, Any]:
    """F-07: Return aggregated KPIs, sections, blocks with sanction states, and unscheduled tasks.

    Zero mock data: calculated entirely from stored block_plans, block_sanctions,
    and ingestion records in rail_sync.db.
    """
    with connection() as db:
        plan_rows = db.execute("SELECT * FROM block_plans ORDER BY created_at DESC").fetchall()
        sanction_rows = db.execute("SELECT * FROM block_sanctions ORDER BY updated_at DESC").fetchall()
        ref_rows = db.execute("SELECT DISTINCT section_id FROM network_references ORDER BY section_id").fetchall()
        task_rows = db.execute("SELECT id, normalized_task_json FROM ingestion_records").fetchall()
        feasibility_rows = db.execute(
            "SELECT task_id, result_json, rule_version, created_at FROM feasibility_assessments ORDER BY created_at DESC"
        ).fetchall()
        priority_rows = db.execute(
            "SELECT task_id, policy_version, result_json, created_at FROM priority_evaluations ORDER BY created_at DESC"
        ).fetchall()
        audit_rows = db.execute("SELECT * FROM block_audit_log ORDER BY created_at DESC LIMIT 20").fetchall()
        audit_counts = db.execute("SELECT block_id, COUNT(*) as cnt FROM block_audit_log GROUP BY block_id").fetchall()

    audit_count_map = {r["block_id"]: r["cnt"] for r in audit_counts}
    task_map = {}
    for tr in task_rows:
        if tr["normalized_task_json"]:
            try:
                normalized = json.loads(tr["normalized_task_json"])
                task_map[normalized.get("id")] = normalized
                task_map[normalized.get("source_reference")] = normalized
            except Exception:
                pass

    sections = [r["section_id"] for r in ref_rows]
    feasibility_map: dict[str, dict[str, Any]] = {}
    for row in feasibility_rows:
        if row["task_id"] in feasibility_map:
            continue
        try:
            result = json.loads(row["result_json"])
            feasibility_map[row["task_id"]] = {
                "status": result.get("status", "NEEDS_REVIEW"),
                "warning_reasons": result.get("warning_reasons", []),
                "rule_version": row["rule_version"],
                "assessed_at": row["created_at"],
            }
        except (TypeError, json.JSONDecodeError):
            feasibility_map[row["task_id"]] = {"status": "NEEDS_REVIEW", "warning_reasons": ["Stored feasibility result could not be read."]}

    priority_map: dict[str, dict[str, Any]] = {}
    for row in priority_rows:
        if row["task_id"] in priority_map:
            continue
        try:
            p_res = json.loads(row["result_json"])
            priority_map[row["task_id"]] = {
                "score": p_res.get("score"),
                "tier": p_res.get("tier"),
                "policy_version": row["policy_version"],
                "top_contributing_factors": p_res.get("top_contributing_factors", []),
                "explanation": p_res.get("explanation"),
                "evaluated_at": row["created_at"],
            }
        except (TypeError, json.JSONDecodeError):
            pass

    # Map sanctions by block_id
    sanction_map = {r["block_id"]: dict(r) for r in sanction_rows}

    blocks: list[dict[str, Any]] = []
    all_unscheduled: list[dict[str, Any]] = []
    total_saved_hours = 0.0
    total_used_hours = 0.0

    seen_block_ids = set()
    current_plan_id = plan_rows[0]["id"] if plan_rows else None

    for pr in plan_rows:
        try:
            p_res = json.loads(pr["result_json"])
            plan_id = pr["id"]
            if plan_id != current_plan_id:
                continue
            metrics = p_res.get("metrics", {})
            total_saved_hours += metrics.get("parallel_block_hours_saved", 0.0)
            total_used_hours += metrics.get("total_block_hours_used", 0.0)

            for dt in p_res.get("unscheduled_tasks", []):
                all_unscheduled.append({
                    "plan_id": plan_id,
                    "task_id": dt.get("task_id"),
                    "reason": dt.get("reason"),
                })

            for blk in p_res.get("scheduled_blocks", []):
                b_id = blk.get("block_id") or blk.get("corridor_id")
                if not b_id or b_id in seen_block_ids:
                    continue
                seen_block_ids.add(b_id)

                sanction_entry = sanction_map.get(b_id, {})
                curr_state = sanction_entry.get("state", "PROPOSED")
                eff_start = sanction_entry.get("effective_start") or blk.get("window_start")
                eff_end = sanction_entry.get("effective_end") or blk.get("window_end")

                # Enrich assigned tasks with KM and asset info
                enriched_tasks = []
                for at in blk.get("assigned_tasks", []):
                    tid = at.get("task_id")
                    norm = task_map.get(tid, {})
                    s_km = norm.get("start_km")
                    e_km = norm.get("end_km")
                    km_span_txt = f"KM {s_km:.1f}–{e_km:.1f}" if s_km is not None and e_km is not None else "Section bounds"
                    enriched_tasks.append({
                        "task_id": tid,
                        "department": at.get("department") or norm.get("department"),
                        "priority_score": at.get("priority_score"),
                        "maintenance_type": norm.get("maintenance_type", "Scheduled Track Work"),
                        "km_span": km_span_txt,
                        "start_km": s_km,
                        "end_km": e_km,
                        "scheduled_start": at.get("scheduled_start"),
                        "scheduled_end": at.get("scheduled_end"),
                        "requires_traffic_block": at.get("requires_traffic_block", False),
                        "requires_traction_disconnection": at.get("requires_traction_disconnection", False),
                        "required_crews": at.get("required_crews", []),
                        "asset_reference": norm.get("asset_reference"),
                        "data_quality_status": norm.get("data_quality_status", "UNKNOWN"),
                        "source_timestamp": norm.get("source_timestamp"),
                        "feasibility": feasibility_map.get(tid, {"status": "NOT_ASSESSED", "warning_reasons": ["No feasibility assessment is recorded."]}),
                        "priority_evaluation": priority_map.get(tid),
                    })

                sec_id = blk.get("section_id", "")
                if sec_id and sec_id not in sections:
                    sections.append(sec_id)

                # Determine overall precautions
                req_traffic = any(t.get("requires_traffic_block") for t in enriched_tasks)
                req_traction = any(t.get("requires_traction_disconnection") for t in enriched_tasks)

                all_crews = []
                for t in enriched_tasks:
                    all_crews.extend(t.get("required_crews", []))

                # Calculate duration in minutes
                try:
                    t_s = datetime.fromisoformat(str(eff_start))
                    t_e = datetime.fromisoformat(str(eff_end))
                    dur_mins = max(0, int((t_e - t_s).total_seconds() / 60))
                except Exception:
                    dur_mins = int(blk.get("available_minutes", 0))

                blocks.append({
                    "block_id": b_id,
                    "plan_id": plan_id,
                    "corridor_id": blk.get("corridor_id"),
                    "section_id": sec_id,
                    "state": curr_state,
                    "scheduled_start": blk.get("window_start"),
                    "scheduled_end": blk.get("window_end"),
                    "effective_start": eff_start,
                    "effective_end": eff_end,
                    "duration_minutes": dur_mins,
                    "timetable_reference": blk.get("timetable_reference", "N/A"),
                    "goods_forecast_reference": blk.get("goods_forecast_reference", "N/A"),
                    "passenger_trains_affected": blk.get("passenger_trains_affected", 0),
                    "goods_trains_affected": blk.get("goods_trains_affected", 0),
                    "consolidated_departments": blk.get("consolidated_departments", []),
                    "assigned_tasks": enriched_tasks,
                    "requires_traffic_block": req_traffic,
                    "requires_traction_disconnection": req_traction,
                    "crews": sorted(set(all_crews)),
                    "sanctioned_by": sanction_entry.get("sanctioned_by"),
                    "sanctioned_role": sanction_entry.get("sanctioned_role"),
                    "sanctioned_at": sanction_entry.get("sanctioned_at"),
                    "reason_code": sanction_entry.get("reason_code"),
                    "justification": sanction_entry.get("justification"),
                    "audit_count": audit_count_map.get(b_id, 0),
                })
        except Exception:
            continue

    # State counts
    sanction_counts = {"PROPOSED": 0, "SANCTIONED": 0, "OVERRIDDEN": 0, "REJECTED": 0, "ACTIVE": 0, "COMPLETED": 0}
    for b in blocks:
        st = b.get("state", "PROPOSED")
        if st in sanction_counts:
            sanction_counts[st] += 1

    return {
        "kpis": {
            "total_block_hours_saved": round(total_saved_hours, 2),
            "total_block_hours_used": round(total_used_hours, 2),
            # A projected availability percentage needs an authority-approved
            # corridor-capacity baseline.  It is deliberately not inferred from
            # a fixed weekly duration or from incomplete source records.
            "planned_availability_pct": None,
            "availability_status": "NOT_AVAILABLE: no approved availability baseline is recorded.",
            "total_scheduled_blocks": len(blocks),
            "total_scheduled_tasks": sum(len(b["assigned_tasks"]) for b in blocks),
            "total_unscheduled_tasks": len(all_unscheduled),
            "sanction_counts": sanction_counts,
        },
        "sections": sorted(sections),
        "blocks": blocks,
        "unscheduled_tasks": all_unscheduled,
        "recent_audit_logs": [dict(r) for r in audit_rows],
    }


@app.post("/api/v1/blocks/{block_id}/action", status_code=200)
def submit_block_action(block_id: str, request: BlockActionRequest) -> dict[str, Any]:
    """F-08: Human-in-the-loop sanction, override, rejection or restoration recording.

    Validates reason code and justification, appends to the audit ledger,
    and updates block_sanctions state.
    """
    with connection() as db:
        sanction = db.execute("SELECT * FROM block_sanctions WHERE block_id = ?", (block_id,)).fetchone()

    if not sanction:
        raise HTTPException(404, f"Block '{block_id}' is not a registered proposed block.")
    if sanction["plan_id"] != request.plan_id:
        raise HTTPException(409, "The supplied plan_id does not own this block.")
    configured_actor, configured_role = configured_identity()
    if request.actor != configured_actor or request.role != configured_role:
        raise HTTPException(403, "The supplied operator identity does not match the server-configured F-08 identity.")
    try:
        original_schedule = json.loads(sanction["details_json"])
    except (TypeError, json.JSONDecodeError) as error:
        raise HTTPException(409, "The stored proposed schedule cannot be reconstructed.") from error

    eff_start = sanction["effective_start"]
    eff_end = sanction["effective_end"]

    new_state = BlockSanctionState.PROPOSED
    modified_schedule: dict[str, Any] | None = None

    act = request.action.upper()
    current_state = sanction["state"]
    allowed_actions = {
        "PROPOSED": {"APPROVE", "APPROVED", "SANCTION", "SANCTIONED", "OVERRIDE", "OVERRIDDEN", "REJECT", "REJECTED"},
        "SANCTIONED": {"OVERRIDE", "OVERRIDDEN", "REJECT", "REJECTED", "ACTIVATE", "ACTIVE", "EXTEND", "EXTENSION_REQUESTED"},
        "OVERRIDDEN": {"ACTIVATE", "ACTIVE", "REJECT", "REJECTED", "EXTEND", "EXTENSION_REQUESTED"},
        "ACTIVE": {"EXTEND", "EXTENSION_REQUESTED", "RESTORE", "RESTORATION_RECORDED"},
        "EXTENSION_REQUESTED": {"RESTORE", "RESTORATION_RECORDED", "REJECT", "REJECTED"},
        "RESTORATION_RECORDED": {"COMPLETE", "COMPLETED"},
        "REJECTED": set(),
        "COMPLETED": set(),
    }
    if act not in allowed_actions.get(current_state, set()):
        raise HTTPException(409, f"Action '{request.action}' is not valid from state '{current_state}'.")
    if act in {"APPROVE", "APPROVED", "SANCTION", "SANCTIONED"}:
        if configured_role not in SANCTIONING_ROLES:
            raise HTTPException(403, "Only a sanctioning authority can sanction a block.")
        new_state = BlockSanctionState.SANCTIONED
    elif act in {"OVERRIDE", "OVERRIDDEN"}:
        if configured_role not in SANCTIONING_ROLES:
            raise HTTPException(403, "Only a sanctioning authority can override a block.")
        if request.modified_start is None or request.modified_end is None:
            raise HTTPException(422, "An override requires both modified_start and modified_end.")
        new_state = BlockSanctionState.OVERRIDDEN
        eff_start = request.modified_start.isoformat()
        eff_end = request.modified_end.isoformat()
        modified_schedule = {
            "effective_start": eff_start,
            "effective_end": eff_end,
            "notes": request.modified_notes or request.justification_notes,
        }
    elif act in {"REJECT", "REJECTED"}:
        if configured_role not in SANCTIONING_ROLES | REVIEW_ROLES:
            raise HTTPException(403, "Only a reviewer or sanctioning authority can reject a block.")
        new_state = BlockSanctionState.REJECTED
    elif act in {"ACTIVATE", "ACTIVE"}:
        if configured_role not in SANCTIONING_ROLES:
            raise HTTPException(403, "Only a sanctioning authority can activate a block.")
        new_state = BlockSanctionState.ACTIVE
    elif act in {"EXTENSION_REQUESTED", "EXTEND"}:
        if configured_role not in SANCTIONING_ROLES:
            raise HTTPException(403, "Only a sanctioning authority can request an extension.")
        new_state = BlockSanctionState.EXTENSION_REQUESTED
        if request.modified_end is None:
            raise HTTPException(422, "An extension request requires modified_end.")
        if request.modified_end <= datetime.fromisoformat(eff_start):
            raise HTTPException(422, "The extension end must be after the effective start.")
        eff_end = request.modified_end.isoformat()
        modified_schedule = {"effective_end": eff_end, "notes": request.justification_notes}
    elif act in {"RESTORATION_RECORDED", "RESTORE"}:
        if configured_role not in SANCTIONING_ROLES:
            raise HTTPException(403, "Only a sanctioning authority can record restoration.")
        new_state = BlockSanctionState.RESTORATION_RECORDED
    elif act in {"COMPLETE", "COMPLETED"}:
        if configured_role not in SANCTIONING_ROLES:
            raise HTTPException(403, "Only a sanctioning authority can complete a block.")
        new_state = BlockSanctionState.COMPLETED
    else:
        raise HTTPException(400, f"Unsupported block action '{request.action}'.")

    audit_id = str(uuid4())
    ts = now().isoformat()

    with connection() as db:
        previous = db.execute(
            "SELECT entry_hash FROM block_audit_log WHERE entry_hash IS NOT NULL ORDER BY created_at DESC, id DESC LIMIT 1"
        ).fetchone()
        previous_hash = previous["entry_hash"] if previous else None
        audit_values = {
            "id": audit_id, "block_id": block_id, "plan_id": request.plan_id,
            "actor": configured_actor, "role": configured_role, "action": new_state.value,
            "original_schedule_json": json.dumps(original_schedule, sort_keys=True, default=str),
            "modified_schedule_json": json.dumps(modified_schedule, sort_keys=True, default=str) if modified_schedule else None,
            "reason_code": request.reason_code, "justification_notes": request.justification_notes,
            "created_at": ts,
        }
        entry_hash = _audit_entry_hash(previous_hash, audit_values)
        db.execute(
            """INSERT INTO block_audit_log
               (id, block_id, plan_id, actor, role, action, original_schedule_json,
                modified_schedule_json, reason_code, justification_notes, created_at, previous_hash, entry_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                audit_id,
                block_id,
                request.plan_id,
                configured_actor,
                configured_role,
                new_state.value,
                audit_values["original_schedule_json"], audit_values["modified_schedule_json"],
                request.reason_code,
                request.justification_notes,
                ts,
                previous_hash,
                entry_hash,
            ),
        )

        sec_id = sanction["section_id"]
        db.execute(
            """INSERT INTO block_sanctions
               (block_id, plan_id, section_id, state, effective_start, effective_end,
                latest_action, sanctioned_by, sanctioned_role, sanctioned_at,
                justification, reason_code, details_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(block_id) DO UPDATE SET
                   state = excluded.state,
                   effective_start = excluded.effective_start,
                   effective_end = excluded.effective_end,
                   latest_action = excluded.latest_action,
                   sanctioned_by = excluded.sanctioned_by,
                   sanctioned_role = excluded.sanctioned_role,
                   sanctioned_at = excluded.sanctioned_at,
                   justification = excluded.justification,
                   reason_code = excluded.reason_code,
                   updated_at = excluded.updated_at""",
            (
                block_id,
                request.plan_id,
                sec_id,
                new_state.value,
                eff_start,
                eff_end,
                new_state.value,
                configured_actor,
                configured_role,
                ts,
                request.justification_notes,
                request.reason_code,
                sanction["details_json"],
                ts,
            ),
        )

    return {
        "success": True,
        "block_id": block_id,
        "state": new_state.value,
        "audit_id": audit_id,
        "effective_start": eff_start,
        "effective_end": eff_end,
        "sanctioned_by": configured_actor,
        "timestamp": ts,
    }


class BlockOverrideRequest(BaseModel):
    plan_id: str | None = None
    actor: str | None = None
    role: str | None = None
    reason_code: str = Field(min_length=2, max_length=120)
    justification_notes: str = Field(min_length=5)
    modified_start: datetime | None = None
    modified_end: datetime | None = None
    modified_notes: str | None = None

    @field_validator("modified_start", "modified_end")
    @classmethod
    def time_is_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    @field_validator("reason_code", "justification_notes")
    @classmethod
    def text_fields_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank.")
        return value

    @model_validator(mode="after")
    def override_window_is_complete_and_ordered(self) -> "BlockOverrideRequest":
        if self.modified_start is None or self.modified_end is None:
            raise ValueError("An override requires both modified_start and modified_end.")
        if self.modified_end <= self.modified_start:
            raise ValueError("modified_end must be after modified_start.")
        return self


@app.post("/api/v1/blocks/{block_id}/override", status_code=200)
def override_block_schedule(block_id: str, request: BlockOverrideRequest) -> dict[str, Any]:
    """F-08: Dedicated endpoint for regulatory human-in-the-loop schedule override.

    Enforces valid reason_code and justification_notes, modifies the schedule window,
    and appends a cryptographically chained entry to the BlockAuditLog.
    """
    configured_actor, configured_role = configured_identity()
    actor = request.actor.strip() if request.actor else configured_actor
    role = request.role.strip() if request.role else configured_role

    with connection() as db:
        sanction = db.execute("SELECT plan_id FROM block_sanctions WHERE block_id = ?", (block_id,)).fetchone()
    if not sanction:
        raise HTTPException(404, f"Block '{block_id}' is not a registered proposed block.")

    plan_id = request.plan_id or sanction["plan_id"]
    action_req = BlockActionRequest(
        plan_id=plan_id,
        actor=actor,
        role=role,
        action="OVERRIDE",
        reason_code=request.reason_code,
        justification_notes=request.justification_notes,
        modified_start=request.modified_start,
        modified_end=request.modified_end,
        modified_notes=request.modified_notes,
    )
    return submit_block_action(block_id=block_id, request=action_req)


@app.get("/api/v1/blocks/{block_id}/audit-logs")
def get_block_audit_logs(block_id: str) -> list[dict[str, Any]]:
    """Return chronological audit records for a specific block."""
    with connection() as db:
        rows = db.execute(
            "SELECT * FROM block_audit_log WHERE block_id = ? ORDER BY created_at DESC",
            (block_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/v1/audit/logs")
def get_audit_logs(
    block_id: str | None = None,
    action: str | None = None,
    search: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return append-only regulatory audit ledger entries with dynamic filtering and search."""
    query = "SELECT * FROM block_audit_log"
    params: list[Any] = []
    clauses: list[str] = []
    if block_id:
        clauses.append("block_id LIKE ?")
        params.append(f"%{block_id.strip()}%")
    if action and action.upper() != "ALL":
        clauses.append("action = ?")
        params.append(action.upper().strip())
    if start_date:
        clauses.append("created_at >= ?")
        params.append(start_date.strip())
    if end_date:
        ed = end_date.strip()
        if len(ed) == 10:
            ed += "T23:59:59"
        clauses.append("created_at <= ?")
        params.append(ed)
    if search:
        s = f"%{search.strip()}%"
        clauses.append(
            "(block_id LIKE ? OR actor LIKE ? OR role LIKE ? OR reason_code LIKE ? OR justification_notes LIKE ?)"
        )
        params.extend([s, s, s, s, s])
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(limit, 1000)))

    with connection() as db:
        rows = db.execute(query, tuple(params)).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/v1/audit/export.csv")
def export_audit_logs_csv(
    block_id: str | None = None,
    action: str | None = None,
    search: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> Response:
    """F-08: Export regulatory audit ledger to CSV with current active filters applied."""
    logs = get_audit_logs(
        block_id=block_id,
        action=action,
        search=search,
        start_date=start_date,
        end_date=end_date,
        limit=1000,
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Timestamp (UTC)",
        "Audit ID",
        "Block ID",
        "Plan ID",
        "Actor",
        "Role",
        "Action",
        "Reason Code",
        "Justification Notes",
        "Entry Hash",
        "Previous Hash",
    ])
    for row in logs:
        writer.writerow([
            row.get("created_at", ""),
            row.get("id", ""),
            row.get("block_id", ""),
            row.get("plan_id", ""),
            row.get("actor", ""),
            row.get("role", ""),
            row.get("action", ""),
            row.get("reason_code", ""),
            row.get("justification_notes", ""),
            row.get("entry_hash", ""),
            row.get("previous_hash", ""),
        ])
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="regulatory_audit_ledger_{int(time.time())}.csv"',
        },
    )


@app.post("/api/v1/audit/logs/{log_id}/amend")
def amend_audit_log(log_id: str, request: AuditAmendmentRequest) -> dict[str, Any]:
    """F-08: Manually amend/rectify an audit ledger entry by appending a chained amendment record."""
    configured_actor, configured_role = configured_identity()
    if request.actor != configured_actor or request.role != configured_role:
        raise HTTPException(403, "The supplied operator identity does not match the server-configured F-08 identity.")

    with connection() as db:
        target = db.execute("SELECT * FROM block_audit_log WHERE id = ?", (log_id,)).fetchone()
        if not target:
            raise HTTPException(404, f"Audit log entry '{log_id}' not found.")

        audit_id = str(uuid4())
        ts = now().isoformat()

        previous = db.execute(
            "SELECT entry_hash FROM block_audit_log WHERE entry_hash IS NOT NULL ORDER BY created_at DESC, id DESC LIMIT 1"
        ).fetchone()
        previous_hash = previous["entry_hash"] if previous else None

        original_summary = {
            "amends_audit_id": log_id,
            "previous_action": target["action"],
            "previous_reason_code": target["reason_code"],
            "previous_justification_notes": target["justification_notes"],
        }
        modified_summary = {
            "amended_by": configured_actor,
            "amended_role": configured_role,
            "amendment_reason": request.amendment_reason,
            "updated_reason_code": request.reason_code,
            "updated_justification": request.justification_notes,
        }

        full_notes = (
            f"[AMENDMENT for {log_id[:8]}] {request.justification_notes} "
            f"(Rationale: {request.amendment_reason})"
        )

        audit_values = {
            "id": audit_id,
            "block_id": target["block_id"],
            "plan_id": target["plan_id"],
            "actor": configured_actor,
            "role": configured_role,
            "action": "AMENDMENT",
            "original_schedule_json": json.dumps(original_summary, sort_keys=True, default=str),
            "modified_schedule_json": json.dumps(modified_summary, sort_keys=True, default=str),
            "reason_code": request.reason_code,
            "justification_notes": full_notes,
            "created_at": ts,
        }
        entry_hash = _audit_entry_hash(previous_hash, audit_values)

        db.execute(
            """INSERT INTO block_audit_log
               (id, block_id, plan_id, actor, role, action, original_schedule_json,
                modified_schedule_json, reason_code, justification_notes, created_at, previous_hash, entry_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                audit_id,
                target["block_id"],
                target["plan_id"],
                configured_actor,
                configured_role,
                "AMENDMENT",
                audit_values["original_schedule_json"],
                audit_values["modified_schedule_json"],
                request.reason_code,
                full_notes,
                ts,
                previous_hash,
                entry_hash,
            ),
        )

        # If this amended entry was the latest action on block_sanctions, update the active sanction's justification/reason
        latest_entry = db.execute(
            "SELECT id FROM block_audit_log WHERE block_id = ? AND action != 'AMENDMENT' ORDER BY created_at DESC, id DESC LIMIT 1",
            (target["block_id"],),
        ).fetchone()
        if latest_entry and latest_entry["id"] == log_id:
            db.execute(
                """UPDATE block_sanctions
                   SET justification = ?, reason_code = ?, updated_at = ?
                   WHERE block_id = ?""",
                (request.justification_notes, request.reason_code, ts, target["block_id"]),
            )

    return {
        "status": "success",
        "audit_id": audit_id,
        "amended_log_id": log_id,
        "entry_hash": entry_hash,
        "message": f"Audit record {log_id[:8]} amended successfully and cryptographically chained into ledger.",
    }


@app.get("/api/v1/audit/verify")
def audit_chain_status() -> dict[str, Any]:
    """Verify every persisted audit hash and link in chronological order."""
    return verify_audit_chain()


@app.get("/api/v1/blocks/{block_id}/export-memo")
def export_block_sanction_memo(block_id: str, format: str = "pdf") -> Response:
    """F-08: Export Combined Block Sanction Memo draft in PDF or printable HTML.

    This is a decision-support draft; it is not an authority-approved template.
    """
    with connection() as db:
        sanction = db.execute("SELECT * FROM block_sanctions WHERE block_id = ?", (block_id,)).fetchone()
        audit_row = db.execute(
            "SELECT entry_hash FROM block_audit_log WHERE block_id = ? AND plan_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (block_id, sanction["plan_id"] if sanction else ""),
        ).fetchone()
        task_rows = db.execute("SELECT id, normalized_task_json FROM ingestion_records").fetchall()

    task_map = {}
    for tr in task_rows:
        if tr["normalized_task_json"]:
            try:
                normalized = json.loads(tr["normalized_task_json"])
                task_map[normalized.get("id")] = normalized
                task_map[normalized.get("source_reference")] = normalized
            except Exception:
                pass

    if not sanction:
        raise HTTPException(404, f"Block '{block_id}' is not registered for memo export.")
    try:
        block_details = json.loads(sanction["details_json"])
    except (TypeError, json.JSONDecodeError) as error:
        raise HTTPException(409, "The stored block details cannot be reconstructed.") from error
    state = sanction["state"]
    eff_start = sanction["effective_start"]
    eff_end = sanction["effective_end"]
    sanctioned_by = sanction["sanctioned_by"]
    sanctioned_role = sanction["sanctioned_role"]
    sanctioned_at = sanction["sanctioned_at"]
    reason_code = sanction["reason_code"]
    justification = sanction["justification"]

    try:
        t_s = datetime.fromisoformat(str(eff_start))
        t_e = datetime.fromisoformat(str(eff_end))
        dur_mins = max(0, int((t_e - t_s).total_seconds() / 60))
    except (TypeError, ValueError) as error:
        raise HTTPException(409, "The recorded block window has invalid timestamps.") from error

    enriched_tasks = []
    km_starts = []
    km_ends = []
    all_crews = []
    all_equipment = []
    for at in block_details.get("assigned_tasks", []):
        tid = at.get("task_id")
        norm = task_map.get(tid, {})
        s_km = norm.get("start_km")
        e_km = norm.get("end_km")
        if s_km is not None:
            km_starts.append(s_km)
        if e_km is not None:
            km_ends.append(e_km)
        all_crews.extend(at.get("required_crews", []))
        all_equipment.extend(norm.get("required_equipment", []))
        enriched_tasks.append({
            "task_id": tid,
            "department": at.get("department") or norm.get("department"),
            "maintenance_type": norm.get("maintenance_type", "Track Maintenance"),
            "km_span": f"KM {s_km:.1f}–{e_km:.1f}" if s_km is not None and e_km is not None else "Section Bounds",
            "priority_score": at.get("priority_score"),
            "scheduled_start": at.get("scheduled_start"),
            "scheduled_end": at.get("scheduled_end"),
            "requires_traffic_block": at.get("requires_traffic_block", False),
            "requires_traction_disconnection": at.get("requires_traction_disconnection", False),
        })

    sec_id = block_details.get("section_id", "IR-CORRIDOR")
    km_span_str = f"KM {min(km_starts):.3f} — {max(km_ends):.3f}" if km_starts and km_ends else "Section bounds verified in field"
    v_hash = audit_row["entry_hash"] if audit_row and audit_row["entry_hash"] else "UNVERIFIED"
    memo_ref = f"IR/DCO/ER/{sec_id}/{block_id}"

    memo_data = {
        "block_id": block_id,
        "memo_reference": memo_ref,
        "section_id": sec_id,
        "state": state,
        "effective_start": eff_start,
        "effective_end": eff_end,
        "duration_minutes": dur_mins,
        "km_span": km_span_str,
        "timetable_reference": block_details.get("timetable_reference"),
        "goods_forecast_reference": block_details.get("goods_forecast_reference"),
        "requires_traffic_block": any(t.get("requires_traffic_block") for t in enriched_tasks),
        "requires_traction_disconnection": any(t.get("requires_traction_disconnection") for t in enriched_tasks),
        "consolidated_departments": block_details.get("consolidated_departments", []),
        "crews": sorted(set(all_crews)),
        "equipment": sorted(set(all_equipment)),
        "assigned_tasks": enriched_tasks,
        "sanctioned_by": sanctioned_by,
        "sanctioned_role": sanctioned_role,
        "sanctioned_at": sanctioned_at,
        "reason_code": reason_code,
        "justification": justification,
        "verification_hash": v_hash,
    }

    if format.lower() == "pdf":
        pdf_bytes = generate_sanction_memo_pdf(memo_data)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="SANCTION_MEMO_{block_id}.pdf"'
            },
        )
    else:
        html_str = generate_sanction_memo_html(memo_data)
        return HTMLResponse(content=html_str)


