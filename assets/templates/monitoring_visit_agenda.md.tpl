# Clinical Monitoring Visit Agenda

**Site Identifier:** {site_id} ({site_name})
**Study Protocol:** {study_id} ({protocol_code})
**Region / Country:** {region} / {country_code}
**Assigned Monitor:** {assigned_cra_id}
**Site Priority Tier:** {priority_tier} (Composite Risk Score: {risk_score})
**Last Monitoring Visit:** {last_monitoring_date} ({days_since_monitoring} days elapsed)
**Generated Reference Date:** {reference_date}

---

## 1. Executive Pre-Visit Assessment

{site_summary}

## 2. Priority Signals Scheduled for Source Review

{selected_signals_section}

## 3. Open EDC Queries, Informed Consent, and Protocol Deviations

- **Total Open EDC Queries at Site:** {open_queries_total} (Oldest Query Age: {max_query_age_days} days)
- **Unverified Source Data Pages (SDV Backlog):** {sdv_backlog_total} pages
- **Open Protocol Deviations:** {open_pd_count}
- **IRT Available Drug Kits:** {available_kits} kits (Minimum Buffer: {min_buffer_kits} kits; Unresolved Excursion: {unresolved_excursion_label})

## 4. Candidate Patient Screening Alignment (Trial Selection Workbench)

{candidate_patients_section}

## 5. Required Post-Visit Deliverables

1. Verify resolution evidence for all CRITICAL and HIGH signals listed in Section 2.
2. Confirm Principal Investigator sign-off on pending causality assessments and open Major Protocol Deviations.
3. Reconcile delegation and training logs against current GCP and protocol amendment certificates.
4. Record human-in-the-loop signal dispositions (ACCEPT, MODIFY, or DISMISS) in the System of Action workbench.
