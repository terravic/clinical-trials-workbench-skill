# Clinical Trial Eligibility Criteria and Patient Selection Guide

## 1. Selection Evaluation Pipeline

`scripts/trial_selector.py` evaluates every de-identified candidate patient (`ehr_patients.csv` joined with `lab_biomarkers.csv`) against each trial protocol in `trial_eligibility_criteria.json`. Evaluation is deterministic, auditable, and isolated from any PII/PHI attributes.

### Step 1: Inclusion Criteria Checks
1. **Age Window:** `min_age <= age_years <= max_age`.
2. **Diagnostic Alignment:** Patient `primary_icd10` matches one of the protocol's `allowed_icd10_prefixes` (for example, `C34` for non-small cell lung cancer, `I50` for heart failure, `G30`/`G31` for prodromal neurodegeneration, `K50`/`K51` for inflammatory bowel disease).
3. **Performance Status:** `ecog_status <= max_ecog`.
4. **Prior Therapy & Washout:** `prior_lines_therapy <= max_prior_lines` and (if `prior_lines_therapy > 0`) `washout_days >= min_washout_days`.
5. **Pre-Screening Consent:** `pre_screening_consent == 1`.
6. **Molecular & Disease Biomarkers:** Protocol-specific assay rules (for example, `egfr_mutation == 'POSITIVE'` or `pdl1_tps_pct >= 50` for `NCT-SYN-0101`; `lvef_pct <= 45` for `NCT-SYN-0204`; `csf_ptau_pg_ml >= 28.0` for `NCT-SYN-0309`; `fecal_calprotectin_ug_g >= 250.0` for `NCT-SYN-0412`).
7. **Baseline Organ Function Thresholds:** Minimum `egfr_ml_min`, `anc_k_ul`, `hemoglobin_g_dl`, `platelets_k_ul`, and maximum `bilirubin_mg_dl` or `hba1c_pct`, plus specimen recency (`collection_days_ago <= 30`).

### Step 2: Exclusion Criteria Checks
1. **Comorbidity Exclusions:** Evaluates semicolon-delimited `comorbidities` tokens against the protocol's `exclusion_conditions` list (such as `ACTIVE_CNS_METS`, `UNCONTROLLED_ARRHYTHMIA`, `SEVERE_HEPATIC_IMPAIRMENT`, `ACTIVE_TUBERCULOSIS`, `RECENT_MAJOR_SURGERY`).

---

## 2. Classification States

Each `(patient_id, study_id)` evaluation yields one of three mutually exclusive classifications:

| Classification | Definition | Operational Next Step |
|---|---|---|
| `ELIGIBLE` | All inclusion and exclusion checks return `PASS`, and central lab panel was collected within 30 days. | Route candidate ID to the highest-ranked active clinical site for formal screening visit scheduling. |
| `POTENTIAL_NEEDS_LABS` | Zero criteria return `FAIL`, and primary diagnosis/age/ECOG/exclusions pass, but one or more biomarker/organ-function values are missing (`NULL`) or stale (`collection_days_ago > 30`). | Order targeted central lab draw or refresh stale panel prior to site referral. |
| `INELIGIBLE` | One or more inclusion or exclusion rules explicitly return `FAIL`. | Exclude from current study cohort; retain audit log of failed criteria; evaluate against alternative protocols. |

---

## 3. Composite Eligibility Match Score (0-100)

The numerical match score quantifies readiness and margin safety for cohort prioritization:

- **Base Score for Diagnostic Alignment (`primary_icd10` match):** `40` points (`0` if diagnosis mismatches).
- **Clinical & Demographic Criteria (`age`, `ecog`, `prior_lines`, `washout`, `consent`, `exclusions`):** Up to `30` points proportional to passed criteria.
- **Biomarker & Organ Function Criteria:** Up to `30` points proportional to passed assays (`MISSING_DATA` awards half-credit `50%` of the assay weight so high-potential candidates missing a single lab surface cleanly above failed candidates).
- **Hard Failure Cap:** Any `INELIGIBLE` evaluation is capped at `<= 49` so `ELIGIBLE` (`85-100`) and `POTENTIAL_NEEDS_LABS` (`60-84`) cleanly separate in tables and 3D coordinate plots.

---

## 4. Optimal Clinical Site Placement Ranking

For `ELIGIBLE` and `POTENTIAL_NEEDS_LABS` candidates, eligible sites executing the target `study_id` are ranked using a 100-point operational readiness score:

1. **Geographic & Patient Proximity (`40` pts):** Full points if `site_id` is in the patient's `candidate_site_ids` list; `20` points if in the same `region` (`NA`, `EMEA`, `APAC`).
2. **Site Operational Status (`25` pts):** Sites must have `site_status == 'ACTIVE'`. Sites on `REGULATORY_HOLD` or `ENROLLMENT_PAUSED` are disqualified from immediate placement.
3. **Investigational Supply Readiness (`20` pts):** Full points when `available_kits > min_buffer_kits` and `unresolved_excursion == 0`; `0` points if an unresolved cold-chain excursion blocks dosing.
4. **Enrollment Capacity & Site Risk Tier (`15` pts):** Higher weight for sites with open enrollment slots (`target_enrollment - enrolled_count > 0`) and lower safety/compliance risk scores (`Continue to Monitor` > `Important Signals Next` > `Priority Action Required`).
