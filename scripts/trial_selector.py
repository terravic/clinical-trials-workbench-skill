"""Clinical Trial Patient Selection and Site Eligibility Matching Engine.

Evaluates de-identified synthetic patients and central laboratory biomarker
panels against structured trial inclusion/exclusion rules, produces full
criterion-by-criterion audit trails, classifies candidates into ELIGIBLE,
POTENTIAL_NEEDS_LABS, or INELIGIBLE, and ranks active clinical sites.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from typing import Any

from scripts import EntityNotFoundError


@dataclass(frozen=True)
class _CompiledClause:
    field: str
    op: str
    expected: Any
    expected_cmp: Any
    missing_msg: str


@dataclass(frozen=True)
class _CompiledCriteria:
    study_id: str
    protocol_code: str
    min_age: int
    max_age: int
    allowed_prefixes: tuple[str, ...]
    diag_req: str
    age_req: str
    max_ecog: int
    ecog_req: str
    max_prior_lines: int
    min_washout_days: int
    prior_req: str
    excl_set: frozenset[str]
    excl_req: str
    biomarker_logic_or: bool
    biomarker_desc: str
    biomarker_clauses: tuple[_CompiledClause, ...]
    organ_specs: tuple[tuple[str, str, bool, float, str, str], ...]


@dataclass(frozen=True)
class _PrecomputedSite:
    site_id: str
    site_name: str
    region: str
    site_status: str
    disqualified_entry: dict[str, Any] | None
    base_score: int
    suffix_notes: str


@dataclass
class SelectionPrecompute:
    """Reusable Pass-1 criterion evaluation state across all (patient, study) pairs."""

    criteria_list: list[_CompiledCriteria]
    valid_study_ids: frozenset[str]
    sites_by_study: dict[str, list[dict[str, Any]]]
    irt_by_site: dict[str, dict[str, Any]]
    patients: list[dict[str, Any]]
    patients_by_id: dict[str, dict[str, Any]]
    patient_placement_meta: list[tuple[frozenset[str], str]]
    labs_by_pid: dict[str, dict[str, Any]]
    base_evals: list[tuple[int, dict[str, Any]]]
    summary_by_study: dict[str, dict[str, Any]]
    local_elig_by_site: dict[str, int]


def _compile_criteria(criteria: dict[str, Any]) -> _CompiledCriteria:
    allowed_list: list[str] = list(criteria["allowed_icd10_prefixes"])
    min_age = int(criteria["min_age"])
    max_age = int(criteria["max_age"])
    max_ecog = int(criteria["max_ecog"])
    max_prior = int(criteria["max_prior_lines"])
    min_washout = int(criteria["min_washout_days"])
    excl_list = criteria["exclusion_conditions"]

    bio_rule = criteria["biomarker_rule"]
    compiled_clauses: list[_CompiledClause] = []
    for c in bio_rule.get("clauses", []):
        field = c["field"]
        op = c["op"]
        expected = c["value"]
        if op == "==":
            expected_cmp: Any = str(expected).upper()
        elif op in (">=", "<=", ">", "<"):
            expected_cmp = float(expected)
        else:
            expected_cmp = expected
        compiled_clauses.append(
            _CompiledClause(
                field=field,
                op=op,
                expected=expected,
                expected_cmp=expected_cmp,
                missing_msg=f"{field} is missing (requires {op} {expected})",
            )
        )

    thresholds = criteria["lab_thresholds"]
    raw_organ_specs = (
        ("ORGAN_EGFR", "egfr_ml_min", ">=", thresholds["min_egfr"], "mL/min"),
        ("ORGAN_ANC", "anc_k_ul", ">=", thresholds["min_anc"], "k/uL"),
        ("ORGAN_HEMOGLOBIN", "hemoglobin_g_dl", ">=", thresholds["min_hb"], "g/dL"),
        ("ORGAN_PLATELETS", "platelets_k_ul", ">=", thresholds["min_platelets"], "k/uL"),
        ("ORGAN_BILIRUBIN", "bilirubin_mg_dl", "<=", thresholds["max_bilirubin"], "mg/dL"),
        ("ORGAN_HBA1C", "hba1c_pct", "<=", thresholds["max_hba1c"], "%"),
    )
    organ_specs = tuple(
        (
            crit_id,
            field_key,
            op_sym == ">=",
            float(limit_val),
            unit,
            f"{field_key} {op_sym} {limit_val} {unit}",
        )
        for crit_id, field_key, op_sym, limit_val, unit in raw_organ_specs
    )

    return _CompiledCriteria(
        study_id=criteria["study_id"],
        protocol_code=criteria["protocol_code"],
        min_age=min_age,
        max_age=max_age,
        allowed_prefixes=tuple(allowed_list),
        diag_req=f"ICD-10 prefix in {allowed_list}",
        age_req=f"{min_age}-{max_age} years",
        max_ecog=max_ecog,
        ecog_req=f"ECOG <= {max_ecog}",
        max_prior_lines=max_prior,
        min_washout_days=min_washout,
        prior_req=f"<= {max_prior} lines; >= {min_washout}d washout",
        excl_set=frozenset(excl_list),
        excl_req=f"Exclude: {', '.join(excl_list)}",
        biomarker_logic_or=(bio_rule.get("type", "ALL").upper() == "OR"),
        biomarker_desc=bio_rule.get("description", ""),
        biomarker_clauses=tuple(compiled_clauses),
        organ_specs=organ_specs,
    )


def _parse_candidate_site_ids(raw_ids: Any) -> frozenset[str]:
    if not raw_ids:
        return frozenset()
    text = raw_ids if isinstance(raw_ids, str) else str(raw_ids)
    return frozenset(s.strip() for s in text.split(";") if s.strip())


def _parse_comorbidities(raw_comorb: Any) -> frozenset[str]:
    if not raw_comorb or raw_comorb == "NONE":
        return frozenset()
    text = raw_comorb if isinstance(raw_comorb, str) else str(raw_comorb)
    return frozenset(c.strip() for c in text.split(";") if c.strip() and c.strip() != "NONE")


def _precompute_study_sites(
    sites_for_study: list[dict[str, Any]],
    irt_by_site: dict[str, dict[str, Any]],
    site_risk_scores: dict[str, int] | None = None,
) -> tuple[_PrecomputedSite, ...]:
    """Precompute patient-independent readiness components once per study."""
    risk_map = site_risk_scores or {}
    precomputed: list[_PrecomputedSite] = []
    for site in sites_for_study:
        sid = site["site_id"]
        sname = site["site_name"]
        sreg = site["region"]
        sstatus = site["site_status"]

        if sstatus != "ACTIVE":
            precomputed.append(
                _PrecomputedSite(
                    site_id=sid,
                    site_name=sname,
                    region=sreg,
                    site_status=sstatus,
                    disqualified_entry={
                        "site_id": sid,
                        "site_name": sname,
                        "region": sreg,
                        "site_status": sstatus,
                        "placement_eligible": False,
                        "readiness_score": 0,
                        "disqualification_reason": f"Site status is {sstatus}",
                    },
                    base_score=0,
                    suffix_notes="",
                )
            )
            continue

        irt = irt_by_site.get(sid, {})
        if irt.get("unresolved_excursion", 0) == 1:
            precomputed.append(
                _PrecomputedSite(
                    site_id=sid,
                    site_name=sname,
                    region=sreg,
                    site_status=sstatus,
                    disqualified_entry={
                        "site_id": sid,
                        "site_name": sname,
                        "region": sreg,
                        "site_status": sstatus,
                        "placement_eligible": False,
                        "readiness_score": 0,
                        "disqualification_reason": "Unresolved IRT cold-chain temperature excursion",
                    },
                    base_score=0,
                    suffix_notes="",
                )
            )
            continue

        # Active operational status (25 pts)
        base_score = 25
        avail_kits = int(irt.get("available_kits", 0))
        min_buf = int(irt.get("min_buffer_kits", 0))
        if avail_kits > min_buf:
            base_score += 20
            supply_note = f"IP stock healthy ({avail_kits} kits)"
        elif avail_kits > 0:
            base_score += 8
            supply_note = f"IP stock tight ({avail_kits}/{min_buf} buffer)"
        else:
            supply_note = "Zero IP kits on shelf"

        remaining_slots = max(0, int(site["target_enrollment"]) - int(site["enrolled_count"]))
        cap_pts = 8 if remaining_slots > 8 else remaining_slots
        risk_val = risk_map.get(sid, 25)
        risk_pts = 7 if risk_val < 35 else (4 if risk_val < 70 else 1)
        base_score += cap_pts + risk_pts

        precomputed.append(
            _PrecomputedSite(
                site_id=sid,
                site_name=sname,
                region=sreg,
                site_status=sstatus,
                disqualified_entry=None,
                base_score=base_score,
                suffix_notes=f"{supply_note}; {remaining_slots} open slots",
            )
        )
    return tuple(precomputed)


def _find_best_site_fast(
    candidate_ids: frozenset[str],
    pat_region: str,
    precomputed_sites: tuple[_PrecomputedSite, ...],
) -> tuple[str | None, int]:
    """Return (best_site_id, best_score) matching stable-sort reverse tie-breaking without allocating dicts."""
    best_sid: str | None = None
    best_score = -1
    for ps in precomputed_sites:
        if ps.disqualified_entry is not None:
            continue
        score = ps.base_score
        if ps.site_id in candidate_ids:
            score += 40
        elif ps.region == pat_region:
            score += 20
        if score >= 45 and score > best_score:
            best_score = score
            best_sid = ps.site_id
    return (best_sid, best_score) if best_sid is not None else (None, 0)


def _rank_precomputed_sites(
    candidate_ids: frozenset[str],
    pat_region: str,
    precomputed_sites: tuple[_PrecomputedSite, ...],
) -> list[dict[str, Any]]:
    rankings: list[dict[str, Any]] = []
    for ps in precomputed_sites:
        if ps.disqualified_entry is not None:
            rankings.append(dict(ps.disqualified_entry))
            continue

        score = ps.base_score
        if ps.site_id in candidate_ids:
            score += 40
            notes = f"Direct catchment match; {ps.suffix_notes}"
        elif ps.region == pat_region:
            score += 20
            notes = f"Regional match; {ps.suffix_notes}"
        else:
            notes = ps.suffix_notes

        rankings.append(
            {
                "site_id": ps.site_id,
                "site_name": ps.site_name,
                "region": ps.region,
                "site_status": ps.site_status,
                "placement_eligible": score >= 45,
                "readiness_score": score,
                "notes": notes,
            }
        )

    rankings.sort(key=lambda r: (r["placement_eligible"], r["readiness_score"]), reverse=True)
    return rankings


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


def _evaluate_compiled_biomarker(
    compiled: _CompiledCriteria, lab_row: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    has_pass = False
    has_fail = False
    has_missing = False
    detail_parts: list[str] = []

    for cl in compiled.biomarker_clauses:
        observed = lab_row.get(cl.field)
        if observed is None or (isinstance(observed, str) and observed.strip() == ""):
            has_missing = True
            detail_parts.append(cl.missing_msg)
            continue

        op = cl.op
        if op == "==":
            obs_str = observed if isinstance(observed, str) else str(observed)
            passed = obs_str.upper() == cl.expected_cmp
        else:
            fval = observed if isinstance(observed, float) else float(observed)
            if op == ">=":
                passed = fval >= cl.expected_cmp
            elif op == "<=":
                passed = fval <= cl.expected_cmp
            elif op == ">":
                passed = fval > cl.expected_cmp
            elif op == "<":
                passed = fval < cl.expected_cmp
            else:
                raise ValueError(f"Unsupported biomarker operator: {op}")

        if passed:
            has_pass = True
        else:
            has_fail = True
        detail_parts.append(f"{cl.field}={observed} (rule: {op} {cl.expected})")

    if compiled.biomarker_logic_or:
        if has_pass:
            final_status = "PASS"
        elif has_missing:
            final_status = "MISSING_DATA"
        else:
            final_status = "FAIL"
    else:
        if has_fail:
            final_status = "FAIL"
        elif has_missing:
            final_status = "MISSING_DATA"
        else:
            final_status = "PASS"

    details = detail_parts[0] if len(detail_parts) == 1 else "; ".join(detail_parts)
    return final_status, {
        "criterion_id": "BIOMARKER_CRITERIA",
        "category": "INCLUSION",
        "status": final_status,
        "observed": details,
        "requirement": compiled.biomarker_desc,
    }


def _evaluate_criteria_core(
    patient: dict[str, Any],
    labs: dict[str, Any],
    compiled: _CompiledCriteria,
    pat_comorb: frozenset[str],
) -> dict[str, Any]:
    """Evaluate all 14 protocol inclusion/exclusion criteria without site placement."""
    checks: list[dict[str, Any]] = []
    fail_items: list[str] = []
    missing_items: list[str] = []

    # 1. Primary Diagnosis ICD-10 prefix
    raw_icd = patient["primary_icd10"]
    icd = raw_icd if isinstance(raw_icd, str) else str(raw_icd)
    diag_pass = icd.startswith(compiled.allowed_prefixes)
    if not diag_pass:
        fail_items.append("PRIMARY_DIAGNOSIS")
    checks.append(
        {
            "criterion_id": "PRIMARY_DIAGNOSIS",
            "category": "INCLUSION",
            "status": "PASS" if diag_pass else "FAIL",
            "observed": icd,
            "requirement": compiled.diag_req,
        }
    )

    clin_passed = 0

    # 2. Age bounds
    raw_age = patient["age_years"]
    age = raw_age if isinstance(raw_age, int) else int(raw_age)
    age_pass = compiled.min_age <= age <= compiled.max_age
    if age_pass:
        clin_passed += 1
    else:
        fail_items.append("AGE_WINDOW")
    checks.append(
        {
            "criterion_id": "AGE_WINDOW",
            "category": "INCLUSION",
            "status": "PASS" if age_pass else "FAIL",
            "observed": f"{age} years",
            "requirement": compiled.age_req,
        }
    )

    # 3. ECOG performance status
    raw_ecog = patient["ecog_status"]
    ecog = raw_ecog if isinstance(raw_ecog, int) else int(raw_ecog)
    ecog_pass = ecog <= compiled.max_ecog
    if ecog_pass:
        clin_passed += 1
    else:
        fail_items.append("ECOG_PERFORMANCE")
    checks.append(
        {
            "criterion_id": "ECOG_PERFORMANCE",
            "category": "INCLUSION",
            "status": "PASS" if ecog_pass else "FAIL",
            "observed": f"ECOG {ecog}",
            "requirement": compiled.ecog_req,
        }
    )

    # 4. Prior therapy lines & washout
    raw_prior = patient["prior_lines_therapy"]
    prior = raw_prior if isinstance(raw_prior, int) else int(raw_prior)
    raw_wash = patient["washout_days"]
    washout = raw_wash if isinstance(raw_wash, int) else int(raw_wash)
    prior_ok = (prior <= compiled.max_prior_lines) and (
        prior == 0 or washout >= compiled.min_washout_days
    )
    if prior_ok:
        clin_passed += 1
    else:
        fail_items.append("PRIOR_THERAPY_WASHOUT")
    checks.append(
        {
            "criterion_id": "PRIOR_THERAPY_WASHOUT",
            "category": "INCLUSION",
            "status": "PASS" if prior_ok else "FAIL",
            "observed": f"{prior} prior lines; {washout}d washout",
            "requirement": compiled.prior_req,
        }
    )

    # 5. Pre-screening consent
    raw_consent = patient["pre_screening_consent"]
    consent_ok = (raw_consent if isinstance(raw_consent, int) else int(raw_consent)) == 1
    if consent_ok:
        clin_passed += 1
    else:
        fail_items.append("PRE_SCREENING_CONSENT")
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
    if pat_comorb and not compiled.excl_set.isdisjoint(pat_comorb):
        triggered_excl = sorted(compiled.excl_set.intersection(pat_comorb))
        fail_items.append("EXCLUSION_COMORBIDITIES")
        excl_status = "FAIL"
        excl_obs = ", ".join(triggered_excl)
    else:
        clin_passed += 1
        excl_status = "PASS"
        excl_obs = "None triggered"
    checks.append(
        {
            "criterion_id": "EXCLUSION_COMORBIDITIES",
            "category": "EXCLUSION",
            "status": excl_status,
            "observed": excl_obs,
            "requirement": compiled.excl_req,
        }
    )

    lab_credit = 0.0

    # 7. Disease Biomarker Rule
    bio_status, bio_check = _evaluate_compiled_biomarker(compiled, labs)
    if bio_status == "PASS":
        lab_credit += 1.0
    elif bio_status == "MISSING_DATA":
        lab_credit += 0.5
        missing_items.append("BIOMARKER_CRITERIA")
    else:
        fail_items.append("BIOMARKER_CRITERIA")
    checks.append(bio_check)

    # 8. Baseline Organ Function Labs
    for crit_id, field_key, is_gte, limit_val, unit, req_str in compiled.organ_specs:
        obs_val = labs.get(field_key)
        if obs_val is None or (isinstance(obs_val, str) and obs_val.strip() == ""):
            c_status = "MISSING_DATA"
            obs_str = "Missing assay"
            lab_credit += 0.5
            missing_items.append(crit_id)
        else:
            fval = obs_val if isinstance(obs_val, float) else float(obs_val)
            passed = (fval >= limit_val) if is_gte else (fval <= limit_val)
            if passed:
                c_status = "PASS"
                lab_credit += 1.0
            else:
                c_status = "FAIL"
                fail_items.append(crit_id)
            obs_str = f"{fval} {unit}"
        checks.append(
            {
                "criterion_id": crit_id,
                "category": "INCLUSION",
                "status": c_status,
                "observed": obs_str,
                "requirement": req_str,
            }
        )

    # 9. Specimen Recency (<= 30 days)
    coll_days = labs.get("collection_days_ago")
    if coll_days is None:
        rec_status = "MISSING_DATA"
        rec_obs = "No collection date"
        lab_credit += 0.5
        missing_items.append("SPECIMEN_RECENCY")
    elif (coll_days if isinstance(coll_days, int) else int(coll_days)) > 30:
        rec_status = "MISSING_DATA"
        rec_obs = f"Stale panel ({coll_days} days old)"
        lab_credit += 0.5
        missing_items.append("SPECIMEN_RECENCY")
    else:
        rec_status = "PASS"
        rec_obs = f"{coll_days} days ago"
        lab_credit += 1.0
    checks.append(
        {
            "criterion_id": "SPECIMEN_RECENCY",
            "category": "INCLUSION",
            "status": rec_status,
            "observed": rec_obs,
            "requirement": "Central lab panel collected <= 30 days ago",
        }
    )

    if fail_items:
        classification = "INELIGIBLE"
    elif missing_items:
        classification = "POTENTIAL_NEEDS_LABS"
    else:
        classification = "ELIGIBLE"

    # Composite Match Score (0-100): 40 diag + 30 clinical (5 checks) + 30 lab (8 checks)
    raw_score = round((40.0 if diag_pass else 0.0) + (clin_passed / 5.0) * 30.0 + (lab_credit / 8.0) * 30.0)
    match_score = min(49, raw_score) if classification == "INELIGIBLE" else raw_score

    return {
        "patient_id": patient["patient_id"],
        "study_id": compiled.study_id,
        "protocol_code": compiled.protocol_code,
        "classification": classification,
        "match_score": int(match_score),
        "failed_criteria": fail_items,
        "missing_criteria": missing_items,
        "criteria_audit": checks,
    }


def _rank_sites_for_patient(
    patient: dict[str, Any],
    study_id: str,
    sites_for_study: list[dict[str, Any]],
    irt_by_site: dict[str, dict[str, Any]],
    site_risk_scores: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    del study_id  # Retained in signature for compatibility
    precomputed = _precompute_study_sites(sites_for_study, irt_by_site, site_risk_scores)
    return _rank_precomputed_sites(
        _parse_candidate_site_ids(patient["candidate_site_ids"]),
        patient["region"],
        precomputed,
    )


def evaluate_patient_for_study(
    patient: dict[str, Any],
    labs: dict[str, Any],
    criteria: dict[str, Any],
    sites_for_study: list[dict[str, Any]],
    irt_by_site: dict[str, dict[str, Any]],
    site_risk_scores: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Evaluate a single patient against a single study protocol."""
    compiled = _compile_criteria(criteria)
    pat_comorb = _parse_comorbidities(patient["comorbidities"])
    base = _evaluate_criteria_core(patient, labs, compiled, pat_comorb)

    site_rankings = _rank_sites_for_patient(
        patient, compiled.study_id, sites_for_study, irt_by_site, site_risk_scores
    )
    best_site = next((r for r in site_rankings if r["placement_eligible"]), None)
    not_ineligible = base["classification"] != "INELIGIBLE"

    base["recommended_site_id"] = best_site["site_id"] if best_site and not_ineligible else None
    base["recommended_site_score"] = best_site["readiness_score"] if best_site and not_ineligible else 0
    base["site_rankings"] = site_rankings
    return base


