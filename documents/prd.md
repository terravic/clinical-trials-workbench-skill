# Product Requirement Document: Clinical Trials System of Action and Selection Workbench Skill

**Document Version:** 1.1.0  
**Target Architecture:** Standardized Agent Skill (`SKILL.md` / CLI / Standalone 2D & 3D Web Workbench)  
**Compliance Classification:** 100% Synthetic Data, HIPAA Safe Harbor De-Identified, Non-GxP Reference Implementation  

---

## 1. User Stories and Scope

### 1.1 Executive Problem Statement
Global clinical operations teams oversee multi-center trials across 15 or more disconnected systems of record, including Clinical Trial Management Systems (CTMS), Electronic Health Record (EHR) screening registries, Central Laboratories, Electronic Data Capture (EDC), Interactive Response Technology (IRT), Pharmacovigilance/Safety databases, and Quality Management Systems (QMS). Operational staff currently contend with three structural bottlenecks:

1. **Fragmented Data Access:** Site monitors and screening coordinators manually export and reconcile datasets across separate portals to evaluate site operational health and candidate patient eligibility.
2. **Manual Risk and Eligibility Review:** Detecting emerging safety delays, informed consent mismatches, supply risks, and protocol-eligible patients requires repetitive cross-referencing across heterogeneous schemas.
3. **Disconnected Actioning:** After identifying a risk signal or eligible patient cohort, staff must manually draft visit agendas, follow-up correspondence, and issue logs without unified provenance tracking.

This skill provides an agentic "System of Action" and Clinical Trial Selection Workbench layered on top of unchanged source data feeds. It aggregates multi-source synthetic clinical datasets into a unified semantic store, executes deterministic and rule-driven risk detection across 32 signals in 7 operational families, matches synthetic patients against trial inclusion/exclusion criteria, supports transparent SQL-backed conversational analytics, and renders a self-contained, offline-capable interactive 2D/3D workbench (`dashboard.html`).

### 1.2 Target Personas

| Persona | Phase Scope | Operational Responsibility | Technical Proficiency |
|---|---|---|---|
| **Clinical Research Associate (CRA) / Site Monitor** | Phase 1 Primary | Oversees assigned clinical sites, reviews safety reporting timelines, verifies informed consent and GCP training compliance, prepares monitoring visit agendas, and follows up on open queries and deviations. | Non-technical business user interacting via natural-language prompts in an agent harness or visual controls in `dashboard.html`. |
| **Clinical Trial Screening Coordinator** | Phase 1 Primary | Screens candidate patient pools against protocol inclusion/exclusion criteria, identifies missing biomarker/lab panels, and routes eligible candidates to active, compliant clinical sites with drug inventory capacity. | Non-technical clinical user relying on criterion-by-criterion eligibility matrices and site readiness indicators. |
| **Clinical Operations System Administrator** | Phase 1 Secondary | Configures signal rules and severity thresholds (`assets/schemas/signal_rules_config.json`), validates cross-system ingestion pipelines, audits PII/PHI guardrails, and runs automated verification suites. | Technical administrator comfortable with JSON configuration schemas and CLI execution. |
| **Central Monitor / Clinical Scientist / Leadership** | Phase 2 Expansion | Conducts cross-study benchmarking, cohort trend analysis, and portfolio resource allocation across global regions. | Analytical user leveraging conversational SQL transparency and portfolio heatmaps. |

### 1.3 Core User Stories

