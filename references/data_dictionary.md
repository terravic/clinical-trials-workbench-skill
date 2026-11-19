# Cross-System Synthetic Data Dictionary

All synthetic files reside in `assets/synthetic_data/` and contain 100% synthetic, HIPAA Safe Harbor-compliant clinical records.

## 1. `ctms_studies.json` (Format: JSON Array)
| Field | Type | Description |
|---|---|---|
| `study_id` | `string` | Primary study identifier (`NCT-SYN-0101`..`NCT-SYN-0412`). |
| `protocol_code` | `string` | Sponsor short protocol code (`ONCO-301`, `CARDIO-204`, `NEURO-309`, `IMMUNO-412`). |
| `title` | `string` | Descriptive study title. |
| `phase` | `string` | Clinical trial phase (`Phase II`, `Phase IIb`, `Phase III`). |
| `therapeutic_area` | `string` | Clinical domain (`Oncology`, `Cardiology`, `Neurology`, `Immunology`). |
| `target_enrollment` | `integer` | Study-wide enrollment goal. |
| `current_icf_version` | `string` | Active IRB-approved Informed Consent Form version (`v3.0`, `v2.1`, etc.). |
| `sae_target_days` | `integer` | Target cycle time in days for complete SAE follow-up reporting (`7`). |
| `baseline_sae_days` | `integer` | Historical manual process SAE reporting cycle time (`28`). |

## 2. `ctms_sites.csv` (Format: CSV)
| Field | Type | Description |
|---|---|---|
| `site_id` | `string` | Unique clinical site identifier (`SITE-101`..`SITE-116`). |
| `study_id` | `string` | Foreign key to `ctms_studies.json`. |
| `site_name` | `string` | Synthetic institutional research center label. |
| `country_code` | `string` | ISO-2 country code (`US`, `CA`, `DE`, `UK`, `FR`, `JP`, `AU`, `SG`). |
| `region` | `string` | Operational region (`NA`, `EMEA`, `APAC`). |
| `assigned_cra_id` | `string` | Primary Site Monitor identifier (`CRA-01`..`CRA-04`). |
| `peer_cra_id` | `string` | Secondary cross-coverage CRA identifier. |
| `site_status` | `string` | `ACTIVE`, `ENROLLMENT_PAUSED`, or `REGULATORY_HOLD`. |
| `target_enrollment` | `integer` | Site-level enrollment goal. |
| `enrolled_count` | `integer` | Subjects currently randomized/enrolled at site. |
| `screened_count` | `integer` | Total candidate subjects screened at site. |
| `screen_fail_count` | `integer` | Total candidates who failed screening at site. |
| `last_monitoring_date` | `string` | ISO-8601 date (`YYYY-MM-DD`) of most recent monitoring visit. |
| `days_since_monitoring` | `integer` | Elapsed days since last monitoring visit relative to workspace reference date. |
| `days_since_last_screen` | `integer` | Elapsed days since most recent subject screening activity. |

## 3. `trial_eligibility_criteria.json` (Format: JSON Array)
| Field | Type | Description |
|---|---|---|
| `study_id` | `string` | Foreign key to `ctms_studies.json`. |
| `min_age` / `max_age` | `integer` | Inclusive age bounds in years (`<= 89`). |
| `allowed_icd10_prefixes` | `array<string>` | Permitted primary diagnosis ICD-10 prefixes. |
| `max_ecog` | `integer` | Maximum allowed ECOG performance status (`0`–`2`). |
| `max_prior_lines` | `integer` | Maximum prior systemic therapy lines permitted. |
| `min_washout_days` | `integer` | Minimum washout days required if `prior_lines_therapy > 0`. |
| `biomarker_rule` | `object` | Study-specific molecular or physiological inclusion rule. |
| `lab_thresholds` | `object` | Organ-function bounds (`min_egfr`, `min_anc`, `min_hb`, `min_platelets`, `max_bilirubin`, `max_hba1c`). |
| `exclusion_conditions` | `array<string>` | Disqualifying comorbidity codes. |

## 4. `ehr_patients.csv` (Format: CSV)
| Field | Type | Description |
|---|---|---|
| `patient_id` | `string` | Synthetic screening candidate ID (`PAT-10001`..`PAT-10140`). |
| `age_years` | `integer` | Patient age in years (`22`–`85`, strictly `<= 89`). |
| `sex` | `string` | Biological sex code (`F`, `M`). |
| `region` | `string` | Geographic region (`NA`, `EMEA`, `APAC`). |
| `candidate_site_ids` | `string` | Semicolon-delimited reachable clinical site IDs. |
| `primary_icd10` | `string` | Primary ICD-10 diagnosis code (`C34.1`, `I50.22`, `G31.84`, `K50.10`, etc.). |
| `disease_stage` | `string` | Clinical disease staging descriptor. |
| `ecog_status` | `integer` | ECOG performance score (`0`–`3`). |
| `prior_lines_therapy` | `integer` | Count of prior systemic therapy regimens (`0`–`4`). |
| `washout_days` | `integer` | Days elapsed since completion of prior regimen. |
| `comorbidities` | `string` | Semicolon-delimited comorbidity flags or `NONE`. |
| `pre_screening_consent` | `integer` | `1` if candidate authorized trial registry matching, `0` otherwise. |
| `severity_index` | `float` | Normalized clinical disease burden index (`0.0`–`100.0`) used in 3D stratification views. |

