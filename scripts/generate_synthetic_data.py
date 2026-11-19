"""Deterministic multi-source synthetic clinical data generator.

Generates 100% synthetic, HIPAA Safe Harbor-compliant clinical datasets across
CSV, JSON, and NDJSON formats mimicking CTMS, Protocol Registry, EHR Screening,
Central Laboratory, EDC, IRT/IWRS, Safety/PV, and QMS/LMS source systems.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
import json
from pathlib import Path
import random
import sys
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import configure_logging, resolve_data_dir

REFERENCE_DATE = date(2026, 11, 18)


def _iso_days_ago(days: int) -> str:
    return (REFERENCE_DATE - timedelta(days=days)).isoformat()


def build_studies() -> list[dict[str, Any]]:
    """Return 4 representative Phase II/III clinical studies."""
    return [
        {
            "study_id": "NCT-SYN-0101",
            "protocol_code": "ONCO-301",
            "title": "Phase III Randomized Study of Selective Kinase/PD-L1 Regimen in Advanced NSCLC",
            "phase": "Phase III",
            "therapeutic_area": "Oncology",
            "target_enrollment": 120,
            "current_icf_version": "v3.0",
            "sae_target_days": 7,
            "baseline_sae_days": 28,
        },
        {
            "study_id": "NCT-SYN-0204",
            "protocol_code": "CARDIO-204",
            "title": "Phase IIb Cardiorenal Remodeling Trial in Reduced Ejection Fraction Heart Failure",
            "phase": "Phase IIb",
            "therapeutic_area": "Cardiology",
            "target_enrollment": 100,
            "current_icf_version": "v2.1",
            "sae_target_days": 7,
            "baseline_sae_days": 28,
        },
        {
            "study_id": "NCT-SYN-0309",
            "protocol_code": "NEURO-309",
            "title": "Phase II CSF pTau-217 Guided Synaptic Protection Trial in Early Prodromal Decline",
            "phase": "Phase II",
            "therapeutic_area": "Neurology",
            "target_enrollment": 80,
            "current_icf_version": "v2.0",
            "sae_target_days": 7,
            "baseline_sae_days": 28,
        },
        {
            "study_id": "NCT-SYN-0412",
            "protocol_code": "IMMUNO-412",
            "title": "Phase III Dual-Cytokine Blockade Study in Moderate-to-Severe Active IBD",
            "phase": "Phase III",
            "therapeutic_area": "Immunology",
            "target_enrollment": 110,
            "current_icf_version": "v4.0",
            "sae_target_days": 7,
            "baseline_sae_days": 28,
        },
    ]


def build_eligibility_criteria() -> list[dict[str, Any]]:
    """Return structured protocol inclusion and exclusion rules per study."""
    return [
        {
            "study_id": "NCT-SYN-0101",
            "protocol_code": "ONCO-301",
            "min_age": 18,
            "max_age": 85,
            "allowed_icd10_prefixes": ["C34"],
            "max_ecog": 1,
            "max_prior_lines": 2,
            "min_washout_days": 21,
            "biomarker_rule": {
                "description": "EGFR activating mutation POSITIVE or PD-L1 TPS >= 50%",
                "type": "OR",
                "clauses": [
                    {"field": "egfr_mutation", "op": "==", "value": "POSITIVE"},
                    {"field": "pdl1_tps_pct", "op": ">=", "value": 50.0},
                ],
            },
            "lab_thresholds": {
                "min_egfr": 50.0,
                "min_anc": 1.5,
                "min_hb": 9.5,
                "min_platelets": 100.0,
                "max_bilirubin": 1.5,
                "max_hba1c": 9.0,
            },
            "exclusion_conditions": [
                "ACTIVE_CNS_METS",
                "INTERSTITIAL_LUNG_DISEASE",
                "SEVERE_HEPATIC_IMPAIRMENT",
                "RECENT_MAJOR_SURGERY",
            ],
        },
        {
            "study_id": "NCT-SYN-0204",
            "protocol_code": "CARDIO-204",
            "min_age": 35,
            "max_age": 85,
            "allowed_icd10_prefixes": ["I50"],
            "max_ecog": 2,
            "max_prior_lines": 3,
            "min_washout_days": 14,
            "biomarker_rule": {
                "description": "Left Ventricular Ejection Fraction (LVEF) <= 45%",
                "type": "ALL",
                "clauses": [
                    {"field": "lvef_pct", "op": "<=", "value": 45.0},
                ],
            },
            "lab_thresholds": {
                "min_egfr": 35.0,
                "min_anc": 1.2,
                "min_hb": 9.0,
                "min_platelets": 90.0,
                "max_bilirubin": 2.0,
                "max_hba1c": 9.5,
            },
            "exclusion_conditions": [
                "UNCONTROLLED_ARRHYTHMIA",
                "RECENT_MAJOR_SURGERY",
                "SEVERE_HEPATIC_IMPAIRMENT",
            ],
        },
        {
            "study_id": "NCT-SYN-0309",
            "protocol_code": "NEURO-309",
            "min_age": 50,
            "max_age": 84,
            "allowed_icd10_prefixes": ["G30", "G31"],
            "max_ecog": 1,
            "max_prior_lines": 1,
            "min_washout_days": 28,
            "biomarker_rule": {
                "description": "CSF phosphorylated tau-217 (pTau) >= 28.0 pg/mL",
                "type": "ALL",
                "clauses": [
                    {"field": "csf_ptau_pg_ml", "op": ">=", "value": 28.0},
                ],
            },
            "lab_thresholds": {
                "min_egfr": 45.0,
                "min_anc": 1.5,
                "min_hb": 10.0,
                "min_platelets": 110.0,
                "max_bilirubin": 1.6,
                "max_hba1c": 8.5,
            },
            "exclusion_conditions": [
                "UNCONTROLLED_ARRHYTHMIA",
                "RECENT_STROKE_TIA",
                "SEVERE_HEPATIC_IMPAIRMENT",
            ],
        },
        {
            "study_id": "NCT-SYN-0412",
            "protocol_code": "IMMUNO-412",
            "min_age": 18,
            "max_age": 78,
            "allowed_icd10_prefixes": ["K50", "K51"],
            "max_ecog": 1,
            "max_prior_lines": 3,
            "min_washout_days": 14,
            "biomarker_rule": {
                "description": "Fecal Calprotectin >= 250.0 ug/g",
                "type": "ALL",
                "clauses": [
                    {"field": "fecal_calprotectin_ug_g", "op": ">=", "value": 250.0},
                ],
            },
            "lab_thresholds": {
                "min_egfr": 50.0,
                "min_anc": 1.5,
                "min_hb": 9.0,
                "min_platelets": 100.0,
                "max_bilirubin": 1.5,
                "max_hba1c": 8.5,
            },
            "exclusion_conditions": [
                "ACTIVE_TUBERCULOSIS",
                "OPPORTUNISTIC_INFECTION",
                "RECENT_MAJOR_SURGERY",
                "SEVERE_HEPATIC_IMPAIRMENT",
            ],
        },
    ]


def build_sites() -> list[dict[str, Any]]:
    """Return 16 global clinical trial sites across NA, EMEA, and APAC."""
    site_specs = [
        # NCT-SYN-0101 (ONCO-301)
        ("SITE-101", "NCT-SYN-0101", "Northeast Thoracic Research Center", "US", "NA", "CRA-01", "CRA-02", "ACTIVE", 30, 19, 36, 17, 44, 12),
        ("SITE-102", "NCT-SYN-0101", "Pacific Coast Oncology Institute", "US", "NA", "CRA-01", "CRA-04", "ACTIVE", 30, 10, 28, 18, 68, 35),
        ("SITE-103", "NCT-SYN-0101", "Rhein-Main University Clinical Center", "DE", "EMEA", "CRA-02", "CRA-01", "ACTIVE", 30, 21, 34, 13, 29, 9),
        ("SITE-104", "NCT-SYN-0101", "Kansai Precision Pulmonary Hospital", "JP", "APAC", "CRA-03", "CRA-02", "ACTIVE", 30, 24, 33, 9, 21, 6),
        # NCT-SYN-0204 (CARDIO-204)
        ("SITE-105", "NCT-SYN-0204", "Great Lakes Heart and Vascular Unit", "US", "NA", "CRA-01", "CRA-03", "ACTIVE", 25, 14, 26, 12, 37, 15),
        ("SITE-106", "NCT-SYN-0204", "Ontario Cardiorenal Academic Site", "CA", "NA", "CRA-04", "CRA-01", "ACTIVE", 25, 8, 22, 14, 64, 34),
        ("SITE-107", "NCT-SYN-0204", "Midlands Cardiovascular Clinical Unit", "UK", "EMEA", "CRA-02", "CRA-04", "ACTIVE", 25, 16, 29, 13, 31, 11),
        ("SITE-108", "NCT-SYN-0204", "Southern Cross Heart Institute", "AU", "APAC", "CRA-03", "CRA-04", "ACTIVE", 25, 20, 27, 7, 19, 8),
        # NCT-SYN-0309 (NEURO-309)
        ("SITE-109", "NCT-SYN-0309", "Atlantic Memory and Biomarker Clinic", "US", "NA", "CRA-04", "CRA-01", "ENROLLMENT_PAUSED", 20, 9, 21, 12, 52, 41),
        ("SITE-110", "NCT-SYN-0309", "Ile-de-France Neurocognitive Center", "FR", "EMEA", "CRA-02", "CRA-03", "ACTIVE", 20, 7, 19, 12, 48, 18),
        ("SITE-111", "NCT-SYN-0309", "Bavaria Translational Neuro Site", "DE", "EMEA", "CRA-02", "CRA-01", "ACTIVE", 20, 15, 22, 7, 25, 10),
        ("SITE-112", "NCT-SYN-0309", "Marina Bay Neuroscience Center", "SG", "APAC", "CRA-03", "CRA-02", "ACTIVE", 20, 16, 23, 7, 22, 7),
        # NCT-SYN-0412 (IMMUNO-412)
        ("SITE-113", "NCT-SYN-0412", "Midwest Mucosal Immunology Clinic", "US", "NA", "CRA-01", "CRA-04", "ACTIVE", 28, 15, 31, 16, 55, 14),
        ("SITE-114", "NCT-SYN-0412", "Thames Valley Gastroenterology Unit", "UK", "EMEA", "CRA-02", "CRA-03", "ACTIVE", 28, 10, 25, 15, 63, 33),
        ("SITE-115", "NCT-SYN-0412", "Tokyo Metropolitan GI Research Hospital", "JP", "APAC", "CRA-03", "CRA-01", "ACTIVE", 27, 17, 29, 12, 39, 13),
        ("SITE-116", "NCT-SYN-0412", "Sydney Translational Immunology Site", "AU", "APAC", "CRA-03", "CRA-04", "ACTIVE", 27, 22, 30, 8, 17, 5),
    ]
    rows: list[dict[str, Any]] = []
    for (
        site_id,
        study_id,
        site_name,
        country,
        region,
        cra_id,
        peer_cra,
        status,
        target,
        enrolled,
        screened,
        screen_fail,
        days_mon,
        days_scr,
    ) in site_specs:
        rows.append(
            {
                "site_id": site_id,
                "study_id": study_id,
                "site_name": site_name,
                "country_code": country,
                "region": region,
                "assigned_cra_id": cra_id,
                "peer_cra_id": peer_cra,
                "site_status": status,
                "target_enrollment": target,
                "enrolled_count": enrolled,
                "screened_count": screened,
                "screen_fail_count": screen_fail,
                "last_monitoring_date": _iso_days_ago(days_mon),
                "days_since_monitoring": days_mon,
                "days_since_last_screen": days_scr,
            }
        )
    return rows


def build_patients_and_labs(
    rng: random.Random, sites: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Generate 140 synthetic candidate patients and paired central lab panels."""
    study_site_map: dict[tuple[str, str], list[str]] = {}
    for s in sites:
        key = (s["study_id"], s["region"])
        study_site_map.setdefault(key, []).append(s["site_id"])

    cohort_templates = [
        ("NCT-SYN-0101", ["C34.1", "C34.3"], ["Stage IIIB", "Stage IVA", "Stage IVB"], 36),
        ("NCT-SYN-0204", ["I50.22", "I50.32"], ["NYHA Class II", "NYHA Class III"], 34),
        ("NCT-SYN-0309", ["G31.84", "G30.0"], ["Prodromal MCI", "Early Mild Stage"], 34),
        ("NCT-SYN-0412", ["K50.10", "K51.00"], ["Moderate Active", "Severe Active"], 36),
    ]

    patients: list[dict[str, Any]] = []
    labs: list[dict[str, Any]] = []
    idx = 1

    for study_id, icd_choices, stages, count in cohort_templates:
        for local_i in range(count):
            patient_id = f"PAT-{10000 + idx}"
            idx += 1
            region = ["NA", "EMEA", "APAC"][local_i % 3]
            sex = "F" if (local_i + idx) % 2 == 0 else "M"
            matching_sites = study_site_map.get((study_id, region), [])
            # Also include 1 cross-study regional hospital to mimic general EHR network
            all_region_sites = [s["site_id"] for s in sites if s["region"] == region]
            extra_sites = [sid for sid in all_region_sites if sid not in matching_sites]
            cand_sites = list(matching_sites)
            if extra_sites:
                cand_sites.append(extra_sites[local_i % len(extra_sites)])

            # Partition cohort deterministically into ELIGIBLE (~38%), POTENTIAL_NEEDS_LABS (~22%), INELIGIBLE (~40%)
            bucket_mod = local_i % 10
            if bucket_mod in (0, 1, 2, 3):
                target_profile = "ELIGIBLE"
            elif bucket_mod in (4, 5):
                target_profile = "POTENTIAL_NEEDS_LABS"
            else:
                target_profile = "INELIGIBLE"

            # Default passing attributes per study
            if study_id == "NCT-SYN-0101":
                age = rng.randint(44, 79)
                ecog = rng.choice([0, 1])
                prior_lines = rng.choice([0, 1, 2])
                washout = rng.randint(24, 60) if prior_lines > 0 else 0
            elif study_id == "NCT-SYN-0204":
                age = rng.randint(48, 82)
                ecog = rng.choice([0, 1, 2])
                prior_lines = rng.choice([1, 2])
                washout = rng.randint(18, 45)
            elif study_id == "NCT-SYN-0309":
                age = rng.randint(56, 81)
                ecog = rng.choice([0, 1])
                prior_lines = rng.choice([0, 1])
                washout = rng.randint(30, 75) if prior_lines > 0 else 0
            else:
                age = rng.randint(24, 72)
                ecog = rng.choice([0, 1])
                prior_lines = rng.choice([1, 2, 3])
                washout = rng.randint(16, 50)

            icd10 = icd_choices[local_i % len(icd_choices)]
            stage = stages[local_i % len(stages)]
            comorbidities = "NONE" if local_i % 3 != 0 else "CONTROLLED_HYPERTENSION"
            consent = 1

            # Baseline passing labs
            collection_days = rng.randint(3, 24)
            egfr: float | str = round(rng.uniform(54.0, 96.0), 1)
            anc: float | str = round(rng.uniform(1.8, 5.4), 2)
            hb: float | str = round(rng.uniform(10.4, 14.6), 1)
            platelets: float | str = round(rng.uniform(135.0, 310.0), 0)
            bilirubin: float | str = round(rng.uniform(0.4, 1.3), 2)
            hba1c: float | str = round(rng.uniform(5.2, 7.6), 1)

            lvef: float | str = round(rng.uniform(52.0, 64.0), 1)
            egfr_mut = "NEGATIVE"
            pdl1: float | str = round(rng.uniform(5.0, 35.0), 1)
            csf_ptau: float | str = ""
            calprotectin: float | str = ""

            if study_id == "NCT-SYN-0101":
                if local_i % 2 == 0:
                    egfr_mut = "POSITIVE"
                    pdl1 = round(rng.uniform(15.0, 85.0), 1)
                else:
                    egfr_mut = "NEGATIVE"
                    pdl1 = round(rng.uniform(55.0, 92.0), 1)
                biomarker_z = round(float(pdl1) if egfr_mut == "NEGATIVE" else min(98.0, float(pdl1) + 35.0), 1)
            elif study_id == "NCT-SYN-0204":
                lvef = round(rng.uniform(26.0, 43.0), 1)
                biomarker_z = round(100.0 - float(lvef), 1)
            elif study_id == "NCT-SYN-0309":
                csf_ptau = round(rng.uniform(30.5, 68.0), 1)
                biomarker_z = round(min(99.0, float(csf_ptau) * 1.35), 1)
            else:
                calprotectin = round(rng.uniform(280.0, 920.0), 1)
                biomarker_z = round(min(99.0, float(calprotectin) / 9.5), 1)

            if target_profile == "POTENTIAL_NEEDS_LABS":
                if local_i % 2 == 0:
                    # Missing specialty biomarker assay
                    if study_id == "NCT-SYN-0101":
                        egfr_mut = ""
                        pdl1 = ""
                    elif study_id == "NCT-SYN-0204":
                        lvef = ""
                    elif study_id == "NCT-SYN-0309":
                        csf_ptau = ""
                    else:
                        calprotectin = ""
                else:
                    # Stale lab panel (>30 days) or missing renal panel
                    if local_i % 4 == 1:
                        collection_days = rng.randint(34, 52)
                    else:
                        egfr = ""

            elif target_profile == "INELIGIBLE":
                fail_mode = local_i % 5
                if fail_mode == 0:
                    # Biomarker failure
                    if study_id == "NCT-SYN-0101":
                        egfr_mut = "NEGATIVE"
                        pdl1 = round(rng.uniform(2.0, 38.0), 1)
                        biomarker_z = float(pdl1)
                    elif study_id == "NCT-SYN-0204":
                        lvef = round(rng.uniform(49.0, 62.0), 1)
                        biomarker_z = round(100.0 - float(lvef), 1)
                    elif study_id == "NCT-SYN-0309":
                        csf_ptau = round(rng.uniform(11.0, 23.5), 1)
                        biomarker_z = round(float(csf_ptau) * 1.35, 1)
                    else:
                        calprotectin = round(rng.uniform(65.0, 195.0), 1)
                        biomarker_z = round(float(calprotectin) / 9.5, 1)
                elif fail_mode == 1:
                    # Exclusionary comorbidity
                    excl_map = {
                        "NCT-SYN-0101": "ACTIVE_CNS_METS",
                        "NCT-SYN-0204": "UNCONTROLLED_ARRHYTHMIA",
                        "NCT-SYN-0309": "RECENT_STROKE_TIA",
                        "NCT-SYN-0412": "ACTIVE_TUBERCULOSIS",
                    }
                    comorbidities = excl_map[study_id]
                elif fail_mode == 2:
                    # Organ function failure (low eGFR or high bilirubin)
                    egfr = round(rng.uniform(21.0, 31.5), 1)
                    bilirubin = round(rng.uniform(2.2, 3.1), 2)
                elif fail_mode == 3:
                    # Performance status or insufficient washout
                    ecog = 3
                    prior_lines = 2
                    washout = 6
                else:
                    # Non-matching primary diagnosis or declined pre-screening consent
                    if local_i % 2 == 0:
                        icd10 = "E11.9"
                        stage = "Chronic Metabolic"
                    else:
                        consent = 0

            severity_index = round(
                min(98.0, max(12.0, 28.0 + ecog * 16.0 + prior_lines * 9.0 + (biomarker_z * 0.35))),
                1,
            )

            patients.append(
                {
                    "patient_id": patient_id,
                    "age_years": age,
                    "sex": sex,
                    "region": region,
                    "candidate_site_ids": ";".join(cand_sites),
                    "primary_icd10": icd10,
                    "disease_stage": stage,
                    "ecog_status": ecog,
                    "prior_lines_therapy": prior_lines,
                    "washout_days": washout,
                    "comorbidities": comorbidities,
                    "pre_screening_consent": consent,
                    "severity_index": severity_index,
                }
            )
            labs.append(
                {
                    "patient_id": patient_id,
                    "collection_days_ago": collection_days,
                    "egfr_ml_min": egfr,
                    "anc_k_ul": anc,
                    "hemoglobin_g_dl": hb,
                    "platelets_k_ul": platelets,
                    "bilirubin_mg_dl": bilirubin,
                    "hba1c_pct": hba1c,
                    "lvef_pct": lvef,
                    "egfr_mutation": egfr_mut,
                    "pdl1_tps_pct": pdl1,
                    "csf_ptau_pg_ml": csf_ptau,
                    "fecal_calprotectin_ug_g": calprotectin,
                    "biomarker_composite_z": biomarker_z,
                }
            )

    return patients, labs