1. **US-01 (Portfolio Triage):** As a CRA, I want a single portfolio summary ("morning coffee" digest) grouping my assigned and peer-coverage sites into *Priority Action Required*, *Important Signals Next*, and *Continue to Monitor* tiers so that I focus first on critical safety and aging compliance risks.
2. **US-02 (SAE Cycle-Time Reduction):** As a CRA, I want automated detection of Serious Adverse Events (SAEs) exceeding the 24-hour initial awareness window or 7-day follow-up reporting target (down from a 28-day historical baseline) so that I can intervene before regulatory reporting breaches occur.
3. **US-03 (Single-Site Drill-Down and Drafting):** As a CRA preparing for a site visit, I want to inspect a single site's active signals, events, issues, protocol deviations, and candidate patients, and generate a structured monitoring visit agenda and follow-up email draft from selected items.
4. **US-04 (Signal Evidence Audit and Feedback):** As a CRA reviewing an elevated signal, I want full traceability to the originating source file, record ID, observed metric, threshold rule, and 8-week site signal history, along with controls to Accept, Modify, or Dismiss the recommended action and to export or erase my persisted reviewer feedback records.
5. **US-05 (Multi-Source Clinical Trial Selection):** As a Screening Coordinator, I want to evaluate synthetic patients across EHR demographics, diagnoses, and central lab biomarkers against structured protocol inclusion/exclusion criteria, distinguishing fully eligible candidates from those missing a single lab panel, and matching them to compliant clinical sites.
6. **US-06 (Transparent Conversational Analytics):** As a Clinical Operations Analyst, I want to ask natural-language questions about study performance, signal trends, and patient eligibility and receive a narrative answer alongside the exact read-only SQL query, tabular dataset, and chart visualization.
7. **US-07 (Interactive Visual Workbench):** As an end user invoking this skill, I want a self-contained, WCAG 2.2 AA-compliant interactive workbench (`dashboard.html`) with self-hosted typography, Light/Dark/System themes, interactive 2D/3D plots, network node maps, keyboard-accessible tables and tabs, and offline or Server-Sent Events (SSE) execution with zero third-party network calls.

### 1.4 Scope Boundaries

| Capability Area | In-Scope (Phase 1 Implementation) | Out-of-Scope (Phase 2+ Backlog) |
|---|---|---|
| **Data Ingestion** | Local ingestion of CSV, JSON, and NDJSON feeds representing CTMS, Protocols, EHR, Central Labs, EDC, IRT, Safety/PV, and QMS into a unified in-memory SQLite semantic store. | Live OAuth/REST connectors to commercial cloud CTMS/EDC vendors (such as Veeva Vault, Medidata Rave, or Oracle Siebel). |
| **Patient Trial Selection** | Deterministic criterion-by-criterion evaluation across age, ICD-10 prefix, ECOG, washout periods, biomarkers, organ function labs, exclusions, and site placement scoring. | Unstructured free-text pathology report OCR or wet-lab assay ordering integration. |
| **Signal Intelligence** | Config-driven evaluation of 32 signal rules across 7 signal families with severity weighting, 8-week trend synthesis, and 3-tier site prioritization. | Online reinforcement learning weight updates from historical production telemetry. |
| **Action Execution & Privacy Rights** | Generation of copy-ready monitoring visit agendas, follow-up emails, and JSON issue payloads with atomic local human-in-the-loop (HITL) feedback persistence plus reviewer data export and erasure workflows. | Direct write-back API mutations or browser computer-use RPA into external GxP source systems. |
| **Analytics, Security & UI** | Hardened read-only NL-to-SQL engine, standalone WCAG 2.2 AA `dashboard.html` with self-hosted WOFF2 fonts, hardened local HTTP/SSE server with strict headers/auth/validation, and draft legal templates (`/privacy`, `/terms`, `/refunds`, `/cookies`). | Multi-tenant SSO identity provider integration and external TLS reverse proxy provisioning (operator responsibility for non-local deployments). |

---

## 2. Technical Architecture

### 2.1 Four-Layer System Architecture (ASCII)