## 5. `lab_biomarkers.csv` (Format: CSV)
| Field | Type | Description |
|---|---|---|
| `patient_id` | `string` | Foreign key to `ehr_patients.csv`. |
| `collection_days_ago` | `integer` | Days elapsed since central lab specimen collection. |
| `egfr_ml_min` | `float/empty` | Estimated glomerular filtration rate (mL/min/1.73m2). |
| `anc_k_ul` | `float/empty` | Absolute neutrophil count (10^3/uL). |
| `hemoglobin_g_dl` | `float/empty` | Hemoglobin concentration (g/dL). |
| `platelets_k_ul` | `float/empty` | Platelet count (10^3/uL). |
| `bilirubin_mg_dl` | `float/empty` | Total serum bilirubin (mg/dL). |
| `hba1c_pct` | `float/empty` | Glycated hemoglobin (%). |
| `lvef_pct` | `float/empty` | Left ventricular ejection fraction (%). |
| `egfr_mutation` | `string/empty` | `POSITIVE`, `NEGATIVE`, or empty if untested. |
| `pdl1_tps_pct` | `float/empty` | PD-L1 Tumor Proportion Score (`0`–`100`). |
| `csf_ptau_pg_ml` | `float/empty` | Cerebrospinal fluid phosphorylated tau-217 (pg/mL). |
| `fecal_calprotectin_ug_g` | `float/empty` | Fecal calprotectin inflammatory marker (ug/g). |
| `biomarker_composite_z` | `float` | Standardized primary disease biomarker score (`0.0`–`100.0`) for spatial projection. |

## 6. `edc_subject_visits.csv` (Format: CSV)
| Field | Type | Description |
|---|---|---|
| `visit_record_id` | `string` | Unique eCRF visit record ID (`VIS-7001`..). |
| `subject_id` | `string` | Enrolled trial subject ID (`SUBJ-5001`..). |
| `site_id` / `study_id` | `string` | Foreign keys to site and study. |
| `visit_name` | `string` | Protocol visit milestone (`Screening`, `Baseline`, `Week 4`, `Week 12`). |
| `visit_status` | `string` | `COMPLETED`, `OVERDUE_ENTRY`, or `WITHDRAWN_EARLY`. |
| `days_out_of_window` | `integer` | Days outside protocol-specified visit window. |
| `open_queries` | `integer` | Count of unresolved EDC data management queries. |
| `max_query_age_days` | `integer` | Age in days of oldest unresolved eCRF query. |
| `signed_icf_version` | `string` | ICF version signed by the subject. |
| `icf_sign_date` | `string` | ISO-8601 date of ICF signature. |
| `screening_proc_date` | `string` | ISO-8601 date of initial screening procedure. |
| `icf_verified` | `integer` | `1` if CRA verified physical/eConsent form, `0` if pending. |
| `sdv_backlog_pages` | `integer` | Unverified eCRF source pages awaiting monitor review. |

## 7. `edc_protocol_deviations.ndjson` (Format: Newline-Delimited JSON)
Each line is a JSON object with `pd_id`, `site_id`, `study_id`, `subject_id`, `category`, `severity` (`MAJOR`/`MINOR`), `status` (`OPEN`/`IN_REVIEW`/`CLOSED`), `days_open`, `capa_required` (`0`/`1`), and `description`.

## 8. `irt_randomization_inventory.json` (Format: JSON Object/Array)
Contains per-site records with `site_id`, `study_id`, `available_kits`, `min_buffer_kits`, `resupply_in_transit` (`0`/`1`), `temp_excursion_count`, `unresolved_excursion` (`0`/`1`), and `median_rand_to_dose_days`.

## 9. `safety_sae_events.csv` (Format: CSV)
Contains `sae_id`, `site_id`, `study_id`, `subject_id`, `event_term`, `ctcae_grade` (`1`–`4`), `serious` (`0`/`1`), `onset_date`, `awareness_date`, `initial_report_hours`, `followup_cycle_days`, `report_status` (`OPEN_FOLLOWUP`, `SUBMITTED`), and `causality_assessed` (`0`/`1`).

## 10. `qms_training_issues.json` (Format: JSON Object)
Contains two arrays:
- `training_records`: `training_id`, `site_id`, `staff_role_id`, `cert_type` (`GCP`, `PROTOCOL_AMENDMENT`, `IRT_EDC_SYSTEM`), `status` (`ACTIVE`, `EXPIRED`, `EXPIRING_SOON`, `INCOMPLETE`), `days_until_expiry`.
- `quality_issues`: `issue_id`, `site_id`, `study_id`, `category`, `priority`, `status` (`OPEN`, `CAPA_IN_PROGRESS`, `CLOSED`), `days_open`, `overdue_capa` (`0`/`1`), `summary`.
