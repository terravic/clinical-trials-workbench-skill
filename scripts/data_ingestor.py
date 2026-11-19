"""Multi-source clinical data ingestion, PII/PHI guardrail scanner, and SQLite builder.

Implements the 'Listen' stage of the System of Action pipeline by reading CSV,
JSON, and NDJSON feeds, enforcing HIPAA Safe Harbor and physiological range
guardrails, recording SHA-256 provenance, and populating a unified relational
SQLite semantic store.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from scripts import (
    ClinicalRangeValidationError,
    MalformedRecordError,
    MissingDataSourceError,
    PIIComplianceViolationError,
    configure_logging,
    resolve_data_dir,
)

REQUIRED_SOURCE_FILES: tuple[tuple[str, str], ...] = (
    ("ctms_studies.json", "json"),
    ("ctms_sites.csv", "csv"),
    ("trial_eligibility_criteria.json", "json"),
    ("ehr_patients.csv", "csv"),
    ("lab_biomarkers.csv", "csv"),
    ("edc_subject_visits.csv", "csv"),
    ("edc_protocol_deviations.ndjson", "ndjson"),
    ("irt_randomization_inventory.json", "json"),
    ("safety_sae_events.csv", "csv"),
    ("qms_training_issues.json", "json"),
)


class PIIGuardrailScanner:
    """Scans clinical payloads for PII/PHI leakage and Safe Harbor age violations."""

    SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
    PHONE_PATTERN = re.compile(
        r"(?<![A-Za-z0-9-])(?:\+\d{1,2}\s?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?![A-Za-z0-9-])"
    )
    EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
    FORBIDDEN_PII_KEYS = frozenset(
        {
            "patient_name",
            "first_name",
            "last_name",
            "full_name",
            "ssn",
            "social_security",
            "street_address",
            "home_address",
            "phone_number",
            "mrn",
            "date_of_birth",
            "dob",
        }
    )

    @classmethod
    def scan_record(cls, source_name: str, record_id: str, payload: dict[str, Any]) -> list[str]:
        """Return list of human-readable compliance violation messages (empty if clean)."""
        violations: list[str] = []
        for raw_key, raw_val in payload.items():
            if raw_key is None:
                continue
            key_lower = str(raw_key).strip().lower()
            if key_lower in cls.FORBIDDEN_PII_KEYS:
                violations.append(
                    f"{source_name}[{record_id}]: forbidden PII attribute '{raw_key}' detected"
                )
            if raw_val is None:
                continue
            if key_lower in ("age", "age_years") and str(raw_val).strip() != "":
                try:
                    age_num = int(float(raw_val))
                    if age_num > 89 or age_num < 0:
                        violations.append(
                            f"{source_name}[{record_id}]: age_years={age_num} violates HIPAA Safe Harbor (<0 or >89)"
                        )
                except ValueError:
                    violations.append(
                        f"{source_name}[{record_id}]: non-numeric age_years='{raw_val}'"
                    )

            text = str(raw_val)
            if cls.SSN_PATTERN.search(text):
                violations.append(
                    f"{source_name}[{record_id}].{raw_key}: SSN-like pattern detected"
                )
            if cls.PHONE_PATTERN.search(text):
                violations.append(
                    f"{source_name}[{record_id}].{raw_key}: telephone number pattern detected"
                )
            for match in cls.EMAIL_PATTERN.finditer(text):
                domain = match.group(1).lower()
                if not (
                    domain.endswith(".example")
                    or domain in ("example.com", "example.org", "example.net")
                ):
                    violations.append(
                        f"{source_name}[{record_id}].{raw_key}: non-reserved email domain '{domain}' detected"
                    )
        return violations


@dataclass
class IngestionReport:
    """Summary of multi-source ingestion and validation checks."""

    data_dir: str
    files_loaded: int = 0
    total_records_loaded: int = 0
    quarantined_records: int = 0
    provenance: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.upper() in ("NULL", "NA", "NONE"):
        return None
    return float(text)


def _parse_int(value: Any, field_name: str, source: str, rec_id: str) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError) as exc:
        raise MalformedRecordError(
            f"{source}[{rec_id}]: invalid integer for '{field_name}': {value!r}"
        ) from exc


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS studies (
            study_id TEXT PRIMARY KEY,
            protocol_code TEXT NOT NULL,
            title TEXT NOT NULL,
            phase TEXT NOT NULL,
            therapeutic_area TEXT NOT NULL,
            target_enrollment INTEGER NOT NULL,
            current_icf_version TEXT NOT NULL,
            sae_target_days INTEGER NOT NULL,
            baseline_sae_days INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS eligibility_criteria (
            study_id TEXT PRIMARY KEY,
            protocol_code TEXT NOT NULL,
            min_age INTEGER NOT NULL,
            max_age INTEGER NOT NULL,
            allowed_icd10_json TEXT NOT NULL,
            max_ecog INTEGER NOT NULL,
            max_prior_lines INTEGER NOT NULL,
            min_washout_days INTEGER NOT NULL,
            biomarker_rule_json TEXT NOT NULL,
            lab_thresholds_json TEXT NOT NULL,
            exclusion_conditions_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sites (
            site_id TEXT PRIMARY KEY,
            study_id TEXT NOT NULL,
            site_name TEXT NOT NULL,
            country_code TEXT NOT NULL,
            region TEXT NOT NULL,
            assigned_cra_id TEXT NOT NULL,
            peer_cra_id TEXT NOT NULL,
            site_status TEXT NOT NULL,
            target_enrollment INTEGER NOT NULL,
            enrolled_count INTEGER NOT NULL,
            screened_count INTEGER NOT NULL,
            screen_fail_count INTEGER NOT NULL,
            last_monitoring_date TEXT NOT NULL,
            days_since_monitoring INTEGER NOT NULL,
            days_since_last_screen INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS patients (
            patient_id TEXT PRIMARY KEY,
            age_years INTEGER NOT NULL,
            sex TEXT NOT NULL,
            region TEXT NOT NULL,
            candidate_site_ids TEXT NOT NULL,
            primary_icd10 TEXT NOT NULL,
            disease_stage TEXT NOT NULL,
            ecog_status INTEGER NOT NULL,
            prior_lines_therapy INTEGER NOT NULL,
            washout_days INTEGER NOT NULL,
            comorbidities TEXT NOT NULL,
            pre_screening_consent INTEGER NOT NULL,
            severity_index REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS patient_labs (
            patient_id TEXT PRIMARY KEY,
            collection_days_ago INTEGER,
            egfr_ml_min REAL,
            anc_k_ul REAL,
            hemoglobin_g_dl REAL,
            platelets_k_ul REAL,
            bilirubin_mg_dl REAL,
            hba1c_pct REAL,
            lvef_pct REAL,
            egfr_mutation TEXT,
            pdl1_tps_pct REAL,
            csf_ptau_pg_ml REAL,
            fecal_calprotectin_ug_g REAL,
            biomarker_composite_z REAL
        );

        CREATE TABLE IF NOT EXISTS edc_visits (
            visit_record_id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL,
            site_id TEXT NOT NULL,
            study_id TEXT NOT NULL,
            visit_name TEXT NOT NULL,
            visit_status TEXT NOT NULL,
            days_out_of_window INTEGER NOT NULL,
            open_queries INTEGER NOT NULL,
            max_query_age_days INTEGER NOT NULL,
            signed_icf_version TEXT NOT NULL,
            icf_sign_date TEXT NOT NULL,
            screening_proc_date TEXT NOT NULL,
            icf_verified INTEGER NOT NULL,
            sdv_backlog_pages INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS protocol_deviations (
            pd_id TEXT PRIMARY KEY,
            site_id TEXT NOT NULL,
            study_id TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            category TEXT NOT NULL,
            severity TEXT NOT NULL,
            status TEXT NOT NULL,
            days_open INTEGER NOT NULL,
            capa_required INTEGER NOT NULL,
            description TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS irt_inventory (
            site_id TEXT PRIMARY KEY,
            study_id TEXT NOT NULL,
            available_kits INTEGER NOT NULL,
            min_buffer_kits INTEGER NOT NULL,
            resupply_in_transit INTEGER NOT NULL,
            temp_excursion_count INTEGER NOT NULL,
            unresolved_excursion INTEGER NOT NULL,
            median_rand_to_dose_days REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS safety_events (
            sae_id TEXT PRIMARY KEY,
            site_id TEXT NOT NULL,
            study_id TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            event_term TEXT NOT NULL,
            ctcae_grade INTEGER NOT NULL,
            serious INTEGER NOT NULL,
            onset_date TEXT NOT NULL,
            awareness_date TEXT NOT NULL,
            initial_report_hours INTEGER NOT NULL,
            followup_cycle_days INTEGER NOT NULL,
            report_status TEXT NOT NULL,
            causality_assessed INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS qms_training (
            training_id TEXT PRIMARY KEY,
            site_id TEXT NOT NULL,
            staff_role_id TEXT NOT NULL,
            cert_type TEXT NOT NULL,
            status TEXT NOT NULL,
            days_until_expiry INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS qms_issues (
            issue_id TEXT PRIMARY KEY,
            site_id TEXT NOT NULL,
            study_id TEXT NOT NULL,
            category TEXT NOT NULL,
            priority TEXT NOT NULL,
            status TEXT NOT NULL,
            days_open INTEGER NOT NULL,
            overdue_capa INTEGER NOT NULL,
            summary TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS validation_quarantine (
            quarantine_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT NOT NULL,
            record_id TEXT NOT NULL,
            error_type TEXT NOT NULL,
            reason TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ingestion_provenance (
            source_file TEXT PRIMARY KEY,
            file_format TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            records_ingested INTEGER NOT NULL,
            records_quarantined INTEGER NOT NULL
        );
        """
    )


