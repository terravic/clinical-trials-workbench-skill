# Clinical Domain Ontology and 32-Signal Catalog

## 1. Semantic Domain Model

The workbench normalizes heterogeneous clinical operations feeds into a unified relational ontology so deterministic rule engines and conversational analytics share identical business definitions.

| Entity | Primary Key | Originating System | Business Definition |
|---|---|---|---|
| `studies` | `study_id` | CTMS | Sponsored clinical trial protocol with target enrollment, current Informed Consent Form (ICF) version, and safety reporting SLA. |
| `sites` | `site_id` | CTMS | Investigator clinical institution executing a specific study, assigned to a primary CRA and secondary peer-coverage CRA. |
| `patients` | `patient_id` | EHR Screening Registry | De-identified candidate patient evaluated for clinical trial selection prior to formal enrollment. |
| `patient_labs` | `patient_id` | Central Laboratory | Quantitative hematology, chemistry, organ-function, and molecular biomarker assays associated with a candidate patient. |
| `edc_visits` | `visit_record_id` | EDC | Subject visit eCRF record capturing query aging, visit window adherence, ICF signature versioning, and SDV backlog. |
| `protocol_deviations` | `pd_id` | EDC / eTMF | Documented departure from the IRB-approved protocol classified as `MAJOR` or `MINOR`. |
| `irt_inventory` | `site_id` | IRT / IWRS | Site-level investigational product kit inventory, buffer thresholds, randomization-to-dosing lag, and temperature excursion status. |
| `safety_events` | `sae_id` | Safety / PV Database | Adverse Event (AE) or Serious Adverse Event (SAE) record tracking CTCAE grade, causality review, and reporting cycle time. |
| `qms_training` | `training_id` | LMS / QMS | Site staff Good Clinical Practice (GCP), protocol amendment, and system access certification state. |
| `qms_issues` | `issue_id` | QMS | Site-level quality events and Corrective and Preventive Action (CAPA) tracking items. |

---

## 2. Three-Tier Site Prioritization Rules

Following signal evaluation, every clinical site is classified into one of three operational priority bands:

1. **Priority Action Required:**
   - Any active `CRITICAL` signal, OR
   - Any active breach in the `SAFETY` or `INFORMED_CONSENT` signal families with `CRITICAL` severity, OR
   - Composite site risk score `>= 70`.
2. **Important Signals Next:**
   - Does not meet `Priority Action Required` criteria, AND
   - Has `>= 1` `HIGH` severity signal OR composite site risk score between `35` and `69`.
3. **Continue to Monitor:**
   - Zero `CRITICAL` or `HIGH` signals AND composite site risk score `< 35`.

---

## 3. Core 32-Signal Catalog Across 7 Families

All 32 rules are declaratively parameterized in `assets/schemas/signal_rules_config.json`.

### Family 1: Safety (`SAFETY`)
| Rule ID | Signal Title | Severity | Trigger Condition |
|---|---|---|---|
| `SR-SAF-01` | SAE Initial Reporting Delay (>24h) | `CRITICAL` | `serious = 1` and `initial_report_hours > 24` |
| `SR-SAF-02` | SAE Follow-Up Cycle Time Breach (>7d Target) | `CRITICAL` | `serious = 1` and `followup_cycle_days > 7` |
| `SR-SAF-03` | Extended SAE Open Lag (>21d Baseline Risk) | `CRITICAL` | `serious = 1` and `followup_cycle_days > 21` |
| `SR-SAF-04` | Unassessed Causality on Grade 3+ Event | `HIGH` | `ctcae_grade >= 3` and `causality_assessed = 0` |
| `SR-SAF-05` | Site High-Grade AE Cluster (>=2 Grade 3+ Events) | `HIGH` | Site count of `ctcae_grade >= 3` events `>= 2` |

### Family 2: Informed Consent (`INFORMED_CONSENT`)
| Rule ID | Signal Title | Severity | Trigger Condition |
|---|---|---|---|
| `SR-ICF-01` | Outdated ICF Version Not Re-Consented | `CRITICAL` | `signed_icf_version != current_icf_version` |
| `SR-ICF-02` | Screening Procedure Prior to ICF Signature | `CRITICAL` | `screening_proc_date < icf_sign_date` |
| `SR-ICF-03` | Unverified ICF Source Documentation | `HIGH` | `icf_verified = 0` |
| `SR-ICF-04` | Multi-Subject Amendment Re-Consent Backlog | `HIGH` | Site count of outdated ICF subjects `>= 2` |

