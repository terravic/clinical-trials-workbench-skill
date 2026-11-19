"""Clinical Trial Patient Selection and Site Eligibility Matching Engine.

Evaluates de-identified synthetic patients and central laboratory biomarker
panels against structured trial inclusion/exclusion rules, produces full
criterion-by-criterion audit trails, classifies candidates into ELIGIBLE,
POTENTIAL_NEEDS_LABS, or INELIGIBLE, and ranks active clinical sites.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from scripts import EntityNotFoundError


def _evaluate_clause(clause: dict[str, Any], lab_row: dict[str, Any]) -> tuple[str, str]:
    field = clause["field"]
    op = clause["op"]
    expected = clause["value"]
    observed = lab_row.get(field)
    if observed is None or str(observed).strip() == "":
        return "MISSING_DATA", f"{field} is missing (requires {op} {expected})"

    if op == "==":
        passed = str(observed).upper() == str(expected).upper()
    elif op == ">=":
        passed = float(observed) >= float(expected)
    elif op == "<=":
        passed = float(observed) <= float(expected)
    elif op == ">":
        passed = float(observed) > float(expected)
    elif op == "<":
        passed = float(observed) < float(expected)
    else:
        raise ValueError(f"Unsupported biomarker operator: {op}")

    status = "PASS" if passed else "FAIL"
    return status, f"{field}={observed} (rule: {op} {expected})"


def _evaluate_biomarker_rule(rule: dict[str, Any], lab_row: dict[str, Any]) -> dict[str, Any]:
    clauses = rule.get("clauses", [])
    logic_type = rule.get("type", "ALL").upper()
    outcomes = [_evaluate_clause(c, lab_row) for c in clauses]
    statuses = [o[0] for o in outcomes]
    details = "; ".join(o[1] for o in outcomes)

    if logic_type == "OR":
        if "PASS" in statuses:
            final_status = "PASS"
        elif "MISSING_DATA" in statuses:
            final_status = "MISSING_DATA"
        else:
            final_status = "FAIL"
    else:
        if "FAIL" in statuses:
            final_status = "FAIL"
        elif "MISSING_DATA" in statuses:
            final_status = "MISSING_DATA"
        else:
            final_status = "PASS"

    return {
        "criterion_id": "BIOMARKER_CRITERIA",
        "category": "INCLUSION",
        "status": final_status,
        "observed": details,
        "requirement": rule.get("description", ""),
    }


def _rank_sites_for_patient(
    patient: dict[str, Any],
    study_id: str,
    sites_for_study: list[dict[str, Any]],
    irt_by_site: dict[str, dict[str, Any]],
    site_risk_scores: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    candidate_ids = {
        s.strip() for s in str(patient["candidate_site_ids"]).split(";") if s.strip()
    }
    rankings: list[dict[str, Any]] = []
    for site in sites_for_study:
        sid = site["site_id"]
        if site["site_status"] != "ACTIVE":
            rankings.append(
                {
                    "site_id": sid,
                    "site_name": site["site_name"],
                    "region": site["region"],
                    "site_status": site["site_status"],
                    "placement_eligible": False,
                    "readiness_score": 0,
                    "disqualification_reason": f"Site status is {site['site_status']}",
                }
            )
            continue

        irt = irt_by_site.get(sid, {})
        if irt.get("unresolved_excursion", 0) == 1:
            rankings.append(
                {
                    "site_id": sid,
                    "site_name": site["site_name"],
                    "region": site["region"],
                    "site_status": site["site_status"],
                    "placement_eligible": False,
                    "readiness_score": 0,
                    "disqualification_reason": "Unresolved IRT cold-chain temperature excursion",
                }
            )
            continue

        score = 0
        notes: list[str] = []
        if sid in candidate_ids:
            score += 40
            notes.append("Direct catchment match")
        elif site["region"] == patient["region"]:
            score += 20
            notes.append("Regional match")

        # Active operational status
        score += 25

        # Supply readiness (20 pts)
        avail_kits = int(irt.get("available_kits", 0))
        min_buf = int(irt.get("min_buffer_kits", 0))
        if avail_kits > min_buf:
            score += 20
            notes.append(f"IP stock healthy ({avail_kits} kits)")
        elif avail_kits > 0:
            score += 8
            notes.append(f"IP stock tight ({avail_kits}/{min_buf} buffer)")
        else:
            notes.append("Zero IP kits on shelf")

        # Enrollment capacity & low operational risk (15 pts)
        remaining_slots = max(0, int(site["target_enrollment"]) - int(site["enrolled_count"]))
        cap_pts = min(8, remaining_slots)
        risk_val = (site_risk_scores or {}).get(sid, 25)
        risk_pts = 7 if risk_val < 35 else (4 if risk_val < 70 else 1)
        score += cap_pts + risk_pts
        notes.append(f"{remaining_slots} open slots")

        rankings.append(
            {
                "site_id": sid,
                "site_name": site["site_name"],
                "region": site["region"],
                "site_status": site["site_status"],
                "placement_eligible": score >= 45,
                "readiness_score": score,
                "notes": "; ".join(notes),
            }
        )

    rankings.sort(key=lambda r: (r["placement_eligible"], r["readiness_score"]), reverse=True)
    return rankings


def evaluate_patient_for_study(
    patient: dict[str, Any],
    labs: dict[str, Any],
    criteria: dict[str, Any],
    sites_for_study: list[dict[str, Any]],
    irt_by_site: dict[str, dict[str, Any]],
    site_risk_scores: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Evaluate a single patient against a single study protocol."""
    checks: list[dict[str, Any]] = []

    # 1. Primary Diagnosis ICD-10 prefix
    allowed_prefixes: list[str] = criteria["allowed_icd10_prefixes"]
    icd = str(patient["primary_icd10"])
    diag_pass = any(icd.startswith(pref) for pref in allowed_prefixes)
    checks.append(
        {
            "criterion_id": "PRIMARY_DIAGNOSIS",
            "category": "INCLUSION",
            "status": "PASS" if diag_pass else "FAIL",
            "observed": icd,
            "requirement": f"ICD-10 prefix in {allowed_prefixes}",
        }
    )

    # 2. Age bounds
    age = int(patient["age_years"])
    age_pass = int(criteria["min_age"]) <= age <= int(criteria["max_age"])
    checks.append(
        {
            "criterion_id": "AGE_WINDOW",
            "category": "INCLUSION",
            "status": "PASS" if age_pass else "FAIL",
            "observed": f"{age} years",
            "requirement": f"{criteria['min_age']}-{criteria['max_age']} years",
        }
    )

    # 3. ECOG performance status
    ecog = int(patient["ecog_status"])
    ecog_pass = ecog <= int(criteria["max_ecog"])
    checks.append(
        {
            "criterion_id": "ECOG_PERFORMANCE",
            "category": "INCLUSION",
            "status": "PASS" if ecog_pass else "FAIL",
            "observed": f"ECOG {ecog}",
            "requirement": f"ECOG <= {criteria['max_ecog']}",
        }
    )

    # 4. Prior therapy lines & washout
    prior = int(patient["prior_lines_therapy"])
    washout = int(patient["washout_days"])
    prior_pass = prior <= int(criteria["max_prior_lines"])
    washout_pass = True if prior == 0 else (washout >= int(criteria["min_washout_days"]))
    checks.append(
        {
            "criterion_id": "PRIOR_THERAPY_WASHOUT",
            "category": "INCLUSION",
            "status": "PASS" if (prior_pass and washout_pass) else "FAIL",
            "observed": f"{prior} prior lines; {washout}d washout",
            "requirement": f"<= {criteria['max_prior_lines']} lines; >= {criteria['min_washout_days']}d washout",
        }
    )

    # 5. Pre-screening consent
    consent_ok = int(patient["pre_screening_consent"]) == 1
    checks.append(
        {
            "criterion_id": "PRE_SCREENING_CONSENT",
            "category": "INCLUSION",
            "status": "PASS" if consent_ok else "FAIL",
            "observed": "Authorized" if consent_ok else "Declined/Pending",
            "requirement": "Authorized registry consent (1)",
        }
    )

    # 6. Exclusionary comorbidities
    excl_set = set(criteria["exclusion_conditions"])
    pat_comorb = {
        c.strip()
        for c in str(patient["comorbidities"]).split(";")
        if c.strip() and c.strip() != "NONE"
    }
    triggered_excl = sorted(excl_set.intersection(pat_comorb))
    checks.append(
        {
            "criterion_id": "EXCLUSION_COMORBIDITIES",
            "category": "EXCLUSION",
            "status": "FAIL" if triggered_excl else "PASS",
            "observed": ", ".join(triggered_excl) if triggered_excl else "None triggered",
            "requirement": f"Exclude: {', '.join(criteria['exclusion_conditions'])}",
        }
    )

    # 7. Disease Biomarker Rule
    checks.append(_evaluate_biomarker_rule(criteria["biomarker_rule"], labs))

    # 8. Baseline Organ Function Labs
    thresholds = criteria["lab_thresholds"]
    organ_specs = [
        ("ORGAN_EGFR", "egfr_ml_min", ">=", thresholds["min_egfr"], "mL/min"),
        ("ORGAN_ANC", "anc_k_ul", ">=", thresholds["min_anc"], "k/uL"),
        ("ORGAN_HEMOGLOBIN", "hemoglobin_g_dl", ">=", thresholds["min_hb"], "g/dL"),
        ("ORGAN_PLATELETS", "platelets_k_ul", ">=", thresholds["min_platelets"], "k/uL"),
        ("ORGAN_BILIRUBIN", "bilirubin_mg_dl", "<=", thresholds["max_bilirubin"], "mg/dL"),
        ("ORGAN_HBA1C", "hba1c_pct", "<=", thresholds["max_hba1c"], "%"),
    ]
    for crit_id, field_key, op_sym, limit_val, unit in organ_specs:
        obs_val = labs.get(field_key)
        if obs_val is None or str(obs_val).strip() == "":
            c_status = "MISSING_DATA"
            obs_str = "Missing assay"
        else:
            fval = float(obs_val)
            passed = (fval >= float(limit_val)) if op_sym == ">=" else (fval <= float(limit_val))
            c_status = "PASS" if passed else "FAIL"
            obs_str = f"{fval} {unit}"
        checks.append(
            {
                "criterion_id": crit_id,
                "category": "INCLUSION",
                "status": c_status,
                "observed": obs_str,
                "requirement": f"{field_key} {op_sym} {limit_val} {unit}",
            }
        )

    # 9. Specimen Recency (<= 30 days)
    coll_days = labs.get("collection_days_ago")
    if coll_days is None:
        rec_status = "MISSING_DATA"
        rec_obs = "No collection date"
    elif int(coll_days) > 30:
        rec_status = "MISSING_DATA"
        rec_obs = f"Stale panel ({coll_days} days old)"
    else:
        rec_status = "PASS"
        rec_obs = f"{coll_days} days ago"
    checks.append(
        {
            "criterion_id": "SPECIMEN_RECENCY",
            "category": "INCLUSION",
            "status": rec_status,
            "observed": rec_obs,
            "requirement": "Central lab panel collected <= 30 days ago",
        }
    )

    fail_items = [c["criterion_id"] for c in checks if c["status"] == "FAIL"]
    missing_items = [c["criterion_id"] for c in checks if c["status"] == "MISSING_DATA"]

    if fail_items:
        classification = "INELIGIBLE"
    elif missing_items:
        classification = "POTENTIAL_NEEDS_LABS"
    else:
        classification = "ELIGIBLE"

    # Composite Match Score (0-100)
    diag_pts = 40.0 if diag_pass else 0.0
    clin_Ids = {
        "AGE_WINDOW",
        "ECOG_PERFORMANCE",
        "PRIOR_THERAPY_WASHOUT",
        "PRE_SCREENING_CONSENT",
        "EXCLUSION_COMORBIDITIES",
    }
    clin_passed = sum(1 for c in checks if c["criterion_id"] in clin_Ids and c["status"] == "PASS")
    clin_pts = (clin_passed / len(clin_Ids)) * 30.0

    lab_checks = [c for c in checks if c["criterion_id"] not in clin_Ids and c["criterion_id"] != "PRIMARY_DIAGNOSIS"]
    lab_credit = sum(
        1.0 if c["status"] == "PASS" else (0.5 if c["status"] == "MISSING_DATA" else 0.0)
        for c in lab_checks
    )
    lab_pts = (lab_credit / len(lab_checks)) * 30.0
    raw_score = round(diag_pts + clin_pts + lab_pts)
    match_score = min(49, raw_score) if classification == "INELIGIBLE" else raw_score

    site_rankings = _rank_sites_for_patient(
        patient, criteria["study_id"], sites_for_study, irt_by_site, site_risk_scores
    )
    best_site = next((r for r in site_rankings if r["placement_eligible"]), None)

    return {
        "patient_id": patient["patient_id"],
        "study_id": criteria["study_id"],
        "protocol_code": criteria["protocol_code"],
        "classification": classification,
        "match_score": int(match_score),
        "failed_criteria": fail_items,
        "missing_criteria": missing_items,
        "recommended_site_id": best_site["site_id"] if best_site and classification != "INELIGIBLE" else None,
        "recommended_site_score": best_site["readiness_score"] if best_site and classification != "INELIGIBLE" else 0,
        "site_rankings": site_rankings,
        "criteria_audit": checks,
    }