def _read_csv_checked(
    path: Path, required_columns: set[str], strict: bool, conn: sqlite3.Connection, report: IngestionReport
) -> list[dict[str, Any]]:
    source_name = path.name
    valid_rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise MalformedRecordError(f"{source_name}: CSV header row is missing")
        missing_cols = required_columns - set(reader.fieldnames)
        if missing_cols:
            raise MalformedRecordError(
                f"{source_name}: missing required columns: {sorted(missing_cols)}"
            )
        for line_num, row in enumerate(reader, start=2):
            if None in row or any(v is None for k, v in row.items() if k in required_columns):
                msg = f"{source_name} line {line_num}: column count mismatch or truncated CSV row"
                if strict:
                    raise MalformedRecordError(msg)
                _quarantine(conn, report, source_name, f"line-{line_num}", "MALFORMED_CSV", msg)
                continue
            valid_rows.append(row)
    return valid_rows


def _read_json_checked(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MalformedRecordError(
            f"{path.name} line {exc.lineno}: invalid JSON syntax ({exc.msg})"
        ) from exc


def _read_ndjson_checked(
    path: Path, strict: bool, conn: sqlite3.Connection, report: IngestionReport
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_num, raw_line in enumerate(handle, start=1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
                if not isinstance(parsed, dict):
                    raise MalformedRecordError(
                        f"{path.name} line {line_num}: expected JSON object"
                    )
                records.append(parsed)
            except (json.JSONDecodeError, MalformedRecordError) as exc:
                msg = f"{path.name} line {line_num}: malformed NDJSON record ({exc})"
                if strict:
                    if isinstance(exc, MalformedRecordError):
                        raise
                    raise MalformedRecordError(msg) from exc
                _quarantine(conn, report, path.name, f"line-{line_num}", "MALFORMED_NDJSON", msg)
    return records


def _quarantine(
    conn: sqlite3.Connection,
    report: IngestionReport,
    source_file: str,
    record_id: str,
    error_type: str,
    reason: str,
) -> None:
    report.quarantined_records += 1
    report.warnings.append(reason)
    conn.execute(
        """
        INSERT INTO validation_quarantine (source_file, record_id, error_type, reason)
        VALUES (?, ?, ?, ?)
        """,
        (source_file, record_id, error_type, reason),
    )


def _check_pii(
    conn: sqlite3.Connection,
    report: IngestionReport,
    source_file: str,
    record_id: str,
    payload: dict[str, Any],
    strict: bool,
) -> bool:
    violations = PIIGuardrailScanner.scan_record(source_file, record_id, payload)
    if not violations:
        return True
    combined = "; ".join(violations)
    if strict:
        raise PIIComplianceViolationError(combined)
    _quarantine(conn, report, source_file, record_id, "PII_GUARDRAIL_VIOLATION", combined)
    return False


def _validate_lab_ranges(
    source_file: str, record_id: str, lab_vals: dict[str, float | None], strict: bool, conn: sqlite3.Connection, report: IngestionReport
) -> dict[str, float | None]:
    bounds: dict[str, tuple[float, float]] = {
        "egfr_ml_min": (0.0, 250.0),
        "anc_k_ul": (0.0, 50.0),
        "hemoglobin_g_dl": (2.0, 25.0),
        "platelets_k_ul": (5.0, 1500.0),
        "bilirubin_mg_dl": (0.0, 40.0),
        "hba1c_pct": (2.5, 25.0),
        "lvef_pct": (5.0, 95.0),
        "pdl1_tps_pct": (0.0, 100.0),
        "csf_ptau_pg_ml": (0.0, 500.0),
        "fecal_calprotectin_ug_g": (0.0, 10000.0),
    }
    cleaned = dict(lab_vals)
    for field_name, (low, high) in bounds.items():
        val = cleaned.get(field_name)
        if val is None:
            continue
        if val < low or val > high:
            msg = f"{source_file}[{record_id}]: {field_name}={val} outside physiological bounds [{low}, {high}]"
            if strict:
                raise ClinicalRangeValidationError(msg)
            _quarantine(conn, report, source_file, record_id, "CLINICAL_RANGE_OUT_OF_BOUNDS", msg)
            cleaned[field_name] = None
    return cleaned


def ingest_workspace_data(
    data_dir: str | Path | None = None,
    db_path: str | Path = ":memory:",
    strict: bool = False,
) -> tuple[sqlite3.Connection, IngestionReport]:
    """Ingest all 10 multi-source clinical feeds into a unified SQLite store."""
    logger = configure_logging()
    resolved_dir = resolve_data_dir(data_dir)
    if not resolved_dir.exists():
        raise MissingDataSourceError(
            f"Data directory does not exist: {resolved_dir}. Run 'generate-data' first."
        )

    for filename, _ in REQUIRED_SOURCE_FILES:
        fpath = resolved_dir / filename
        if not fpath.is_file():
            raise MissingDataSourceError(f"Missing required clinical data feed: {fpath}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return _populate_workspace_db(conn, resolved_dir, strict, logger)
    except Exception:
        conn.close()
        raise


def _populate_workspace_db(
    conn: sqlite3.Connection,
    resolved_dir: Path,
    strict: bool,
    logger: Any,
) -> tuple[sqlite3.Connection, IngestionReport]:
    _create_schema(conn)
    report = IngestionReport(data_dir=str(resolved_dir))

    def record_file_prov(filename: str, fmt: str, ingested: int, q_before: int) -> None:
        fpath = resolved_dir / filename
        q_delta = report.quarantined_records - q_before
        prov = {
            "source_file": filename,
            "file_format": fmt,
            "sha256": _sha256_file(fpath),
            "records_ingested": ingested,
            "records_quarantined": q_delta,
        }
        report.files_loaded += 1
        report.total_records_loaded += ingested
        report.provenance.append(prov)
        conn.execute(
            """
            INSERT OR REPLACE INTO ingestion_provenance
            (source_file, file_format, sha256, records_ingested, records_quarantined)
            VALUES (?, ?, ?, ?, ?)
            """,
            (filename, fmt, prov["sha256"], ingested, q_delta),
        )

    # 1. ctms_studies.json
    q0 = report.quarantined_records
    studies_raw = _read_json_checked(resolved_dir / "ctms_studies.json")
    if not isinstance(studies_raw, list):
        raise MalformedRecordError("ctms_studies.json: root must be a JSON array")
    studies_ingested = 0
    for item in studies_raw:
        if not isinstance(item, dict) or "study_id" not in item:
            if strict:
                raise MalformedRecordError("ctms_studies.json: study object missing study_id")
            _quarantine(conn, report, "ctms_studies.json", "unknown", "MALFORMED_JSON", "Missing study_id")
            continue
        sid = str(item["study_id"])
        if not _check_pii(conn, report, "ctms_studies.json", sid, item, strict):
            continue
        conn.execute(
            """
            INSERT INTO studies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sid,
                str(item["protocol_code"]),
                str(item["title"]),
                str(item["phase"]),
                str(item["therapeutic_area"]),
                _parse_int(item["target_enrollment"], "target_enrollment", "ctms_studies.json", sid),
                str(item["current_icf_version"]),
                _parse_int(item["sae_target_days"], "sae_target_days", "ctms_studies.json", sid),
                _parse_int(item.get("baseline_sae_days", 28), "baseline_sae_days", "ctms_studies.json", sid),
            ),
        )
        studies_ingested += 1
    record_file_prov("ctms_studies.json", "json", studies_ingested, q0)

    # 2. trial_eligibility_criteria.json
    q0 = report.quarantined_records
    crit_raw = _read_json_checked(resolved_dir / "trial_eligibility_criteria.json")
    if not isinstance(crit_raw, list):
        raise MalformedRecordError("trial_eligibility_criteria.json: root must be a JSON array")
    crit_ingested = 0
    for item in crit_raw:
        sid = str(item.get("study_id", "unknown"))
        if not _check_pii(conn, report, "trial_eligibility_criteria.json", sid, item, strict):
            continue
        conn.execute(
            """
            INSERT INTO eligibility_criteria VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sid,
                str(item["protocol_code"]),
                _parse_int(item["min_age"], "min_age", "trial_eligibility_criteria.json", sid),
                _parse_int(item["max_age"], "max_age", "trial_eligibility_criteria.json", sid),
                json.dumps(item["allowed_icd10_prefixes"]),
                _parse_int(item["max_ecog"], "max_ecog", "trial_eligibility_criteria.json", sid),
                _parse_int(item["max_prior_lines"], "max_prior_lines", "trial_eligibility_criteria.json", sid),
                _parse_int(item["min_washout_days"], "min_washout_days", "trial_eligibility_criteria.json", sid),
                json.dumps(item["biomarker_rule"]),
                json.dumps(item["lab_thresholds"]),
                json.dumps(item["exclusion_conditions"]),
            ),
        )
        crit_ingested += 1
    record_file_prov("trial_eligibility_criteria.json", "json", crit_ingested, q0)

    # 3. ctms_sites.csv
    q0 = report.quarantined_records
    sites_rows = _read_csv_checked(
        resolved_dir / "ctms_sites.csv",
        {
            "site_id",
            "study_id",
            "site_name",
            "country_code",
            "region",
            "assigned_cra_id",
            "peer_cra_id",
            "site_status",
            "target_enrollment",
            "enrolled_count",
            "screened_count",
            "screen_fail_count",
            "last_monitoring_date",
            "days_since_monitoring",
            "days_since_last_screen",
        },
        strict,
        conn,
        report,
    )
    sites_ingested = 0
    for row in sites_rows:
        sid = row["site_id"]
        if not _check_pii(conn, report, "ctms_sites.csv", sid, row, strict):
            continue
        conn.execute(
            """
            INSERT INTO sites VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sid,
                row["study_id"],
                row["site_name"],
                row["country_code"],
                row["region"],
                row["assigned_cra_id"],
                row["peer_cra_id"],
                row["site_status"],
                _parse_int(row["target_enrollment"], "target_enrollment", "ctms_sites.csv", sid),
                _parse_int(row["enrolled_count"], "enrolled_count", "ctms_sites.csv", sid),
                _parse_int(row["screened_count"], "screened_count", "ctms_sites.csv", sid),
                _parse_int(row["screen_fail_count"], "screen_fail_count", "ctms_sites.csv", sid),
                row["last_monitoring_date"],
                _parse_int(row["days_since_monitoring"], "days_since_monitoring", "ctms_sites.csv", sid),
                _parse_int(row["days_since_last_screen"], "days_since_last_screen", "ctms_sites.csv", sid),
            ),
        )
        sites_ingested += 1
    record_file_prov("ctms_sites.csv", "csv", sites_ingested, q0)

    # 4. ehr_patients.csv
    q0 = report.quarantined_records
    pat_rows = _read_csv_checked(
        resolved_dir / "ehr_patients.csv",
        {
            "patient_id",
            "age_years",
            "sex",
            "region",
            "candidate_site_ids",
            "primary_icd10",
            "disease_stage",
            "ecog_status",
            "prior_lines_therapy",
            "washout_days",
            "comorbidities",
            "pre_screening_consent",
            "severity_index",
        },
        strict,
        conn,
        report,
    )
    pat_ingested = 0
    for row in pat_rows:
        pid = row["patient_id"]
        if not _check_pii(conn, report, "ehr_patients.csv", pid, row, strict):
            continue
        try:
            conn.execute(
                """
                INSERT INTO patients VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pid,
                    _parse_int(row["age_years"], "age_years", "ehr_patients.csv", pid),
                    row["sex"],
                    row["region"],
                    row["candidate_site_ids"],
                    row["primary_icd10"],
                    row["disease_stage"],
                    _parse_int(row["ecog_status"], "ecog_status", "ehr_patients.csv", pid),
                    _parse_int(row["prior_lines_therapy"], "prior_lines_therapy", "ehr_patients.csv", pid),
                    _parse_int(row["washout_days"], "washout_days", "ehr_patients.csv", pid),
                    row["comorbidities"],
                    _parse_int(row["pre_screening_consent"], "pre_screening_consent", "ehr_patients.csv", pid),
                    float(row["severity_index"]),
                ),
            )
            pat_ingested += 1
        except (MalformedRecordError, ValueError) as exc:
            if strict:
                raise MalformedRecordError(str(exc)) from exc
            _quarantine(conn, report, "ehr_patients.csv", pid, "INVALID_FIELD", str(exc))
    record_file_prov("ehr_patients.csv", "csv", pat_ingested, q0)

    # 5. lab_biomarkers.csv
    q0 = report.quarantined_records
    lab_rows = _read_csv_checked(
        resolved_dir / "lab_biomarkers.csv",
        {
            "patient_id",
            "collection_days_ago",
            "egfr_ml_min",
            "anc_k_ul",
            "hemoglobin_g_dl",
            "platelets_k_ul",
            "bilirubin_mg_dl",
            "hba1c_pct",
            "lvef_pct",
            "egfr_mutation",
            "pdl1_tps_pct",
            "csf_ptau_pg_ml",
            "fecal_calprotectin_ug_g",
            "biomarker_composite_z",
        },
        strict,
        conn,
        report,
    )
    labs_ingested = 0
    for row in lab_rows:
        pid = row["patient_id"]
        if not _check_pii(conn, report, "lab_biomarkers.csv", pid, row, strict):
            continue
        try:
            coll_days = (
                None
                if str(row["collection_days_ago"]).strip() == ""
                else _parse_int(row["collection_days_ago"], "collection_days_ago", "lab_biomarkers.csv", pid)
            )
            numeric_fields = {
                k: _parse_optional_float(row[k])
                for k in (
                    "egfr_ml_min",
                    "anc_k_ul",
                    "hemoglobin_g_dl",
                    "platelets_k_ul",
                    "bilirubin_mg_dl",
                    "hba1c_pct",
                    "lvef_pct",
                    "pdl1_tps_pct",
                    "csf_ptau_pg_ml",
                    "fecal_calprotectin_ug_g",
                    "biomarker_composite_z",
                )
            }
        except (MalformedRecordError, ValueError) as exc:
            if strict:
                raise MalformedRecordError(f"lab_biomarkers.csv[{pid}]: {exc}") from exc
            _quarantine(conn, report, "lab_biomarkers.csv", pid, "INVALID_LAB_NUMERIC", str(exc))
            continue

        cleaned_nums = _validate_lab_ranges(
            "lab_biomarkers.csv", pid, numeric_fields, strict, conn, report
        )
        egfr_mut = str(row["egfr_mutation"]).strip() or None
        conn.execute(
            """
            INSERT INTO patient_labs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pid,
                coll_days,
                cleaned_nums["egfr_ml_min"],
                cleaned_nums["anc_k_ul"],
                cleaned_nums["hemoglobin_g_dl"],
                cleaned_nums["platelets_k_ul"],
                cleaned_nums["bilirubin_mg_dl"],
                cleaned_nums["hba1c_pct"],
                cleaned_nums["lvef_pct"],
                egfr_mut,
                cleaned_nums["pdl1_tps_pct"],
                cleaned_nums["csf_ptau_pg_ml"],
                cleaned_nums["fecal_calprotectin_ug_g"],
                cleaned_nums["biomarker_composite_z"],
            ),
        )
        labs_ingested += 1
    record_file_prov("lab_biomarkers.csv", "csv", labs_ingested, q0)

    # 6. edc_subject_visits.csv
    q0 = report.quarantined_records
    vis_rows = _read_csv_checked(
        resolved_dir / "edc_subject_visits.csv",
        {
            "visit_record_id",
            "subject_id",
            "site_id",
            "study_id",
            "visit_name",
            "visit_status",
            "days_out_of_window",
            "open_queries",
            "max_query_age_days",
            "signed_icf_version",
            "icf_sign_date",
            "screening_proc_date",
            "icf_verified",
            "sdv_backlog_pages",
        },
        strict,
        conn,
        report,
    )
    vis_ingested = 0
    for row in vis_rows:
        vid = row["visit_record_id"]
        if not _check_pii(conn, report, "edc_subject_visits.csv", vid, row, strict):
            continue
        conn.execute(
            """
            INSERT INTO edc_visits VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                vid,
                row["subject_id"],
                row["site_id"],
                row["study_id"],
                row["visit_name"],
                row["visit_status"],
                _parse_int(row["days_out_of_window"], "days_out_of_window", "edc_subject_visits.csv", vid),
                _parse_int(row["open_queries"], "open_queries", "edc_subject_visits.csv", vid),
                _parse_int(row["max_query_age_days"], "max_query_age_days", "edc_subject_visits.csv", vid),
                row["signed_icf_version"],
                row["icf_sign_date"],
                row["screening_proc_date"],
                _parse_int(row["icf_verified"], "icf_verified", "edc_subject_visits.csv", vid),
                _parse_int(row["sdv_backlog_pages"], "sdv_backlog_pages", "edc_subject_visits.csv", vid),
            ),
        )
        vis_ingested += 1
    record_file_prov("edc_subject_visits.csv", "csv", vis_ingested, q0)

    # 7. edc_protocol_deviations.ndjson
    q0 = report.quarantined_records
    pd_rows = _read_ndjson_checked(
        resolved_dir / "edc_protocol_deviations.ndjson", strict, conn, report
    )
    pd_ingested = 0
    for row in pd_rows:
        pd_id = str(row.get("pd_id", "unknown"))
        if not _check_pii(conn, report, "edc_protocol_deviations.ndjson", pd_id, row, strict):
            continue
        conn.execute(
            """
            INSERT INTO protocol_deviations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pd_id,
                str(row["site_id"]),
                str(row["study_id"]),
                str(row["subject_id"]),
                str(row["category"]),
                str(row["severity"]),
                str(row["status"]),
                _parse_int(row["days_open"], "days_open", "edc_protocol_deviations.ndjson", pd_id),
                _parse_int(row["capa_required"], "capa_required", "edc_protocol_deviations.ndjson", pd_id),
                str(row["description"]),
            ),
        )
        pd_ingested += 1
    record_file_prov("edc_protocol_deviations.ndjson", "ndjson", pd_ingested, q0)

    # 8. irt_randomization_inventory.json
    q0 = report.quarantined_records
    irt_raw = _read_json_checked(resolved_dir / "irt_randomization_inventory.json")
    if not isinstance(irt_raw, list):
        raise MalformedRecordError("irt_randomization_inventory.json: root must be a JSON array")
    irt_ingested = 0
    for row in irt_raw:
        sid = str(row.get("site_id", "unknown"))
        if not _check_pii(conn, report, "irt_randomization_inventory.json", sid, row, strict):
            continue
        conn.execute(
            """
            INSERT INTO irt_inventory VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sid,
                str(row["study_id"]),
                _parse_int(row["available_kits"], "available_kits", "irt_randomization_inventory.json", sid),
                _parse_int(row["min_buffer_kits"], "min_buffer_kits", "irt_randomization_inventory.json", sid),
                _parse_int(row["resupply_in_transit"], "resupply_in_transit", "irt_randomization_inventory.json", sid),
                _parse_int(row["temp_excursion_count"], "temp_excursion_count", "irt_randomization_inventory.json", sid),
                _parse_int(row["unresolved_excursion"], "unresolved_excursion", "irt_randomization_inventory.json", sid),
                float(row["median_rand_to_dose_days"]),
            ),
        )
        irt_ingested += 1
    record_file_prov("irt_randomization_inventory.json", "json", irt_ingested, q0)

    # 9. safety_sae_events.csv
    q0 = report.quarantined_records
    sae_rows = _read_csv_checked(
        resolved_dir / "safety_sae_events.csv",
        {
            "sae_id",
            "site_id",
            "study_id",
            "subject_id",
            "event_term",
            "ctcae_grade",
            "serious",
            "onset_date",
            "awareness_date",
            "initial_report_hours",
            "followup_cycle_days",
            "report_status",
            "causality_assessed",
        },
        strict,
        conn,
        report,
    )
    sae_ingested = 0
    for row in sae_rows:
        sae_id = row["sae_id"]
        if not _check_pii(conn, report, "safety_sae_events.csv", sae_id, row, strict):
            continue
        conn.execute(
            """
            INSERT INTO safety_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sae_id,
                row["site_id"],
                row["study_id"],
                row["subject_id"],
                row["event_term"],
                _parse_int(row["ctcae_grade"], "ctcae_grade", "safety_sae_events.csv", sae_id),
                _parse_int(row["serious"], "serious", "safety_sae_events.csv", sae_id),
                row["onset_date"],
                row["awareness_date"],
                _parse_int(row["initial_report_hours"], "initial_report_hours", "safety_sae_events.csv", sae_id),
                _parse_int(row["followup_cycle_days"], "followup_cycle_days", "safety_sae_events.csv", sae_id),
                row["report_status"],
                _parse_int(row["causality_assessed"], "causality_assessed", "safety_sae_events.csv", sae_id),
            ),
        )
        sae_ingested += 1
    record_file_prov("safety_sae_events.csv", "csv", sae_ingested, q0)

    # 10. qms_training_issues.json
    q0 = report.quarantined_records
    qms_raw = _read_json_checked(resolved_dir / "qms_training_issues.json")
    if not isinstance(qms_raw, dict) or "training_records" not in qms_raw or "quality_issues" not in qms_raw:
        raise MalformedRecordError(
            "qms_training_issues.json: root must contain 'training_records' and 'quality_issues'"
        )
    qms_ingested = 0
    for trn in qms_raw["training_records"]:
        tid = str(trn.get("training_id", "unknown"))
        if not _check_pii(conn, report, "qms_training_issues.json", tid, trn, strict):
            continue
        conn.execute(
            """
            INSERT INTO qms_training VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                tid,
                str(trn["site_id"]),
                str(trn["staff_role_id"]),
                str(trn["cert_type"]),
                str(trn["status"]),
                _parse_int(trn["days_until_expiry"], "days_until_expiry", "qms_training_issues.json", tid),
            ),
        )
        qms_ingested += 1

    for iss in qms_raw["quality_issues"]:
        iid = str(iss.get("issue_id", "unknown"))
        if not _check_pii(conn, report, "qms_training_issues.json", iid, iss, strict):
            continue
        conn.execute(
            """
            INSERT INTO qms_issues VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                iid,
                str(iss["site_id"]),
                str(iss["study_id"]),
                str(iss["category"]),
                str(iss["priority"]),
                str(iss["status"]),
                _parse_int(iss["days_open"], "days_open", "qms_training_issues.json", iid),
                _parse_int(iss["overdue_capa"], "overdue_capa", "qms_training_issues.json", iid),
                str(iss["summary"]),
            ),
        )
        qms_ingested += 1
    record_file_prov("qms_training_issues.json", "json", qms_ingested, q0)

    conn.commit()
    logger.info(
        "Ingested %d records across %d files (%d quarantined)",
        report.total_records_loaded,
        report.files_loaded,
        report.quarantined_records,
    )
    return conn, report
