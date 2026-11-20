# Terms of Service

**DRAFT — REQUIRES LEGAL REVIEW**

> **Notice:** This document is a template prepared for review by qualified legal counsel. Replace all bracketed placeholder tokens and verify jurisdictional enforceability before external publication.

- **Provider:** `[ORGANIZATION_LEGAL_NAME]`
- **Registered Address:** `[REGISTERED_ADDRESS]`
- **Contact Email:** `[LEGAL_CONTACT_EMAIL]`
- **Effective Date:** `[EFFECTIVE_DATE]`
- **Governing Law and Venue:** `[GOVERNING_JURISDICTION]`

---

## 1. Acceptance of Terms

By accessing or running the Clinical Trials Workbench (`clinical-trials-workbench`), you agree to these Terms of Service and the accompanying Apache License, Version 2.0 (`LICENSE`) applicable to the source code.

## 2. Non-Medical and Non-GxP Reference Scope

This software is provided strictly as a technical reference implementation operating on synthetic data. It is **not** a validated clinical system under 21 CFR Part 11, EU Annex 11, ICH-GCP, or medical device regulations (FDA/MDR). Outputs, risk signals, patient eligibility classifications, monitoring visit agendas, email drafts, and issue payloads must not be used for clinical diagnosis, patient treatment, regulatory submissions, or automated source-system writes without independent validation and human oversight.

## 3. Operator Responsibilities

Operators deploying or modifying this software agree to:

1. Maintain synthetic-only data boundaries unless full regulatory, privacy, and security validation has been independently completed.
2. Enforce TLS termination, secret-managed authentication tokens (`CTW_API_TOKEN`), and access controls before exposing the HTTP server beyond loopback (`127.0.0.1`).
3. Review all generated agendas, correspondence, and CAPA issue exports prior to external distribution.

## 4. Intellectual Property and Open-Source Licensing

Source code is licensed under the Apache License, Version 2.0 (`LICENSE`). Self-hosted font software under `assets/fonts/` is licensed under the SIL Open Font License, Version 1.1 (`assets/fonts/LICENSES.txt`).

## 5. Disclaimers and Limitation of Liability

TO THE MAXIMUM EXTENT PERMITTED BY `[GOVERNING_JURISDICTION]` LAW, THE SOFTWARE IS PROVIDED "AS IS" WITHOUT WARRANTIES OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, OR NON-INFRINGEMENT. `[ORGANIZATION_LEGAL_NAME]` SHALL NOT BE LIABLE FOR INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES ARISING FROM USE OF THIS SOFTWARE.

## 6. Contact

Questions regarding these Terms may be directed to `[LEGAL_CONTACT_EMAIL]`.