```text
+--------------------------------------------------------------------------------------------------+
| LAYER 1: ROLE & PRESENTATION LAYER                                                               |
|   - Agent Harness Conversational Interface (via SKILL.md operational runbooks)                   |
|   - Standalone 2D/3D Workbench (dashboard.html: Bento UI, Self-Hosted Fonts, WCAG 2.2 AA)        |
|   - Five Views: L1 Portfolio | L2 Single-Site | L3 Signal Evidence | Trial Selection | Analytics |
|   - Hardened HTTP + SSE Server (CSP/HSTS/Host/Origin/Bearer Auth/Schema Validation/Legal Routes) |
+--------------------------------------------------------------------------------------------------+
                                                |
                                                v
+--------------------------------------------------------------------------------------------------+
| LAYER 2: INTELLIGENCE & ACTION LAYER (scripts/)                                                  |
|   - trial_selector.py      : Multi-trial Inclusion/Exclusion matcher & site placement scorer     |
|   - signal_engine.py       : 32-rule Listen->Detect->Assess evaluator across 7 signal families   |
|   - action_drafter.py      : Briefings, agendas, emails, atomic HITL store, export & erasure     |
|   - analytics_assistant.py : Intent-to-SQL translator + SQLite authorizer & VM opcode sandbox    |
+--------------------------------------------------------------------------------------------------+
                                                |
                                                v
+--------------------------------------------------------------------------------------------------+
| LAYER 3: SEMANTIC & GOVERNANCE LAYER (references/, assets/schemas/, legal/)                      |
|   - Unified SQLite Relational Ontology (in-memory :memory: store; parameterized SQL bindings)    |
|   - signal_rules_config.json : Declarative thresholds, severity weights, and action templates    |
|   - PIIGuardrailScanner      : HIPAA Safe Harbor & schema validation gate with redacted logs     |
|   - legal/*.md               : Draft legal templates (privacy, terms, refunds, cookies)          |
+--------------------------------------------------------------------------------------------------+
                                                |
                                                v
+--------------------------------------------------------------------------------------------------+
| LAYER 4: FRAGMENTED SOURCE DATA & SELF-HOSTED ASSET LAYER (assets/)                              |
|   [CTMS: .json/.csv]  [Protocol I/E: .json]  [EHR Patients: .csv]  [Central Labs: .csv]          |
|   [EDC Visits: .csv]  [EDC PDs: .ndjson]     [IRT Supply: .json]   [Safety PV: .csv] [QMS: .json]|
|   [Self-Hosted SIL OFL WOFF2 Fonts: assets/fonts/*.woff2]                                        |
+--------------------------------------------------------------------------------------------------+
```

### 2.2 Operational Execution Sequence (ASCII)

```text
User / Harness          SKILL.md          workbench_cli.py       data_ingestor.py       SQLite / Engines       dashboard.html
      |                    |                     |                      |                      |                     |
      |-- User Request --->|                     |                      |                      |                     |
      |                    |-- Select Runbook -->|                      |                      |                     |
      |                    |                     |-- Load Feeds ------->|                      |                     |
      |                    |                     |                      |-- PII & Schema Check |                     |
      |                    |                     |                      |-- Populate Tables -->|                     |
      |                    |                     |-- Execute Command ---|--------------------->|                     |
      |                    |                     |   (portfolio / site /|                      |-- Evaluate 32 Rules |
      |                    |                     |    select-patients / |                      |-- Match Patients    |
      |                    |                     |    ask / draft /     |                      |-- Guarded SELECT    |
      |                    |                     |    build-dashboard)  |<-- Structured JSON --|                     |
      |                    |                     |-- Atomic Emit -------|----------------------|-------------------->|
      |<-- Grounded Report + Provenance ---------|                      |                      |                     |
      |<-- Offline / Hardened HTTP+SSE Inspection (Zero External Calls) |----------------------|---------------------|
```

---

## 3. Functional Requirements

### 3.1 Multi-Source Synthetic Data Inputs (`FR-DATA`)
The ingestion pipeline (`scripts/data_ingestor.py`) shall consume ten synthetic clinical data files (representing eight source clinical systems) stored in `assets/synthetic_data/` (or a custom directory specified via `--data-dir` or `CTW_DATA_DIR`):