def evaluate_selection_criteria_once(conn: sqlite3.Connection) -> SelectionPrecompute:
    """Load tables once, evaluate all 14 protocol criteria once, and compute Pass-1 local site counts."""
    criteria_rows = conn.execute("SELECT * FROM eligibility_criteria ORDER BY study_id").fetchall()
    criteria_list = [
        _compile_criteria(
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
        )
        for r in criteria_rows
    ]
    valid_study_ids = frozenset(c.study_id for c in criteria_list)

    sites_all = [dict(r) for r in conn.execute("SELECT * FROM sites ORDER BY site_id").fetchall()]
    sites_by_study: dict[str, list[dict[str, Any]]] = {}
    for s in sites_all:
        sites_by_study.setdefault(s["study_id"], []).append(s)

    irt_by_site = {
        r["site_id"]: dict(r)
        for r in conn.execute("SELECT * FROM irt_inventory").fetchall()
    }
    patients = [dict(r) for r in conn.execute("SELECT * FROM patients ORDER BY patient_id").fetchall()]
    patients_by_id = {p["patient_id"]: p for p in patients}
    labs_by_pid = {
        r["patient_id"]: dict(r)
        for r in conn.execute("SELECT * FROM patient_labs").fetchall()
    }

    # Precompute Pass-1 site readiness (default risk_val=25 -> risk_pts=7) for SR-ENR-05 counts
    pass1_sites_by_study = {
        c.study_id: _precompute_study_sites(sites_by_study.get(c.study_id, []), irt_by_site, None)
        for c in criteria_list
    }

    patient_placement_meta: list[tuple[frozenset[str], str]] = []
    base_evals: list[tuple[int, dict[str, Any]]] = []
    summary_by_study: dict[str, dict[str, Any]] = {
        c.study_id: {
            "study_id": c.study_id,
            "protocol_code": c.protocol_code,
            "ELIGIBLE": 0,
            "POTENTIAL_NEEDS_LABS": 0,
            "INELIGIBLE": 0,
        }
        for c in criteria_list
    }
    local_elig_by_site: dict[str, int] = {}

    for pat_idx, pat in enumerate(patients):
        pid = pat["patient_id"]
        lab = labs_by_pid.get(pid, {})
        cand_ids = _parse_candidate_site_ids(pat["candidate_site_ids"])
        pat_region = pat["region"]
        pat_comorb = _parse_comorbidities(pat["comorbidities"])
        patient_placement_meta.append((cand_ids, pat_region))

        for compiled in criteria_list:
            base_ev = _evaluate_criteria_core(pat, lab, compiled, pat_comorb)
            cls_name = base_ev["classification"]
            summary_by_study[compiled.study_id][cls_name] += 1
            if cls_name == "ELIGIBLE":
                best_sid, _ = _find_best_site_fast(
                    cand_ids, pat_region, pass1_sites_by_study[compiled.study_id]
                )
                if best_sid is not None:
                    local_elig_by_site[best_sid] = local_elig_by_site.get(best_sid, 0) + 1
            base_evals.append((pat_idx, base_ev))

    return SelectionPrecompute(
        criteria_list=criteria_list,
        valid_study_ids=valid_study_ids,
        sites_by_study=sites_by_study,
        irt_by_site=irt_by_site,
        patients=patients,
        patients_by_id=patients_by_id,
        patient_placement_meta=patient_placement_meta,
        labs_by_pid=labs_by_pid,
        base_evals=base_evals,
        summary_by_study=summary_by_study,
        local_elig_by_site=local_elig_by_site,
    )


