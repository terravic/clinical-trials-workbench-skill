# Cookie and Local Storage Notice

**DRAFT — REQUIRES LEGAL REVIEW**

> **Notice:** This document is a template prepared for review by qualified legal counsel. Replace all bracketed placeholder tokens and verify ePrivacy Directive / regional consent obligations before external publication.

- **Operator:** `[ORGANIZATION_LEGAL_NAME]`
- **Privacy Contact:** `[PRIVACY_CONTACT_EMAIL]`
- **Effective Date:** `[EFFECTIVE_DATE]`

---

## 1. Zero HTTP Tracking Cookies

In its default architecture, the Clinical Trials Workbench (`dashboard.html` and `scripts/workbench_cli.py`) sets **zero HTTP cookies**, **zero advertising trackers**, **zero third-party analytics scripts**, and **zero cross-site beacons**.

## 2. Browser Local Storage Keys

The standalone dashboard uses browser `localStorage` solely for strictly necessary user-requested local UI state. No authentication credentials or session tokens are stored in `localStorage`.

| Storage Mechanism | Key Name | Purpose | Category | Expiration / Control |
|---|---|---|---|---|
| `localStorage` | `ctw_theme_mode` | Stores selected Light, Dark, or System display theme | Strictly Necessary (UI Preference) | Persists locally until cleared via browser settings or dashboard reset control |
| `localStorage` | `ctw_hitl_feedback` | Stores reviewer signal dispositions (`ACCEPT`, `MODIFY`, `DISMISS`) and optional notes in offline mode | Strictly Necessary (User Action State) | Persists locally until exported/cleared via the dashboard footer data control or browser settings |

## 3. Third-Party Storage and External Requests

- All web fonts (`DM Sans`, `IBM Plex Mono`, `Instrument Serif`) are self-hosted locally under `assets/fonts/`.
- No requests are made to external font CDNs, third-party script CDNs, social widgets, or remote avatar services.
- If `[ORGANIZATION_LEGAL_NAME]` adds optional analytics, authentication cookies, or third-party integrations in a hosted deployment, a consent management mechanism blocking non-essential storage until explicit opt-in consent is recorded must be implemented prior to launch.

## 4. Managing or Clearing Local Storage

Users can export or permanently delete local dashboard feedback and preference keys at any time using the **Export Local Data** and **Clear Local Data** controls in the dashboard footer, or through standard browser site-data settings.