1. **`ctms_studies.json` (JSON):** Study metadata (`study_id`, `protocol_code`, `title`, `phase`, `therapeutic_area`, `target_enrollment`, `current_icf_version`, `sae_target_days`). Models 4 studies: `NCT-SYN-0101` (Phase III NSCLC), `NCT-SYN-0204` (Phase IIb Heart Failure), `NCT-SYN-0309` (Phase II Prodromal Neurodegeneration), and `NCT-SYN-0412` (Phase III Autoimmune IBD).
2. **`ctms_sites.csv` (CSV):** 16 clinical sites (`SITE-101` through `SITE-116`) across `NA`, `EMEA`, and `APAC`, containing `study_id`, `country_code`, `region`, `assigned_cra_id`, `peer_cra_id`, `site_status`, `target_enrollment`, `enrolled_count`, `screened_count`, `screen_fail_count`, and `last_monitoring_date`.
3. **`trial_eligibility_criteria.json` (JSON):** Per-study structured eligibility specifications (`min_age`, `max_age`, `allowed_icd10_prefixes`, `max_ecog`, `min_washout_days`, `max_prior_lines`, `required_biomarkers`, `lab_thresholds`, `exclusion_conditions`).
4. **`ehr_patients.csv` (CSV):** 140 synthetic candidate patients (`PAT-10001` through `PAT-10140`) with `age_years` (22–85), `sex`, `region`, `candidate_site_ids`, `primary_icd10`, `disease_stage`, `ecog_status`, `prior_lines_therapy`, `washout_days`, `comorbidities`, and `pre_screening_consent`.
5. **`lab_biomarkers.csv` (CSV):** Quantitative lab panels and molecular biomarkers keyed by `patient_id` (`egfr_ml_min`, `anc_k_ul`, `hemoglobin_g_dl`, `platelets_k_ul`, `bilirubin_mg_dl`, `hba1c_pct`, `lvef_pct`, `egfr_mutation`, `pdl1_tps_pct`, `csf_ptau_pg_ml`, `fecal_calprotectin_ug_g`, `collection_days_ago`).
6. **`edc_subject_visits.csv` (CSV):** Enrolled subject records (`subject_id`, `site_id`, `study_id`, `visit_name`, `visit_status`, `days_out_of_window`, `open_queries`, `max_query_age_days`, `signed_icf_version`, `icf_sign_date`, `screening_proc_date`, `sdv_backlog_pages`).
7. **`edc_protocol_deviations.ndjson` (NDJSON):** Line-delimited protocol deviation logs (`pd_id`, `site_id`, `study_id`, `subject_id`, `category`, `severity`, `days_open`, `description`, `capa_required`).
8. **`irt_randomization_inventory.json` (JSON):** Site investigational product (IP) inventory (`available_kits`, `min_buffer_kits`, `resupply_in_transit`, `temp_excursion_count`, `unresolved_excursion`, `median_rand_to_dose_days`).
9. **`safety_sae_events.csv` (CSV):** Pharmacovigilance records (`sae_id`, `site_id`, `study_id`, `subject_id`, `event_term`, `ctcae_grade`, `serious`, `onset_date`, `awareness_date`, `initial_report_hours`, `followup_cycle_days`, `report_status`, `causality_assessed`).
10. **`qms_training_issues.json` (JSON):** Site staff training compliance (`staff_role_id`, `cert_type`, `status`, `days_until_expiry`) and site quality issues/CAPAs (`issue_id`, `category`, `priority`, `status`, `days_open`, `overdue_capa`).

### 3.2 Clinical Trial Selection Engine (`FR-SELECT`)
1. **Criterion Evaluation:** For each `(patient, study)` pair, `scripts/trial_selector.py` shall evaluate every inclusion and exclusion rule, emitting structured rule outcomes (`PASS`, `FAIL`, `MISSING_DATA`) with observed value vs. protocol requirement.
2. **Classification Rules:**
   - `ELIGIBLE`: Zero `FAIL` outcomes and zero `MISSING_DATA` outcomes across all required criteria.
   - `POTENTIAL_NEEDS_LABS`: Zero `FAIL` outcomes, with one or more `MISSING_DATA` or stale lab (>30 days) outcomes that can be resolved via central lab draw.
   - `INELIGIBLE`: One or more explicit `FAIL` outcomes on inclusion or exclusion rules.
3. **Match Scoring & Site Placement:** Compute a deterministic 0–100 composite match score and rank candidate clinical sites in the patient's region based on active site status, remaining enrollment capacity, IRT kit availability (`available_kits > min_buffer_kits` and no unresolved temperature excursion), and operational priority tier.