def filter_selection_evaluations(
    all_evaluations: list[dict[str, Any]],
    patients_by_id: dict[str, dict[str, Any]],
    best_study_by_patient: dict[str, str],
    valid_study_ids: frozenset[str],
    study_id: str | None = None,
    status_filter: str | None = None,
    site_id_filter: str | None = None,
    region_filter: str | None = None,
    min_score: int | None = None,
) -> list[dict[str, Any]]:
    """Filter precomputed evaluations in O(P*S) with O(1) patient lookups."""
    if study_id and study_id not in valid_study_ids:
        raise EntityNotFoundError(
            f"Unknown study_id '{study_id}'. Valid studies: {sorted(valid_study_ids)}"
        )

    status_u = status_filter.upper() if status_filter else None
    region_u = region_filter.upper() if region_filter else None
    unfiltered_study_status = study_id is None and status_u is None

    filtered: list[dict[str, Any]] = []
    for ev in all_evaluations:
        if study_id and ev["study_id"] != study_id:
            continue
        if status_u and ev["classification"] != status_u:
            continue
        if site_id_filter and ev["recommended_site_id"] != site_id_filter:
            continue
        if region_u and patients_by_id[ev["patient_id"]]["region"] != region_u:
            continue
        if min_score is not None and ev["match_score"] < min_score:
            continue
        if unfiltered_study_status and best_study_by_patient[ev["patient_id"]] != ev["study_id"]:
            continue
        filtered.append(ev)
    return filtered