### Family 3: Training Compliance (`TRAINING_COMPLIANCE`)
| Rule ID | Signal Title | Severity | Trigger Condition |
|---|---|---|---|
| `SR-TRN-01` | Expired GCP Certification on Active Staff | `CRITICAL` | `cert_type = 'GCP'` and `days_until_expiry < 0` |
| `SR-TRN-02` | Expired Protocol Amendment Training | `HIGH` | `cert_type = 'PROTOCOL_AMENDMENT'` and `days_until_expiry < 0` |
| `SR-TRN-03` | Imminent Certification Expiration (<=14d) | `MEDIUM` | `days_until_expiry` between `0` and `14` |
| `SR-TRN-04` | Incomplete EDC/IRT System Training | `MEDIUM` | `status = 'INCOMPLETE'` |

### Family 4: Data Quality & EDC (`DATA_QUALITY_EDC`)
| Rule ID | Signal Title | Severity | Trigger Condition |
|---|---|---|---|
| `SR-EDC-01` | Critical eCRF Query Aging (>21d) | `HIGH` | `max_query_age_days > 21` |
| `SR-EDC-02` | Elevated eCRF Query Aging (15-21d) | `MEDIUM` | `max_query_age_days` between `15` and `21` |
| `SR-EDC-03` | High Open Query Density (>=5 per Subject) | `MEDIUM` | `open_queries >= 5` |
| `SR-EDC-04` | Source Data Verification Backlog (>25 Pages) | `HIGH` | `sdv_backlog_pages > 25` |
| `SR-EDC-05` | Overdue Visit eCRF Data Entry | `MEDIUM` | `visit_status = 'OVERDUE_ENTRY'` |

### Family 5: Enrollment & Retention (`ENROLLMENT_RETENTION`)
| Rule ID | Signal Title | Severity | Trigger Condition |
|---|---|---|---|
| `SR-ENR-01` | Site Enrollment Severely Below Target (<40%) | `HIGH` | `(enrolled_count / target_enrollment) < 0.40` |
| `SR-ENR-02` | Elevated Screen Failure Rate (>55%) | `MEDIUM` | `screened_count >= 5` and `(screen_fail_count / screened_count) > 0.55` |
| `SR-ENR-03` | Stalled Site Screening Activity (>30d) | `MEDIUM` | `days_since_last_screen > 30` |
| `SR-ENR-04` | Early Subject Withdrawal Alert | `MEDIUM` | `visit_status = 'WITHDRAWN_EARLY'` |
| `SR-ENR-05` | Unscreened Local Eligible Candidate Pool (>=3) | `LOW` | Under-target site has `>= 3` matched `ELIGIBLE` local candidates |

### Family 6: Investigational Supply & IRT (`SUPPLY_IRT`)
| Rule ID | Signal Title | Severity | Trigger Condition |
|---|---|---|---|
| `SR-IRT-01` | Unresolved Cold-Chain Temperature Excursion | `CRITICAL` | `unresolved_excursion = 1` |
| `SR-IRT-02` | Drug Kit Inventory Below Buffer Minimum | `HIGH` | `available_kits < min_buffer_kits` |
| `SR-IRT-03` | Low Kit Buffer Without Transit Resupply | `MEDIUM` | `available_kits <= min_buffer_kits + 2` and `resupply_in_transit = 0` |
| `SR-IRT-04` | Randomization-to-First-Dose Lag (>5d) | `MEDIUM` | `median_rand_to_dose_days > 5.0` |

### Family 7: Protocol & Quality Compliance (`PROTOCOL_COMPLIANCE`)
| Rule ID | Signal Title | Severity | Trigger Condition |
|---|---|---|---|
| `SR-CMP-01` | Major Protocol Deviation Open >14 Days | `HIGH` | `severity = 'MAJOR'` and `days_open > 14` |
| `SR-CMP-02` | Unclosed Protocol Deviation Requiring CAPA | `CRITICAL` | `capa_required = 1` and `status != 'CLOSED'` |
| `SR-CMP-03` | Overdue Site CAPA Remediation | `HIGH` | `overdue_capa = 1` |
| `SR-CMP-04` | Monitoring Visit Cadence Overdue (>60d) | `HIGH` | `days_since_monitoring > 60` |
| `SR-CMP-05` | Out-of-Window Subject Visit Drift (>5d) | `LOW` | `days_out_of_window > 5` |
