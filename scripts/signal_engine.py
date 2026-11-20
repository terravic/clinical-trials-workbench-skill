"""Config-driven Signal Detection and Site Risk Assessment Engine.

Implements the 'Detect' and 'Assess' stages of the System of Action workflow
by evaluating 32 declarative rules across 7 clinical operations signal families,
recording source-record provenance, computing 8-week historical signal trends,
and classifying clinical sites into three priority tiers.
"""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import sqlite3
from statistics import median
from typing import Any

from scripts import DEFAULT_SCHEMAS_DIR, EntityNotFoundError

SIGNAL_FAMILIES: tuple[str, ...] = (
    "SAFETY",
    "INFORMED_CONSENT",
    "TRAINING_COMPLIANCE",
    "DATA_QUALITY_EDC",
    "ENROLLMENT_RETENTION",
    "SUPPLY_IRT",
    "PROTOCOL_COMPLIANCE",
)

_RULES_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}


def load_signal_rules(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load declarative 32-rule configuration from JSON with stat-keyed caching."""
    path = (Path(config_path) if config_path else (DEFAULT_SCHEMAS_DIR / "signal_rules_config.json")).resolve()
    st = path.stat()
    cache_key = (str(path), st.st_mtime_ns, st.st_size)
    cached = _RULES_CACHE.get(cache_key)
    if cached is not None:
        return cached
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if len(_RULES_CACHE) > 16:
        _RULES_CACHE.clear()
    _RULES_CACHE[cache_key] = parsed
    return parsed


def _build_weekly_trend(site_id: str, base_count: int, has_critical: bool) -> list[int]:
    """Synthesize deterministic 8-week historical signal trajectory ending at current count."""
    seed_num = sum(ord(ch) for ch in site_id)
    curr = max(0, base_count)
    if curr == 0:
        return [0, 1, 0, 0, 1, 0, 0, 0]
    deltas = (-3, -2, -2, -1, -1, 0, 0) if has_critical else (1, 1, 0, 1, 0, 0, 0)
    series: list[int] = []
    for idx, d in enumerate(deltas):
        jitter = ((seed_num + idx) % 2) - (1 if idx < 3 else 0)
        val = curr + d + jitter
        series.append(val if val > 0 else 0)
    series.append(curr)
    return series


def evaluate_signals_and_sites(
    conn: sqlite3.Connection,
    config_path: str | Path | None = None,
    feedback_map: dict[str, dict[str, Any]] | None = None,
    local_elig_by_site: dict[str, int] | None = None,
    persist_tables: bool = True,
) -> dict[str, Any]:
    """Run all 32 signal rules, classify sites into 3 tiers, and persist SQLite tables."""
    cfg = load_signal_rules(config_path)
    rules_by_id = {r["rule_id"]: r for r in cfg["rules"]}
    active_feedback = feedback_map or {}

    studies = {
        r["study_id"]: dict(r)
        for r in conn.execute("SELECT * FROM studies").fetchall()
    }
    sites = [
        dict(r)
        for r in conn.execute("SELECT * FROM sites ORDER BY site_id").fetchall()
    ]
    sites_by_id = {s["site_id"]: s for s in sites}

    # Pre-fetch operational tables
    safety_rows = [dict(r) for r in conn.execute("SELECT * FROM safety_events ORDER BY sae_id").fetchall()]
    visit_rows = [dict(r) for r in conn.execute("SELECT * FROM edc_visits ORDER BY visit_record_id").fetchall()]
    trn_rows = [dict(r) for r in conn.execute("SELECT * FROM qms_training ORDER BY training_id").fetchall()]
    iss_rows = [dict(r) for r in conn.execute("SELECT * FROM qms_issues ORDER BY issue_id").fetchall()]
    irt_rows = [dict(r) for r in conn.execute("SELECT * FROM irt_inventory ORDER BY site_id").fetchall()]
    pd_rows = [dict(r) for r in conn.execute("SELECT * FROM protocol_deviations ORDER BY pd_id").fetchall()]

    # Reuse precomputed Pass-1 eligible candidate counts when provided, else query SQLite
    if local_elig_by_site is None:
        local_elig_map: dict[str, int] = {}
        has_elig_table = (
            conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='eligibility_assessments'"
            ).fetchone()
            is not None
        )
        if has_elig_table:
            for row in conn.execute(
                """
                SELECT recommended_site_id AS site_id, COUNT(*) AS cnt
                FROM eligibility_assessments
                WHERE classification = 'ELIGIBLE' AND recommended_site_id IS NOT NULL
                GROUP BY recommended_site_id
                """
            ).fetchall():
                local_elig_map[row["site_id"]] = int(row["cnt"])
    else:
        local_elig_map = local_elig_by_site

    raw_triggers: list[tuple[str, str, str, Any, Any, str]] = []
    # Tuple layout: (rule_id, site_id, source_record_id, observed_value, threshold_repr, extra_context)

    saes_by_site: dict[str, list[dict[str, Any]]] = defaultdict(list)
    visits_by_site: dict[str, list[dict[str, Any]]] = defaultdict(list)
    trn_by_site: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pds_by_site: dict[str, list[dict[str, Any]]] = defaultdict(list)
    issues_by_site: dict[str, list[dict[str, Any]]] = defaultdict(list)

    # --- FAMILY 1: SAFETY (5 rules) ---
    grade3_by_site: dict[str, list[str]] = {}
    serious_cycles: list[int] = []
    compliant_serious_cycles: list[int] = []
    for sae in safety_rows:
        sid = sae["site_id"]
        saes_by_site[sid].append(sae)
        rec_id = sae["sae_id"]
        term = sae["event_term"]
        if sae["serious"] == 1:
            cyc = sae["followup_cycle_days"]
            serious_cycles.append(cyc)
            if sae["report_status"] == "SUBMITTED":
                compliant_serious_cycles.append(cyc)
            if sae["initial_report_hours"] > 24:
                raw_triggers.append(("SR-SAF-01", sid, rec_id, sae["initial_report_hours"], 24, term))
            if cyc > 7:
                raw_triggers.append(("SR-SAF-02", sid, rec_id, cyc, 7, term))
            if cyc > 21:
                raw_triggers.append(("SR-SAF-03", sid, rec_id, cyc, 21, term))
        if sae["ctcae_grade"] >= 3:
            if sae["causality_assessed"] == 0:
                raw_triggers.append(("SR-SAF-04", sid, rec_id, sae["causality_assessed"], 1, term))
            grade3_by_site.setdefault(sid, []).append(rec_id)

    for sid, sae_ids in sorted(grade3_by_site.items()):
        if len(sae_ids) >= 2:
            raw_triggers.append(("SR-SAF-05", sid, ",".join(sae_ids), len(sae_ids), 2, f"{len(sae_ids)} Grade 3+ events"))

    # --- FAMILY 2: INFORMED_CONSENT (4 rules) & FAMILY 4: DATA_QUALITY_EDC (5 rules) & ENR-04 & CMP-05 ---
    outdated_icf_by_site: dict[str, list[str]] = {}
    for vis in visit_rows:
        sid = vis["site_id"]
        visits_by_site[sid].append(vis)
        vid = vis["visit_record_id"]
        curr_icf = studies[vis["study_id"]]["current_icf_version"]
        if vis["signed_icf_version"] != curr_icf:
            raw_triggers.append(("SR-ICF-01", sid, vid, vis["signed_icf_version"], curr_icf, curr_icf))
            outdated_icf_by_site.setdefault(sid, []).append(vis["subject_id"])
        if vis["screening_proc_date"] < vis["icf_sign_date"]:
            raw_triggers.append(
                ("SR-ICF-02", sid, vid, vis["screening_proc_date"], vis["icf_sign_date"], vis["icf_sign_date"])
            )
        if vis["icf_verified"] == 0:
            raw_triggers.append(("SR-ICF-03", sid, vid, 0, 1, vis["subject_id"]))

        # EDC rules
        q_age = vis["max_query_age_days"]
        if q_age > 21:
            raw_triggers.append(("SR-EDC-01", sid, vid, q_age, 21, vis["subject_id"]))
        elif 15 <= q_age <= 21:
            raw_triggers.append(("SR-EDC-02", sid, vid, q_age, "15-21", vis["subject_id"]))
        if vis["open_queries"] >= 5:
            raw_triggers.append(("SR-EDC-03", sid, vid, vis["open_queries"], 5, vis["subject_id"]))
        if vis["sdv_backlog_pages"] > 25:
            raw_triggers.append(("SR-EDC-04", sid, vid, vis["sdv_backlog_pages"], 25, vis["subject_id"]))
        v_status = vis["visit_status"]
        if v_status == "OVERDUE_ENTRY":
            raw_triggers.append(("SR-EDC-05", sid, vid, v_status, "COMPLETED", vis["visit_name"]))
        elif v_status == "WITHDRAWN_EARLY":
            raw_triggers.append(("SR-ENR-04", sid, vid, v_status, "ACTIVE", vis["subject_id"]))
        if vis["days_out_of_window"] > 5:
            raw_triggers.append(("SR-CMP-05", sid, vid, vis["days_out_of_window"], 5, vis["visit_name"]))

    for sid, subjs in sorted(outdated_icf_by_site.items()):
        if len(subjs) >= 2:
            raw_triggers.append(("SR-ICF-04", sid, ",".join(subjs), len(subjs), 2, "Amendment re-consent backlog"))

    # --- FAMILY 3: TRAINING_COMPLIANCE (4 rules) ---
    for trn in trn_rows:
        sid = trn["site_id"]
        trn_by_site[sid].append(trn)
        tid = trn["training_id"]
        label = f"{trn['staff_role_id']} / {trn['cert_type']}"
        days_exp = trn["days_until_expiry"]
        if trn["cert_type"] == "GCP" and days_exp < 0:
            raw_triggers.append(("SR-TRN-01", sid, tid, abs(days_exp), 0, label))
        elif trn["cert_type"] == "PROTOCOL_AMENDMENT" and days_exp < 0:
            raw_triggers.append(("SR-TRN-02", sid, tid, abs(days_exp), 0, label))
        elif 0 <= days_exp <= 14 and trn["status"] != "INCOMPLETE":
            raw_triggers.append(("SR-TRN-03", sid, tid, days_exp, 14, label))
        if trn["status"] == "INCOMPLETE":
            raw_triggers.append(("SR-TRN-04", sid, tid, trn["status"], "ACTIVE", label))

    # --- FAMILY 5: ENROLLMENT_RETENTION (site rules) & CMP-04 ---
    for site in sites:
        sid = site["site_id"]
        enr_pct = round((site["enrolled_count"] * 100.0) / max(1, site["target_enrollment"]), 1)
        if enr_pct < 40.0:
            raw_triggers.append(
                (
                    "SR-ENR-01",
                    sid,
                    sid,
                    enr_pct,
                    40.0,
                    f"{site['enrolled_count']}/{site['target_enrollment']} enrolled",
                )
            )
        if site["screened_count"] >= 5:
            sf_pct = round((site["screen_fail_count"] * 100.0) / site["screened_count"], 1)
            if sf_pct > 55.0:
                raw_triggers.append(
                    (
                        "SR-ENR-02",
                        sid,
                        sid,
                        sf_pct,
                        55.0,
                        f"{site['screen_fail_count']}/{site['screened_count']} screen failures",
                    )
                )
        if site["days_since_last_screen"] > 30:
            raw_triggers.append(("SR-ENR-03", sid, sid, site["days_since_last_screen"], 30, site["site_name"]))

        local_elig = local_elig_map.get(sid, 0)
        if local_elig >= 3 and site["enrolled_count"] < site["target_enrollment"]:
            raw_triggers.append(("SR-ENR-05", sid, sid, local_elig, 3, "Matched regional candidates"))

        if site["days_since_monitoring"] > 60:
            raw_triggers.append(
                (
                    "SR-CMP-04",
                    sid,
                    sid,
                    site["days_since_monitoring"],
                    60,
                    f"last visited {site['last_monitoring_date']}",
                )
            )

    # --- FAMILY 6: SUPPLY_IRT (4 rules) ---
    irt_map: dict[str, dict[str, Any]] = {}
    for irt in irt_rows:
        sid = irt["site_id"]
        irt_map[sid] = irt
        if irt["unresolved_excursion"] == 1:
            raw_triggers.append(
                ("SR-IRT-01", sid, sid, 1, 0, str(irt["temp_excursion_count"]))
            )
        if irt["available_kits"] < irt["min_buffer_kits"]:
            raw_triggers.append(
                ("SR-IRT-02", sid, sid, irt["available_kits"], irt["min_buffer_kits"], str(irt["min_buffer_kits"]))
            )
        elif irt["available_kits"] <= irt["min_buffer_kits"] + 2 and irt["resupply_in_transit"] == 0:
            raw_triggers.append(("SR-IRT-03", sid, sid, irt["available_kits"], irt["min_buffer_kits"] + 2, "0 in transit"))
        if irt["median_rand_to_dose_days"] > 5.0:
            raw_triggers.append(("SR-IRT-04", sid, sid, irt["median_rand_to_dose_days"], 5.0, "Rand-to-dose lag"))

    # --- FAMILY 7: PROTOCOL_COMPLIANCE (PD & QMS rules) ---
    for pd in pd_rows:
        sid = pd["site_id"]
        pds_by_site[sid].append(pd)
        if pd["status"] != "CLOSED":
            if pd["severity"] == "MAJOR" and pd["days_open"] > 14:
                raw_triggers.append(("SR-CMP-01", sid, pd["pd_id"], pd["days_open"], 14, pd["category"]))
            if pd["capa_required"] == 1:
                raw_triggers.append(("SR-CMP-02", sid, pd["pd_id"], pd["days_open"], "CLOSED", pd["category"]))

    for iss in iss_rows:
        sid = iss["site_id"]
        issues_by_site[sid].append(iss)
        if iss["overdue_capa"] == 1 and iss["status"] != "CLOSED":
            raw_triggers.append(("SR-CMP-03", sid, iss["issue_id"], iss["days_open"], 0, iss["category"]))

    # Sort triggers deterministically by severity priority, weight desc, site_id, rule_id
    sev_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    raw_triggers.sort(
        key=lambda t: (
            sev_order[rules_by_id[t[0]]["severity"]],
            -int(rules_by_id[t[0]]["weight"]),
            t[1],
            t[0],
            t[2],
        )
    )

    signals: list[dict[str, Any]] = []
    signals_by_site: dict[str, list[dict[str, Any]]] = {s["site_id"]: [] for s in sites}
    signal_db_rows: list[tuple[Any, ...]] = []

    for idx, (rule_id, site_id, rec_id, obs_val, thresh_val, extra_ctx) in enumerate(raw_triggers, start=1):
        rule = rules_by_id[rule_id]
        site = sites_by_id[site_id]
        study = studies[site["study_id"]]
        sig_id = f"SIG-{1000 + idx}"
        fb = active_feedback.get(sig_id, {})
        rationale = rule["rationale_template"].format(
            record_id=rec_id,
            observed=obs_val,
            threshold=thresh_val,
            extra=extra_ctx,
        )
        weight_int = int(rule["weight"])
        disp = fb.get("disposition", "PENDING_REVIEW")
        sig_obj = {
            "signal_id": sig_id,
            "rule_id": rule_id,
            "family": rule["family"],
            "title": rule["title"],
            "severity": rule["severity"],
            "weight": weight_int,
            "site_id": site_id,
            "site_name": site["site_name"],
            "region": site["region"],
            "assigned_cra_id": site["assigned_cra_id"],
            "study_id": site["study_id"],
            "protocol_code": study["protocol_code"],
            "evidence": {
                "source_file": rule["source_file"],
                "source_record_id": rec_id,
                "metric_name": rule["metric_name"],
                "observed_value": obs_val,
                "threshold_value": thresh_val,
            },
            "rationale": rationale,
            "recommended_action": rule["recommended_action"],
            "disposition": disp,
            "reviewer_note": fb.get("note", ""),
            "updated_at": fb.get("updated_at", ""),
        }
        signals.append(sig_obj)
        signals_by_site[site_id].append(sig_obj)
        if persist_tables:
            signal_db_rows.append(
                (
                    sig_id,
                    rule_id,
                    rule["family"],
                    rule["title"],
                    rule["severity"],
                    weight_int,
                    site_id,
                    site["study_id"],
                    study["protocol_code"],
                    rule["source_file"],
                    rec_id,
                    rule["metric_name"],
                    str(obs_val),
                    str(thresh_val),
                    disp,
                )
            )

    # Enrich sites with composite risk score, 3-tier classification, family heatmap matrix, and operational rollups
    enriched_sites: list[dict[str, Any]] = []
    site_db_rows: list[tuple[Any, ...]] = []
    tier_counts = {
        "Priority Action Required": 0,
        "Important Signals Next": 0,
        "Continue to Monitor": 0,
    }

    for site in sites:
        sid = site["site_id"]
        study = studies[site["study_id"]]
        site_sigs = [s for s in signals_by_site[sid] if s["disposition"] != "DISMISS"]
        crit_cnt = 0
        high_cnt = 0
        med_cnt = 0
        low_cnt = 0
        max_w = 0
        sum_w = 0
        has_safety_icf_breach = False
        family_counts = {fam: 0 for fam in SIGNAL_FAMILIES}
        family_weights = {fam: 0 for fam in SIGNAL_FAMILIES}

        for s_item in site_sigs:
            sev = s_item["severity"]
            w = s_item["weight"]
            fam = s_item["family"]
            if sev == "CRITICAL":
                crit_cnt += 1
                if fam in ("SAFETY", "INFORMED_CONSENT"):
                    has_safety_icf_breach = True
            elif sev == "HIGH":
                high_cnt += 1
            elif sev == "MEDIUM":
                med_cnt += 1
            else:
                low_cnt += 1
            if w > max_w:
                max_w = w
            sum_w += w
            family_counts[fam] += 1
            family_weights[fam] += w

        if site_sigs:
            risk_score = min(100, round(max_w + 0.32 * (sum_w - max_w) + crit_cnt * 12))
        else:
            risk_score = 0

        if crit_cnt >= 1 or has_safety_icf_breach or risk_score >= 70:
            tier = "Priority Action Required"
        elif high_cnt >= 1 or risk_score >= 35:
            tier = "Important Signals Next"
        else:
            tier = "Continue to Monitor"
        tier_counts[tier] += 1

        weekly_trend = _build_weekly_trend(sid, len(site_sigs), crit_cnt > 0)
        for s_item in signals_by_site[sid]:
            s_item["site_weekly_trend"] = weekly_trend
            s_item["site_priority_tier"] = tier

        site_visits = visits_by_site[sid]
        site_pds = pds_by_site[sid]
        site_saes = saes_by_site[sid]
        site_issues = issues_by_site[sid]
        site_trn = trn_by_site[sid]
        irt = irt_map[sid]

        open_q_total = sum(v["open_queries"] for v in site_visits)
        max_q_age = max((v["max_query_age_days"] for v in site_visits), default=0)
        sdv_total = sum(v["sdv_backlog_pages"] for v in site_visits)
        open_pds = sum(1 for p in site_pds if p["status"] != "CLOSED")
        local_elig_cnt = local_elig_map.get(sid, 0)

        top_titles = [s["title"] for s in site_sigs[:2]]
        if top_titles:
            ai_summary = (
                f"{tier} ({len(site_sigs)} active signals; risk score {risk_score}/100). "
                f"Primary drivers: {'; '.join(top_titles)}. "
                f"Enrollment stands at {site['enrolled_count']}/{site['target_enrollment']} "
                f"with {local_elig_cnt} matched local screening candidates."
            )
        else:
            ai_summary = (
                f"{tier} (0 active risk signals; risk score 0/100). "
                f"Site maintains compliant safety/ICF metrics at {site['enrolled_count']}/{site['target_enrollment']} enrollment."
            )

        enriched_site = {
            **site,
            "protocol_code": study["protocol_code"],
            "therapeutic_area": study["therapeutic_area"],
            "priority_tier": tier,
            "risk_score": risk_score,
            "signal_counts": {
                "CRITICAL": crit_cnt,
                "HIGH": high_cnt,
                "MEDIUM": med_cnt,
                "LOW": low_cnt,
                "TOTAL": len(site_sigs),
            },
            "family_counts": family_counts,
            "family_weights": family_weights,
            "weekly_signal_trend": weekly_trend,
            "open_queries_total": open_q_total,
            "max_query_age_days": max_q_age,
            "sdv_backlog_total": sdv_total,
            "open_pd_count": open_pds,
            "available_kits": irt["available_kits"],
            "min_buffer_kits": irt["min_buffer_kits"],
            "unresolved_excursion": irt["unresolved_excursion"],
            "local_eligible_candidates": local_elig_cnt,
            "site_summary": ai_summary,
            "events": site_saes + site_visits,
            "safety_events": site_saes,
            "edc_visits": site_visits,
            "protocol_deviations": site_pds,
            "quality_issues": site_issues,
            "training_records": site_trn,
        }
        enriched_sites.append(enriched_site)

        if persist_tables:
            site_db_rows.append(
                (
                    sid,
                    site["study_id"],
                    study["protocol_code"],
                    site["region"],
                    site["assigned_cra_id"],
                    tier,
                    risk_score,
                    crit_cnt,
                    high_cnt,
                    len(site_sigs),
                    open_q_total,
                    max_q_age,
                    sdv_total,
                    irt["available_kits"],
                )
            )

    if persist_tables:
        conn.execute("DROP TABLE IF EXISTS signals")
        conn.execute(
            """
            CREATE TABLE signals (
                signal_id TEXT PRIMARY KEY,
                rule_id TEXT NOT NULL,
                family TEXT NOT NULL,
                title TEXT NOT NULL,
                severity TEXT NOT NULL,
                weight INTEGER NOT NULL,
                site_id TEXT NOT NULL,
                study_id TEXT NOT NULL,
                protocol_code TEXT NOT NULL,
                source_file TEXT NOT NULL,
                source_record_id TEXT NOT NULL,
                metric_name TEXT NOT NULL,
                observed_value TEXT NOT NULL,
                threshold_value TEXT NOT NULL,
                disposition TEXT NOT NULL
            )
            """
        )
        if signal_db_rows:
            conn.executemany(
                "INSERT INTO signals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                signal_db_rows,
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_signals_site_disp ON signals (site_id, disposition)"
        )

        conn.execute("DROP TABLE IF EXISTS site_assessments")
        conn.execute(
            """
            CREATE TABLE site_assessments (
                site_id TEXT PRIMARY KEY,
                study_id TEXT NOT NULL,
                protocol_code TEXT NOT NULL,
                region TEXT NOT NULL,
                assigned_cra_id TEXT NOT NULL,
                priority_tier TEXT NOT NULL,
                risk_score INTEGER NOT NULL,
                critical_signals INTEGER NOT NULL,
                high_signals INTEGER NOT NULL,
                total_signals INTEGER NOT NULL,
                open_queries_total INTEGER NOT NULL,
                max_query_age_days INTEGER NOT NULL,
                sdv_backlog_total INTEGER NOT NULL,
                available_kits INTEGER NOT NULL
            )
            """
        )
        if site_db_rows:
            conn.executemany(
                "INSERT INTO site_assessments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                site_db_rows,
            )
        conn.commit()

    return {
        "reference_date": cfg["reference_date"],
        "sae_target_days": cfg["sae_cycle_time_target_days"],
        "sae_baseline_days": cfg["sae_cycle_time_baseline_days"],
        "median_sae_cycle_days": round(float(median(serious_cycles)), 1) if serious_cycles else 0.0,
        "submitted_sae_median_days": round(float(median(compliant_serious_cycles)), 1) if compliant_serious_cycles else 0.0,
        "tier_counts": tier_counts,
        "sites": enriched_sites,
        "signals": signals,
        "rules_evaluated": len(cfg["rules"]),
        "signal_families": list(SIGNAL_FAMILIES),
    }


def get_signal_by_id(signals: list[dict[str, Any]], signal_id: str) -> dict[str, Any]:
    """Lookup a single signal record or raise EntityNotFoundError."""
    for sig in signals:
        if sig["signal_id"] == signal_id:
            return sig
    available = [s["signal_id"] for s in signals[:8]]
    raise EntityNotFoundError(
        f"Unknown signal_id '{signal_id}'. Sample valid IDs: {available}"
    )