### 3.3 Listen -> Detect -> Assess Signal Engine (`FR-SIGNAL`)
1. **Config-Driven Rules:** `scripts/signal_engine.py` shall load `assets/schemas/signal_rules_config.json` defining 32 signal rules across 7 signal families:
   - `SAFETY` (5 rules)
   - `INFORMED_CONSENT` (4 rules)
   - `TRAINING_COMPLIANCE` (4 rules)
   - `DATA_QUALITY_EDC` (5 rules)
   - `ENROLLMENT_RETENTION` (5 rules)
   - `SUPPLY_IRT` (4 rules)
   - `PROTOCOL_COMPLIANCE` (5 rules)
2. **Evidence & Auditability:** Every triggered signal shall include exact source file name, primary key(s), observed numerical/categorical value, rule threshold, severity (`CRITICAL`, `HIGH`, `MEDIUM`, `LOW`), composite risk score, 8-week site trend array, rule rationale, and concrete recommended CRA actions.
3. **Three-Tier Site Prioritization:**
   - `Priority Action Required`: Any site with >= 1 `CRITICAL` signal, an active `SAFETY` or `INFORMED_CONSENT` breach, or composite risk score >= 70.
   - `Important Signals Next`: Sites without critical safety/consent breaches that have >= 1 `HIGH` signal or composite risk score between 35 and 69.
   - `Continue to Monitor`: Sites with only `MEDIUM`/`LOW` or zero active signals (composite risk score < 35).

### 3.4 Human-in-the-Loop Actioning and Data Rights (`FR-ACTION`)
1. **Level 1 Executive Summary:** Synthesize portfolio KPIs, SAE reporting cycle-time comparison against the 7-day target, priority tier counts, and eligible patient screening opportunities.
2. **Level 2 Site Visit Agendas & Follow-Up Emails:** Compile selected site signals, aging queries, protocol deviations, and screening candidates into structured markdown agendas and plain-text emails.
3. **Level 3 Issue Payloads & Atomic Feedback Loop:** Export structured JSON issue/PD templates and persist reviewer feedback (`ACCEPT`, `MODIFY`, `DISMISS`, reviewer ID, timestamp, rationale) atomically to the workspace state file without mutating source feeds.
4. **Data Portability & Erasure:** Provide verified `export_reviewer_data(reviewer_id)` and `delete_reviewer_data(reviewer_id)` functions in `scripts/action_drafter.py`, owner-authorized HTTP endpoints (`GET /api/user-data/export` and `POST /api/user-data/delete`), and browser footer controls to export or purge `localStorage` entries.

### 3.5 Conversational Analytics Assistant (`FR-ANALYTICS`)
1. **Natural-Language & Direct SQL Support:** Translate domain questions into parameterized SQLite `SELECT` queries or execute user-provided read-only `SELECT` statements against the semantic store.
2. **Full Transparency Contract:** Every response shall return: (a) narrative synthesis, (b) chart configuration with semantic palette mappings, (c) exact underlying rows and column headers, and (d) the executed SQL query string and referenced source feeds.

### 3.6 Interactive 2D/3D Workbench (`FR-UI`)
1. **Self-Contained Output:** `scripts/build_dashboard.py` shall atomically produce a standalone `dashboard.html` at the project root capable of running offline (`file://`) and over HTTP with SSE (`/events`), making zero external third-party network requests.
2. **Design System & Self-Hosted Typography (`DESIGN.md`):**
   - Self-hosted SIL OFL WOFF2 font files under `assets/fonts/` loaded via local `@font-face` rules for `DM Sans`, `Instrument Serif`, and `IBM Plex Mono` (`tabular-nums` on tables/metrics).
   - Segmented three-icon SVG theme switcher (`#theme-btn-light`, `#theme-btn-dark`, `#theme-btn-system`) with safe `localStorage` persistence, `matchMedia('(prefers-color-scheme: dark)')` live tracking, and host OS `system_theme` fallback.
   - Exact Light (`#FAFAF9`, `#FFFFFF`, `#E7E5E4`, `#1C1917`, `#787774`) and Dark (`#0D0E11`, `#16171D`, `#292A30`, `#ECE8E2`, `#9A9895`) surface tokens in a flat single-level bento grid, paired with WCAG 2.2 AA foreground tokens (`>= 4.5:1` contrast) on buttons, muted labels, KPIs, and status badges.
   - Semantic data visualization palette: Vibrant Teal (`#0D9488`), Energetic Amber (`#D97706`), Vivid Indigo (`#4F46E5`), Functional Emerald (`#059669`), and Deep Rose (`#E11D48`).
   - Interactive `<canvas>` visualizations: (1) Cross-Site Signal Concentration Heatmap, (2) Multi-Week Signal & SAE Cycle-Time Trend Chart, (3) Interactive Relationship Node Map, (4) 3D Biomarker & Eligibility Structural Projection, and (5) Analytical Series Plot.