def run_trial_selection(
    conn: sqlite3.Connection,
    study_id: str | None = None,
    status_filter: str | None = None,
    site_id_filter: str | None = None,
    region_filter: str | None = None,
    min_score: int | None = None,
    site_risk_scores: dict[str, int] | None = None,
    persist_table: bool = True,
) -> dict[str, Any]:
    """Evaluate synthetic candidate pool across studies and persist SQLite summary table."""
    criteria_rows = conn.execute("SELECT * FROM eligibility_criteria ORDER BY study_id").fetchall()
    criteria_list = [
        {
            "study_id": r["study_id"],
            "protocol_code": r["protocol_code"],
            "min_age": r["min_age"],
            "max_age": r["max_age"],
            "allowed_icd10_prefixes": json.loads(r["allowed_icd10_json"]),
            "max_ecog": r["max_ecog"],
            "max_prior_lines": r["max_prior_lines"],
            "min_washout_days": r["min_washout_days"],
            "biomarker_rule": json.loads(r["biomarker_rule_json"]),
            "lab_thresholds": json.loads(r["lab_thresholds_json"]),
            "exclusion_conditions": json.loads(r["exclusion_conditions_json"]),
        }
        for r in criteria_rows
    ]
    if study_id:
        valid_ids = {c["study_id"] for c in criteria_list}
        if study_id not in valid_ids:
            raise EntityNotFoundError(
                f"Unknown study_id '{study_id}'. Valid studies: {sorted(valid_ids)}"
            )

    sites_all = [dict(r) for r in conn.execute("SELECT * FROM sites ORDER BY site_id").fetchall()]
    sites_by_study: dict[str, list[dict[str, Any]]] = {}
    for s in sites_all:
        sites_by_study.setdefault(s["study_id"], []).append(s)

    irt_by_site = {
        r["site_id"]: dict(r)
        for r in conn.execute("SELECT * FROM irt_inventory").fetchall()
    }
    patients = [dict(r) for r in conn.execute("SELECT * FROM patients ORDER BY patient_id").fetchall()]
    labs_by_pid = {
        r["patient_id"]: dict(r)
        for r in conn.execute("SELECT * FROM patient_labs").fetchall()
    }

    if persist_table:
        conn.execute("DROP TABLE IF EXISTS eligibility_assessments")
        conn.execute(
            """
            CREATE TABLE eligibility_assessments (
                patient_id TEXT NOT NULL,
                study_id TEXT NOT NULL,
                protocol_code TEXT NOT NULL,
                classification TEXT NOT NULL,
                match_score INTEGER NOT NULL,
                recommended_site_id TEXT,
                recommended_site_score INTEGER NOT NULL,
                failed_count INTEGER NOT NULL,
                missing_count INTEGER NOT NULL,
                PRIMARY KEY (patient_id, study_id)
            )
            """
        )

    all_evaluations: list[dict[str, Any]] = []
    best_per_patient: dict[str, dict[str, Any]] = {}
    summary_by_study: dict[str, dict[str, Any]] = {
        c["study_id"]: {
            "study_id": c["study_id"],
            "protocol_code": c["protocol_code"],
            "ELIGIBLE": 0,
            "POTENTIAL_NEEDS_LABS": 0,
            "INELIGIBLE": 0,
        }
        for c in criteria_list
    }

    for pat in patients:
        pid = pat["patient_id"]
        lab = labs_by_pid.get(pid, {})
        for crit in criteria_list:
            ev = evaluate_patient_for_study(
                pat,
                lab,
                crit,
                sites_by_study.get(crit["study_id"], []),
                irt_by_site,
                site_risk_scores,
            )
            summary_by_study[crit["study_id"]][ev["classification"]] += 1
            if persist_table:
                conn.execute(
                    """
                    INSERT INTO eligibility_assessments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pid,
                        ev["study_id"],
                        ev["protocol_code"],
                        ev["classification"],
                        ev["match_score"],
                        ev["recommended_site_id"],
                        ev["recommended_site_score"],
                        len(ev["failed_criteria"]),
                        len(ev["missing_criteria"]),
                    ),
                )
            all_evaluations.append(ev)

            # Track patient's primary/best study match (aligning diagnosis first, then score)
            prev = best_per_patient.get(pid)
            rank_tuple = (
                2 if ev["classification"] == "ELIGIBLE" else (1 if ev["classification"] == "POTENTIAL_NEEDS_LABS" else 0),
                ev["match_score"],
            )
            if prev is None or rank_tuple > prev["_rank"]:
                best_per_patient[pid] = {**ev, "_rank": rank_tuple}

    if persist_table:
        conn.commit()

    # Apply requested filters on evaluations
    filtered_evals = []
    for ev in all_evaluations:
        pat = next(p for p in patients if p["patient_id"] == ev["patient_id"])
        if study_id and ev["study_id"] != study_id:
            continue
        if status_filter and ev["classification"] != status_filter.upper():
            continue
        if site_id_filter and ev["recommended_site_id"] != site_id_filter:
            continue
        if region_filter and pat["region"] != region_filter.upper():
            continue
        if min_score is not None and ev["match_score"] < min_score:
            continue
        # Only emit diagnostically aligned or explicitly filtered study rows when unfiltered
        if study_id is None and status_filter is None:
            if best_per_patient[ev["patient_id"]]["study_id"] != ev["study_id"]:
                continue
        filtered_evals.append(ev)

    enriched_patients: list[dict[str, Any]] = []
    for pat in patients:
        pid = pat["patient_id"]
        lab = labs_by_pid.get(pid, {})
        best = best_per_patient[pid]
        enriched_patients.append(
            {
                **pat,
                "labs": lab,
                "primary_match_study_id": best["study_id"],
                "primary_match_protocol": best["protocol_code"],
                "primary_classification": best["classification"],
                "primary_match_score": best["match_score"],
                "recommended_site_id": best["recommended_site_id"],
                "missing_criteria": best["missing_criteria"],
                "failed_criteria": best["failed_criteria"],
                "criteria_audit": best["criteria_audit"],
            }
        )

    return {
        "patients": enriched_patients,
        "evaluations": filtered_evals,
        "summary_by_study": list(summary_by_study.values()),
    }
