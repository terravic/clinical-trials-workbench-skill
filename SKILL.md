---
name: clinical-trials-workbench
description: >-
  Use this skill when the user asks to screen or match patients to clinical trials,
  triage clinical trial site risk signals, monitor cross-system CTMS/EDC/IRT/Safety/QMS
  feeds, draft CRA monitoring visit agendas or site follow-up communications, run
  transparent SQL analytics over trial operations, or build and launch an interactive
  clinical trials workbench dashboard (dashboard.html).
---

# Clinical Trials System of Action and Selection Workbench

## 1. Purpose and Operational Model

This skill equips an AI agent to act as an enterprise Clinical Operations "System of Action" and Clinical Trial Selection coordinator over multi-source clinical feeds. It executes a four-stage **Listen -> Detect -> Assess -> Action** workflow paired with deterministic **Patient-to-Trial Eligibility Matching** and an interactive HTML5 Canvas UI (`dashboard.html`).

All commands execute relative to the skill root directory using `python3 scripts/workbench_cli.py` and operate strictly on de-identified, HIPAA Safe Harbor-compliant data.

---

## 2. Reference Architecture and Progressive Disclosure

Consult these reference files on demand when deeper schema or domain rules are required:

- **Product Requirement Document:** `documents/prd.md`
- **32-Signal Catalog & Site Tiering Rules:** `references/clinical_ontology_and_signals.md`
- **Trial Inclusion/Exclusion & Site Placement Scoring:** `references/eligibility_criteria_guide.md`
- **Cross-System Source Schemas (10 Feeds):** `references/data_dictionary.md`
- **Declarative Signal Thresholds:** `assets/schemas/signal_rules_config.json`
- **UI Visual Specification:** `DESIGN.md`

---

## 3. Core Tooling and Execution Runbooks

Always run commands from the project root directory. Append `--format json` when programmatic parsing is needed, or use default `--format markdown` for grounded human-readable tables.

### Runbook A: Initialize or Refresh Synthetic Multi-Source Feeds
If `assets/synthetic_data/` is empty or the user requests fresh synthetic data generation across CTMS, Protocol Registry, EHR Screening, Central Labs, EDC, IRT, Safety/PV, and QMS:

```bash
python3 scripts/workbench_cli.py generate-data --seed 42
```

### Runbook B: Level 1 Portfolio Triage ("Morning Coffee" Briefing)
When a Site Monitor (CRA) or Clinical Operations Lead asks what requires attention across their portfolio or specific CRA assignment (`CRA-01` through `CRA-04`, or `ALL`):

```bash
python3 scripts/workbench_cli.py portfolio --cra ALL
python3 scripts/workbench_cli.py portfolio --cra CRA-01 --tier "Priority Action Required"
```

**Expected Agent Response Structure:**
1. Present the executive summary contrasting closed Serious Adverse Event (SAE) turnaround against the 7-day target (and 28-day historical baseline).
2. Group sites into *Priority Action Required*, *Important Signals Next*, and *Continue to Monitor*.
3. Highlight the top risk-scoring sites and offer to drill into Level 2 site preparation or candidate referrals.

### Runbook C: Clinical Trial Patient Selection and Site Placement
When the user asks to find eligible synthetic patients for a trial (`NCT-SYN-0101`, `NCT-SYN-0204`, `NCT-SYN-0309`, `NCT-SYN-0412`), identify patients needing central lab refreshes (`POTENTIAL_NEEDS_LABS`), or inspect a specific candidate's inclusion/exclusion audit trail:

```bash
# List fully eligible candidates for a specific study
python3 scripts/workbench_cli.py select-patients --study-id NCT-SYN-0101 --status ELIGIBLE

# Identify high-potential candidates missing only a biomarker or recent lab draw
python3 scripts/workbench_cli.py select-patients --status POTENTIAL_NEEDS_LABS --region NA

# Audit all 14 inclusion/exclusion checks for a single synthetic patient
python3 scripts/workbench_cli.py select-patients --patient-id PAT-10001
```

