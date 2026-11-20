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
import io
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Sequence

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

LAB_NUMERIC_FIELDS: tuple[str, ...] = (
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

LAB_PHYSIOLOGICAL_BOUNDS: tuple[tuple[str, float, float], ...] = (
    ("egfr_ml_min", 0.0, 250.0),
    ("anc_k_ul", 0.0, 50.0),
    ("hemoglobin_g_dl", 2.0, 25.0),
    ("platelets_k_ul", 5.0, 1500.0),
    ("bilirubin_mg_dl", 0.0, 40.0),
    ("hba1c_pct", 2.5, 25.0),
    ("lvef_pct", 5.0, 95.0),
    ("pdl1_tps_pct", 0.0, 100.0),
    ("csf_ptau_pg_ml", 0.0, 500.0),
    ("fecal_calprotectin_ug_g", 0.0, 10000.0),
)

_RESERVED_EMAIL_DOMAINS = frozenset({"example.com", "example.org", "example.net"})
_NULL_SENTINELS = frozenset({"", "NULL", "NA", "NONE", "null", "na", "none"})


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
    def prepare_csv_key_meta(cls, fieldnames: Sequence[str]) -> tuple[tuple[str, bool, bool], ...]:
        """Pre-normalize CSV column metadata once per file to avoid per-row key parsing."""
        forbidden = cls.FORBIDDEN_PII_KEYS
        meta: list[tuple[str, bool, bool]] = []
        for raw_key in fieldnames:
            if raw_key is None:
                continue
            key_lower = raw_key.strip().lower()
            meta.append((raw_key, key_lower in forbidden, key_lower in ("age", "age_years")))
        return tuple(meta)

    @classmethod
    def scan_csv_record(
        cls,
        source_name: str,
        record_id: str,
        payload: dict[str, Any],
        key_meta: tuple[tuple[str, bool, bool], ...],
    ) -> list[str]:
        """Fast CSV record scanner using pre-normalized column metadata."""
        violations: list[str] = []
        ssn_search = cls.SSN_PATTERN.search
        phone_search = cls.PHONE_PATTERN.search
        email_finditer = cls.EMAIL_PATTERN.finditer
        reserved_domains = _RESERVED_EMAIL_DOMAINS

        for raw_key, is_forbidden, is_age in key_meta:
            if is_forbidden:
                violations.append(
                    f"{source_name}[{record_id}]: forbidden PII attribute '{raw_key}' detected"
                )
            raw_val = payload.get(raw_key)
            if raw_val is None:
                continue
            if is_age:
                stripped_age = raw_val.strip() if isinstance(raw_val, str) else str(raw_val).strip()
                if stripped_age != "":
                    try:
                        age_num = int(float(stripped_age))
                        if age_num > 89 or age_num < 0:
                            violations.append(
                                f"{source_name}[{record_id}]: age_years={age_num} violates HIPAA Safe Harbor (<0 or >89)"
                            )
                    except ValueError:
                        violations.append(
                            f"{source_name}[{record_id}]: non-numeric age_years=[REDACTED]"
                        )

            if isinstance(raw_val, (int, float, bool)):
                continue
            text = raw_val if isinstance(raw_val, str) else str(raw_val)
            tlen = len(text)
            if tlen >= 11 and "-" in text and ssn_search(text):
                violations.append(
                    f"{source_name}[{record_id}].{raw_key}: SSN-like pattern detected"
                )
            if tlen >= 12 and phone_search(text):
                violations.append(
                    f"{source_name}[{record_id}].{raw_key}: telephone number pattern detected"
                )
            if "@" in text:
                for match in email_finditer(text):
                    domain = match.group(1).lower()
                    if not (domain.endswith(".example") or domain in reserved_domains):
                        violations.append(
                            f"{source_name}[{record_id}].{raw_key}: non-reserved email domain '{domain}' detected"
                        )
        return violations

    @classmethod
    def _scan_nested_value(
        cls,
        source_name: str,
        record_id: str,
        field_path: str,
        raw_val: Any,
        violations: list[str],
    ) -> None:
        if raw_val is None or isinstance(raw_val, (int, float, bool)):
            return
        if isinstance(raw_val, dict):
            cls._scan_dict_recursive(source_name, record_id, raw_val, field_path, violations)
            return
        if isinstance(raw_val, (list, tuple)):
            for idx, item in enumerate(raw_val):
                item_path = f"{field_path}[{idx}]" if field_path else f"[{idx}]"
                cls._scan_nested_value(source_name, record_id, item_path, item, violations)
            return
        text = raw_val if isinstance(raw_val, str) else str(raw_val)
        tlen = len(text)
        if tlen >= 11 and "-" in text and cls.SSN_PATTERN.search(text):
            violations.append(
                f"{source_name}[{record_id}].{field_path}: SSN-like pattern detected"
            )
        if tlen >= 12 and cls.PHONE_PATTERN.search(text):
            violations.append(
                f"{source_name}[{record_id}].{field_path}: telephone number pattern detected"
            )
        if "@" in text:
            for match in cls.EMAIL_PATTERN.finditer(text):
                domain = match.group(1).lower()
                if not (domain.endswith(".example") or domain in _RESERVED_EMAIL_DOMAINS):
                    violations.append(
                        f"{source_name}[{record_id}].{field_path}: non-reserved email domain '{domain}' detected"
                    )

    @classmethod
    def _scan_dict_recursive(
        cls,
        source_name: str,
        record_id: str,
        payload: dict[str, Any],
        prefix: str,
        violations: list[str],
    ) -> None:
        forbidden = cls.FORBIDDEN_PII_KEYS
        for raw_key, raw_val in payload.items():
            if raw_key is None:
                continue
            key_str = raw_key if isinstance(raw_key, str) else str(raw_key)
            key_lower = key_str.strip().lower()
            field_path = f"{prefix}.{key_str}" if prefix else key_str
            if key_lower in forbidden:
                violations.append(
                    f"{source_name}[{record_id}]: forbidden PII attribute '{field_path}' detected"
                )
            if raw_val is None:
                continue
            if key_lower == "age" or key_lower == "age_years":
                stripped_age = raw_val.strip() if isinstance(raw_val, str) else str(raw_val).strip()
                if stripped_age != "":
                    try:
                        age_num = int(float(stripped_age))
                        if age_num > 89 or age_num < 0:
                            violations.append(
                                f"{source_name}[{record_id}]: age_years={age_num} violates HIPAA Safe Harbor (<0 or >89)"
                            )
                    except ValueError:
                        violations.append(
                            f"{source_name}[{record_id}]: non-numeric age_years=[REDACTED]"
                        )
            cls._scan_nested_value(source_name, record_id, field_path, raw_val, violations)

    @classmethod
    def scan_record(cls, source_name: str, record_id: str, payload: dict[str, Any]) -> list[str]:
        """Return list of human-readable compliance violation messages (empty if clean)."""
        violations: list[str] = []
        cls._scan_dict_recursive(source_name, record_id, payload, "", violations)
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


def _read_bytes_and_sha256(path: Path) -> tuple[bytes, str]:
    raw = path.read_bytes()
    return raw, hashlib.sha256(raw).hexdigest()


def _parse_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = value.strip() if isinstance(value, str) else str(value).strip()
    if text in _NULL_SENTINELS or text.upper() in ("NULL", "NA", "NONE"):
        return None
    return float(text)


def _parse_int(value: Any, field_name: str, source: str, rec_id: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    try:
        text = value.strip() if isinstance(value, str) else str(value).strip()
        try:
            return int(text)
        except ValueError:
            return int(float(text))
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
) -> tuple[list[dict[str, Any]], tuple[tuple[str, bool, bool], ...], str]:
    source_name = path.name
    raw_bytes, sha256_hex = _read_bytes_and_sha256(path)
    valid_rows: list[dict[str, Any]] = []
    req_cols = tuple(required_columns)
    with io.StringIO(raw_bytes.decode("utf-8"), newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise MalformedRecordError(f"{source_name}: CSV header row is missing")
        missing_cols = required_columns - set(reader.fieldnames)
        if missing_cols:
            raise MalformedRecordError(
                f"{source_name}: missing required columns: {sorted(missing_cols)}"
            )
        key_meta = PIIGuardrailScanner.prepare_csv_key_meta(reader.fieldnames)
        for line_num, row in enumerate(reader, start=2):
            if None in row or any(row[k] is None for k in req_cols):
                msg = f"{source_name} line {line_num}: column count mismatch or truncated CSV row"
                if strict:
                    raise MalformedRecordError(msg)
                _quarantine(conn, report, source_name, f"line-{line_num}", "MALFORMED_CSV", msg)
                continue
            valid_rows.append(row)
    return valid_rows, key_meta, sha256_hex


def _read_json_checked(path: Path) -> tuple[Any, str]:
    raw_bytes, sha256_hex = _read_bytes_and_sha256(path)
    try:
        return json.loads(raw_bytes.decode("utf-8")), sha256_hex
    except json.JSONDecodeError as exc:
        raise MalformedRecordError(
            f"{path.name} line {exc.lineno}: invalid JSON syntax ({exc.msg})"
        ) from exc


def _read_ndjson_checked(
    path: Path, strict: bool, conn: sqlite3.Connection, report: IngestionReport
) -> tuple[list[dict[str, Any]], str]:
    raw_bytes, sha256_hex = _read_bytes_and_sha256(path)
    records: list[dict[str, Any]] = []
    for line_num, raw_line in enumerate(raw_bytes.decode("utf-8").splitlines(), start=1):
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
    return records, sha256_hex


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


def _check_pii_csv(
    conn: sqlite3.Connection,
    report: IngestionReport,
    source_file: str,
    record_id: str,
    payload: dict[str, Any],
    key_meta: tuple[tuple[str, bool, bool], ...],
    strict: bool,
) -> bool:
    violations = PIIGuardrailScanner.scan_csv_record(source_file, record_id, payload, key_meta)
    if not violations:
        return True
    combined = "; ".join(violations)
    if strict:
        raise PIIComplianceViolationError(combined)
    _quarantine(conn, report, source_file, record_id, "PII_GUARDRAIL_VIOLATION", combined)
    return False


def _validate_lab_ranges(
    source_file: str,
    record_id: str,
    lab_vals: dict[str, float | None],
    strict: bool,
    conn: sqlite3.Connection,
    report: IngestionReport,
) -> dict[str, float | None]:
    cleaned: dict[str, float | None] | None = None
    for field_name, low, high in LAB_PHYSIOLOGICAL_BOUNDS:
        val = lab_vals.get(field_name)
        if val is None:
            continue
        if val < low or val > high:
            msg = f"{source_file}[{record_id}]: {field_name}={val} outside physiological bounds [{low}, {high}]"
            if strict:
                raise ClinicalRangeValidationError(msg)
            _quarantine(conn, report, source_file, record_id, "CLINICAL_RANGE_OUT_OF_BOUNDS", msg)
            if cleaned is None:
                cleaned = dict(lab_vals)
            cleaned[field_name] = None
    return cleaned if cleaned is not None else lab_vals


def ingest_workspace_data(
    data_dir: str | Path | None = None,
    db_path: str | Path = ":memory:",
    strict: bool = False,
    check_same_thread: bool = True,
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

    conn = sqlite3.connect(str(db_path), check_same_thread=check_same_thread)
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
    prov_rows: list[tuple[str, str, str, int, int]] = []

    def record_file_prov(filename: str, fmt: str, sha256_hex: str, ingested: int, q_before: int) -> None:
        q_delta = report.quarantined_records - q_before
        prov = {
            "source_file": filename,
            "file_format": fmt,
            "sha256": sha256_hex,
            "records_ingested": ingested,
            "records_quarantined": q_delta,
        }
        report.files_loaded += 1
        report.total_records_loaded += ingested
        report.provenance.append(prov)
        prov_rows.append((filename, fmt, sha256_hex, ingested, q_delta))

    # 1. ctms_studies.json
    q0 = report.quarantined_records
    studies_raw, studies_sha = _read_json_checked(resolved_dir / "ctms_studies.json")
    if not isinstance(studies_raw, list):
        raise MalformedRecordError("ctms_studies.json: root must be a JSON array")
    studies_batch: list[tuple[Any, ...]] = []
    for item in studies_raw:
        if not isinstance(item, dict) or "study_id" not in item:
            if strict:
                raise MalformedRecordError("ctms_studies.json: study object missing study_id")
            _quarantine(conn, report, "ctms_studies.json", "unknown", "MALFORMED_JSON", "Missing study_id")
            continue
        sid = str(item["study_id"])
        if not _check_pii(conn, report, "ctms_studies.json", sid, item, strict):
            continue
        studies_batch.append(
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
            )
        )
    if studies_batch:
        conn.executemany("INSERT INTO studies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", studies_batch)
    record_file_prov("ctms_studies.json", "json", studies_sha, len(studies_batch), q0)

    # 2. trial_eligibility_criteria.json
    q0 = report.quarantined_records
    crit_raw, crit_sha = _read_json_checked(resolved_dir / "trial_eligibility_criteria.json")
    if not isinstance(crit_raw, list):
        raise MalformedRecordError("trial_eligibility_criteria.json: root must be a JSON array")
    crit_batch: list[tuple[Any, ...]] = []
    for item in crit_raw:
        sid = str(item.get("study_id", "unknown"))
        if not _check_pii(conn, report, "trial_eligibility_criteria.json", sid, item, strict):
            continue
        crit_batch.append(
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
            )
        )
    if crit_batch:
        conn.executemany(
            "INSERT INTO eligibility_criteria VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            crit_batch,
        )
    record_file_prov("trial_eligibility_criteria.json", "json", crit_sha, len(crit_batch), q0)

    # 3. ctms_sites.csv
    q0 = report.quarantined_records
    sites_rows, sites_meta, sites_sha = _read_csv_checked(
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
    sites_batch: list[tuple[Any, ...]] = []
    for row in sites_rows:
        sid = row["site_id"]
        if not _check_pii_csv(conn, report, "ctms_sites.csv", sid, row, sites_meta, strict):
            continue
        sites_batch.append(
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
            )
        )
    if sites_batch:
        conn.executemany(
            "INSERT INTO sites VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            sites_batch,
        )
    record_file_prov("ctms_sites.csv", "csv", sites_sha, len(sites_batch), q0)

    # 4. ehr_patients.csv
    q0 = report.quarantined_records
    pat_rows, pat_meta, pat_sha = _read_csv_checked(
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
    pat_batch: list[tuple[Any, ...]] = []
    for row in pat_rows:
        pid = row["patient_id"]
        if not _check_pii_csv(conn, report, "ehr_patients.csv", pid, row, pat_meta, strict):
            continue
        try:
            pat_batch.append(
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
                )
            )
        except (MalformedRecordError, ValueError) as exc:
            if strict:
                raise MalformedRecordError(str(exc)) from exc
            _quarantine(conn, report, "ehr_patients.csv", pid, "INVALID_FIELD", str(exc))
    if pat_batch:
        conn.executemany(
            "INSERT INTO patients VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            pat_batch,
        )
    record_file_prov("ehr_patients.csv", "csv", pat_sha, len(pat_batch), q0)

    # 5. lab_biomarkers.csv
    q0 = report.quarantined_records
    lab_rows, lab_meta, lab_sha = _read_csv_checked(
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
    labs_batch: list[tuple[Any, ...]] = []
    for row in lab_rows:
        pid = row["patient_id"]
        if not _check_pii_csv(conn, report, "lab_biomarkers.csv", pid, row, lab_meta, strict):
            continue
        try:
            raw_coll = row["collection_days_ago"]
            coll_str = raw_coll.strip() if isinstance(raw_coll, str) else str(raw_coll).strip()
            coll_days = (
                None
                if coll_str == ""
                else _parse_int(coll_str, "collection_days_ago", "lab_biomarkers.csv", pid)
            )
            numeric_fields = {k: _parse_optional_float(row[k]) for k in LAB_NUMERIC_FIELDS}
        except (MalformedRecordError, ValueError) as exc:
            if strict:
                raise MalformedRecordError(f"lab_biomarkers.csv[{pid}]: {exc}") from exc
            _quarantine(conn, report, "lab_biomarkers.csv", pid, "INVALID_LAB_NUMERIC", str(exc))
            continue

        cleaned_nums = _validate_lab_ranges(
            "lab_biomarkers.csv", pid, numeric_fields, strict, conn, report
        )
        egfr_mut = row["egfr_mutation"].strip() or None
        labs_batch.append(
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
            )
        )
    if labs_batch:
        conn.executemany(
            "INSERT INTO patient_labs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            labs_batch,
        )
    record_file_prov("lab_biomarkers.csv", "csv", lab_sha, len(labs_batch), q0)

    # 6. edc_subject_visits.csv
    q0 = report.quarantined_records
    vis_rows, vis_meta, vis_sha = _read_csv_checked(
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
    vis_batch: list[tuple[Any, ...]] = []
    for row in vis_rows:
        vid = row["visit_record_id"]
        if not _check_pii_csv(conn, report, "edc_subject_visits.csv", vid, row, vis_meta, strict):
            continue
        vis_batch.append(
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
            )
        )
    if vis_batch:
        conn.executemany(
            "INSERT INTO edc_visits VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            vis_batch,
        )
    record_file_prov("edc_subject_visits.csv", "csv", vis_sha, len(vis_batch), q0)

    # 7. edc_protocol_deviations.ndjson
    q0 = report.quarantined_records
    pd_rows, pd_sha = _read_ndjson_checked(
        resolved_dir / "edc_protocol_deviations.ndjson", strict, conn, report
    )
    pd_batch: list[tuple[Any, ...]] = []
    for row in pd_rows:
        pd_id = str(row.get("pd_id", "unknown"))
        if not _check_pii(conn, report, "edc_protocol_deviations.ndjson", pd_id, row, strict):
            continue
        pd_batch.append(
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
            )
        )
    if pd_batch:
        conn.executemany(
            "INSERT INTO protocol_deviations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            pd_batch,
        )
    record_file_prov("edc_protocol_deviations.ndjson", "ndjson", pd_sha, len(pd_batch), q0)

    # 8. irt_randomization_inventory.json
    q0 = report.quarantined_records
    irt_raw, irt_sha = _read_json_checked(resolved_dir / "irt_randomization_inventory.json")
    if not isinstance(irt_raw, list):
        raise MalformedRecordError("irt_randomization_inventory.json: root must be a JSON array")
    irt_batch: list[tuple[Any, ...]] = []
    for row in irt_raw:
        sid = str(row.get("site_id", "unknown"))
        if not _check_pii(conn, report, "irt_randomization_inventory.json", sid, row, strict):
            continue
        irt_batch.append(
            (
                sid,
                str(row["study_id"]),
                _parse_int(row["available_kits"], "available_kits", "irt_randomization_inventory.json", sid),
                _parse_int(row["min_buffer_kits"], "min_buffer_kits", "irt_randomization_inventory.json", sid),
                _parse_int(row["resupply_in_transit"], "resupply_in_transit", "irt_randomization_inventory.json", sid),
                _parse_int(row["temp_excursion_count"], "temp_excursion_count", "irt_randomization_inventory.json", sid),
                _parse_int(row["unresolved_excursion"], "unresolved_excursion", "irt_randomization_inventory.json", sid),
                float(row["median_rand_to_dose_days"]),
            )
        )
    if irt_batch:
        conn.executemany("INSERT INTO irt_inventory VALUES (?, ?, ?, ?, ?, ?, ?, ?)", irt_batch)
    record_file_prov("irt_randomization_inventory.json", "json", irt_sha, len(irt_batch), q0)

    # 9. safety_sae_events.csv
    q0 = report.quarantined_records
    sae_rows, sae_meta, sae_sha = _read_csv_checked(
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
    sae_batch: list[tuple[Any, ...]] = []
    for row in sae_rows:
        sae_id = row["sae_id"]
        if not _check_pii_csv(conn, report, "safety_sae_events.csv", sae_id, row, sae_meta, strict):
            continue
        sae_batch.append(
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
            )
        )
    if sae_batch:
        conn.executemany(
            "INSERT INTO safety_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            sae_batch,
        )
    record_file_prov("safety_sae_events.csv", "csv", sae_sha, len(sae_batch), q0)

    # 10. qms_training_issues.json
    q0 = report.quarantined_records
    qms_raw, qms_sha = _read_json_checked(resolved_dir / "qms_training_issues.json")
    if not isinstance(qms_raw, dict) or "training_records" not in qms_raw or "quality_issues" not in qms_raw:
        raise MalformedRecordError(
            "qms_training_issues.json: root must contain 'training_records' and 'quality_issues'"
        )
    trn_batch: list[tuple[Any, ...]] = []
    for trn in qms_raw["training_records"]:
        tid = str(trn.get("training_id", "unknown"))
        if not _check_pii(conn, report, "qms_training_issues.json", tid, trn, strict):
            continue
        trn_batch.append(
            (
                tid,
                str(trn["site_id"]),
                str(trn["staff_role_id"]),
                str(trn["cert_type"]),
                str(trn["status"]),
                _parse_int(trn["days_until_expiry"], "days_until_expiry", "qms_training_issues.json", tid),
            )
        )
    if trn_batch:
        conn.executemany("INSERT INTO qms_training VALUES (?, ?, ?, ?, ?, ?)", trn_batch)

    iss_batch: list[tuple[Any, ...]] = []
    for iss in qms_raw["quality_issues"]:
        iid = str(iss.get("issue_id", "unknown"))
        if not _check_pii(conn, report, "qms_training_issues.json", iid, iss, strict):
            continue
        iss_batch.append(
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
            )
        )
    if iss_batch:
        conn.executemany("INSERT INTO qms_issues VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", iss_batch)
    record_file_prov("qms_training_issues.json", "json", qms_sha, len(trn_batch) + len(iss_batch), q0)

    if prov_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO ingestion_provenance
            (source_file, file_format, sha256, records_ingested, records_quarantined)
            VALUES (?, ?, ?, ?, ?)
            """,
            prov_rows,
        )

    conn.commit()
    logger.info(
        "Ingested %d records across %d files (%d quarantined)",
        report.total_records_loaded,
        report.files_loaded,
        report.quarantined_records,
    )
    return conn, report