3. **Accessibility & Legal Routes:**
   - Semantic HTML landmarks (`<header>`, `<nav>`, `<main>`, `<footer>`), skip-to-main link, strict `h1 -> h2` heading hierarchy, visible `:focus-visible` indicators, keyboard-operable tabs/subtabs/sortable headers/patient rows, explicit bracketed text status tags (`[CRITICAL]`, `[ELIGIBLE]`, `[PASS]`, `[OFFLINE]`, `[LIVE SSE]`), and accessible names on inputs and plot surfaces.
   - Local HTTP server exposes draft legal documents (`legal/*.md`, marked `DRAFT — REQUIRES LEGAL REVIEW`) at `/privacy`, `/terms`, `/refunds`, and `/cookies`.

---

## 4. Non-Functional Requirements

| Requirement ID | Category | Specification |
|---|---|---|
| **NFR-PERF-01** | CLI Latency | Full multi-source ingestion, 32-rule signal evaluation, and 140-patient x 4-study eligibility matching shall complete in under 2.0 seconds on standard workstation hardware. |
| **NFR-PERF-02** | UI Responsiveness | `dashboard.html` theme switches, table filters, and `<canvas>` pan/zoom/3D rotation interactions shall render within 16ms–32ms per frame without resetting scroll position or active selections. |
| **NFR-SEC-01** | SQL Sandboxing | `analytics_assistant.py` shall enforce a multi-layer read-only barrier: lexical statement validation (single `SELECT`/`WITH`, max 4,096 chars), SQLite C-API `set_authorizer` blocking writes/schema/dangerous functions (`randomblob`, `zeroblob`, `load_extension`) and `sqlite_*` tables, plus a `set_progress_handler` VM opcode budget (`250,000` steps). |
| **NFR-SEC-02** | HTTP/SSE Hardening | `workbench_cli.py` shall emit strict security headers (`Content-Security-Policy`, `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cross-Origin-Resource-Policy: same-origin`), validate `Host` and same-origin `Origin` headers, enforce 64 KB body limits and strict key-allowlist JSON schema validation, cap SSE subscribers (`32`), require `CTW_API_TOKEN` for non-loopback binds and data-rights routes, and enforce owner authorization on reviewer data export/erasure. |
| **NFR-PRIV-01** | HCLS / HIPAA Safe Harbor | Zero PHI or PII across code, tests, templates, and data. All synthetic patient ages must be <= 89, identifiers must use non-reversible synthetic codes, email placeholders must use `.example` reserved domains, and PII guardrail diagnostics must redact raw offending values. |
| **NFR-A11Y-01** | WCAG 2.2 AA | All text, muted labels, status badges, buttons, and focus indicators in Light and Dark themes achieve `>= 4.5:1` contrast; all interactive controls support keyboard navigation and visible focus rings; states use explicit text tags alongside color. |
| **NFR-PORT-01** | Environment Portability & Asset Isolation | Zero hardcoded absolute paths and zero outbound third-party asset requests (fonts self-hosted under `assets/fonts/`). All paths resolve relative to the repository root with overrides via CLI flags and `CTW_*` environment variables. Core runtime uses Python 3.10+ standard library only. |
| **NFR-EXT-01** | Extensibility | Adding a new signal rule or trial eligibility criterion requires only editing `signal_rules_config.json` or `trial_eligibility_criteria.json` without modifying Python engine logic. |

---

## 5. Error Handling and Edge Cases Matrix

