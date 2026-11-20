# Privacy Notice

**DRAFT — REQUIRES LEGAL REVIEW**

> **Notice:** This document is a template prepared for review by qualified legal counsel. Replace all bracketed placeholder tokens and verify jurisdictional compliance before external publication.

- **Data Controller:** `[ORGANIZATION_LEGAL_NAME]`
- **Registered Address:** `[REGISTERED_ADDRESS]`
- **Privacy Contact:** `[PRIVACY_CONTACT_EMAIL]`
- **Data Protection Officer (if applicable):** `[DPO_CONTACT_EMAIL]`
- **Effective Date:** `[EFFECTIVE_DATE]`

---

## 1. Scope and Clinical Data Classification

The Clinical Trials Workbench (`clinical-trials-workbench`) is a local, non-GxP reference implementation designed to operate exclusively on synthetic, de-identified datasets compliant with HIPAA Safe Harbor age and identifier constraints. Operators must not load real Protected Health Information (PHI) or real patient Personally Identifiable Information (PII) into this software unless an independent compliance assessment, technical hardening review, and binding Data Processing Agreements are completed by `[ORGANIZATION_LEGAL_NAME]`.

## 2. Categories of Data Processed

| Data Category | Storage Location | Purpose | Retention Period |
|---|---|---|---|
| **Synthetic Clinical Feeds** | Local files under `assets/synthetic_data/` and ephemeral in-memory SQLite (`:memory:`) | Demonstration of signal detection, trial eligibility matching, and read-only SQL analytics | Ephemeral in memory; static synthetic files on disk |
| **Reviewer Feedback Records** (`reviewer_id`, `signal_id`, `disposition`, `note`, `updated_at`) | Local JSON state file (`assets/workbench_feedback_state.json`) and optional browser `localStorage` (`ctw_hitl_feedback`) | Recording human-in-the-loop signal dispositions | `[RETENTION_PERIOD_DAYS]` days or until erased via erasure workflow |
| **Interface Preferences** (`ctw_theme_mode`) | Browser `localStorage` | Persisting Light, Dark, or System display theme | Until cleared by user |
| **Local HTTP Request Metadata** | Ephemeral request handling in `scripts/workbench_cli.py` (loopback `127.0.0.1` by default) | Serving local dashboard assets and API responses; IP addresses are not persisted to disk logs by default | Transient (not persisted) |

## 3. Third-Party Processors and Cross-Border Data Transfers

In its default local configuration, this software makes **zero outbound network requests** to third-party web font hosts, CDNs, analytics vendors, advertising networks, or external API providers. All web fonts (`DM Sans`, `IBM Plex Mono`, `Instrument Serif`) and visualization scripts are self-hosted locally.

If `[ORGANIZATION_LEGAL_NAME]` deploys this software on hosted infrastructure or integrates external systems, complete the processor inventory below prior to launch:

| Processor Name | Processing Role | Physical Hosting Region | Transfer Mechanism (EEA/UK) | DPA Status |
|---|---|---|---|---|
| `[HOSTING_PROVIDER_NAME]` | `[COMPUTE_AND_STORAGE_ROLE]` | `[HOSTING_REGION_E_G_EU_WEST]` | `[ADEQUACY_OR_SCC_OR_DPF]` | `[DPA_EXECUTION_DATE]` |
| `[LLM_OR_API_PROVIDER_NAME]` | `[OPTIONAL_MODEL_OR_API_ROLE]` | `[ENDPOINT_REGION_E_G_EUROPE_WEST]` | `[ADEQUACY_OR_SCC_OR_DPF]` | `[DPA_EXECUTION_DATE]` |

## 4. Data Subject Rights (Access, Portability, and Erasure)

Subject to applicable law (including GDPR Articles 15-20 and `[APPLICABLE_REGIONAL_PRIVACY_LAW]`), individuals whose reviewer records are persisted by an operator may request:

- **Access and Portability (JSON Export):** Export all persisted reviewer feedback records via `export_reviewer_data(reviewer_id)` or authenticated `GET /api/user-data/export?reviewer_id=<id>`, and export browser `localStorage` entries via the dashboard footer control.
- **Erasure (Right to be Forgotten):** Permanently purge all persisted reviewer feedback records via `delete_reviewer_data(reviewer_id)` or authenticated `POST /api/user-data/delete`, and clear browser `localStorage` entries via the dashboard footer control.
- **Response SLA:** `[ORGANIZATION_LEGAL_NAME]` will respond to verified privacy requests within **30 calendar days** (or shorter period mandated by local statute).

Submit written requests to `[PRIVACY_CONTACT_EMAIL]` or `[DPO_CONTACT_EMAIL]`.

## 5. Security Controls

- Read-only SQLite authorizer sandbox and VM opcode budget for analytical queries.
- Strict server-side JSON schema validation, request body limits, same-origin `Host`/`Origin` verification, security HTTP response headers, and optional constant-time Bearer token authentication (`CTW_API_TOKEN`).
- Automated PII/PHI Safe Harbor scanning (`PIIGuardrailScanner`) on all ingested data feeds.