**Expected Agent Response Structure:**
1. Report the study eligibility funnel (`ELIGIBLE`, `POTENTIAL_NEEDS_LABS`, `INELIGIBLE`).
2. Cite exact patient IDs, match scores (0-100), primary ICD-10 diagnosis, biomarker evidence, and recommended active site ID (noting that sites on enrollment pause or with unresolved IRT temperature excursions are automatically excluded from placement).

### Runbook D: Level 2 Single-Site Drill-Down, Visit Agendas, and Follow-Up Emails
When preparing for a monitoring visit or drafting follow-up communications for a specific site (`SITE-101`..`SITE-116`):

```bash
python3 scripts/workbench_cli.py site --site-id SITE-101 --draft-agenda --draft-email
```

Optionally restrict drafts to specific signals via `--signals SIG-1001,SIG-1005`. Present drafted communications clearly as Phase 1 Human-in-the-Loop drafts requiring monitor verification prior to external dispatch.

### Runbook E: Level 3 Signal Evidence Audit and Human-in-the-Loop Feedback
When auditing the exact source-system record behind a signal (`SIG-1001`..), exporting an issue/CAPA JSON record, or recording a monitor's decision (`ACCEPT`, `MODIFY`, `DISMISS`):

```bash
# Inspect evidence provenance and generate exportable issue JSON
python3 scripts/workbench_cli.py signal --signal-id SIG-1001 --draft-issue

# Persist human-in-the-loop disposition and rebuild dashboard
python3 scripts/workbench_cli.py signal --signal-id SIG-1001 --feedback ACCEPT --reviewer CRA-01 --note "Scheduled 48h safety reconciliation call with PI"
python3 scripts/workbench_cli.py build-dashboard
```

### Runbook F: Transparent Conversational Analytics (NL-to-SQL)
When the user poses an open-ended quantitative question across clinical/operational datasets:

```bash
# Natural-language intent routing
python3 scripts/workbench_cli.py ask --question "Compare SAE follow-up reporting cycle times across studies"

# Custom verified read-only SQLite SELECT query
python3 scripts/workbench_cli.py ask --sql "SELECT site_id, protocol_code, max_query_age_days, sdv_backlog_total FROM site_assessments WHERE max_query_age_days > 15 ORDER BY max_query_age_days DESC"
```

**Transparency Mandate:** Always surface four elements to the user: (1) narrative synthesis, (2) exact executed SQL query, (3) underlying tabular rows, and (4) originating source files.

### Runbook G: Build or Serve the Interactive HTML5 Canvas Workbench (`dashboard.html`)
Whenever the user requests a visual UI, interactive charts, 3D biomarker exploration, or network relationship views, generate `dashboard.html` at the project root:

```bash
# Build standalone offline-capable dashboard.html
python3 scripts/workbench_cli.py build-dashboard --output dashboard.html

# Optional: Launch local HTTP server with live Server-Sent Events (/events)
python3 scripts/workbench_cli.py serve --host 127.0.0.1 --port 8765
```

---

## 4. Safety, Privacy, and Formatting Guardrails

1. **HCLS Privacy Compliance:** Never introduce real or realistic patient names, SSNs, phone numbers, street addresses, dates of birth, or ages above 89 into custom data files or prompts. If `PIIComplianceViolationError` (exit code `3`) occurs under `--strict`, halt processing and alert the operator.
2. **Read-Only Source Integrity:** Phase 1 operates strictly read-only against source clinical feeds. Never attempt `INSERT`, `UPDATE`, `DELETE`, or `DROP` statements via `ask --sql` (`SQLSafetyError`, exit code `4`).
3. **Human Decision Authority:** All drafted visit agendas, site follow-up emails, trial referrals, and CAPA issue exports must remain subject to explicit human review before clinical or regulatory action.
4. **Professional Clinical Tone:** Present outputs in clean markdown tables and concise technical prose without emojis or decorative icons.