| Failure / Edge Case Scenario | Detection Mechanism | System Behavior (Strict Mode) | System Behavior (Quarantine / Operational Mode) | Exit Code / User Feedback |
|---|---|---|---|---|
| **Missing Source Data File** | `data_ingestor.py` checks file existence and readability prior to ingestion. | Raises `MissingDataSourceError` with exact missing path. | Halts pipeline cleanly before partial state output; logs actionable remediation command (`generate-data`). | Non-zero CLI exit code (`2`); structured JSON/text error message. |
| **Corrupted CSV / JSON / NDJSON Syntax** | `csv.DictReader` row-length check, `json.loads` line/document parser with line-number tracking. | Raises `MalformedRecordError` citing file name and 1-indexed line number. | Logs warning, records malformed payload in `validation_quarantine` SQLite table, and continues processing valid records. | Exit code `2` in `--strict` mode; exit code `0` with quarantine count in operational mode. |
| **PII / PHI Guardrail Violation** | `PIIGuardrailScanner` checks every field for SSN patterns, phone numbers, real email domains, and `age_years > 89`. | Immediately raises `PIIComplianceViolationError` and aborts DB commit. | Quarantines offending row, redacts raw field values in audit log, and blocks row from entering semantic tables or `dashboard.html`. | Exit code `3` in `--strict` mode; redacted audit entry in quarantine table. |
| **Missing Patient Biomarker or Stale Lab Panel** | `trial_selector.py` checks `None`/empty fields and `collection_days_ago > 30`. | N/A (valid clinical screening state). | Marks specific criterion as `MISSING_DATA` or `STALE_LAB`, classifies patient as `POTENTIAL_NEEDS_LABS` (if no hard exclusions fail), and lists exact required lab assays. | Included in selection output with diagnostic action item. |
| **Out-of-Bounds / Negative Clinical Lab Values** | Range validator checks physiological bounds (e.g., `egfr_ml_min < 0` or `pdl1_tps_pct > 100`). | Raises `ClinicalRangeValidationError` with field name and value. | Quarantines invalid lab record and treats patient's lab value as unverified (`MISSING_DATA`) to prevent false-positive trial enrollment. | Logged in validation provenance summary. |
| **Unsafe, Mutating, or Expensive SQL Query** | Lexical validator + SQLite `set_authorizer` whitelist + `set_progress_handler` opcode ceiling. | Raises `SQLSafetyError`; query is blocked or aborted cleanly. | Rejects query, preserves database integrity, and returns safe error explanation in CLI/UI response payload. | Exit code `4` on CLI; `400 Bad Request` on HTTP API; inline alert in UI. |
| **Invalid HTTP Payload, Unknown Keys, or Unauthorized Access** | `workbench_cli.py` validates `Host`, `Origin`, Bearer token, reviewer owner, `Content-Length` (<= 64 KB), and strict key allowlists. | Rejects malformed or unauthorized requests before touching state or SQLite. | Returns `400` (schema/size), `401` (missing auth), `403` (bad token, cross-origin, bad host, non-owner), `413` (oversized), or `503` (SSE cap). | Structured JSON error response without internal path reflection. |
| **Unknown Study, Site, or Signal Identifier** | Parameter lookup against `studies`, `sites`, and `signals` tables. | Raises `EntityNotFoundError` listing valid available identifiers. | Returns structured empty result with valid identifier suggestions. | Exit code `5` on CLI; informative empty-state message in UI. |
| **Corrupted HITL Feedback State File** | JSON parse + schema validation in `action_drafter.py`. | Raises `StateFileError`. | Backs up unreadable file to `.corrupt.bak`, initializes clean empty feedback store, and writes updates atomically via `os.replace`. | Operational continuity preserved; warning emitted to stderr. |
| **Headless Linux / Restricted Webview OS Theme Check** | `build_dashboard.py` catches `FileNotFoundError` / `subprocess.SubprocessError` on OS theme probes. | N/A (expected in container/CI environments). | Defaults embedded `system_theme` fallback to `"light"` while browser `matchMedia` resolves client OS preference when available. | Clean `dashboard.html` build (`0` exit code). |
