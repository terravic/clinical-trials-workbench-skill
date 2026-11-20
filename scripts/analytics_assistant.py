"""Conversational Analytics Assistant and Sandboxed Read-Only SQL Engine.

Translates natural-language clinical operations and trial selection questions
into transparent SQLite SELECT queries, enforces a two-layer read-only security
guardrail (lexical filter + SQLite authorizer callback), and returns narrative
synthesis, chart metadata, underlying rows, and exact SQL provenance.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from scripts import SQLSafetyError

FORBIDDEN_SQL_TOKENS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE|ATTACH|DETACH|PRAGMA|VACUUM|REINDEX|BEGIN|COMMIT|ROLLBACK)\b",
    re.IGNORECASE,
)

MAX_SQL_LENGTH = 4096
MAX_SQL_VM_OPCODES = 250_000
PROGRESS_CHECK_INTERVAL = 5_000

DISALLOWED_SQLITE_FUNCTIONS = frozenset(
    {
        "load_extension",
        "randomblob",
        "zeroblob",
    }
)

ALLOWED_SQLITE_ACTIONS = frozenset(
    {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
    }
)


def _readonly_authorizer(
    action_code: int,
    param1: str | None,
    param2: str | None,
    db_name: str | None,
    trigger_name: str | None,
) -> int:
    if action_code not in ALLOWED_SQLITE_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action_code == sqlite3.SQLITE_FUNCTION and param2:
        if param2.lower() in DISALLOWED_SQLITE_FUNCTIONS:
            return sqlite3.SQLITE_DENY
    if action_code == sqlite3.SQLITE_READ and param1:
        if param1.lower().startswith("sqlite_"):
            return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def validate_readonly_sql(sql: str) -> str:
    """Validate that a SQL string is a single, comment-free, read-only SELECT/WITH query."""
    cleaned = sql.strip()
    if not cleaned:
        raise SQLSafetyError("Empty SQL query is not permitted.")
    if len(cleaned) > MAX_SQL_LENGTH:
        raise SQLSafetyError(f"SQL query exceeds maximum allowed length ({MAX_SQL_LENGTH} characters).")
    if "--" in cleaned or "/*" in cleaned or "*/" in cleaned:
        raise SQLSafetyError("SQL comments are disallowed by security guardrails.")
    # Allow optional trailing semicolon, forbid internal semicolons (multi-statement)
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].strip()
    if ";" in cleaned:
        raise SQLSafetyError("Multi-statement SQL execution is disallowed.")
    if not re.match(r"^(SELECT|WITH)\b", cleaned, flags=re.IGNORECASE):
        raise SQLSafetyError("Only read-only SELECT or WITH queries are permitted.")
    match = FORBIDDEN_SQL_TOKENS.search(cleaned)
    if match:
        raise SQLSafetyError(f"Disallowed SQL keyword detected: '{match.group(1).upper()}'")
    return cleaned


def execute_guarded_sql(
    conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = (), max_rows: int = 200
) -> tuple[list[str], list[dict[str, Any]]]:
    """Execute a validated read-only query under a strict SQLite authorizer and VM step budget."""
    safe_sql = validate_readonly_sql(sql)
    opcode_ticks = 0

    def _progress_guard() -> int:
        nonlocal opcode_ticks
        opcode_ticks += PROGRESS_CHECK_INTERVAL
        return 1 if opcode_ticks > MAX_SQL_VM_OPCODES else 0

    conn.set_authorizer(_readonly_authorizer)
    conn.set_progress_handler(_progress_guard, PROGRESS_CHECK_INTERVAL)
    try:
        cursor = conn.execute(safe_sql, params)
        col_names = [desc[0] for desc in (cursor.description or [])]
        fetched = cursor.fetchmany(max_rows)
        rows = [dict(zip(col_names, r)) for r in fetched]
        return col_names, rows
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc).lower():
            raise SQLSafetyError("SQL query aborted: exceeded read-only execution budget.") from exc
        if "not authorized" in str(exc).lower():
            raise SQLSafetyError(f"SQLite authorizer blocked non-read-only operation: {exc}") from exc
        raise SQLSafetyError(f"SQL execution error: {exc}") from exc
    except sqlite3.DatabaseError as exc:
        if "not authorized" in str(exc).lower():
            raise SQLSafetyError(f"SQLite authorizer blocked non-read-only operation: {exc}") from exc
        raise SQLSafetyError(f"SQL execution error: {exc}") from exc
    finally:
        conn.set_progress_handler(None, 0)
        conn.set_authorizer(None)


ANALYTICS_INTENT_TEMPLATES: list[dict[str, Any]] = [
    {
        "intent_id": "SAE_CYCLE_TIME_BY_STUDY",
        "sample_prompt": "Compare SAE follow-up reporting cycle times across studies against the 7-day target",
        "keywords": ["sae", "cycle", "safety", "reporting", "28", "7-day", "turnaround"],
        "sql": """
            SELECT
                st.protocol_code,
                se.study_id,
                COUNT(*) AS total_sae_count,
                ROUND(AVG(se.followup_cycle_days), 1) AS avg_cycle_days,
                MAX(se.followup_cycle_days) AS max_cycle_days,
                SUM(CASE WHEN se.followup_cycle_days > 7 THEN 1 ELSE 0 END) AS breaches_over_7d,
                st.sae_target_days AS target_days
            FROM safety_events se
            JOIN studies st ON st.study_id = se.study_id
            WHERE se.serious = 1
            GROUP BY st.protocol_code, se.study_id, st.sae_target_days
            ORDER BY avg_cycle_days DESC
        """,
        "chart_spec": {
            "type": "bar",
            "title": "Mean Serious Adverse Event Cycle Time by Study (Days vs 7d Target)",
            "x_key": "protocol_code",
            "y_key": "avg_cycle_days",
            "target_line": 7.0,
            "color": "#E11D48",
        },
        "provenance": ["safety_sae_events.csv", "ctms_studies.json"],
    },
    {
        "intent_id": "SIGNALS_BY_FAMILY",
        "sample_prompt": "Show active signal counts and severity distribution across the 7 signal families",
        "keywords": ["family", "families", "distribution", "breakdown", "category", "signals by"],
        "sql": """
            SELECT
                family,
                COUNT(*) AS active_signals,
                SUM(CASE WHEN severity = 'CRITICAL' THEN 1 ELSE 0 END) AS critical_count,
                SUM(CASE WHEN severity = 'HIGH' THEN 1 ELSE 0 END) AS high_count,
                SUM(weight) AS total_risk_weight
            FROM signals
            WHERE disposition != 'DISMISS'
            GROUP BY family
            ORDER BY total_risk_weight DESC
        """,
        "chart_spec": {
            "type": "bar",
            "title": "Active Clinical Risk Signals by Domain Family",
            "x_key": "family",
            "y_key": "active_signals",
            "color": "#0D9488",
        },
        "provenance": [
            "safety_sae_events.csv",
            "edc_subject_visits.csv",
            "qms_training_issues.json",
            "irt_randomization_inventory.json",
            "edc_protocol_deviations.ndjson",
            "ctms_sites.csv",
        ],
    },
    {
        "intent_id": "ELIGIBLE_PATIENTS_BY_STUDY",
        "sample_prompt": "Summarize clinical trial patient eligibility funnel and lab-refresh candidates per study",
        "keywords": ["eligible", "eligibility", "patient", "funnel", "selection", "candidates", "biomarker", "match"],
        "sql": """
            SELECT
                protocol_code,
                study_id,
                SUM(CASE WHEN classification = 'ELIGIBLE' THEN 1 ELSE 0 END) AS eligible_patients,
                SUM(CASE WHEN classification = 'POTENTIAL_NEEDS_LABS' THEN 1 ELSE 0 END) AS needs_labs_patients,
                ROUND(AVG(CASE WHEN classification = 'ELIGIBLE' THEN match_score END), 1) AS avg_eligible_score
            FROM eligibility_assessments
            GROUP BY protocol_code, study_id
            ORDER BY eligible_patients DESC
        """,
        "chart_spec": {
            "type": "bar",
            "title": "Eligible Candidate Patient Pool by Trial Protocol",
            "x_key": "protocol_code",
            "y_key": "eligible_patients",
            "secondary_key": "needs_labs_patients",
            "color": "#059669",
        },
        "provenance": ["ehr_patients.csv", "lab_biomarkers.csv", "trial_eligibility_criteria.json"],
    },
    {
        "intent_id": "EDC_QUERY_AND_SDV_BACKLOG",
        "sample_prompt": "Which clinical sites have aging EDC queries over 14 days or high SDV backlogs?",
        "keywords": ["edc", "query", "queries", "aging", "sdv", "backlog", "ecrf"],
        "sql": """
            SELECT
                site_id,
                protocol_code,
                region,
                open_queries_total,
                max_query_age_days,
                sdv_backlog_total,
                priority_tier
            FROM site_assessments
            WHERE max_query_age_days > 14 OR sdv_backlog_total > 25
            ORDER BY max_query_age_days DESC, sdv_backlog_total DESC
        """,
        "chart_spec": {
            "type": "bar",
            "title": "Oldest Unresolved EDC Query Age by Site (Days)",
            "x_key": "site_id",
            "y_key": "max_query_age_days",
            "target_line": 21.0,
            "color": "#D97706",
        },
        "provenance": ["edc_subject_visits.csv", "ctms_sites.csv"],
    },
    {
        "intent_id": "IRT_SUPPLY_AND_EXCURSIONS",
        "sample_prompt": "Identify sites with low IRT investigational drug inventory or cold-chain temperature excursions",
        "keywords": ["irt", "kit", "kits", "supply", "inventory", "excursion", "temperature", "cold"],
        "sql": """
            SELECT
                i.site_id,
                s.site_name,
                i.study_id,
                i.available_kits,
                i.min_buffer_kits,
                i.resupply_in_transit,
                i.temp_excursion_count,
                i.unresolved_excursion,
                i.median_rand_to_dose_days
            FROM irt_inventory i
            JOIN sites s ON s.site_id = i.site_id
            WHERE i.available_kits <= i.min_buffer_kits + 2 OR i.unresolved_excursion = 1
            ORDER BY i.unresolved_excursion DESC, i.available_kits ASC
        """,
        "chart_spec": {
            "type": "bar",
            "title": "Available Investigational Drug Kits at Supply-Constrained Sites",
            "x_key": "site_id",
            "y_key": "available_kits",
            "secondary_key": "min_buffer_kits",
            "color": "#4F46E5",
        },
        "provenance": ["irt_randomization_inventory.json", "ctms_sites.csv"],
    },
    {
        "intent_id": "GCP_TRAINING_AND_CAPA",
        "sample_prompt": "List sites with expired GCP or amendment training and overdue CAPAs",
        "keywords": ["gcp", "training", "expired", "certification", "capa", "qms", "compliance"],
        "sql": """
            SELECT
                t.site_id,
                s.study_id,
                t.staff_role_id,
                t.cert_type,
                t.status,
                t.days_until_expiry
            FROM qms_training t
            JOIN sites s ON s.site_id = t.site_id
            WHERE t.days_until_expiry <= 14 OR t.status = 'INCOMPLETE'
            ORDER BY t.days_until_expiry ASC
        """,
        "chart_spec": {
            "type": "bar",
            "title": "Training Days Relative to Expiration (Negative Indicates Expired)",
            "x_key": "site_id",
            "y_key": "days_until_expiry",
            "color": "#E11D48",
        },
        "provenance": ["qms_training_issues.json", "ctms_sites.csv"],
    },
    {
        "intent_id": "PRIORITY_SITES_RANKING",
        "sample_prompt": "Rank all clinical sites by composite risk score and priority tier",
        "keywords": ["priority", "rank", "sites", "tier", "score", "risk", "portfolio", "overview"],
        "sql": """
            SELECT
                site_id,
                protocol_code,
                region,
                assigned_cra_id,
                priority_tier,
                risk_score,
                critical_signals,
                high_signals,
                total_signals
            FROM site_assessments
            ORDER BY risk_score DESC, site_id ASC
        """,
        "chart_spec": {
            "type": "bar",
            "title": "Clinical Site Composite Risk Score Ranking",
            "x_key": "site_id",
            "y_key": "risk_score",
            "target_line": 70.0,
            "color": "#0D9488",
        },
        "provenance": ["ctms_sites.csv", "signal_rules_config.json"],
    },
]


def _select_intent(question: str) -> dict[str, Any]:
    q_lower = question.lower()
    best_tpl = ANALYTICS_INTENT_TEMPLATES[-1]
    best_hits = -1
    for tpl in ANALYTICS_INTENT_TEMPLATES:
        hits = sum(1 for kw in tpl["keywords"] if kw in q_lower)
        if hits > best_hits:
            best_hits = hits
            best_tpl = tpl
    return best_tpl


def _synthesize_narrative(question: str, intent_id: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return f"Query completed for '{question}' and returned 0 matching records across the active clinical store."
    top = rows[0]
    if intent_id == "SAE_CYCLE_TIME_BY_STUDY":
        total_breaches = sum(int(r.get("breaches_over_7d", 0)) for r in rows)
        return (
            f"Across {len(rows)} clinical studies, {top['protocol_code']} exhibits the longest mean SAE follow-up "
            f"cycle time at {top['avg_cycle_days']} days (max {top['max_cycle_days']} days). "
            f"In total, {total_breaches} serious events currently exceed the 7-day System-of-Action reporting target."
        )
    if intent_id == "SIGNALS_BY_FAMILY":
        total_sigs = sum(int(r.get("active_signals", 0)) for r in rows)
        return (
            f"The workspace currently tracks {total_sigs} active risk signals across {len(rows)} families. "
            f"{top['family']} carries the highest cumulative risk weight ({top['total_risk_weight']}) "
            f"with {top['active_signals']} signals ({top['critical_count']} CRITICAL)."
        )
    if intent_id == "ELIGIBLE_PATIENTS_BY_STUDY":
        tot_elig = sum(int(r.get("eligible_patients", 0)) for r in rows)
        tot_pend = sum(int(r.get("needs_labs_patients", 0)) for r in rows)
        return (
            f"Protocol eligibility evaluation identified {tot_elig} fully ELIGIBLE candidates matches and "
            f"{tot_pend} POTENTIAL_NEEDS_LABS candidates across {len(rows)} studies. "
            f"Top recruitment pool: {top['protocol_code']} ({top['eligible_patients']} eligible)."
        )
    if intent_id == "CUSTOM_SQL":
        preview_pairs = ", ".join(f"{k}={v}" for k, v in list(top.items())[:4])
        return f"Executed verified read-only SQL returning {len(rows)} row(s). Lead record: {preview_pairs}."

    preview_pairs = ", ".join(f"{k}={v}" for k, v in list(top.items())[:4])
    return (
        f"Returned {len(rows)} operational record(s) matching '{question}'. "
        f"Highest-priority entry: {preview_pairs}."
    )


def answer_analytics_question(
    conn: sqlite3.Connection,
    question: str | None = None,
    custom_sql: str | None = None,
) -> dict[str, Any]:
    """Execute either a natural-language analytical query or a verified read-only SELECT query."""
    if custom_sql and custom_sql.strip():
        sql_to_run = validate_readonly_sql(custom_sql)
        cols, rows = execute_guarded_sql(conn, sql_to_run)
        # Auto-infer simple chart spec when numeric column exists
        x_col = cols[0] if cols else "index"
        y_col = next(
            (
                c
                for c in cols[1:]
                if rows and isinstance(rows[0].get(c), (int, float))
            ),
            cols[0] if cols else "value",
        )
        return {
            "question": question or "Custom Read-Only SQL Query",
            "intent_id": "CUSTOM_SQL",
            "narrative": _synthesize_narrative(question or "Custom SQL", "CUSTOM_SQL", rows),
            "sql": sql_to_run,
            "columns": cols,
            "rows": rows,
            "chart_spec": {
                "type": "bar",
                "title": "Custom SQL Query Result Series",
                "x_key": x_col,
                "y_key": y_col,
                "color": "#0D9488",
            },
            "provenance_sources": ["unified_sqlite_semantic_store"],
        }

    prompt_text = (question or "").strip()
    if not prompt_text:
        raise ValueError("Either 'question' or 'custom_sql' must be provided.")

    # Allow user to type SELECT directly in question box as well
    if re.match(r"^(SELECT|WITH)\b", prompt_text, flags=re.IGNORECASE) or FORBIDDEN_SQL_TOKENS.search(prompt_text):
        if re.match(r"^(SELECT|WITH|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|PRAGMA|ATTACH)\b", prompt_text, flags=re.IGNORECASE):
            return answer_analytics_question(conn, question="Direct SQL Input", custom_sql=prompt_text)

    tpl = _select_intent(prompt_text)
    clean_sql = "\n".join(line.rstrip() for line in tpl["sql"].strip().splitlines())
    cols, rows = execute_guarded_sql(conn, clean_sql)
    return {
        "question": prompt_text,
        "intent_id": tpl["intent_id"],
        "narrative": _synthesize_narrative(prompt_text, tpl["intent_id"], rows),
        "sql": clean_sql,
        "columns": cols,
        "rows": rows,
        "chart_spec": tpl["chart_spec"],
        "provenance_sources": tpl["provenance"],
    }


def build_analytics_catalog(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Pre-compute all curated analytical queries for instant offline UI inspection."""
    return [
        answer_analytics_question(conn, question=tpl["sample_prompt"])
        for tpl in ANALYTICS_INTENT_TEMPLATES
    ]