def build_edc_visits_and_pds(
    sites: list[dict[str, Any]], studies_by_id: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Generate enrolled subject EDC visit records and protocol deviations."""
    visits: list[dict[str, Any]] = []
    pds: list[dict[str, Any]] = []
    vis_seq = 7001
    subj_seq = 5001

    for site in sites:
        site_id = site["site_id"]
        study_id = site["study_id"]
        curr_icf = studies_by_id[study_id]["current_icf_version"]
        old_icf = "v1.0" if curr_icf != "v1.0" else "v0.9"

        # Generate 4 representative enrolled subject visit rows per site (64 rows total)
        for s_idx in range(4):
            vis_id = f"VIS-{vis_seq}"
            subj_id = f"SUBJ-{subj_seq}"
            vis_seq += 1
            subj_seq += 1

            visit_name = ["Screening", "Baseline", "Week 4", "Week 12"][s_idx]
            visit_status = "COMPLETED"
            days_oow = 1 if s_idx % 2 == 0 else 0
            open_queries = 1 if s_idx == 2 else 0
            max_q_age = 6 if open_queries > 0 else 0
            signed_icf = curr_icf
            icf_sign_date = _iso_days_ago(60 - s_idx * 5)
            scr_proc_date = _iso_days_ago(58 - s_idx * 5)
            icf_verified = 1
            sdv_pages = 8

            # Inject targeted operational anomalies per site profile
            if site_id == "SITE-101" and s_idx in (0, 1):
                signed_icf = old_icf
                open_queries = 6 if s_idx == 0 else 3
                max_q_age = 26 if s_idx == 0 else 16
            elif site_id == "SITE-102" and s_idx == 1:
                sdv_pages = 34
                open_queries = 5
                max_q_age = 24
                visit_status = "OVERDUE_ENTRY"
            elif site_id == "SITE-103" and s_idx == 2:
                icf_verified = 0
                days_oow = 8
            elif site_id == "SITE-105" and s_idx == 3:
                visit_status = "WITHDRAWN_EARLY"
                open_queries = 4
                max_q_age = 18
            elif site_id == "SITE-106" and s_idx == 0:
                sdv_pages = 29
                max_q_age = 23
                open_queries = 4
            elif site_id == "SITE-107" and s_idx == 1:
                open_queries = 5
                max_q_age = 17
                days_oow = 7
            elif site_id == "SITE-109" and s_idx == 0:
                # Screening procedure performed 2 days BEFORE ICF signed
                icf_sign_date = _iso_days_ago(45)
                scr_proc_date = _iso_days_ago(47)
                icf_verified = 0
            elif site_id == "SITE-110" and s_idx == 2:
                visit_status = "OVERDUE_ENTRY"
                sdv_pages = 28
            elif site_id == "SITE-113" and s_idx == 1:
                signed_icf = old_icf
                open_queries = 6
                max_q_age = 28
            elif site_id == "SITE-114" and s_idx == 2:
                max_q_age = 19
                open_queries = 3
            elif site_id == "SITE-115" and s_idx == 0:
                icf_verified = 0
                days_oow = 6

            visits.append(
                {
                    "visit_record_id": vis_id,
                    "subject_id": subj_id,
                    "site_id": site_id,
                    "study_id": study_id,
                    "visit_name": visit_name,
                    "visit_status": visit_status,
                    "days_out_of_window": days_oow,
                    "open_queries": open_queries,
                    "max_query_age_days": max_q_age,
                    "signed_icf_version": signed_icf,
                    "icf_sign_date": icf_sign_date,
                    "screening_proc_date": scr_proc_date,
                    "icf_verified": icf_verified,
                    "sdv_backlog_pages": sdv_pages,
                }
            )

    pd_specs = [
        ("PD-9001", "SITE-101", "NCT-SYN-0101", "SUBJ-5001", "INFORMED_CONSENT", "MAJOR", "OPEN", 19, 1, "Subject completed Week 4 biomarker blood draw prior to signing Amendment v3.0 ICF."),
        ("PD-9002", "SITE-103", "NCT-SYN-0101", "SUBJ-5010", "DOSING_MODIFICATION", "MAJOR", "IN_REVIEW", 22, 1, "Dose reduction step omitted following Grade 3 transaminase elevation."),
        ("PD-9003", "SITE-105", "NCT-SYN-0204", "SUBJ-5017", "CONCOMITANT_MEDICATION", "MINOR", "OPEN", 11, 0, "Short-course prohibited loop diuretic titration logged 3 days late."),
        ("PD-9004", "SITE-107", "NCT-SYN-0204", "SUBJ-5026", "VISIT_WINDOW", "MINOR", "CLOSED", 6, 0, "Week 4 echocardiography completed 7 days outside target window due to clinic holiday."),
        ("PD-9005", "SITE-109", "NCT-SYN-0309", "SUBJ-5033", "INFORMED_CONSENT", "MAJOR", "OPEN", 27, 1, "Baseline cognitive battery administered 48 hours prior to dated consent signature."),
        ("PD-9006", "SITE-110", "NCT-SYN-0309", "SUBJ-5038", "LAB_ASSESSMENT", "MAJOR", "OPEN", 16, 0, "Missed central ECG safety transmission at Week 4 visit."),
        ("PD-9007", "SITE-113", "NCT-SYN-0412", "SUBJ-5050", "ELIGIBILITY_WAIVER", "MAJOR", "OPEN", 18, 1, "Washout verification source note missing from screening binder at randomization."),
        ("PD-9008", "SITE-115", "NCT-SYN-0412", "SUBJ-5057", "VISIT_WINDOW", "MINOR", "OPEN", 9, 0, "Stool biomarker sample returned 6 days outside protocol window."),
    ]
    for pd_id, site_id, study_id, subj_id, cat, sev, status, days_open, capa_req, desc in pd_specs:
        pds.append(
            {
                "pd_id": pd_id,
                "site_id": site_id,
                "study_id": study_id,
                "subject_id": subj_id,
                "category": cat,
                "severity": sev,
                "status": status,
                "days_open": days_open,
                "capa_required": capa_req,
                "description": desc,
            }
        )

    return visits, pds


def build_irt_inventory(sites: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build IRT randomization and investigational drug kit inventory records."""
    overrides = {
        "SITE-101": (11, 8, 1, 0, 0, 3.2),
        "SITE-102": (6, 8, 1, 0, 0, 4.5),
        "SITE-103": (9, 8, 0, 1, 0, 3.8),
        "SITE-104": (18, 8, 1, 0, 0, 2.1),
        "SITE-105": (4, 8, 0, 2, 1, 6.4),
        "SITE-106": (9, 8, 0, 0, 0, 4.9),
        "SITE-107": (7, 8, 1, 0, 0, 5.6),
        "SITE-108": (16, 8, 1, 0, 0, 2.4),
        "SITE-109": (10, 6, 0, 1, 0, 4.8),
        "SITE-110": (7, 6, 0, 0, 0, 5.8),
        "SITE-111": (14, 6, 1, 0, 0, 2.9),
        "SITE-112": (15, 6, 1, 0, 0, 2.5),
        "SITE-113": (5, 8, 0, 1, 0, 4.2),
        "SITE-114": (9, 8, 0, 0, 0, 5.3),
        "SITE-115": (12, 8, 1, 0, 0, 3.4),
        "SITE-116": (19, 8, 1, 0, 0, 2.0),
    }
    records: list[dict[str, Any]] = []
    for s in sites:
        sid = s["site_id"]
        avail, buf, transit, exc_cnt, unres_exc, rand_lag = overrides[sid]
        records.append(
            {
                "site_id": sid,
                "study_id": s["study_id"],
                "available_kits": avail,
                "min_buffer_kits": buf,
                "resupply_in_transit": transit,
                "temp_excursion_count": exc_cnt,
                "unresolved_excursion": unres_exc,
                "median_rand_to_dose_days": rand_lag,
            }
        )
    return records


def build_safety_events() -> list[dict[str, Any]]:
    """Build pharmacovigilance AE/SAE records contrasting 28d legacy lag vs 7d target."""
    specs = [
        ("SAE-3001", "SITE-101", "NCT-SYN-0101", "SUBJ-5001", "Pneumonitis Grade 3", 3, 1, 30, 29, 36, 26, "OPEN_FOLLOWUP", 0),
        ("SAE-3002", "SITE-101", "NCT-SYN-0101", "SUBJ-5003", "Febrile Neutropenia", 3, 1, 18, 18, 14, 11, "OPEN_FOLLOWUP", 1),
        ("SAE-3003", "SITE-103", "NCT-SYN-0101", "SUBJ-5010", "Hepatic Transaminase Elevation", 3, 1, 16, 15, 31, 9, "OPEN_FOLLOWUP", 0),
        ("SAE-3004", "SITE-104", "NCT-SYN-0101", "SUBJ-5014", "Infusion Related Reaction", 2, 1, 22, 22, 8, 4, "SUBMITTED", 1),
        ("SAE-3005", "SITE-105", "NCT-SYN-0204", "SUBJ-5017", "Acute Decompensated Heart Failure", 4, 1, 20, 19, 19, 13, "OPEN_FOLLOWUP", 1),
        ("SAE-3006", "SITE-107", "NCT-SYN-0204", "SUBJ-5025", "Symptomatic Hypotension", 3, 0, 14, 14, 12, 5, "SUBMITTED", 0),
        ("SAE-3007", "SITE-108", "NCT-SYN-0204", "SUBJ-5030", "Transient Hyperkalemia", 2, 1, 25, 25, 10, 5, "SUBMITTED", 1),
        ("SAE-3008", "SITE-109", "NCT-SYN-0309", "SUBJ-5034", "Syncope Episode", 3, 1, 24, 23, 29, 15, "OPEN_FOLLOWUP", 1),
        ("SAE-3009", "SITE-111", "NCT-SYN-0309", "SUBJ-5042", "Post-Lumbar Puncture Headache", 2, 1, 19, 19, 9, 4, "SUBMITTED", 1),
        ("SAE-3010", "SITE-112", "NCT-SYN-0309", "SUBJ-5046", "Viral Gastroenteritis", 2, 1, 28, 28, 11, 6, "SUBMITTED", 1),
        ("SAE-3011", "SITE-113", "NCT-SYN-0412", "SUBJ-5049", "Severe Colitis Flare Hospitalization", 3, 1, 33, 32, 42, 29, "OPEN_FOLLOWUP", 0),
        ("SAE-3012", "SITE-113", "NCT-SYN-0412", "SUBJ-5051", "Opportunistic Herpes Zoster", 3, 1, 15, 15, 18, 10, "OPEN_FOLLOWUP", 1),
        ("SAE-3013", "SITE-115", "NCT-SYN-0412", "SUBJ-5058", "Bacterial Pneumonia", 3, 0, 12, 12, 15, 6, "SUBMITTED", 0),
        ("SAE-3014", "SITE-116", "NCT-SYN-0412", "SUBJ-5062", "Dehydration Observation", 2, 1, 21, 21, 7, 3, "SUBMITTED", 1),
    ]
    rows: list[dict[str, Any]] = []
    for (
        sae_id,
        site_id,
        study_id,
        subj_id,
        term,
        grade,
        serious,
        onset_ago,
        aware_ago,
        init_hrs,
        cycle_days,
        status,
        causality,
    ) in specs:
        rows.append(
            {
                "sae_id": sae_id,
                "site_id": site_id,
                "study_id": study_id,
                "subject_id": subj_id,
                "event_term": term,
                "ctcae_grade": grade,
                "serious": serious,
                "onset_date": _iso_days_ago(onset_ago),
                "awareness_date": _iso_days_ago(aware_ago),
                "initial_report_hours": init_hrs,
                "followup_cycle_days": cycle_days,
                "report_status": status,
                "causality_assessed": causality,
            }
        )
    return rows


def build_qms_training_and_issues() -> dict[str, list[dict[str, Any]]]:
    """Build LMS/QMS site training compliance and quality CAPA issue logs."""
    training_specs = [
        ("TRN-401", "SITE-101", "SUBINV-101A", "PROTOCOL_AMENDMENT", "EXPIRED", -9),
        ("TRN-402", "SITE-102", "COORD-102B", "IRT_EDC_SYSTEM", "INCOMPLETE", 20),
        ("TRN-403", "SITE-103", "COORD-103A", "GCP", "EXPIRING_SOON", 11),
        ("TRN-404", "SITE-104", "PI-104A", "GCP", "ACTIVE", 240),
        ("TRN-405", "SITE-105", "PHARM-105A", "GCP", "EXPIRING_SOON", 8),
        ("TRN-406", "SITE-106", "SUBINV-106B", "PROTOCOL_AMENDMENT", "EXPIRED", -14),
        ("TRN-407", "SITE-108", "PI-108A", "GCP", "ACTIVE", 310),
        ("TRN-408", "SITE-109", "COORD-109A", "GCP", "EXPIRED", -18),
        ("TRN-409", "SITE-110", "COORD-110B", "IRT_EDC_SYSTEM", "INCOMPLETE", 15),
        ("TRN-410", "SITE-111", "PI-111A", "GCP", "ACTIVE", 195),
        ("TRN-411", "SITE-113", "SUBINV-113A", "GCP", "EXPIRED", -22),
        ("TRN-412", "SITE-114", "COORD-114A", "GCP", "EXPIRING_SOON", 6),
        ("TRN-413", "SITE-115", "SUBINV-115B", "PROTOCOL_AMENDMENT", "EXPIRED", -5),
        ("TRN-414", "SITE-116", "PI-116A", "GCP", "ACTIVE", 275),
    ]
    issue_specs = [
        ("ISS-801", "SITE-101", "NCT-SYN-0101", "CONSENT_PROCESS", "HIGH", "CAPA_IN_PROGRESS", 24, 1, "Re-consent tracking log missing version control check after Amendment v3.0."),
        ("ISS-802", "SITE-102", "NCT-SYN-0101", "DATA_ENTRY_LAG", "MEDIUM", "OPEN", 16, 0, "Backup coordinator turnover created 3-week eCRF transcription backlog."),
        ("ISS-803", "SITE-105", "NCT-SYN-0204", "COLD_CHAIN_STORAGE", "CRITICAL", "OPEN", 7, 0, "Pharmacy refrigerator excursion (+11.4C for 3 hours) awaiting stability disposition."),
        ("ISS-804", "SITE-109", "NCT-SYN-0309", "REGULATORY_HOLD_CAPA", "CRITICAL", "CAPA_IN_PROGRESS", 31, 1, "Enrollment paused pending IRB notification and CAPA verification on pre-consent assessment."),
        ("ISS-805", "SITE-113", "NCT-SYN-0412", "SAFETY_REPORTING", "CRITICAL", "CAPA_IN_PROGRESS", 28, 1, "Delayed hospital discharge summary retrieval caused 29-day SAE follow-up lag."),
        ("ISS-806", "SITE-114", "NCT-SYN-0412", "SCREENING_FUNNEL", "MEDIUM", "OPEN", 19, 0, "High screen failure rate linked to outdated local calprotectin pre-screen cutoff."),
    ]

    training_records = [
        {
            "training_id": tid,
            "site_id": sid,
            "staff_role_id": role,
            "cert_type": ctype,
            "status": status,
            "days_until_expiry": days,
        }
        for tid, sid, role, ctype, status, days in training_specs
    ]
    quality_issues = [
        {
            "issue_id": iid,
            "site_id": sid,
            "study_id": stid,
            "category": cat,
            "priority": prio,
            "status": status,
            "days_open": d_open,
            "overdue_capa": overdue,
            "summary": summary,
        }
        for iid, sid, stid, cat, prio, status, d_open, overdue, summary in issue_specs
    ]
    return {"training_records": training_records, "quality_issues": quality_issues}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"Cannot write empty CSV dataset: {path}")
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def generate_all_synthetic_data(output_dir: Path | None = None, seed: int = 42) -> dict[str, Any]:
    """Generate all 10 synthetic data files and return summary counts."""
    logger = configure_logging()
    target_dir = resolve_data_dir(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)

    studies = build_studies()
    studies_by_id = {s["study_id"]: s for s in studies}
    criteria = build_eligibility_criteria()
    sites = build_sites()
    patients, labs = build_patients_and_labs(rng, sites)
    visits, pds = build_edc_visits_and_pds(sites, studies_by_id)
    irt_records = build_irt_inventory(sites)
    safety_events = build_safety_events()
    qms_payload = build_qms_training_and_issues()

    (target_dir / "ctms_studies.json").write_text(
        json.dumps(studies, indent=2) + "\n", encoding="utf-8"
    )
    (target_dir / "trial_eligibility_criteria.json").write_text(
        json.dumps(criteria, indent=2) + "\n", encoding="utf-8"
    )
    _write_csv(target_dir / "ctms_sites.csv", sites)
    _write_csv(target_dir / "ehr_patients.csv", patients)
    _write_csv(target_dir / "lab_biomarkers.csv", labs)
    _write_csv(target_dir / "edc_subject_visits.csv", visits)

    with (target_dir / "edc_protocol_deviations.ndjson").open("w", encoding="utf-8") as handle:
        for record in pds:
            handle.write(json.dumps(record) + "\n")

    (target_dir / "irt_randomization_inventory.json").write_text(
        json.dumps(irt_records, indent=2) + "\n", encoding="utf-8"
    )
    _write_csv(target_dir / "safety_sae_events.csv", safety_events)
    (target_dir / "qms_training_issues.json").write_text(
        json.dumps(qms_payload, indent=2) + "\n", encoding="utf-8"
    )

    summary = {
        "output_dir": str(target_dir),
        "seed": seed,
        "files_written": 10,
        "counts": {
            "studies": len(studies),
            "eligibility_protocols": len(criteria),
            "sites": len(sites),
            "patients": len(patients),
            "lab_panels": len(labs),
            "edc_visits": len(visits),
            "protocol_deviations": len(pds),
            "irt_inventory": len(irt_records),
            "safety_events": len(safety_events),
            "qms_training_records": len(qms_payload["training_records"]),
            "qms_quality_issues": len(qms_payload["quality_issues"]),
        },
    }
    logger.info("Generated synthetic clinical datasets at %s", target_dir)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate 100% synthetic multi-source clinical trial datasets."
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Target directory for synthetic datasets (default: assets/synthetic_data).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Deterministic random seed (default: 42).",
    )
    args = parser.parse_args(argv)
    summary = generate_all_synthetic_data(output_dir=args.output_dir, seed=args.seed)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
