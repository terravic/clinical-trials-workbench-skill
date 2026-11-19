"""Human-in-the-Loop Action Drafting and Feedback Persistence Engine.

Implements the 'Action' stage of the System of Action pipeline by generating
Portfolio executive briefings, Site Monitoring Visit Agendas, Site Follow-Up
Emails, exportable QMS/CTMS Issue records, and managing human reviewer
dispositions (ACCEPT, MODIFY, DISMISS).
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from scripts import (
    DEFAULT_TEMPLATES_DIR,
    EntityNotFoundError,
    StateFileError,
    configure_logging,
    resolve_state_file,
)

VALID_DISPOSITIONS = frozenset({"ACCEPT", "MODIFY", "DISMISS", "PENDING_REVIEW"})


def load_feedback_state(
    state_file: str | Path | None = None, strict: bool = False
) -> dict[str, dict[str, Any]]:
    """Load persisted human-in-the-loop signal feedback dispositions."""
    logger = configure_logging()
    path = resolve_state_file(state_file)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("signals", {}), dict):
            raise ValueError("Root must be an object containing a 'signals' mapping")
        return payload.get("signals", {})
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        if strict:
            raise StateFileError(f"Corrupted feedback state file at {path}: {exc}") from exc
        backup_path = path.with_suffix(path.suffix + ".corrupt.bak")
        try:
            path.replace(backup_path)
        except OSError:
            pass
        logger.warning("Backed up unreadable state file to %s; initialized clean store", backup_path)
        return {}


def record_signal_feedback(
    signal_id: str,
    disposition: str,
    note: str = "",
    reviewer_id: str = "CRA-01",
    state_file: str | Path | None = None,
) -> dict[str, Any]:
    """Persist human reviewer disposition (ACCEPT, MODIFY, DISMISS) for a signal."""
    disp_upper = disposition.strip().upper()
    if disp_upper not in VALID_DISPOSITIONS:
        raise ValueError(
            f"Invalid disposition '{disposition}'. Allowed values: {sorted(VALID_DISPOSITIONS)}"
        )
    path = resolve_state_file(state_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    current = load_feedback_state(path, strict=False)
    entry = {
        "signal_id": signal_id,
        "disposition": disp_upper,
        "note": note.strip(),
        "reviewer_id": reviewer_id.strip() or "CRA-01",
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    current[signal_id] = entry
    path.write_text(json.dumps({"signals": current}, indent=2) + "\n", encoding="utf-8")
    return entry


def build_portfolio_executive_briefing(
    signal_bundle: dict[str, Any],
    selection_bundle: dict[str, Any],
    cra_filter: str | None = None,
    include_peer_sites: bool = True,
) -> dict[str, Any]:
    """Synthesize Level 1 Portfolio 'morning coffee' executive summary."""
    sites = signal_bundle["sites"]
    signals = signal_bundle["signals"]
    if cra_filter and cra_filter.upper() != "ALL":
        cra_u = cra_filter.upper()
        sites = [
            s
            for s in sites
            if s["assigned_cra_id"] == cra_u
            or (include_peer_sites and s["peer_cra_id"] == cra_u)
        ]
        allowed_site_ids = {s["site_id"] for s in sites}
        signals = [sig for sig in signals if sig["site_id"] in allowed_site_ids]

    active_signals = [s for s in signals if s["disposition"] != "DISMISS"]
    tier_counts = {
        "Priority Action Required": sum(1 for s in sites if s["priority_tier"] == "Priority Action Required"),
        "Important Signals Next": sum(1 for s in sites if s["priority_tier"] == "Important Signals Next"),
        "Continue to Monitor": sum(1 for s in sites if s["priority_tier"] == "Continue to Monitor"),
    }
    eligible_patients = [
        p for p in selection_bundle["patients"] if p["primary_classification"] == "ELIGIBLE"
    ]
    needs_labs_patients = [
        p for p in selection_bundle["patients"] if p["primary_classification"] == "POTENTIAL_NEEDS_LABS"
    ]

    priority_sites = [
        f"{s['site_id']} ({s['protocol_code']}, score {s['risk_score']})"
        for s in sorted(sites, key=lambda x: x["risk_score"], reverse=True)
        if s["priority_tier"] == "Priority Action Required"
    ]
    crit_count = sum(1 for s in active_signals if s["severity"] == "CRITICAL")

    scope_label = f"CRA scope {cra_filter.upper()}" if (cra_filter and cra_filter.upper() != "ALL") else "Global Portfolio"
    briefing_text = (
        f"{scope_label} Briefing ({signal_bundle['reference_date']}): "
        f"{tier_counts['Priority Action Required']} of {len(sites)} clinical sites require Priority Action "
        f"({', '.join(priority_sites[:4]) if priority_sites else 'none'}), driven by {crit_count} CRITICAL signals "
        f"across Safety, Informed Consent, Cold-Chain Supply, and GCP Training. "
        f"Closed SAE follow-up turnaround averages {signal_bundle['submitted_sae_median_days']} days "
        f"(meeting the {signal_bundle['sae_target_days']}-day System-of-Action target vs. {signal_bundle['sae_baseline_days']}-day legacy baseline), "
        f"while open SAEs at priority sites require immediate escalation. "
        f"Across the Clinical Trial Selection registry, {len(eligible_patients)} synthetic candidates are fully ELIGIBLE "
        f"for immediate site referral and {len(needs_labs_patients)} candidates require targeted biomarker/lab refresh."
    )

    return {
        "executive_briefing": briefing_text,
        "scope": scope_label,
        "total_sites": len(sites),
        "tier_counts": tier_counts,
        "active_signal_count": len(active_signals),
        "critical_signal_count": crit_count,
        "median_sae_cycle_days": signal_bundle["median_sae_cycle_days"],
        "submitted_sae_median_days": signal_bundle["submitted_sae_median_days"],
        "sae_target_days": signal_bundle["sae_target_days"],
        "sae_baseline_days": signal_bundle["sae_baseline_days"],
        "eligible_patient_count": len(eligible_patients),
        "potential_needs_labs_count": len(needs_labs_patients),
    }


def draft_monitoring_visit_agenda(
    site_id: str,
    signal_bundle: dict[str, Any],
    selection_bundle: dict[str, Any],
    selected_signal_ids: list[str] | None = None,
    templates_dir: Path | None = None,
) -> str:
    """Render Level 2 CRA Monitoring Visit Agenda markdown for a clinical site."""
    site = next((s for s in signal_bundle["sites"] if s["site_id"] == site_id), None)
    if site is None:
        valid = [s["site_id"] for s in signal_bundle["sites"]]
        raise EntityNotFoundError(f"Unknown site_id '{site_id}'. Valid sites: {valid}")

    site_signals = [
        s
        for s in signal_bundle["signals"]
        if s["site_id"] == site_id and s["disposition"] != "DISMISS"
    ]
    if selected_signal_ids:
        id_set = set(selected_signal_ids)
        site_signals = [s for s in site_signals if s["signal_id"] in id_set]

    if site_signals:
        sig_lines = [
            f"- **[{s['severity']}] {s['signal_id']} ({s['rule_id']} - {s['family']}):** {s['title']}  \n"
            f"  *Evidence:* `{s['evidence']['source_file']}` record `{s['evidence']['source_record_id']}` "
            f"(`{s['evidence']['metric_name']} = {s['evidence']['observed_value']}`).  \n"
            f"  *Action:* {s['recommended_action']}"
            for s in site_signals
        ]
        signals_block = "\n".join(sig_lines)
    else:
        signals_block = "- No open CRITICAL/HIGH signals selected; conduct routine protocol source verification."

    matched_candidates = [
        p
        for p in selection_bundle["patients"]
        if p["recommended_site_id"] == site_id
        and p["primary_classification"] in ("ELIGIBLE", "POTENTIAL_NEEDS_LABS")
    ]
    if matched_candidates:
        cand_lines = [
            f"- **{p['patient_id']}** ({p['primary_classification']}, Score {p['primary_match_score']}/100): "
            f"ICD-10 `{p['primary_icd10']}`, Age {p['age_years']}, ECOG {p['ecog_status']}"
            + (
                f" — Missing/Stale: `{', '.join(p['missing_criteria'])}`"
                if p["missing_criteria"]
                else " — Ready for formal site screening schedule"
            )
            for p in matched_candidates[:6]
        ]
        cand_block = "\n".join(cand_lines)
    else:
        cand_block = "- Zero pending candidates routed to this site (check site enrollment pause or cold-chain status)."

    tpl_path = (templates_dir or DEFAULT_TEMPLATES_DIR) / "monitoring_visit_agenda.md.tpl"
    tpl = tpl_path.read_text(encoding="utf-8")
    return tpl.format(
        site_id=site["site_id"],
        site_name=site["site_name"],
        study_id=site["study_id"],
        protocol_code=site["protocol_code"],
        region=site["region"],
        country_code=site["country_code"],
        assigned_cra_id=site["assigned_cra_id"],
        priority_tier=site["priority_tier"],
        risk_score=site["risk_score"],
        last_monitoring_date=site["last_monitoring_date"],
        days_since_monitoring=site["days_since_monitoring"],
        reference_date=signal_bundle["reference_date"],
        site_summary=site["site_summary"],
        selected_signals_section=signals_block,
        open_queries_total=site["open_queries_total"],
        max_query_age_days=site["max_query_age_days"],
        sdv_backlog_total=site["sdv_backlog_total"],
        open_pd_count=site["open_pd_count"],
        available_kits=site["available_kits"],
        min_buffer_kits=site["min_buffer_kits"],
        unresolved_excursion_label="YES (CRITICAL)" if site["unresolved_excursion"] else "No",
        candidate_patients_section=cand_block,
    )


def draft_site_followup_email(
    site_id: str,
    signal_bundle: dict[str, Any],
    selected_signal_ids: list[str] | None = None,
    templates_dir: Path | None = None,
) -> str:
    """Render Level 2/3 plain-text site follow-up email using reserved .example domains."""
    site = next((s for s in signal_bundle["sites"] if s["site_id"] == site_id), None)
    if site is None:
        valid = [s["site_id"] for s in signal_bundle["sites"]]
        raise EntityNotFoundError(f"Unknown site_id '{site_id}'. Valid sites: {valid}")

    site_signals = [
        s
        for s in signal_bundle["signals"]
        if s["site_id"] == site_id and s["disposition"] != "DISMISS"
    ]
    if selected_signal_ids:
        id_set = set(selected_signal_ids)
        site_signals = [s for s in site_signals if s["signal_id"] in id_set]

    if site_signals:
        items = [
            f"   [{idx}] ({s['severity']} - {s['family']}) {s['title']} [Ref: {s['evidence']['source_record_id']}]\n"
            f"       Next Step: {s['recommended_action']}"
            for idx, s in enumerate(site_signals, start=1)
        ]
        items_text = "\n".join(items)
    else:
        items_text = "   [1] Routine monthly monitoring check-in; no critical risk signals active."

    tpl_path = (templates_dir or DEFAULT_TEMPLATES_DIR) / "site_followup_email.txt.tpl"
    tpl = tpl_path.read_text(encoding="utf-8")
    return tpl.format(
        site_id_lower=site["site_id"].lower(),
        cra_id_lower=site["assigned_cra_id"].lower(),
        protocol_code=site["protocol_code"],
        site_id=site["site_id"],
        priority_tier=site["priority_tier"],
        site_name=site["site_name"],
        study_id=site["study_id"],
        reference_date=signal_bundle["reference_date"],
        email_action_items=items_text,
        enrolled_count=site["enrolled_count"],
        target_enrollment=site["target_enrollment"],
        open_queries_total=site["open_queries_total"],
        max_query_age_days=site["max_query_age_days"],
        available_kits=site["available_kits"],
        min_buffer_kits=site["min_buffer_kits"],
        eligible_candidates_count=site["local_eligible_candidates"],
        assigned_cra_id=site["assigned_cra_id"],
    )


def draft_issue_payload(
    signal_id: str,
    signal_bundle: dict[str, Any],
    templates_dir: Path | None = None,
) -> dict[str, Any]:
    """Render Level 3 exportable source-system issue/CAPA JSON payload."""
    sig = next((s for s in signal_bundle["signals"] if s["signal_id"] == signal_id), None)
    if sig is None:
        raise EntityNotFoundError(f"Unknown signal_id '{signal_id}'")

    tpl_path = (templates_dir or DEFAULT_TEMPLATES_DIR) / "issue_capa_record.json.tpl"
    raw_tpl = tpl_path.read_text(encoding="utf-8")
    # Perform safe JSON field population
    template_obj = json.loads(raw_tpl)
    template_obj["generated_date"] = signal_bundle["reference_date"]
    template_obj["study_id"] = sig["study_id"]
    template_obj["site_id"] = sig["site_id"]
    template_obj["originating_signal_id"] = sig["signal_id"]
    template_obj["rule_id"] = sig["rule_id"]
    template_obj["signal_family"] = sig["family"]
    template_obj["severity"] = sig["severity"]
    template_obj["issue_title"] = sig["title"]
    template_obj["evidence_provenance"] = {
        "source_file": sig["evidence"]["source_file"],
        "source_record_id": sig["evidence"]["source_record_id"],
        "metric_name": sig["evidence"]["metric_name"],
        "observed_value": sig["evidence"]["observed_value"],
        "threshold_value": sig["evidence"]["threshold_value"],
    }
    template_obj["rationale"] = sig["rationale"]
    template_obj["recommended_capa_action"] = sig["recommended_action"]
    template_obj["assigned_owner_role"] = sig["assigned_cra_id"]
    return template_obj