def finalize_selection_with_site_risks(
    conn: sqlite3.Connection,
    precompute: SelectionPrecompute,
    site_risk_scores: dict[str, int] | None = None,
    study_id: str | None = None,
    status_filter: str | None = None,
    site_id_filter: str | None = None,
    region_filter: str | None = None,
    min_score: int | None = None,
    persist_table: bool = True,
    include_site_rankings: bool = True,
) -> dict[str, Any]:
    """Attach live site placement rankings to precomputed criteria and optionally persist to SQLite."""
    if study_id and study_id not in precompute.valid_study_ids:
        raise EntityNotFoundError(
            f"Unknown study_id '{study_id}'. Valid studies: {sorted(precompute.valid_study_ids)}"
        )

    sites_pre_by_study = {
        c.study_id: _precompute_study_sites(
            precompute.sites_by_study.get(c.study_id, []),
            precompute.irt_by_site,
            site_risk_scores,
        )
        for c in precompute.criteria_list
    }

    all_evaluations: list[dict[str, Any]] = []
    db_rows: list[tuple[Any, ...]] = []
    best_per_patient: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}
    placement_meta = precompute.patient_placement_meta

    for pat_idx, base_ev in precompute.base_evals:
        pid = base_ev["patient_id"]
        sid_study = base_ev["study_id"]
        cls_name = base_ev["classification"]
        cand_ids, pat_region = placement_meta[pat_idx]
        pre_sites = sites_pre_by_study[sid_study]

        if include_site_rankings:
            site_rankings = _rank_precomputed_sites(cand_ids, pat_region, pre_sites)
            best_site = next((r for r in site_rankings if r["placement_eligible"]), None)
            if best_site is not None and cls_name != "INELIGIBLE":
                rec_site_id: str | None = best_site["site_id"]
                rec_site_score = best_site["readiness_score"]
            else:
                rec_site_id = None
                rec_site_score = 0
            ev = {
                **base_ev,
                "recommended_site_id": rec_site_id,
                "recommended_site_score": rec_site_score,
                "site_rankings": site_rankings,
            }
        else:
            if cls_name != "INELIGIBLE":
                rec_site_id, rec_site_score = _find_best_site_fast(cand_ids, pat_region, pre_sites)
            else:
                rec_site_id = None
                rec_site_score = 0
            ev = {
                **base_ev,
                "recommended_site_id": rec_site_id,
                "recommended_site_score": rec_site_score,
            }

        if persist_table:
            db_rows.append(
                (
                    pid,
                    sid_study,
                    ev["protocol_code"],
                    cls_name,
                    ev["match_score"],
                    rec_site_id,
                    rec_site_score,
                    len(ev["failed_criteria"]),
                    len(ev["missing_criteria"]),
                )
            )
        all_evaluations.append(ev)

        rank_tuple = (
            2 if cls_name == "ELIGIBLE" else (1 if cls_name == "POTENTIAL_NEEDS_LABS" else 0),
            ev["match_score"],
        )
        prev = best_per_patient.get(pid)
        if prev is None or rank_tuple > prev[0]:
            best_per_patient[pid] = (rank_tuple, ev)

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
        if db_rows:
            conn.executemany(
                "INSERT INTO eligibility_assessments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                db_rows,
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_elig_study_class ON eligibility_assessments (study_id, classification)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_elig_rec_site ON eligibility_assessments (recommended_site_id)"
        )
        conn.commit()

    best_study_by_patient = {pid: pair[1]["study_id"] for pid, pair in best_per_patient.items()}
    filtered_evals = filter_selection_evaluations(
        all_evaluations,
        precompute.patients_by_id,
        best_study_by_patient,
        precompute.valid_study_ids,
        study_id=study_id,
        status_filter=status_filter,
        site_id_filter=site_id_filter,
        region_filter=region_filter,
        min_score=min_score,
    )

    labs_by_pid = precompute.labs_by_pid
    enriched_patients: list[dict[str, Any]] = []
    for pat in precompute.patients:
        pid = pat["patient_id"]
        best = best_per_patient[pid][1]
        enriched_patients.append(
            {
                **pat,
                "labs": labs_by_pid.get(pid, {}),
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
        "summary_by_study": list(precompute.summary_by_study.values()),
        "_all_evaluations": all_evaluations,
        "_patients_by_id": precompute.patients_by_id,
        "_best_study_by_patient": best_study_by_patient,
        "_valid_study_ids": precompute.valid_study_ids,
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
    precompute = evaluate_selection_criteria_once(conn)
    result = finalize_selection_with_site_risks(
        conn,
        precompute,
        site_risk_scores=site_risk_scores,
        study_id=study_id,
        status_filter=status_filter,
        site_id_filter=site_id_filter,
        region_filter=region_filter,
        min_score=min_score,
        persist_table=persist_table,
        include_site_rankings=True,
    )
    return {
        "patients": result["patients"],
        "evaluations": result["evaluations"],
        "summary_by_study": result["summary_by_study"],
    }
