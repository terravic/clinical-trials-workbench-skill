"""Standalone Interactive HTML5 Canvas Dashboard Builder.

Compiles empirical multi-source clinical trial selection, signal detection,
human-in-the-loop drafting, analytics, and SHA-256 provenance data into a
self-contained `dashboard.html` compliant with DESIGN.md specifications.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from scripts import DEFAULT_DASHBOARD_PATH, PROJECT_ROOT, configure_logging
from scripts.action_drafter import (
    build_portfolio_executive_briefing,
    load_feedback_state,
)
from scripts.analytics_assistant import build_analytics_catalog
from scripts.data_ingestor import ingest_workspace_data
from scripts.signal_engine import evaluate_signals_and_sites
from scripts.trial_selector import run_trial_selection


def detect_host_system_theme() -> str:
    """Detect native host OS light/dark theme preference across macOS, Windows, and Linux."""
    try:
        if sys.platform == "darwin":
            proc = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
            if proc.returncode == 0 and "dark" in proc.stdout.lower():
                return "dark"
            return "light"

        if sys.platform == "win32":
            import winreg  # type: ignore[import-not-found]

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return "light" if int(val) == 1 else "dark"

        # Linux checks: GTK_THEME env, gsettings, kdeglobals
        gtk_env = os.environ.get("GTK_THEME", "").lower()
        if "dark" in gtk_env:
            return "dark"

        for cmd in (
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            ["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"],
        ):
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=1.5, check=False
                )
                if proc.returncode == 0 and "dark" in proc.stdout.lower():
                    return "dark"
            except (FileNotFoundError, subprocess.SubprocessError):
                continue

        kde_cfg = Path.home() / ".config" / "kdeglobals"
        if kde_cfg.is_file():
            content = kde_cfg.read_text(encoding="utf-8", errors="ignore").lower()
            if "breezedark" in content or "colorscheme=dark" in content:
                return "dark"
    except Exception:
        return "light"
    return "light"


def compile_workspace_bundle(
    data_dir: str | Path | None = None,
    state_file: str | Path | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Run full Listen -> Detect -> Assess -> Action + Selection pipeline and emit JSON bundle."""
    conn, ingestion_report = ingest_workspace_data(data_dir=data_dir, strict=strict)
    try:
        feedback_map = load_feedback_state(state_file=state_file, strict=strict)
        # First pass selection to populate eligibility_assessments for SR-ENR-05 signal rule
        prelim_selection = run_trial_selection(conn, persist_table=True)
        signal_bundle = evaluate_signals_and_sites(conn, feedback_map=feedback_map)
        site_risk_map = {s["site_id"]: s["risk_score"] for s in signal_bundle["sites"]}
        # Re-rank candidate site placements incorporating live site risk scores
        selection_bundle = run_trial_selection(
            conn, site_risk_scores=site_risk_map, persist_table=True
        )
        del prelim_selection

        portfolio_summary = build_portfolio_executive_briefing(
            signal_bundle, selection_bundle, cra_filter="ALL", include_peer_sites=True
        )
        analytics_catalog = build_analytics_catalog(conn)
        studies = [
            dict(r) for r in conn.execute("SELECT * FROM studies ORDER BY study_id").fetchall()
        ]
        quarantine_rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM validation_quarantine ORDER BY quarantine_id"
            ).fetchall()
        ]
    finally:
        conn.close()

    return {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "system_theme": detect_host_system_theme(),
            "reference_date": signal_bundle["reference_date"],
            "schema_version": "1.0.0",
            "synthetic_data_only": True,
        },
        "portfolio_summary": portfolio_summary,
        "studies": studies,
        "sites": signal_bundle["sites"],
        "signals": signal_bundle["signals"],
        "signal_families": signal_bundle["signal_families"],
        "patient_selection": selection_bundle,
        "analytics_catalog": analytics_catalog,
        "feedback_state": feedback_map,
        "provenance": {
            "files_loaded": ingestion_report.files_loaded,
            "total_records_loaded": ingestion_report.total_records_loaded,
            "quarantined_records": ingestion_report.quarantined_records,
            "sources": ingestion_report.provenance,
            "quarantine_log": quarantine_rows,
            "rules_evaluated": signal_bundle["rules_evaluated"],
        },
    }


DASHBOARD_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Clinical Trials Workbench</title>
  <meta name="description" content="Enterprise Clinical Operations System of Action and Synthetic Patient Trial Eligibility Selection Workbench with interactive HTML5 Canvas analytics." />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&amp;family=IBM+Plex+Mono:wght@400;500;600&amp;family=Instrument+Serif:ital@0;1&amp;display=swap" rel="stylesheet" />
  <style>
    :root,
    [data-theme="light"] {
      --bg-page: #FAFAF9;
      --bg-surface: #FFFFFF;
      --bg-subtle: #F5F5F4;
      --border-color: #E7E5E4;
      --text-primary: #1C1917;
      --text-secondary: #787774;
      --grid-line: #E7E5E4;

      --viz-teal: #0D9488;
      --viz-amber: #D97706;
      --viz-indigo: #4F46E5;
      --viz-emerald: #059669;
      --viz-rose: #E11D48;

      --badge-rose-bg: rgba(225, 29, 72, 0.10);
      --badge-amber-bg: rgba(217, 119, 6, 0.12);
      --badge-emerald-bg: rgba(5, 150, 105, 0.11);
      --badge-teal-bg: rgba(13, 148, 136, 0.11);
      --badge-indigo-bg: rgba(79, 70, 229, 0.11);
    }

    [data-theme="dark"] {
      --bg-page: #0D0E11;
      --bg-surface: #16171D;
      --bg-subtle: #1E2028;
      --border-color: #292A30;
      --text-primary: #ECE8E2;
      --text-secondary: #9A9895;
      --grid-line: #292A30;

      --badge-rose-bg: rgba(225, 29, 72, 0.18);
      --badge-amber-bg: rgba(217, 119, 6, 0.18);
      --badge-emerald-bg: rgba(5, 150, 105, 0.18);
      --badge-teal-bg: rgba(13, 148, 136, 0.18);
      --badge-indigo-bg: rgba(79, 70, 229, 0.18);
    }

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }

    body {
      background-color: var(--bg-page);
      color: var(--text-primary);
      font-family: "DM Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
    }

    .mono,
    table td.num,
    table th.num,
    .kpi-value,
    code,
    pre {
      font-family: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
      font-variant-numeric: tabular-nums;
    }

    /* Top Header Navigation */
    .top-bar {
      background-color: var(--bg-surface);
      border-bottom: 1px solid var(--border-color);
      padding: 12px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      flex-wrap: wrap;
      position: sticky;
      top: 0;
      z-index: 40;
    }

    .brand-group {
      display: flex;
      align-items: baseline;
      gap: 14px;
      flex-wrap: wrap;
    }

    .brand-group h1 {
      font-family: "Instrument Serif", Georgia, serif;
      font-weight: 400;
      font-size: 26px;
      letter-spacing: -0.01em;
      line-height: 1.1;
      color: var(--text-primary);
    }

    .brand-subtitle {
      font-size: 12px;
      color: var(--text-secondary);
      border-left: 1px solid var(--border-color);
      padding-left: 12px;
    }

    .header-controls {
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }

    .control-inline {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 12px;
      color: var(--text-secondary);
    }

    select,
    input[type="text"],
    input[type="search"],
    textarea {
      background-color: var(--bg-page);
      color: var(--text-primary);
      border: 1px solid var(--border-color);
      border-radius: 6px;
      padding: 6px 10px;
      font-family: inherit;
      font-size: 13px;
      outline: none;
      transition: border-color 0.15s ease;
    }

    select:focus,
    input:focus,
    textarea:focus {
      border-color: var(--viz-teal);
    }

    button {
      background-color: var(--bg-surface);
      color: var(--text-primary);
      border: 1px solid var(--border-color);
      border-radius: 6px;
      padding: 6px 12px;
      font-family: inherit;
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
      transition: background-color 0.15s ease, border-color 0.15s ease;
    }

    button:hover {
      background-color: var(--bg-subtle);
    }

    button.btn-primary {
      background-color: var(--viz-teal);
      border-color: var(--viz-teal);
      color: #FFFFFF;
    }

    button.btn-primary:hover {
      opacity: 0.92;
    }

    /* Three-Icon Theme Selector (DESIGN.md Section 3) */
    .theme-segmented {
      display: inline-flex;
      border: 1px solid var(--border-color);
      border-radius: 6px;
      overflow: hidden;
      background-color: var(--bg-page);
    }

    .theme-segmented button {
      border: none;
      border-right: 1px solid var(--border-color);
      border-radius: 0;
      padding: 6px 9px;
      background: transparent;
      color: var(--text-secondary);
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }

    .theme-segmented button:last-child {
      border-right: none;
    }

    .theme-segmented button.active {
      background-color: var(--bg-surface);
      color: var(--text-primary);
    }

    .theme-segmented svg {
      width: 15px;
      height: 15px;
      stroke: currentColor;
      fill: none;
      stroke-width: 1.8;
      stroke-linecap: round;
      stroke-linejoin: round;
    }

    /* Primary View Navigation Tabs */
    .view-nav {
      background-color: var(--bg-surface);
      border-bottom: 1px solid var(--border-color);
      padding: 0 24px;
      display: flex;
      gap: 4px;
      overflow-x: auto;
    }

    .view-tab {
      border: none;
      border-bottom: 2px solid transparent;
      border-radius: 0;
      background: transparent;
      padding: 11px 14px;
      font-size: 13px;
      font-weight: 500;
      color: var(--text-secondary);
      white-space: nowrap;
    }

    .view-tab:hover {
      background: transparent;
      color: var(--text-primary);
    }

    .view-tab.active {
      color: var(--text-primary);
      border-bottom-color: var(--viz-teal);
      font-weight: 600;
    }

    /* Main Workspace Container & Single-Level Bento Grid */
    main.workspace {
      max-width: 1560px;
      margin: 0 auto;
      padding: 20px 24px 40px;
    }

    .view-panel {
      display: none;
    }

    .view-panel.active {
      display: block;
    }

    .bento-grid {
      display: grid;
      grid-template-columns: repeat(12, minmax(0, 1fr));
      gap: 16px;
    }

    .pane {
      background-color: var(--bg-surface);
      border: 1px solid var(--border-color);
      border-radius: 8px;
      padding: 16px 18px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      min-width: 0;
    }

    .col-12 { grid-column: span 12; }
    .col-8 { grid-column: span 8; }
    .col-7 { grid-column: span 7; }
    .col-6 { grid-column: span 6; }
    .col-5 { grid-column: span 5; }
    .col-4 { grid-column: span 4; }
    .col-3 { grid-column: span 3; }

    @media (max-width: 1100px) {
      .col-8, .col-7, .col-6, .col-5, .col-4, .col-3 {
        grid-column: span 12;
      }
    }

    .pane-header {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 12px;
      padding-bottom: 8px;
      border-bottom: 1px solid var(--border-color);
      flex-wrap: wrap;
    }

    .pane-title {
      font-size: 14px;
      font-weight: 600;
      letter-spacing: -0.01em;
      color: var(--text-primary);
    }

    .pane-meta {
      font-size: 12px;
      color: var(--text-secondary);
    }

    /* KPI Strip Cells */
    .kpi-value {
      font-size: 26px;
      font-weight: 600;
      letter-spacing: -0.02em;
      line-height: 1.15;
    }

    .kpi-sub {
      font-size: 12px;
      color: var(--text-secondary);
    }

    /* Status Badges */
    .badge {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 6px;
      font-family: "IBM Plex Mono", monospace;
      font-size: 11px;
      font-weight: 500;
      white-space: nowrap;
    }

    .badge-critical,
    .badge-priority,
    .badge-ineligible {
      background-color: var(--badge-rose-bg);
      color: var(--viz-rose);
    }

    .badge-high,
    .badge-important,
    .badge-needs-labs {
      background-color: var(--badge-amber-bg);
      color: var(--viz-amber);
    }

    .badge-emerald,
    .badge-monitor,
    .badge-eligible {
      background-color: var(--badge-emerald-bg);
      color: var(--viz-emerald);
    }

    .badge-teal {
      background-color: var(--badge-teal-bg);
      color: var(--viz-teal);
    }

    .badge-indigo {
      background-color: var(--badge-indigo-bg);
      color: var(--viz-indigo);
    }

    .badge-muted {
      background-color: var(--bg-subtle);
      color: var(--text-secondary);
    }

    /* Filter Bars */
    .filter-bar {
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }

    /* Tables */
    .table-wrap {
      overflow-x: auto;
      max-height: 430px;
      overflow-y: auto;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }

    th,
    td {
      text-align: left;
      padding: 9px 10px;
      border-bottom: 1px solid var(--border-color);
      vertical-align: middle;
    }

    th {
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--text-secondary);
      background-color: var(--bg-surface);
      position: sticky;
      top: 0;
      z-index: 2;
      cursor: pointer;
      user-select: none;
    }

    td.num,
    th.num {
      text-align: right;
    }

    tbody tr {
      transition: background-color 0.1s ease;
    }

    tbody tr:hover {
      background-color: var(--bg-subtle);
    }

    tbody tr.selected-row {
      background-color: var(--badge-teal-bg);
    }

    /* Canvas Wrappers & Scientific Toolbars */
    .viz-toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      flex-wrap: wrap;
      padding: 4px 0 2px;
    }

    .viz-btn-group {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      flex-wrap: wrap;
    }

    .viz-chip {
      padding: 3px 8px;
      font-family: "IBM Plex Mono", monospace;
      font-size: 11px;
      border-radius: 6px;
      border: 1px solid var(--border-color);
      background-color: var(--bg-page);
      color: var(--text-secondary);
    }

    .viz-chip:hover {
      color: var(--text-primary);
      background-color: var(--bg-subtle);
    }

    .viz-chip.active {
      background-color: var(--badge-teal-bg);
      border-color: var(--viz-teal);
      color: var(--viz-teal);
      font-weight: 600;
    }

    .canvas-stage {
      position: relative;
      width: 100%;
      min-height: 270px;
      border-radius: 6px;
      overflow: hidden;
    }

    .canvas-stage.tall {
      min-height: 395px;
    }

    .canvas-stage canvas {
      display: block;
      width: 100%;
      height: 100%;
    }

    .canvas-tooltip {
      position: fixed;
      pointer-events: none;
      background-color: var(--bg-surface);
      color: var(--text-primary);
      border: 1px solid var(--border-color);
      border-radius: 6px;
      padding: 8px 11px;
      font-family: "IBM Plex Mono", monospace;
      font-size: 11px;
      line-height: 1.45;
      white-space: pre-line;
      box-shadow: 0 6px 20px rgba(0, 0, 0, 0.14);
      z-index: 99;
      display: none;
      max-width: 350px;
    }

    /* Sub-tabs */
    .subtab-bar {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      border-bottom: 1px solid var(--border-color);
      padding-bottom: 8px;
    }

    .subtab-btn.active {
      background-color: var(--bg-subtle);
      border-color: var(--text-secondary);
      font-weight: 600;
    }

    pre.code-output {
      background-color: var(--bg-page);
      border: 1px solid var(--border-color);
      border-radius: 6px;
      padding: 12px;
      font-size: 12px;
      line-height: 1.5;
      white-space: pre-wrap;
      word-break: break-word;
      max-height: 320px;
      overflow-y: auto;
    }

    .kv-list {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 10px;
      padding: 8px 0;
      border-bottom: 1px solid var(--border-color);
    }

    .kv-label {
      font-size: 11px;
      color: var(--text-secondary);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }

    .kv-val {
      font-size: 13px;
      font-weight: 600;
    }

    .legend-inline {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      font-size: 11px;
      color: var(--text-secondary);
    }

    .legend-swatch {
      display: inline-block;
      width: 9px;
      height: 9px;
      border-radius: 2px;
      margin-right: 4px;
      vertical-align: middle;
    }
  </style>
</head>
<body>
  <header class="top-bar">
    <div class="brand-group">
      <h1>Clinical Trials Workbench</h1>
      <span class="brand-subtitle">Cross-System Risk Intelligence &amp; Trial Selection Workbench</span>
    </div>
    <div class="header-controls">
      <div class="control-inline">
        <label for="cra-scope-select">Monitor Context</label>
        <select id="cra-scope-select">
          <option value="ALL">Global Oversight (All Sites)</option>
          <option value="CRA-01">CRA-01 (North America)</option>
          <option value="CRA-02">CRA-02 (EMEA)</option>
          <option value="CRA-03">CRA-03 (APAC)</option>
          <option value="CRA-04">CRA-04 (Cross-Regional)</option>
        </select>
      </div>
      <label class="control-inline" for="peer-coverage-toggle">
        <input type="checkbox" id="peer-coverage-toggle" checked />
        Include Peer Coverage
      </label>
      <span id="telemetry-status-badge" class="badge badge-muted mono">Offline Snapshot</span>
      <div class="theme-segmented" role="group" aria-label="Appearance Theme Selector">
        <button id="theme-btn-light" type="button" title="Light Theme" aria-label="Light Theme" data-theme-mode="light" aria-pressed="false">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="12" cy="12" r="4"></circle>
            <line x1="12" y1="2" x2="12" y2="5"></line>
            <line x1="12" y1="19" x2="12" y2="22"></line>
            <line x1="4.93" y1="4.93" x2="7.05" y2="7.05"></line>
            <line x1="16.95" y1="16.95" x2="19.07" y2="19.07"></line>
            <line x1="2" y1="12" x2="5" y2="12"></line>
            <line x1="19" y1="12" x2="22" y2="12"></line>
            <line x1="4.93" y1="19.07" x2="7.05" y2="16.95"></line>
            <line x1="16.95" y1="7.05" x2="19.07" y2="4.93"></line>
          </svg>
        </button>
        <button id="theme-btn-dark" type="button" title="Dark Theme" aria-label="Dark Theme" data-theme-mode="dark" aria-pressed="false">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>
          </svg>
        </button>
        <button id="theme-btn-system" type="button" title="System Theme" aria-label="System Theme" data-theme-mode="system" aria-pressed="true" class="active">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect>
            <line x1="8" y1="21" x2="16" y2="21"></line>
            <line x1="12" y1="17" x2="12" y2="21"></line>
          </svg>
        </button>
      </div>
    </div>
  </header>

  <nav class="view-nav" aria-label="Workbench Views">
    <button id="nav-tab-portfolio" class="view-tab active" data-view="portfolio" type="button">Level 1: Portfolio Workbench</button>
    <button id="nav-tab-site" class="view-tab" data-view="site" type="button">Level 2: Single-Site View</button>
    <button id="nav-tab-signal" class="view-tab" data-view="signal" type="button">Level 3: Signal Detail &amp; Evidence</button>
    <button id="nav-tab-selection" class="view-tab" data-view="selection" type="button">Trial Selection Workbench</button>
    <button id="nav-tab-analytics" class="view-tab" data-view="analytics" type="button">Conversational Analytics &amp; Provenance</button>
  </nav>

  <main class="workspace">
    <!-- VIEW 1: LEVEL 1 PORTFOLIO WORKBENCH -->
    <section id="view-portfolio" class="view-panel active" aria-labelledby="nav-tab-portfolio">
      <div class="bento-grid">
        <div class="pane col-12">
          <div class="pane-header">
            <span class="pane-title">Executive Briefing</span>
            <span id="briefing-ref-date" class="pane-meta mono"></span>
          </div>
          <p id="executive-briefing-text"></p>
        </div>

        <div class="pane col-3">
          <span class="pane-meta">Priority Action Sites</span>
          <div id="kpi-priority-sites" class="kpi-value" style="color: var(--viz-rose);">0</div>
          <span id="kpi-tier-breakdown" class="kpi-sub mono"></span>
        </div>
        <div class="pane col-3">
          <span class="pane-meta">Active Risk Signals</span>
          <div id="kpi-active-signals" class="kpi-value" style="color: var(--viz-amber);">0</div>
          <span id="kpi-critical-signals" class="kpi-sub mono"></span>
        </div>
        <div class="pane col-3">
          <span class="pane-meta">Closed SAE Turnaround</span>
          <div id="kpi-sae-days" class="kpi-value" style="color: var(--viz-teal);">0d</div>
          <span class="kpi-sub mono">Target: 7.0d | Legacy Baseline: 28.0d</span>
        </div>
        <div class="pane col-3">
          <span class="pane-meta">Trial-Eligible Candidates</span>
          <div id="kpi-eligible-pool" class="kpi-value" style="color: var(--viz-emerald);">0</div>
          <span id="kpi-needs-labs-pool" class="kpi-sub mono"></span>
        </div>

        <div class="pane col-7">
          <div class="pane-header">
            <span class="pane-title">Cross-Site Signal Concentration Heatmap</span>
            <div class="legend-inline">
              <span><i class="legend-swatch" style="background:#0D9488"></i>Low</span>
              <span><i class="legend-swatch" style="background:#4F46E5"></i>Elevated</span>
              <span><i class="legend-swatch" style="background:#E11D48"></i>Critical</span>
            </div>
          </div>
          <div class="canvas-stage" style="height:310px;">
            <canvas id="canvas-heatmap"></canvas>
          </div>
        </div>

        <div class="pane col-5">
          <div class="pane-header">
            <span class="pane-title">SAE Reporting Cycle Time vs 7-Day Target</span>
            <span class="pane-meta">Mean &amp; Max Days by Protocol</span>
          </div>
          <div class="canvas-stage" style="height:310px;">
            <canvas id="canvas-sae-trend"></canvas>
          </div>
        </div>

        <div class="pane col-12">
          <div class="pane-header">
            <span class="pane-title">Portfolio Clinical Sites Grid</span>
            <div class="filter-bar">
              <input id="portfolio-search" type="search" placeholder="Search site ID, name, country..." aria-label="Search clinical sites" />
              <select id="portfolio-study-filter" aria-label="Filter by study">
                <option value="ALL">All Studies</option>
              </select>
              <select id="portfolio-tier-filter" aria-label="Filter by priority tier">
                <option value="ALL">All Priority Tiers</option>
                <option value="Priority Action Required">Priority Action Required</option>
                <option value="Important Signals Next">Important Signals Next</option>
                <option value="Continue to Monitor">Continue to Monitor</option>
              </select>
            </div>
          </div>
          <div class="table-wrap">
            <table id="portfolio-sites-table">
              <thead>
                <tr>
                  <th data-sort="site_id">Site ID</th>
                  <th data-sort="site_name">Clinical Institution</th>
                  <th data-sort="protocol_code">Protocol</th>
                  <th data-sort="region">Region</th>
                  <th data-sort="assigned_cra_id">CRA</th>
                  <th data-sort="priority_tier">Priority Tier</th>
                  <th class="num" data-sort="risk_score">Risk Score</th>
                  <th class="num" data-sort="total_signals">Signals (Crit/Tot)</th>
                  <th class="num" data-sort="enrolled_count">Enrolled</th>
                  <th class="num" data-sort="local_eligible_candidates">Eligible Pool</th>
                  <th>Last Monitored</th>
                  <th>Drill-Down</th>
                </tr>
              </thead>
              <tbody id="portfolio-sites-tbody"></tbody>
            </table>
          </div>
        </div>
      </div>
    </section>

    <!-- VIEW 2: LEVEL 2 SINGLE-SITE VIEW -->
    <section id="view-site" class="view-panel" aria-labelledby="nav-tab-site">
      <div class="bento-grid">
        <div class="pane col-12">
          <div class="pane-header">
            <div class="filter-bar">
              <span class="pane-title">Single-Site Operational Workbench</span>
              <select id="site-view-selector" aria-label="Select Clinical Site"></select>
              <span id="site-tier-badge" class="badge"></span>
            </div>
            <div class="filter-bar">
              <button id="btn-generate-agenda" class="btn-primary" type="button">Compile Visit Agenda</button>
              <button id="btn-generate-email" type="button">Compile Follow-Up Email</button>
            </div>
          </div>
          <div id="site-metadata-strip" class="kv-list"></div>
          <p id="site-ai-summary"></p>
        </div>

        <div class="pane col-7">
          <div class="subtab-bar" role="tablist">
            <button class="subtab-btn active" data-site-subtab="signals" type="button">Signals</button>
            <button class="subtab-btn" data-site-subtab="events" type="button">Safety &amp; EDC Events</button>
            <button class="subtab-btn" data-site-subtab="issues" type="button">QMS Issues &amp; Training</button>
            <button class="subtab-btn" data-site-subtab="pds" type="button">Protocol Deviations</button>
            <button class="subtab-btn" data-site-subtab="candidates" type="button">Matched Candidates</button>
          </div>
          <div id="site-subtab-container" class="table-wrap"></div>
        </div>

        <div class="pane col-5">
          <div class="pane-header">
            <span id="site-draft-title" class="pane-title">Drafted Monitoring Visit Agenda</span>
            <button id="btn-copy-site-draft" type="button">Copy Draft Text</button>
          </div>
          <pre id="site-draft-output" class="code-output"></pre>
        </div>
      </div>
    </section>

    <!-- VIEW 3: LEVEL 3 SIGNAL DETAIL & EVIDENCE -->
    <section id="view-signal" class="view-panel" aria-labelledby="nav-tab-signal">
      <div class="bento-grid">
        <div class="pane col-12">
          <div class="pane-header">
            <div class="filter-bar">
              <span class="pane-title">Signal Auditability &amp; Source Evidence</span>
              <select id="signal-view-selector" aria-label="Select Risk Signal"></select>
              <span id="signal-severity-badge" class="badge"></span>
              <span id="signal-disposition-badge" class="badge badge-muted"></span>
            </div>
            <button id="btn-back-to-site" type="button">Open Originating Site</button>
          </div>
          <div id="signal-kv-strip" class="kv-list"></div>
          <p id="signal-rationale-text"></p>
          <p><strong>Recommended Action:</strong> <span id="signal-recommended-action"></span></p>
        </div>

        <div class="pane col-6">
          <div class="pane-header">
            <span class="pane-title">Source-System Evidence Provenance</span>
            <span class="pane-meta">Immutable Audit Citation</span>
          </div>
          <div class="table-wrap">
            <table>
              <tbody id="signal-evidence-tbody"></tbody>
            </table>
          </div>
          <div class="pane-header" style="margin-top:8px;">
            <span class="pane-title">Human-in-the-Loop Disposition</span>
            <span class="pane-meta">Captured in Feedback Store</span>
          </div>
          <div class="filter-bar">
            <input id="signal-feedback-note" type="text" placeholder="Optional reviewer rationale or modification..." style="flex:1; min-width:200px;" />
            <button id="btn-feedback-accept" class="btn-primary" type="button">Accept</button>
            <button id="btn-feedback-modify" type="button">Modify</button>
            <button id="btn-feedback-dismiss" type="button">Dismiss</button>
          </div>
        </div>

        <div class="pane col-6">
          <div class="pane-header">
            <span class="pane-title">Historical 8-Week Site Signal Trend</span>
            <span id="signal-trend-subtitle" class="pane-meta mono"></span>
          </div>
          <div class="viz-toolbar">
            <div class="legend-inline">
              <span><i class="legend-swatch" style="background:#E11D48"></i>Site Trajectory</span>
              <span><i class="legend-swatch" style="background:#4F46E5"></i>Peer Cohort Avg</span>
              <span><i class="legend-swatch" style="background:#0D9488"></i>Weekly Load</span>
              <span><i class="legend-swatch" style="background:#D97706"></i>Watch Band</span>
            </div>
            <span class="pane-meta mono">Hover Week to Scrub</span>
          </div>
          <div class="canvas-stage" style="height:260px;">
            <canvas id="canvas-site-trend"></canvas>
          </div>
          <div class="pane-header">
            <span class="pane-title">Exportable Source-System Issue / CAPA Payload</span>
            <button id="btn-copy-issue-json" type="button">Copy JSON</button>
          </div>
          <pre id="signal-issue-json" class="code-output" style="max-height:150px;"></pre>
        </div>
      </div>
    </section>

    <!-- VIEW 4: CLINICAL TRIAL SELECTION WORKBENCH -->
    <section id="view-selection" class="view-panel" aria-labelledby="nav-tab-selection">
      <div class="bento-grid">
        <div class="pane col-6">
          <div class="pane-header">
            <span class="pane-title">3D Biomarker &amp; Organ-Function Projection</span>
            <div class="legend-inline">
              <span><i class="legend-swatch" style="background:#059669"></i>Eligible</span>
              <span><i class="legend-swatch" style="background:#D97706"></i>Needs Labs</span>
              <span><i class="legend-swatch" style="background:#E11D48"></i>Ineligible</span>
            </div>
          </div>
          <div class="viz-toolbar">
            <div class="viz-btn-group" role="group" aria-label="3D Camera Presets">
              <button type="button" class="viz-chip active" data-cam3d="iso">Isometric</button>
              <button type="button" class="viz-chip" data-cam3d="xy">Biomarker / eGFR</button>
              <button type="button" class="viz-chip" data-cam3d="top">Top Cohort</button>
              <button type="button" id="btn-3d-orbit" class="viz-chip">Auto-Orbit</button>
            </div>
            <div class="viz-btn-group">
              <button type="button" id="btn-3d-envelope" class="viz-chip active">Protocol Envelope</button>
              <button type="button" id="btn-3d-reset" class="viz-chip">Reset</button>
            </div>
          </div>
          <div class="canvas-stage tall" style="height:395px;">
            <canvas id="canvas-3d-biomarker"></canvas>
          </div>
        </div>

        <div class="pane col-6">
          <div class="pane-header">
            <span class="pane-title">Interactive Relationship Node Map</span>
            <div class="legend-inline">
              <span><i class="legend-swatch" style="background:#4F46E5"></i>Protocol</span>
              <span><i class="legend-swatch" style="background:#E11D48"></i>Priority Site/Signal</span>
              <span><i class="legend-swatch" style="background:#0D9488"></i>Referral</span>
            </div>
          </div>
          <div class="viz-toolbar">
            <div class="viz-btn-group" role="group" aria-label="Topology Layer Mode">
              <button type="button" class="viz-chip active" data-nodelayer="all">Full Topology</button>
              <button type="button" class="viz-chip" data-nodelayer="signals">Risk Signals</button>
              <button type="button" class="viz-chip" data-nodelayer="referrals">Candidate Flow</button>
            </div>
            <div class="viz-btn-group">
              <button id="btn-nodemap-zout" class="viz-chip" type="button" title="Zoom Out">-</button>
              <button id="btn-nodemap-zin" class="viz-chip" type="button" title="Zoom In">+</button>
              <button id="btn-reset-nodemap" class="viz-chip" type="button">Reset Camera</button>
            </div>
          </div>
          <div class="canvas-stage tall" style="height:395px;">
            <canvas id="canvas-nodemap"></canvas>
          </div>
        </div>

        <div class="pane col-7">
          <div class="pane-header">
            <span class="pane-title">Synthetic Candidate Patient Pool</span>
            <div class="filter-bar">
              <input id="patient-search-input" type="search" placeholder="Search PAT ID, ICD-10, stage..." aria-label="Search synthetic patients" />
              <select id="patient-study-filter" aria-label="Filter candidates by study">
                <option value="ALL">All Protocols</option>
              </select>
              <select id="patient-status-filter" aria-label="Filter candidates by eligibility status">
                <option value="ALL">All Classifications</option>
                <option value="ELIGIBLE">ELIGIBLE</option>
                <option value="POTENTIAL_NEEDS_LABS">POTENTIAL_NEEDS_LABS</option>
                <option value="INELIGIBLE">INELIGIBLE</option>
              </select>
            </div>
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Patient ID</th>
                  <th>Age/Sex</th>
                  <th>Region</th>
                  <th>ICD-10</th>
                  <th class="num">ECOG</th>
                  <th>Matched Protocol</th>
                  <th>Classification</th>
                  <th class="num">Score</th>
                  <th>Recommended Site</th>
                </tr>
              </thead>
              <tbody id="patient-selection-tbody"></tbody>
            </table>
          </div>
        </div>

        <div class="pane col-5">
          <div class="pane-header">
            <span class="pane-title">Inclusion &amp; Exclusion Audit Matrix</span>
            <span id="selected-patient-label" class="pane-meta mono"></span>
          </div>
          <div id="selected-patient-labs" class="kv-list"></div>
          <div class="table-wrap" style="max-height:310px;">
            <table>
              <thead>
                <tr>
                  <th>Criterion</th>
                  <th>Status</th>
                  <th>Observed</th>
                  <th>Protocol Rule</th>
                </tr>
              </thead>
              <tbody id="patient-criteria-tbody"></tbody>
            </table>
          </div>
        </div>
      </div>
    </section>

    <!-- VIEW 5: CONVERSATIONAL ANALYTICS & PROVENANCE -->
    <section id="view-analytics" class="view-panel" aria-labelledby="nav-tab-analytics">
      <div class="bento-grid">
        <div class="pane col-12">
          <div class="pane-header">
            <span class="pane-title">Conversational Clinical &amp; Operational Analytics</span>
            <span class="pane-meta">Full Transparency: Narrative + Chart + SQL + Underlying Rows</span>
          </div>
          <div class="filter-bar">
            <select id="analytics-preset-select" aria-label="Curated analytical questions" style="max-width:440px;"></select>
            <input id="analytics-question-input" type="text" placeholder="Ask a clinical operations question or enter a read-only SELECT query..." style="flex:1; min-width:260px;" />
            <button id="btn-run-analytics" class="btn-primary" type="button">Run Analysis</button>
          </div>
          <p id="analytics-narrative-output"></p>
        </div>

        <div class="pane col-6">
          <div class="pane-header">
            <span id="analytics-chart-title" class="pane-title">Analytical Series Plot</span>
            <span id="analytics-provenance-pills" class="pane-meta mono"></span>
          </div>
          <div class="canvas-stage" style="height:260px;">
            <canvas id="canvas-analytics-chart"></canvas>
          </div>
        </div>

        <div class="pane col-6">
          <div class="pane-header">
            <span class="pane-title">Generated Read-Only SQL Query</span>
            <button id="btn-copy-sql" type="button">Copy SQL</button>
          </div>
          <pre id="analytics-sql-output" class="code-output" style="max-height:260px;"></pre>
        </div>

        <div class="pane col-7">
          <div class="pane-header">
            <span class="pane-title">Underlying Tabular Result Set</span>
            <span id="analytics-row-count" class="pane-meta mono"></span>
          </div>
          <div class="table-wrap" style="max-height:290px;">
            <table id="analytics-results-table">
              <thead id="analytics-results-thead"></thead>
              <tbody id="analytics-results-tbody"></tbody>
            </table>
          </div>
        </div>

        <div class="pane col-5">
          <div class="pane-header">
            <span class="pane-title">Cross-System Data Provenance &amp; Validation</span>
            <span id="provenance-totals-meta" class="pane-meta mono"></span>
          </div>
          <div class="table-wrap" style="max-height:290px;">
            <table>
              <thead>
                <tr>
                  <th>Source Feed</th>
                  <th>Format</th>
                  <th class="num">Records</th>
                  <th>SHA-256 Prefix</th>
                </tr>
              </thead>
              <tbody id="provenance-sources-tbody"></tbody>
            </table>
          </div>
        </div>
      </div>
    </section>
  </main>

  <div id="canvas-tooltip" class="canvas-tooltip" role="status" aria-live="polite"></div>

  <script id="workspace-data-bundle" type="application/json">__WORKSPACE_JSON_PAYLOAD__</script>
  <script>
  (function () {
    "use strict";

    const PALETTE = {
      teal: "#0D9488",
      amber: "#D97706",
      indigo: "#4F46E5",
      emerald: "#059669",
      rose: "#E11D48"
    };

    const rawBundle = document.getElementById("workspace-data-bundle").textContent;
    let WORKSPACE = JSON.parse(rawBundle);

    const STATE = {
      themeMode: localStorage.getItem("ctw_theme_mode") || "system",
      activeTheme: "light",
      activeView: "portfolio",
      craScope: "ALL",
      includePeer: true,
      portfolioSearch: "",
      portfolioStudy: "ALL",
      portfolioTier: "ALL",
      sortCol: "risk_score",
      sortDesc: true,

      selectedSiteId: WORKSPACE.sites[0] ? WORKSPACE.sites[0].site_id : "SITE-101",
      siteSubtab: "signals",
      checkedSignalIds: new Set(),
      siteDraftMode: "agenda",

      selectedSignalId: WORKSPACE.signals[0] ? WORKSPACE.signals[0].signal_id : "SIG-1001",
      localFeedback: JSON.parse(localStorage.getItem("ctw_hitl_feedback") || "{}"),

      patientSearch: "",
      patientStudy: "ALL",
      patientStatus: "ALL",
      selectedPatientId: WORKSPACE.patient_selection.patients[0] ? WORKSPACE.patient_selection.patients[0].patient_id : "PAT-10001",

      hoveredTrendIdx: -1,
      trendHitPoints: [],

      rot3dYaw: 0.64,
      rot3dPitch: 0.38,
      zoom3d: 1.0,
      show3dEnvelope: true,
      autoOrbit3d: false,
      hovered3dId: null,
      projected3dPoints: [],

      nodePanX: 0,
      nodePanY: 0,
      nodeZoom: 1.0,
      nodeLayerMode: "all",
      hoveredNodeId: null,
      renderedNodes: [],
      renderedEdges: [],

      activeAnalyticsResult: WORKSPACE.analytics_catalog[0] || null
    };

    /* Merge persisted localStorage HITL feedback into in-memory signals */
    function syncLocalFeedback() {
      const merged = Object.assign({}, WORKSPACE.feedback_state || {}, STATE.localFeedback);
      WORKSPACE.signals.forEach(function (sig) {
        if (merged[sig.signal_id]) {
          sig.disposition = merged[sig.signal_id].disposition;
          sig.reviewer_note = merged[sig.signal_id].note || "";
        }
      });
    }
    syncLocalFeedback();

    /* --- THEME RESOLUTION (DESIGN.md Section 3) --- */
    const mediaDark = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

    function resolveActiveTheme(mode) {
      if (mode === "light" || mode === "dark") return mode;
      if (mediaDark && typeof mediaDark.matches === "boolean") {
        return mediaDark.matches ? "dark" : "light";
      }
      return (WORKSPACE.metadata && WORKSPACE.metadata.system_theme === "dark") ? "dark" : "light";
    }

    function applyThemeMode(mode) {
      STATE.themeMode = mode;
      localStorage.setItem("ctw_theme_mode", mode);
      STATE.activeTheme = resolveActiveTheme(mode);
      document.documentElement.setAttribute("data-theme", STATE.activeTheme);

      ["light", "dark", "system"].forEach(function (m) {
        const btn = document.getElementById("theme-btn-" + m);
        if (!btn) return;
        const isActive = m === mode;
        btn.classList.toggle("active", isActive);
        btn.setAttribute("aria-pressed", isActive ? "true" : "false");
      });
      redrawAllCanvases();
    }

    if (mediaDark) {
      const onMediaChange = function () {
        if (STATE.themeMode === "system") applyThemeMode("system");
      };
      if (mediaDark.addEventListener) mediaDark.addEventListener("change", onMediaChange);
      else if (mediaDark.addListener) mediaDark.addListener(onMediaChange);
    }

    ["light", "dark", "system"].forEach(function (m) {
      document.getElementById("theme-btn-" + m).addEventListener("click", function () {
        applyThemeMode(m);
      });
    });

    function themeTokens() {
      const dark = STATE.activeTheme === "dark";
      return {
        dark: dark,
        surface: dark ? "#16171D" : "#FFFFFF",
        page: dark ? "#0D0E11" : "#FAFAF9",
        border: dark ? "#292A30" : "#E7E5E4",
        text: dark ? "#ECE8E2" : "#1C1917",
        muted: dark ? "#9A9895" : "#787774"
      };
    }

    function escHtml(val) {
      return String(val == null ? "" : val)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }

    function tierBadgeClass(tier) {
      if (tier === "Priority Action Required") return "badge-priority";
      if (tier === "Important Signals Next") return "badge-important";
      return "badge-monitor";
    }

    function sevBadgeClass(sev) {
      if (sev === "CRITICAL") return "badge-critical";
      if (sev === "HIGH") return "badge-high";
      if (sev === "MEDIUM") return "badge-indigo";
      return "badge-teal";
    }

    function classifBadgeClass(cls) {
      if (cls === "ELIGIBLE") return "badge-eligible";
      if (cls === "POTENTIAL_NEEDS_LABS") return "badge-needs-labs";
      return "badge-ineligible";
    }

    /* --- SCOPED SITES HELPER --- */
    function getScopedSites() {
      return WORKSPACE.sites.filter(function (s) {
        if (STATE.craScope !== "ALL") {
          const matchPrimary = s.assigned_cra_id === STATE.craScope;
          const matchPeer = STATE.includePeer && s.peer_cra_id === STATE.craScope;
          if (!matchPrimary && !matchPeer) return false;
        }
        return true;
      });
    }

    function getFilteredPortfolioSites() {
      const q = STATE.portfolioSearch.trim().toLowerCase();
      const list = getScopedSites().filter(function (s) {
        if (STATE.portfolioStudy !== "ALL" && s.study_id !== STATE.portfolioStudy) return false;
        if (STATE.portfolioTier !== "ALL" && s.priority_tier !== STATE.portfolioTier) return false;
        if (q) {
          const hay = (s.site_id + " " + s.site_name + " " + s.protocol_code + " " + s.country_code + " " + s.region).toLowerCase();
          if (hay.indexOf(q) === -1) return false;
        }
        return true;
      });

      list.sort(function (a, b) {
        let va = STATE.sortCol === "total_signals" ? a.signal_counts.TOTAL : a[STATE.sortCol];
        let vb = STATE.sortCol === "total_signals" ? b.signal_counts.TOTAL : b[STATE.sortCol];
        if (typeof va === "string") {
          return STATE.sortDesc ? vb.localeCompare(va) : va.localeCompare(vb);
        }
        return STATE.sortDesc ? (vb - va) : (va - vb);
      });
      return list;
    }

    /* --- POPULATE STATIC SELECTORS --- */
    function populateSelects() {
      const pStudy = document.getElementById("portfolio-study-filter");
      const selStudy = document.getElementById("patient-study-filter");
      WORKSPACE.studies.forEach(function (st) {
        const label = st.protocol_code + " (" + st.study_id + " - " + st.therapeutic_area + ")";
        pStudy.insertAdjacentHTML("beforeend", '<option value="' + escHtml(st.study_id) + '">' + escHtml(label) + "</option>");
        selStudy.insertAdjacentHTML("beforeend", '<option value="' + escHtml(st.study_id) + '">' + escHtml(label) + "</option>");
      });

      const siteSel = document.getElementById("site-view-selector");
      siteSel.innerHTML = WORKSPACE.sites.map(function (s) {
        return '<option value="' + escHtml(s.site_id) + '">' + escHtml(s.site_id + " - " + s.site_name + " (" + s.protocol_code + ")") + "</option>";
      }).join("");
      siteSel.value = STATE.selectedSiteId;

      const sigSel = document.getElementById("signal-view-selector");
      sigSel.innerHTML = WORKSPACE.signals.map(function (sig) {
        return '<option value="' + escHtml(sig.signal_id) + '">' + escHtml(sig.signal_id + " [" + sig.severity + "] " + sig.site_id + " - " + sig.title) + "</option>";
      }).join("");
      sigSel.value = STATE.selectedSignalId;

      const presetSel = document.getElementById("analytics-preset-select");
      presetSel.innerHTML = WORKSPACE.analytics_catalog.map(function (item, idx) {
        return '<option value="' + idx + '">' + escHtml(item.question) + "</option>";
      }).join("");
    }

    /* --- VIEW NAVIGATION --- */
    function switchView(viewName) {
      STATE.activeView = viewName;
      document.querySelectorAll(".view-tab").forEach(function (btn) {
        btn.classList.toggle("active", btn.getAttribute("data-view") === viewName);
      });
      document.querySelectorAll(".view-panel").forEach(function (panel) {
        panel.classList.toggle("active", panel.id === "view-" + viewName);
      });
      renderActiveView();
    }

    document.querySelectorAll(".view-tab").forEach(function (btn) {
      btn.addEventListener("click", function () {
        switchView(btn.getAttribute("data-view"));
      });
    });

    /* --- LEVEL 1 RENDERER --- */
    function renderPortfolio() {
      const scopedSites = getScopedSites();
      const scopedIds = new Set(scopedSites.map(function (s) { return s.site_id; }));
      const activeSignals = WORKSPACE.signals.filter(function (sig) {
        return scopedIds.has(sig.site_id) && sig.disposition !== "DISMISS";
      });

      const prioCnt = scopedSites.filter(function (s) { return s.priority_tier === "Priority Action Required"; }).length;
      const impCnt = scopedSites.filter(function (s) { return s.priority_tier === "Important Signals Next"; }).length;
      const monCnt = scopedSites.filter(function (s) { return s.priority_tier === "Continue to Monitor"; }).length;
      const critCnt = activeSignals.filter(function (s) { return s.severity === "CRITICAL"; }).length;

      document.getElementById("briefing-ref-date").textContent = "Reference Date: " + WORKSPACE.metadata.reference_date + " | Synthetic HCLS Mode";
      document.getElementById("executive-briefing-text").textContent = WORKSPACE.portfolio_summary.executive_briefing;

      document.getElementById("kpi-priority-sites").textContent = prioCnt + " / " + scopedSites.length;
      document.getElementById("kpi-tier-breakdown").textContent = "Important: " + impCnt + " | Nominal: " + monCnt;
      document.getElementById("kpi-active-signals").textContent = String(activeSignals.length);
      document.getElementById("kpi-critical-signals").textContent = critCnt + " CRITICAL across 7 families";
      document.getElementById("kpi-sae-days").textContent = WORKSPACE.portfolio_summary.submitted_sae_median_days + "d";
      document.getElementById("kpi-eligible-pool").textContent = String(WORKSPACE.portfolio_summary.eligible_patient_count);
      document.getElementById("kpi-needs-labs-pool").textContent = WORKSPACE.portfolio_summary.potential_needs_labs_count + " awaiting lab refresh";

      const rows = getFilteredPortfolioSites();
      const tbody = document.getElementById("portfolio-sites-tbody");
      tbody.innerHTML = rows.map(function (s) {
        return (
          '<tr data-site-row="' + escHtml(s.site_id) + '">' +
          '<td class="mono"><strong>' + escHtml(s.site_id) + '</strong></td>' +
          '<td>' + escHtml(s.site_name) + ' <span class="pane-meta">(' + escHtml(s.country_code) + ')</span></td>' +
          '<td class="mono">' + escHtml(s.protocol_code) + '</td>' +
          '<td class="mono">' + escHtml(s.region) + '</td>' +
          '<td class="mono">' + escHtml(s.assigned_cra_id) + '</td>' +
          '<td><span class="badge ' + tierBadgeClass(s.priority_tier) + '">' + escHtml(s.priority_tier) + '</span></td>' +
          '<td class="num">' + s.risk_score + '</td>' +
          '<td class="num">' + s.signal_counts.CRITICAL + ' / ' + s.signal_counts.TOTAL + '</td>' +
          '<td class="num">' + s.enrolled_count + ' / ' + s.target_enrollment + '</td>' +
          '<td class="num">' + s.local_eligible_candidates + '</td>' +
          '<td class="mono">' + escHtml(s.last_monitoring_date) + ' (' + s.days_since_monitoring + 'd)</td>' +
          '<td><button type="button" data-open-site="' + escHtml(s.site_id) + '">Inspect Site</button></td>' +
          '</tr>'
        );
      }).join("");

      tbody.querySelectorAll("[data-open-site]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          STATE.selectedSiteId = btn.getAttribute("data-open-site");
          STATE.checkedSignalIds = new Set();
          document.getElementById("site-view-selector").value = STATE.selectedSiteId;
          switchView("site");
        });
      });

      drawHeatmapCanvas();
      drawSaeTrendCanvas();
    }

    /* --- LEVEL 2 SINGLE-SITE RENDERER --- */
    function getSelectedSite() {
      return WORKSPACE.sites.find(function (s) { return s.site_id === STATE.selectedSiteId; }) || WORKSPACE.sites[0];
    }

    function compileSiteDraft(site, mode) {
      const siteSignals = WORKSPACE.signals.filter(function (sig) {
        if (sig.site_id !== site.site_id || sig.disposition === "DISMISS") return false;
        if (STATE.checkedSignalIds.size > 0 && !STATE.checkedSignalIds.has(sig.signal_id)) return false;
        return true;
      });
      const matchedCands = WORKSPACE.patient_selection.patients.filter(function (p) {
        return p.recommended_site_id === site.site_id && (p.primary_classification === "ELIGIBLE" || p.primary_classification === "POTENTIAL_NEEDS_LABS");
      });

      if (mode === "email") {
        document.getElementById("site-draft-title").textContent = "Drafted Site Follow-Up Email";
        const actionLines = siteSignals.length
          ? siteSignals.map(function (s, idx) {
              return "  [" + (idx + 1) + "] (" + s.severity + " - " + s.family + ") " + s.title +
                " [Ref: " + s.evidence.source_record_id + "]\\n      Next Step: " + s.recommended_action;
            }).join("\\n")
          : "  [1] Routine monthly monitoring check-in; zero critical signals selected.";
        return (
          "To: site-" + site.site_id.toLowerCase() + "-ops@synthetic-clinical.example\\n" +
          "Cc: pi-" + site.site_id.toLowerCase() + "@synthetic-clinical.example; " + site.assigned_cra_id.toLowerCase() + "@sponsor-ops.example\\n" +
          "Subject: [" + site.protocol_code + " | " + site.site_id + "] Follow-Up Actions (" + site.priority_tier + ")\\n\\n" +
          "Dear " + site.site_name + " Clinical Research Team,\\n\\n" +
          "Following cross-system review as of " + WORKSPACE.metadata.reference_date + ", please review the items below:\\n\\n" +
          "1. PRIORITIZED SIGNALS\\n" + actionLines + "\\n\\n" +
          "2. SITE SNAPSHOT\\n" +
          "- Enrollment: " + site.enrolled_count + " / " + site.target_enrollment + " subjects\\n" +
          "- Open EDC Queries: " + site.open_queries_total + " (Max Age: " + site.max_query_age_days + "d)\\n" +
          "- IRT Drug Kits: " + site.available_kits + " available (Min Buffer: " + site.min_buffer_kits + ")\\n" +
          "- Matched Regional Pre-Screening Candidates: " + matchedCands.length + "\\n\\n" +
          "Sincerely,\\nSite Monitoring Operations (" + site.assigned_cra_id + ")"
        );
      }

      document.getElementById("site-draft-title").textContent = "Drafted Monitoring Visit Agenda";
      const sigMd = siteSignals.length
        ? siteSignals.map(function (s) {
            return "- [" + s.severity + "] " + s.signal_id + " (" + s.family + "): " + s.title +
              "\\n  Source: " + s.evidence.source_file + " (" + s.evidence.source_record_id + ": " + s.evidence.metric_name + "=" + s.evidence.observed_value + ")" +
              "\\n  Action: " + s.recommended_action;
          }).join("\\n")
        : "- Routine protocol source verification; no open critical signals selected.";
      const candMd = matchedCands.length
        ? matchedCands.slice(0, 6).map(function (p) {
            return "- " + p.patient_id + " (" + p.primary_classification + ", Score " + p.primary_match_score + "): ICD-10 " + p.primary_icd10 + ", Age " + p.age_years;
          }).join("\\n")
        : "- Zero active referrals (verify enrollment status and cold-chain clearance).";

      return (
        "# Monitoring Visit Agenda: " + site.site_id + " (" + site.site_name + ")\\n" +
        "Protocol: " + site.study_id + " (" + site.protocol_code + ") | Monitor: " + site.assigned_cra_id + "\\n" +
        "Priority Tier: " + site.priority_tier + " (Score " + site.risk_score + "/100) | Last Visit: " + site.last_monitoring_date + "\\n\\n" +
        "## 1. Priority Signals Selected for Audit\\n" + sigMd + "\\n\\n" +
        "## 2. EDC, Consent & Supply Metrics\\n" +
        "- Open Queries: " + site.open_queries_total + " (Max Age " + site.max_query_age_days + "d) | SDV Backlog: " + site.sdv_backlog_total + " pages\\n" +
        "- Open Protocol Deviations: " + site.open_pd_count + " | IRT Kits: " + site.available_kits + "/" + site.min_buffer_kits + "\\n\\n" +
        "## 3. Trial Selection Screening Candidates\\n" + candMd
      );
    }

    function renderSingleSite() {
      const site = getSelectedSite();
      if (!site) return;
      document.getElementById("site-view-selector").value = site.site_id;
      const badge = document.getElementById("site-tier-badge");
      badge.className = "badge " + tierBadgeClass(site.priority_tier);
      badge.textContent = site.priority_tier;

      document.getElementById("site-metadata-strip").innerHTML = [
        ["Study Protocol", site.protocol_code + " (" + site.study_id + ")"],
        ["Site Status", site.site_status],
        ["Assigned / Peer CRA", site.assigned_cra_id + " / " + site.peer_cra_id],
        ["Last Monitoring", site.last_monitoring_date + " (" + site.days_since_monitoring + "d ago)"],
        ["Enrollment", site.enrolled_count + " / " + site.target_enrollment + " subjects"],
        ["IRT Kits / Excursion", site.available_kits + " kits / " + (site.unresolved_excursion ? "EXCURSION" : "Clear")]
      ].map(function (pair) {
        return '<div><div class="kv-label">' + escHtml(pair[0]) + '</div><div class="kv-val mono">' + escHtml(pair[1]) + "</div></div>";
      }).join("");

      document.getElementById("site-ai-summary").textContent = site.site_summary;

      const container = document.getElementById("site-subtab-container");
      const sub = STATE.siteSubtab;

      if (sub === "signals") {
        const sigs = WORKSPACE.signals.filter(function (s) { return s.site_id === site.site_id; });
        container.innerHTML =
          '<table><thead><tr><th>Include</th><th>Signal</th><th>Family</th><th>Severity</th><th>Title</th><th>Disposition</th><th>Evidence</th></tr></thead><tbody>' +
          (sigs.length ? sigs.map(function (sig) {
            const checked = STATE.checkedSignalIds.size === 0 || STATE.checkedSignalIds.has(sig.signal_id);
            return (
              "<tr>" +
              '<td><input type="checkbox" data-sig-check="' + escHtml(sig.signal_id) + '" ' + (checked ? "checked" : "") + " /></td>" +
              '<td class="mono">' + escHtml(sig.signal_id) + "</td>" +
              '<td class="mono">' + escHtml(sig.family) + "</td>" +
              '<td><span class="badge ' + sevBadgeClass(sig.severity) + '">' + escHtml(sig.severity) + "</span></td>" +
              "<td>" + escHtml(sig.title) + "</td>" +
              '<td class="mono">' + escHtml(sig.disposition) + "</td>" +
              '<td><button type="button" data-open-signal="' + escHtml(sig.signal_id) + '">Audit Evidence</button></td>' +
              "</tr>"
            );
          }).join("") : '<tr><td colspan="7">Zero signals triggered for this site.</td></tr>') +
          "</tbody></table>";

        container.querySelectorAll("[data-sig-check]").forEach(function (cb) {
          cb.addEventListener("change", function () {
            const allSiteIds = sigs.map(function (x) { return x.signal_id; });
            if (STATE.checkedSignalIds.size === 0) {
              allSiteIds.forEach(function (id) { STATE.checkedSignalIds.add(id); });
            }
            const id = cb.getAttribute("data-sig-check");
            if (cb.checked) STATE.checkedSignalIds.add(id);
            else STATE.checkedSignalIds.delete(id);
            document.getElementById("site-draft-output").textContent = compileSiteDraft(site, STATE.siteDraftMode);
          });
        });
        container.querySelectorAll("[data-open-signal]").forEach(function (btn) {
          btn.addEventListener("click", function () {
            STATE.selectedSignalId = btn.getAttribute("data-open-signal");
            document.getElementById("signal-view-selector").value = STATE.selectedSignalId;
            switchView("signal");
          });
        });
      } else if (sub === "events") {
        const saes = site.safety_events || [];
        const visits = site.edc_visits || [];
        container.innerHTML =
          '<table><thead><tr><th>Source</th><th>Record ID</th><th>Subject</th><th>Detail</th><th class="num">Metric</th><th>Status</th></tr></thead><tbody>' +
          saes.map(function (e) {
            return '<tr><td class="mono">Safety/PV</td><td class="mono">' + escHtml(e.sae_id) + '</td><td class="mono">' + escHtml(e.subject_id) +
              "</td><td>" + escHtml(e.event_term) + " (Grade " + e.ctcae_grade + ')</td><td class="num">' + e.followup_cycle_days +
              'd cycle</td><td class="mono">' + escHtml(e.report_status) + "</td></tr>";
          }).join("") +
          visits.map(function (v) {
            return '<tr><td class="mono">EDC Visit</td><td class="mono">' + escHtml(v.visit_record_id) + '</td><td class="mono">' + escHtml(v.subject_id) +
              "</td><td>" + escHtml(v.visit_name) + " (ICF " + escHtml(v.signed_icf_version) + ')</td><td class="num">' + v.open_queries +
              " queries (" + v.max_query_age_days + 'd)</td><td class="mono">' + escHtml(v.visit_status) + "</td></tr>";
          }).join("") +
          "</tbody></table>";
      } else if (sub === "issues") {
        const issues = site.quality_issues || [];
        const trn = site.training_records || [];
        container.innerHTML =
          '<table><thead><tr><th>Type</th><th>ID</th><th>Category / Role</th><th class="num">Days / Expiry</th><th>Status</th><th>Summary</th></tr></thead><tbody>' +
          issues.map(function (i) {
            return '<tr><td class="mono">QMS Issue</td><td class="mono">' + escHtml(i.issue_id) + "</td><td>" + escHtml(i.category) +
              '</td><td class="num">' + i.days_open + 'd open</td><td class="mono">' + escHtml(i.status) + "</td><td>" + escHtml(i.summary) + "</td></tr>";
          }).join("") +
          trn.map(function (t) {
            return '<tr><td class="mono">LMS Cert</td><td class="mono">' + escHtml(t.training_id) + "</td><td>" + escHtml(t.staff_role_id + " / " + t.cert_type) +
              '</td><td class="num">' + t.days_until_expiry + 'd</td><td class="mono">' + escHtml(t.status) + "</td><td>Staff certification record</td></tr>";
          }).join("") +
          "</tbody></table>";
      } else if (sub === "pds") {
        const pds = site.protocol_deviations || [];
        container.innerHTML =
          '<table><thead><tr><th>PD ID</th><th>Subject</th><th>Category</th><th>Severity</th><th class="num">Days Open</th><th>CAPA</th><th>Description</th></tr></thead><tbody>' +
          (pds.length ? pds.map(function (p) {
            return '<tr><td class="mono">' + escHtml(p.pd_id) + '</td><td class="mono">' + escHtml(p.subject_id) + "</td><td>" + escHtml(p.category) +
              '</td><td><span class="badge ' + (p.severity === "MAJOR" ? "badge-critical" : "badge-muted") + '">' + escHtml(p.severity) +
              '</span></td><td class="num">' + p.days_open + '</td><td class="mono">' + (p.capa_required ? "Required" : "No") +
              "</td><td>" + escHtml(p.description) + "</td></tr>";
          }).join("") : '<tr><td colspan="7">Zero protocol deviations logged at this site.</td></tr>') +
          "</tbody></table>";
      } else {
        const cands = WORKSPACE.patient_selection.patients.filter(function (p) {
          return p.recommended_site_id === site.site_id;
        });
        container.innerHTML =
          '<table><thead><tr><th>Patient ID</th><th>Age/Sex</th><th>ICD-10</th><th>Classification</th><th class="num">Match Score</th><th>Action</th></tr></thead><tbody>' +
          (cands.length ? cands.map(function (p) {
            return '<tr><td class="mono">' + escHtml(p.patient_id) + '</td><td class="mono">' + p.age_years + "/" + escHtml(p.sex) +
              '</td><td class="mono">' + escHtml(p.primary_icd10) + '</td><td><span class="badge ' + classifBadgeClass(p.primary_classification) + '">' +
              escHtml(p.primary_classification) + '</span></td><td class="num">' + p.primary_match_score +
              '</td><td><button type="button" data-inspect-pat="' + escHtml(p.patient_id) + '">Open in Trial Selector</button></td></tr>';
          }).join("") : '<tr><td colspan="6">No candidates currently routed to this site.</td></tr>') +
          "</tbody></table>";

        container.querySelectorAll("[data-inspect-pat]").forEach(function (b) {
          b.addEventListener("click", function () {
            STATE.selectedPatientId = b.getAttribute("data-inspect-pat");
            switchView("selection");
          });
        });
      }

      document.getElementById("site-draft-output").textContent = compileSiteDraft(site, STATE.siteDraftMode);
    }

    /* --- LEVEL 3 SIGNAL EVIDENCE RENDERER --- */
    function renderSignalDetail() {
      const sig = WORKSPACE.signals.find(function (s) { return s.signal_id === STATE.selectedSignalId; }) || WORKSPACE.signals[0];
      if (!sig) return;
      document.getElementById("signal-view-selector").value = sig.signal_id;
      const sevBadge = document.getElementById("signal-severity-badge");
      sevBadge.className = "badge " + sevBadgeClass(sig.severity);
      sevBadge.textContent = sig.severity + " (Weight " + sig.weight + ")";

      const dispBadge = document.getElementById("signal-disposition-badge");
      dispBadge.textContent = "Disposition: " + sig.disposition;

      document.getElementById("signal-kv-strip").innerHTML = [
        ["Signal / Rule ID", sig.signal_id + " / " + sig.rule_id],
        ["Signal Family", sig.family],
        ["Clinical Site", sig.site_id + " (" + sig.site_name + ")"],
        ["Study Protocol", sig.protocol_code + " (" + sig.study_id + ")"],
        ["Assigned CRA", sig.assigned_cra_id]
      ].map(function (pair) {
        return '<div><div class="kv-label">' + escHtml(pair[0]) + '</div><div class="kv-val mono">' + escHtml(pair[1]) + "</div></div>";
      }).join("");

      document.getElementById("signal-rationale-text").textContent = sig.rationale;
      document.getElementById("signal-recommended-action").textContent = sig.recommended_action;
      document.getElementById("signal-feedback-note").value = sig.reviewer_note || "";

      const ev = sig.evidence;
      document.getElementById("signal-evidence-tbody").innerHTML = [
        ["Source System Feed", ev.source_file],
        ["Originating Record ID", ev.source_record_id],
        ["Evaluated Metric Field", ev.metric_name],
        ["Observed Value", String(ev.observed_value)],
        ["Protocol / SLA Threshold", String(ev.threshold_value)],
        ["Reviewer Note", sig.reviewer_note || "None recorded"]
      ].map(function (r) {
        return "<tr><th>" + escHtml(r[0]) + '</th><td class="mono">' + escHtml(r[1]) + "</td></tr>";
      }).join("");

      const issuePayload = {
        payload_type: "PHASE1_MANUAL_ISSUE_EXPORT",
        synthetic_record: true,
        generated_date: WORKSPACE.metadata.reference_date,
        study_id: sig.study_id,
        site_id: sig.site_id,
        originating_signal_id: sig.signal_id,
        rule_id: sig.rule_id,
        signal_family: sig.family,
        severity: sig.severity,
        issue_title: sig.title,
        evidence_provenance: sig.evidence,
        recommended_capa_action: sig.recommended_action,
        disposition: sig.disposition
      };
      document.getElementById("signal-issue-json").textContent = JSON.stringify(issuePayload, null, 2);
      document.getElementById("signal-trend-subtitle").textContent = sig.site_id + " (W-7 to Current)";
      drawSiteTrendCanvas(sig);
    }

    function submitFeedback(disposition) {
      const sig = WORKSPACE.signals.find(function (s) { return s.signal_id === STATE.selectedSignalId; });
      if (!sig) return;
      const note = document.getElementById("signal-feedback-note").value.trim();
      sig.disposition = disposition;
      sig.reviewer_note = note;
      STATE.localFeedback[sig.signal_id] = {
        signal_id: sig.signal_id,
        disposition: disposition,
        note: note,
        updated_at: new Date().toISOString()
      };
      localStorage.setItem("ctw_hitl_feedback", JSON.stringify(STATE.localFeedback));

      /* Also notify backend if running via HTTP/SSE server */
      if (window.location.protocol.startsWith("http")) {
        fetch("/api/feedback", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ signal_id: sig.signal_id, disposition: disposition, note: note })
        }).catch(function () {});
      }
      renderSignalDetail();
    }

    /* --- VIEW 4: CLINICAL TRIAL SELECTION RENDERER --- */
    function getFilteredPatients() {
      const q = STATE.patientSearch.trim().toLowerCase();
      return WORKSPACE.patient_selection.patients.filter(function (p) {
        if (STATE.patientStudy !== "ALL" && p.primary_match_study_id !== STATE.patientStudy) return false;
        if (STATE.patientStatus !== "ALL" && p.primary_classification !== STATE.patientStatus) return false;
        if (q) {
          const hay = (p.patient_id + " " + p.primary_icd10 + " " + p.disease_stage + " " + p.primary_match_protocol + " " + (p.recommended_site_id || "")).toLowerCase();
          if (hay.indexOf(q) === -1) return false;
        }
        return true;
      });
    }

    function renderTrialSelection() {
      const filtered = getFilteredPatients();
      const tbody = document.getElementById("patient-selection-tbody");
      tbody.innerHTML = filtered.map(function (p) {
        const isSel = p.patient_id === STATE.selectedPatientId;
        return (
          '<tr data-pat-row="' + escHtml(p.patient_id) + '" class="' + (isSel ? "selected-row" : "") + '">' +
          '<td class="mono"><strong>' + escHtml(p.patient_id) + "</strong></td>" +
          '<td class="mono">' + p.age_years + " / " + escHtml(p.sex) + "</td>" +
          '<td class="mono">' + escHtml(p.region) + "</td>" +
          '<td class="mono">' + escHtml(p.primary_icd10) + "</td>" +
          '<td class="num">' + p.ecog_status + "</td>" +
          '<td class="mono">' + escHtml(p.primary_match_protocol) + "</td>" +
          '<td><span class="badge ' + classifBadgeClass(p.primary_classification) + '">' + escHtml(p.primary_classification) + "</span></td>" +
          '<td class="num">' + p.primary_match_score + "</td>" +
          '<td class="mono">' + escHtml(p.recommended_site_id || "Disqualified") + "</td>" +
          "</tr>"
        );
      }).join("");

      tbody.querySelectorAll("[data-pat-row]").forEach(function (tr) {
        tr.addEventListener("click", function () {
          STATE.selectedPatientId = tr.getAttribute("data-pat-row");
          renderTrialSelection();
        });
      });

      const pat = WORKSPACE.patient_selection.patients.find(function (p) { return p.patient_id === STATE.selectedPatientId; }) || filtered[0] || WORKSPACE.patient_selection.patients[0];
      if (pat) {
        document.getElementById("selected-patient-label").textContent = pat.patient_id + " -> " + pat.primary_match_protocol + " (" + pat.primary_match_score + "/100)";
        const L = pat.labs || {};
        document.getElementById("selected-patient-labs").innerHTML = [
          ["Stage / ICD-10", pat.disease_stage + " (" + pat.primary_icd10 + ")"],
          ["eGFR / Bilirubin", (L.egfr_ml_min != null ? L.egfr_ml_min + " mL/min" : "Missing") + " / " + (L.bilirubin_mg_dl != null ? L.bilirubin_mg_dl + " mg/dL" : "Missing")],
          ["Specialty Assay", L.egfr_mutation ? ("EGFR " + L.egfr_mutation + " | PD-L1 " + (L.pdl1_tps_pct ?? "NA") + "%") : (L.csf_ptau_pg_ml != null ? ("pTau " + L.csf_ptau_pg_ml + " pg/mL") : (L.fecal_calprotectin_ug_g != null ? ("Calprotectin " + L.fecal_calprotectin_ug_g) : ("LVEF " + (L.lvef_pct ?? "Missing") + "%")))],
          ["Recommended Site", pat.recommended_site_id || "None (Ineligible)"]
        ].map(function (pair) {
          return '<div><div class="kv-label">' + escHtml(pair[0]) + '</div><div class="kv-val mono">' + escHtml(pair[1]) + "</div></div>";
        }).join("");

        document.getElementById("patient-criteria-tbody").innerHTML = (pat.criteria_audit || []).map(function (c) {
          const bCls = c.status === "PASS" ? "badge-emerald" : (c.status === "MISSING_DATA" ? "badge-needs-labs" : "badge-ineligible");
          return (
            "<tr>" +
            '<td class="mono">' + escHtml(c.criterion_id) + "</td>" +
            '<td><span class="badge ' + bCls + '">' + escHtml(c.status) + "</span></td>" +
            '<td class="mono">' + escHtml(c.observed) + "</td>" +
            "<td>" + escHtml(c.requirement) + "</td>" +
            "</tr>"
          );
        }).join("");
      }

      draw3DBiomarkerCanvas();
      drawNodeMapCanvas();
    }

    /* --- VIEW 5: CONVERSATIONAL ANALYTICS & PROVENANCE --- */
    function renderAnalyticsView() {
      const res = STATE.activeAnalyticsResult || WORKSPACE.analytics_catalog[0];
      if (res) {
        document.getElementById("analytics-narrative-output").textContent = res.narrative;
        document.getElementById("analytics-sql-output").textContent = res.sql;
        document.getElementById("analytics-chart-title").textContent = (res.chart_spec && res.chart_spec.title) || "Analytical Series Plot";
        document.getElementById("analytics-provenance-pills").textContent = "Sources: " + (res.provenance_sources || []).join(", ");
        document.getElementById("analytics-row-count").textContent = (res.rows ? res.rows.length : 0) + " rows";

        const cols = res.columns || [];
        document.getElementById("analytics-results-thead").innerHTML =
          "<tr>" + cols.map(function (c) { return "<th>" + escHtml(c) + "</th>"; }).join("") + "</tr>";
        document.getElementById("analytics-results-tbody").innerHTML = (res.rows || []).map(function (r) {
          return "<tr>" + cols.map(function (c) {
            const v = r[c];
            const cls = typeof v === "number" ? ' class="num"' : ' class="mono"';
            return "<td" + cls + ">" + escHtml(v) + "</td>";
          }).join("") + "</tr>";
        }).join("");
        drawAnalyticsCanvas(res);
      }

      const prov = WORKSPACE.provenance;
      document.getElementById("provenance-totals-meta").textContent =
        prov.total_records_loaded + " records | " + prov.quarantined_records + " quarantined | " + prov.rules_evaluated + " rules";
      document.getElementById("provenance-sources-tbody").innerHTML = (prov.sources || []).map(function (src) {
        return (
          "<tr>" +
          '<td class="mono">' + escHtml(src.source_file) + "</td>" +
          '<td class="mono">' + escHtml(src.file_format.toUpperCase()) + "</td>" +
          '<td class="num">' + src.records_ingested + "</td>" +
          '<td class="mono">' + escHtml(String(src.sha256).slice(0, 14)) + "...</td>" +
          "</tr>"
        );
      }).join("");
    }

    function runClientOrServerAnalytics(promptText) {
      if (!promptText) return;
      if (window.location.protocol.startsWith("http")) {
        fetch("/api/ask", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: promptText })
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data.error) {
              document.getElementById("analytics-narrative-output").textContent = "Guardrail Blocked Query: " + data.error;
              return;
            }
            STATE.activeAnalyticsResult = data;
            renderAnalyticsView();
          })
          .catch(function () { fallbackClientAnalytics(promptText); });
      } else {
        fallbackClientAnalytics(promptText);
      }
    }

    function fallbackClientAnalytics(promptText) {
      const lower = promptText.toLowerCase();
      if (/\\b(drop|delete|update|insert|alter|attach|pragma)\\b/i.test(promptText)) {
        document.getElementById("analytics-narrative-output").textContent =
          "SQLSafetyError: Non-read-only SQL keyword blocked by client security guardrail.";
        return;
      }
      let best = WORKSPACE.analytics_catalog[0];
      let bestScore = -1;
      WORKSPACE.analytics_catalog.forEach(function (item) {
        const words = item.question.toLowerCase().split(/\\W+/);
        let hits = 0;
        words.forEach(function (w) { if (w.length > 3 && lower.indexOf(w) !== -1) hits += 1; });
        if (hits > bestScore) { bestScore = hits; best = item; }
      });
      STATE.activeAnalyticsResult = Object.assign({}, best, { question: promptText });
      renderAnalyticsView();
    }

    /* =====================================================================
       HTML5 CANVAS VISUALIZATION ENGINE (High-DPI, Theme-Aware, Interactive)
       ===================================================================== */
    function setupCanvas(id) {
      const canvas = document.getElementById(id);
      if (!canvas || !canvas.parentElement) return null;
      const rect = canvas.parentElement.getBoundingClientRect();
      const w = Math.max(260, Math.floor(rect.width));
      const h = Math.max(180, Math.floor(rect.height));
      const dpr = window.devicePixelRatio || 1;
      if (canvas.width !== Math.floor(w * dpr) || canvas.height !== Math.floor(h * dpr)) {
        canvas.width = Math.floor(w * dpr);
        canvas.height = Math.floor(h * dpr);
      }
      const ctx = canvas.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      return { canvas: canvas, ctx: ctx, w: w, h: h };
    }

    function hexToRgb(hex) {
      const clean = hex.replace("#", "");
      return [
        parseInt(clean.substring(0, 2), 16),
        parseInt(clean.substring(2, 4), 16),
        parseInt(clean.substring(4, 6), 16)
      ];
    }

    function lerpColor(hexA, hexB, t) {
      const a = hexToRgb(hexA);
      const b = hexToRgb(hexB);
      const r = Math.round(a[0] + (b[0] - a[0]) * t);
      const g = Math.round(a[1] + (b[1] - a[1]) * t);
      const bl = Math.round(a[2] + (b[2] - a[2]) * t);
      return "rgb(" + r + "," + g + "," + bl + ")";
    }

    /* Smooth concentration gradient: surface -> #0D9488 -> #4F46E5 -> #E11D48 (DESIGN.md Section 5) */
    function heatmapColor(normVal, surfaceHex) {
      if (normVal <= 0.001) return surfaceHex;
      if (normVal <= 0.35) return lerpColor(surfaceHex, PALETTE.teal, normVal / 0.35);
      if (normVal <= 0.70) return lerpColor(PALETTE.teal, PALETTE.indigo, (normVal - 0.35) / 0.35);
      return lerpColor(PALETTE.indigo, PALETTE.rose, Math.min(1, (normVal - 0.70) / 0.30));
    }

    const heatmapCells = [];
    function drawHeatmapCanvas() {
      const stage = setupCanvas("canvas-heatmap");
      if (!stage) return;
      const ctx = stage.ctx, w = stage.w, h = stage.h;
      const tk = themeTokens();
      const sites = getScopedSites();
      const fams = WORKSPACE.signal_families || [];
      const shortFam = ["SAFETY", "CONSENT", "TRAINING", "EDC QUAL", "ENROLL", "IRT SUPP", "PROTOCOL"];

      heatmapCells.length = 0;
      if (!sites.length || !fams.length) return;

      const leftPad = 76, topPad = 28, rightPad = 14, bottomPad = 10;
      const gridW = w - leftPad - rightPad;
      const gridH = h - topPad - bottomPad;
      const cellW = gridW / fams.length;
      const cellH = gridH / sites.length;

      ctx.font = '500 10px "IBM Plex Mono", monospace';
      ctx.fillStyle = tk.muted;
      ctx.textAlign = "center";
      fams.forEach(function (_, cIdx) {
        ctx.fillText(shortFam[cIdx] || fams[cIdx], leftPad + cIdx * cellW + cellW / 2, 16);
      });

      sites.forEach(function (site, rIdx) {
        const y = topPad + rIdx * cellH;
        ctx.fillStyle = tk.text;
        ctx.textAlign = "right";
        ctx.font = '500 10px "IBM Plex Mono", monospace';
        ctx.fillText(site.site_id, leftPad - 8, y + cellH * 0.68);

        fams.forEach(function (fam, cIdx) {
          const x = leftPad + cIdx * cellW;
          const weight = (site.family_weights && site.family_weights[fam]) || 0;
          const count = (site.family_counts && site.family_counts[fam]) || 0;
          const norm = Math.min(1, weight / 75);
          ctx.fillStyle = heatmapColor(norm, tk.surface);
          ctx.fillRect(x + 1, y + 1, Math.max(2, cellW - 2), Math.max(2, cellH - 2));
          ctx.strokeStyle = tk.border;
          ctx.lineWidth = 1;
          ctx.strokeRect(x + 1, y + 1, Math.max(2, cellW - 2), Math.max(2, cellH - 2));

          if (count > 0 && cellH >= 13) {
            ctx.fillStyle = norm > 0.3 ? "#FFFFFF" : tk.text;
            ctx.textAlign = "center";
            ctx.fillText(String(count), x + cellW / 2, y + cellH * 0.7);
          }
          heatmapCells.push({
            x: x, y: y, w: cellW, h: cellH,
            site_id: site.site_id, site_name: site.site_name,
            family: fam, count: count, weight: weight
          });
        });
      });
    }

    function drawSaeTrendCanvas() {
      const stage = setupCanvas("canvas-sae-trend");
      if (!stage) return;
      const ctx = stage.ctx, w = stage.w, h = stage.h;
      const tk = themeTokens();
      const saeCatalog = WORKSPACE.analytics_catalog[0];
      const rows = (saeCatalog && saeCatalog.rows) || [];
      if (!rows.length) return;

      const padL = 44, padR = 18, padT = 22, padB = 38;
      const pw = w - padL - padR;
      const ph = h - padT - padB;
      const maxY = 32;

      ctx.strokeStyle = tk.border;
      ctx.lineWidth = 1;
      ctx.font = '10px "IBM Plex Mono", monospace';
      ctx.fillStyle = tk.muted;
      [0, 7, 14, 21, 28].forEach(function (val) {
        const y = padT + ph - (val / maxY) * ph;
        ctx.beginPath();
        ctx.moveTo(padL, y);
        ctx.lineTo(padL + pw, y);
        ctx.stroke();
        ctx.textAlign = "right";
        ctx.fillText(val + "d", padL - 6, y + 3);
      });

      /* 7d target line (#059669) and 28d baseline (#D97706) */
      const yTarget = padT + ph - (7 / maxY) * ph;
      ctx.strokeStyle = PALETTE.emerald;
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(padL, yTarget); ctx.lineTo(padL + pw, yTarget); ctx.stroke();

      const yBase = padT + ph - (28 / maxY) * ph;
      ctx.strokeStyle = PALETTE.amber;
      ctx.beginPath(); ctx.moveTo(padL, yBase); ctx.lineTo(padL + pw, yBase); ctx.stroke();
      ctx.setLineDash([]);

      const groupW = pw / rows.length;
      rows.forEach(function (r, idx) {
        const gx = padL + idx * groupW;
        const barW = Math.min(26, groupW * 0.28);
        const avgH = (Number(r.avg_cycle_days) / maxY) * ph;
        const maxH = (Number(r.max_cycle_days) / maxY) * ph;

        ctx.fillStyle = PALETTE.teal;
        ctx.fillRect(gx + groupW * 0.22, padT + ph - avgH, barW, avgH);

        ctx.fillStyle = Number(r.max_cycle_days) > 7 ? PALETTE.rose : PALETTE.indigo;
        ctx.fillRect(gx + groupW * 0.22 + barW + 5, padT + ph - maxH, barW, maxH);

        ctx.fillStyle = tk.text;
        ctx.textAlign = "center";
        ctx.fillText(r.protocol_code, gx + groupW * 0.5, padT + ph + 18);
      });
    }

    function hexAlpha(hex, alpha) {
      const rgb = hexToRgb(hex);
      return "rgba(" + rgb[0] + "," + rgb[1] + "," + rgb[2] + "," + alpha + ")";
    }

    function drawRoundedRect(ctx, x, y, w, h, r) {
      const rad = Math.min(r, w / 2, h / 2);
      ctx.beginPath();
      ctx.moveTo(x + rad, y);
      ctx.lineTo(x + w - rad, y);
      ctx.quadraticCurveTo(x + w, y, x + w, y + rad);
      ctx.lineTo(x + w, y + h - rad);
      ctx.quadraticCurveTo(x + w, y + h, x + w - rad, y + h);
      ctx.lineTo(x + rad, y + h);
      ctx.quadraticCurveTo(x, y + h, x, y + h - rad);
      ctx.lineTo(x, y + rad);
      ctx.quadraticCurveTo(x, y, x + rad, y);
      ctx.closePath();
    }

    function drawPillBadge(ctx, text, x, y, bgHex, borderHex, textHex) {
      ctx.font = '500 10px "IBM Plex Mono", monospace';
      const tw = ctx.measureText(text).width;
      const pw = tw + 12, ph = 18;
      const bx = x - pw / 2, by = y - ph / 2;
      drawRoundedRect(ctx, bx, by, pw, ph, 4);
      ctx.fillStyle = bgHex;
      ctx.fill();
      ctx.strokeStyle = borderHex;
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.fillStyle = textHex;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(text, x, y + 0.5);
      ctx.textBaseline = "alphabetic";
    }

    function traceSmoothSpline(ctx, pts) {
      if (!pts.length) return;
      ctx.moveTo(pts[0].x, pts[0].y);
      for (let i = 0; i < pts.length - 1; i++) {
        const p0 = pts[Math.max(0, i - 1)];
        const p1 = pts[i];
        const p2 = pts[i + 1];
        const p3 = pts[Math.min(pts.length - 1, i + 2)];
        const cp1x = p1.x + (p2.x - p0.x) / 6;
        const cp1y = p1.y + (p2.y - p0.y) / 6;
        const cp2x = p2.x - (p3.x - p1.x) / 6;
        const cp2y = p2.y - (p3.y - p1.y) / 6;
        ctx.bezierCurveTo(cp1x, cp1y, cp2x, cp2y, p2.x, p2.y);
      }
    }

    function computePortfolioWeeklyPeerAvg(len) {
      const sites = WORKSPACE.sites || [];
      if (!sites.length) return new Array(len).fill(2);
      const avg = [];
      for (let i = 0; i < len; i++) {
        let sum = 0;
        sites.forEach(function (s) {
          const arr = s.weekly_signal_trend || [];
          sum += Number(arr[i] != null ? arr[i] : 0);
        });
        avg.push(Number((sum / sites.length).toFixed(1)));
      }
      return avg;
    }

    /* -------------------------------------------------------------------
       1. HISTORICAL 8-WEEK SITE SIGNAL TREND (Multi-Layer Telemetry Plot)
       ------------------------------------------------------------------- */
    function drawSiteTrendCanvas(sig) {
      const stage = setupCanvas("canvas-site-trend");
      if (!stage || !sig) return;
      const ctx = stage.ctx, w = stage.w, h = stage.h;
      const tk = themeTokens();
      const series = sig.site_weekly_trend || [1, 2, 2, 3, 3, 4, 4, 4];
      const n = series.length;
      const peerSeries = computePortfolioWeeklyPeerAvg(n);

      const siteObj = WORKSPACE.sites.find(function (s) { return s.site_id === sig.site_id; });
      const critShare = siteObj && siteObj.signal_counts.TOTAL > 0
        ? siteObj.signal_counts.CRITICAL / siteObj.signal_counts.TOTAL
        : (sig.severity === "CRITICAL" ? 0.45 : 0.0);

      const primaryColor = sig.severity === "CRITICAL"
        ? PALETTE.rose
        : (sig.severity === "HIGH" ? PALETTE.amber : PALETTE.teal);

      const padL = 40, padR = 64, padT = 36, padB = 30;
      const pw = w - padL - padR;
      const ph = h - padT - padB;
      const peakVal = Math.max.apply(null, series);
      const maxPeer = Math.max.apply(null, peerSeries);
      const maxVal = Math.max(8, Math.ceil(Math.max(peakVal, maxPeer) + 2));

      function yOf(val) {
        return padT + ph - (Math.max(0, val) / maxVal) * ph;
      }
      function xOf(idx) {
        return padL + (idx / Math.max(1, n - 1)) * pw;
      }

      /* Top KPI Summary Strip inside Canvas */
      const currVal = series[n - 1];
      const deltaVal = currVal - series[0];
      const deltaStr = (deltaVal >= 0 ? "+" : "") + deltaVal;
      const vsPeer = (currVal - peerSeries[n - 1]).toFixed(1);
      const vsPeerStr = (Number(vsPeer) >= 0 ? "+" : "") + vsPeer;
      ctx.font = '500 10px "IBM Plex Mono", monospace';
      ctx.textAlign = "left";
      ctx.fillStyle = tk.muted;
      const kpiText =
        "NOW: " + currVal + " SIGS   |   8W DELTA: " + deltaStr +
        "   |   PEAK: " + peakVal + "   |   VS PEER AVG: " + vsPeerStr;
      ctx.fillText(kpiText, padL, 18);

      /* Horizontal Regime Shading Bands (Nominal / Watch / Critical SLA) */
      const yNomTop = yOf(Math.min(maxVal, 3));
      const yWatchTop = yOf(Math.min(maxVal, 6));
      const yBottom = yOf(0);

      ctx.fillStyle = hexAlpha(PALETTE.emerald, tk.dark ? 0.05 : 0.04);
      ctx.fillRect(padL, yNomTop, pw, yBottom - yNomTop);

      if (maxVal > 3) {
        ctx.fillStyle = hexAlpha(PALETTE.amber, tk.dark ? 0.055 : 0.045);
        ctx.fillRect(padL, yWatchTop, pw, yNomTop - yWatchTop);
      }
      if (maxVal > 6) {
        ctx.fillStyle = hexAlpha(PALETTE.rose, tk.dark ? 0.065 : 0.05);
        ctx.fillRect(padL, padT, pw, yWatchTop - padT);
      }

      /* Quantitative Gridlines & Y-Axis Ticks */
      const step = maxVal > 12 ? 4 : 2;
      ctx.font = '10px "IBM Plex Mono", monospace';
      ctx.lineWidth = 1;
      for (let v = 0; v <= maxVal; v += step) {
        const gy = yOf(v);
        ctx.strokeStyle = tk.border;
        ctx.beginPath(); ctx.moveTo(padL, gy); ctx.lineTo(padL + pw, gy); ctx.stroke();
        ctx.fillStyle = tk.muted;
        ctx.textAlign = "right";
        ctx.fillText(String(v), padL - 7, gy + 3);
      }

      /* Vertical Week Guides */
      for (let i = 0; i < n; i++) {
        const gx = xOf(i);
        ctx.strokeStyle = hexAlpha(tk.border, 0.55);
        ctx.beginPath(); ctx.moveTo(gx, padT); ctx.lineTo(gx, yBottom); ctx.stroke();
      }

      /* Critical Threshold SLA Line (y = 6) */
      if (maxVal >= 6) {
        const yCrit = yOf(6);
        ctx.save();
        ctx.strokeStyle = hexAlpha(PALETTE.rose, 0.75);
        ctx.lineWidth = 1.1;
        ctx.setLineDash([5, 4]);
        ctx.beginPath(); ctx.moveTo(padL, yCrit); ctx.lineTo(padL + pw, yCrit); ctx.stroke();
        ctx.restore();
        ctx.fillStyle = PALETTE.rose;
        ctx.font = '600 9px "IBM Plex Mono", monospace';
        ctx.textAlign = "left";
        ctx.fillText("SLA >=6", padL + pw + 6, yCrit + 3);
      }
      ctx.fillStyle = PALETTE.amber;
      ctx.font = '500 9px "IBM Plex Mono", monospace';
      ctx.textAlign = "left";
      ctx.fillText("WATCH", padL + pw + 6, (yWatchTop + yNomTop) / 2 + 3);
      ctx.fillStyle = PALETTE.emerald;
      ctx.fillText("NOMINAL", padL + pw + 6, (yNomTop + yBottom) / 2 + 3);

      /* Background Segmented Weekly Severity Load Columns */
      const barW = Math.min(18, (pw / n) * 0.34);
      series.forEach(function (val, idx) {
        if (val <= 0) return;
        const bx = xOf(idx) - barW / 2;
        const totalH = yBottom - yOf(val);
        const critH = totalH * critShare;
        const baseH = totalH - critH;

        ctx.fillStyle = hexAlpha(PALETTE.teal, tk.dark ? 0.24 : 0.18);
        ctx.fillRect(bx, yBottom - baseH, barW, baseH);
        if (critH > 0.5) {
          ctx.fillStyle = hexAlpha(PALETTE.rose, tk.dark ? 0.32 : 0.24);
          ctx.fillRect(bx, yOf(val), barW, critH);
        }
      });

      const sitePts = series.map(function (v, idx) {
        return { x: xOf(idx), y: yOf(v), val: v, peer: peerSeries[idx], idx: idx, label: idx === n - 1 ? "Now" : "W-" + (n - 1 - idx) };
      });
      const peerPts = peerSeries.map(function (v, idx) {
        return { x: xOf(idx), y: yOf(v), val: v };
      });
      STATE.trendHitPoints = sitePts;

      /* Smooth Area Gradient Under Site Trajectory */
      const grad = ctx.createLinearGradient(0, padT, 0, yBottom);
      grad.addColorStop(0, hexAlpha(primaryColor, tk.dark ? 0.30 : 0.22));
      grad.addColorStop(1, hexAlpha(primaryColor, 0.01));
      ctx.beginPath();
      traceSmoothSpline(ctx, sitePts);
      ctx.lineTo(sitePts[n - 1].x, yBottom);
      ctx.lineTo(sitePts[0].x, yBottom);
      ctx.closePath();
      ctx.fillStyle = grad;
      ctx.fill();

      /* Portfolio Peer Cohort Benchmark Spline (Dashed Vivid Indigo) */
      ctx.save();
      ctx.strokeStyle = PALETTE.indigo;
      ctx.lineWidth = 1.6;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      traceSmoothSpline(ctx, peerPts);
      ctx.stroke();
      ctx.restore();

      peerPts.forEach(function (pt) {
        ctx.save();
        ctx.translate(pt.x, pt.y);
        ctx.rotate(Math.PI / 4);
        ctx.fillStyle = tk.surface;
        ctx.strokeStyle = PALETTE.indigo;
        ctx.lineWidth = 1.3;
        ctx.fillRect(-2.6, -2.6, 5.2, 5.2);
        ctx.strokeRect(-2.6, -2.6, 5.2, 5.2);
        ctx.restore();
      });

      /* Primary Site Smooth Trajectory Spline */
      ctx.strokeStyle = primaryColor;
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      traceSmoothSpline(ctx, sitePts);
      ctx.stroke();

      /* Interactive Hover Scrubber Crosshair */
      if (STATE.hoveredTrendIdx >= 0 && STATE.hoveredTrendIdx < n) {
        const hp = sitePts[STATE.hoveredTrendIdx];
        ctx.save();
        ctx.strokeStyle = tk.text;
        ctx.lineWidth = 1;
        ctx.setLineDash([2, 2]);
        ctx.beginPath(); ctx.moveTo(hp.x, padT); ctx.lineTo(hp.x, yBottom); ctx.stroke();
        ctx.restore();
      }

      /* Multi-Ring Data Nodes & Week Axis Labels */
      sitePts.forEach(function (pt, idx) {
        const isHov = idx === STATE.hoveredTrendIdx || idx === n - 1;
        if (isHov) {
          ctx.fillStyle = hexAlpha(primaryColor, 0.20);
          ctx.beginPath(); ctx.arc(pt.x, pt.y, 9, 0, Math.PI * 2); ctx.fill();
        }
        ctx.fillStyle = tk.surface;
        ctx.beginPath(); ctx.arc(pt.x, pt.y, isHov ? 5.2 : 4.0, 0, Math.PI * 2); ctx.fill();
        ctx.strokeStyle = primaryColor;
        ctx.lineWidth = isHov ? 2.4 : 1.9;
        ctx.stroke();

        ctx.fillStyle = primaryColor;
        ctx.beginPath(); ctx.arc(pt.x, pt.y, 1.8, 0, Math.PI * 2); ctx.fill();

        if (isHov || pt.val === peakVal) {
          ctx.font = '600 10px "IBM Plex Mono", monospace';
          ctx.fillStyle = tk.text;
          ctx.textAlign = "center";
          ctx.fillText(String(pt.val), pt.x, pt.y - 10);
        }

        ctx.font = (idx === n - 1 ? "600" : "400") + ' 10px "IBM Plex Mono", monospace';
        ctx.fillStyle = idx === n - 1 ? tk.text : tk.muted;
        ctx.textAlign = "center";
        ctx.fillText(pt.label, pt.x, yBottom + 17);
      });
    }

    /* -------------------------------------------------------------------
       2. 3D BIOMARKER & ORGAN-FUNCTION PROJECTION (Volumetric Cube Engine)
       ------------------------------------------------------------------- */
    function draw3DBiomarkerCanvas() {
      const stage = setupCanvas("canvas-3d-biomarker");
      if (!stage) return;
      const ctx = stage.ctx, w = stage.w, h = stage.h;
      const tk = themeTokens();
      const cx = w * 0.50, cy = h * 0.53;
      const scale = Math.min(w, h) * 0.305 * STATE.zoom3d;
      const cosY = Math.cos(STATE.rot3dYaw), sinY = Math.sin(STATE.rot3dYaw);
      const cosP = Math.cos(STATE.rot3dPitch), sinP = Math.sin(STATE.rot3dPitch);

      function project(nx, ny, nz) {
        const x1 = nx * cosY - nz * sinY;
        const z1 = nx * sinY + nz * cosY;
        const y2 = ny * cosP - z1 * sinP;
        const z2 = ny * sinP + z1 * cosP;
        const persp = 3.1 / (3.1 + z2 * 0.42);
        return { sx: cx + x1 * scale * persp, sy: cy - y2 * scale * persp, depth: z2, persp: persp };
      }

      function drawQuad(c0, c1, c2, c3, fillStyle, strokeStyle, lineW) {
        ctx.beginPath();
        ctx.moveTo(c0.sx, c0.sy);
        ctx.lineTo(c1.sx, c1.sy);
        ctx.lineTo(c2.sx, c2.sy);
        ctx.lineTo(c3.sx, c3.sy);
        ctx.closePath();
        if (fillStyle) { ctx.fillStyle = fillStyle; ctx.fill(); }
        if (strokeStyle) { ctx.strokeStyle = strokeStyle; ctx.lineWidth = lineW || 1; ctx.stroke(); }
      }

      /* Identify the 3 Farthest (Back-Facing) Cube Planes via Center Depth */
      const cubeFaces = [
        { id: "Y_MIN", center: [0, -1, 0], corners: [[-1,-1,-1],[1,-1,-1],[1,-1,1],[-1,-1,1]], gridA: "x", gridB: "z", fixed: -1 },
        { id: "Y_MAX", center: [0,  1, 0], corners: [[-1, 1,-1],[1, 1,-1],[1, 1,1],[-1, 1,1]], gridA: "x", gridB: "z", fixed:  1 },
        { id: "X_MIN", center: [-1, 0, 0], corners: [[-1,-1,-1],[-1,1,-1],[-1,1,1],[-1,-1,1]], gridA: "y", gridB: "z", fixed: -1 },
        { id: "X_MAX", center: [ 1, 0, 0], corners: [[ 1,-1,-1],[ 1,1,-1],[ 1,1,1],[ 1,-1,1]], gridA: "y", gridB: "z", fixed:  1 },
        { id: "Z_MIN", center: [0, 0, -1], corners: [[-1,-1,-1],[1,-1,-1],[1, 1,-1],[-1,1,-1]], gridA: "x", gridB: "y", fixed: -1 },
        { id: "Z_MAX", center: [0, 0,  1], corners: [[-1,-1, 1],[1,-1, 1],[1, 1, 1],[-1,1, 1]], gridA: "x", gridB: "y", fixed:  1 }
      ].map(function (f) {
        return Object.assign({}, f, { depth: project(f.center[0], f.center[1], f.center[2]).depth });
      });

      cubeFaces.sort(function (a, b) { return b.depth - a.depth; });
      const backFaces = cubeFaces.slice(0, 3);
      const backIds = new Set(backFaces.map(function (f) { return f.id; }));
      const backX = backIds.has("X_MIN") ? -1 : 1;
      const backZ = backIds.has("Z_MIN") ? -1 : 1;

      /* Render 3 Back-Facing Coordinate Planes + Calibrated Sub-Grids */
      const planeFill = tk.dark ? "rgba(255,255,255,0.022)" : "rgba(28,25,23,0.020)";
      backFaces.forEach(function (face) {
        const pc = face.corners.map(function (c) { return project(c[0], c[1], c[2]); });
        drawQuad(pc[0], pc[1], pc[2], pc[3], planeFill, tk.border, 1.1);

        ctx.strokeStyle = hexAlpha(tk.border, tk.dark ? 0.72 : 0.85);
        ctx.lineWidth = 0.8;
        for (let step = -0.5; step <= 0.5; step += 0.5) {
          let a0, a1, b0, b1;
          if (face.gridA === "x" && face.gridB === "z") {
            a0 = project(step, face.fixed, -1); a1 = project(step, face.fixed, 1);
            b0 = project(-1, face.fixed, step); b1 = project(1, face.fixed, step);
          } else if (face.gridA === "y" && face.gridB === "z") {
            a0 = project(face.fixed, step, -1); a1 = project(face.fixed, step, 1);
            b0 = project(face.fixed, -1, step); b1 = project(face.fixed, 1, step);
          } else {
            a0 = project(step, -1, face.fixed); a1 = project(step, 1, face.fixed);
            b0 = project(-1, step, face.fixed); b1 = project(1, step, face.fixed);
          }
          ctx.beginPath(); ctx.moveTo(a0.sx, a0.sy); ctx.lineTo(a1.sx, a1.sy); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(b0.sx, b0.sy); ctx.lineTo(b1.sx, b1.sy); ctx.stroke();
        }
      });

      /* Optional 3D Protocol Acceptance Sub-Volume (Biomarker>=45, eGFR>=45, Severity<=88) */
      if (STATE.show3dEnvelope) {
        const x0 = -0.10, x1 = 1.0, y0 = -0.18, y1 = 1.0, z0 = -1.0, z1 = 0.76;
        const envCorners = [
          project(x0, y0, z0), project(x1, y0, z0), project(x1, y0, z1), project(x0, y0, z1),
          project(x0, y1, z0), project(x1, y1, z0), project(x1, y1, z1), project(x0, y1, z1)
        ];
        const envFill = hexAlpha(PALETTE.emerald, tk.dark ? 0.08 : 0.065);
        const envStroke = hexAlpha(PALETTE.emerald, 0.55);
        drawQuad(envCorners[0], envCorners[1], envCorners[2], envCorners[3], envFill, null);
        drawQuad(envCorners[4], envCorners[5], envCorners[6], envCorners[7], envFill, null);
        ctx.save();
        ctx.strokeStyle = envStroke;
        ctx.lineWidth = 1.1;
        ctx.setLineDash([4, 3]);
        const edgePairs = [[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]];
        edgePairs.forEach(function (ep) {
          ctx.beginPath();
          ctx.moveTo(envCorners[ep[0]].sx, envCorners[ep[0]].sy);
          ctx.lineTo(envCorners[ep[1]].sx, envCorners[ep[1]].sy);
          ctx.stroke();
        });
        ctx.restore();
        ctx.font = '600 9px "IBM Plex Mono", monospace';
        ctx.fillStyle = PALETTE.emerald;
        ctx.textAlign = "left";
        ctx.fillText("PROTOCOL ENVELOPE (BioZ>=45, eGFR>=45)", envCorners[4].sx + 4, envCorners[4].sy - 5);
      }

      /* Quantitative Axis Labels & Numerical Ticks */
      ctx.font = '500 9.5px "IBM Plex Mono", monospace';
      ctx.fillStyle = tk.muted;
      [[-1, "0"], [0, "50"], [1, "100"]].forEach(function (tkItem) {
        const px = project(tkItem[0], -1.06, -backZ);
        ctx.textAlign = "center";
        ctx.fillText(tkItem[1], px.sx, px.sy + 8);
      });
      [[-1, "0"], [0, "55"], [1, "110"]].forEach(function (tkItem) {
        const py = project(backX, tkItem[0], -backZ);
        ctx.textAlign = backX < 0 ? "right" : "left";
        ctx.fillText(tkItem[1], py.sx + (backX < 0 ? -6 : 6), py.sy + 3);
      });

      const axLabels = [
        [project(0.0, -1.20, -backZ), "BIOMARKER Z-SCORE (0-100)", PALETTE.indigo],
        [project(backX * 1.16, 0.05, -backZ), "eGFR RENAL (mL/min)", PALETTE.teal],
        [project(-backX * 1.05, -1.16, 0.0), "SEVERITY INDEX", PALETTE.amber]
      ];
      axLabels.forEach(function (al) {
        ctx.font = '600 10px "IBM Plex Mono", monospace';
        ctx.fillStyle = al[2];
        ctx.textAlign = "center";
        ctx.fillText(al[1], al[0].sx, al[0].sy);
      });

      /* Project All Filtered Candidate Patients */
      const filteredPats = getFilteredPatients();
      const pts = filteredPats.map(function (p) {
        const L = p.labs || {};
        const bioVal = Number(L.biomarker_composite_z != null ? L.biomarker_composite_z : 40);
        const egfrVal = Number(L.egfr_ml_min != null ? L.egfr_ml_min : 48);
        const sevVal = Number(p.severity_index != null ? p.severity_index : 50);
        const bx = Math.max(-0.96, Math.min(0.96, (bioVal / 100) * 2 - 1));
        const ey = Math.max(-0.96, Math.min(0.96, (Math.min(110, egfrVal) / 110) * 2 - 1));
        const sz = Math.max(-0.96, Math.min(0.96, (sevVal / 100) * 2 - 1));
        const proj = project(bx, ey, sz);
        const floorProj = project(bx, -1, sz);
        return {
          pat: p, bx: bx, ey: ey, sz: sz,
          bioVal: bioVal, egfrVal: L.egfr_ml_min, sevVal: sevVal,
          sx: proj.sx, sy: proj.sy, fx: floorProj.sx, fy: floorProj.sy,
          depth: proj.depth, persp: proj.persp
        };
      });

      /* Pass 1: Floor Elliptical Drop Shadows & Subtle Elevation Stems */
      pts.forEach(function (item) {
        const sr = Math.max(2.2, 4.2 * item.persp);
        ctx.fillStyle = tk.dark ? "rgba(0,0,0,0.42)" : "rgba(28,25,23,0.13)";
        ctx.beginPath();
        ctx.ellipse(item.fx, item.fy, sr * 1.15, sr * 0.55, 0, 0, Math.PI * 2);
        ctx.fill();

        if (item.pat.primary_classification === "ELIGIBLE") {
          ctx.strokeStyle = hexAlpha(PALETTE.emerald, tk.dark ? 0.18 : 0.15);
          ctx.lineWidth = 0.8;
          ctx.beginPath(); ctx.moveTo(item.fx, item.fy); ctx.lineTo(item.sx, item.sy); ctx.stroke();
        }
      });

      /* Pass 2: Depth-Sorted 3D Shaded Spheres */
      pts.sort(function (a, b) { return b.depth - a.depth; });
      STATE.projected3dPoints = pts;

      let focusItem = null;
      pts.forEach(function (item) {
        const p = item.pat;
        const isSelected = p.patient_id === STATE.selectedPatientId;
        const isHovered = p.patient_id === STATE.hovered3dId;
        if (isHovered) focusItem = item;
        else if (isSelected && !focusItem) focusItem = item;

        const baseHex = p.primary_classification === "ELIGIBLE"
          ? PALETTE.emerald
          : (p.primary_classification === "POTENTIAL_NEEDS_LABS" ? PALETTE.amber : PALETTE.rose);

        const r = (isSelected || isHovered ? 7.2 : (p.primary_classification === "ELIGIBLE" ? 4.8 : 4.0)) * item.persp;
        const specHex = lerpColor(baseHex, "#FFFFFF", 0.62);
        const rimHex = lerpColor(baseHex, "#0D0E11", 0.42);
        const sphereGrad = ctx.createRadialGradient(
          item.sx - r * 0.32, item.sy - r * 0.32, r * 0.12,
          item.sx, item.sy, r
        );
        sphereGrad.addColorStop(0, specHex);
        sphereGrad.addColorStop(0.58, baseHex);
        sphereGrad.addColorStop(1, rimHex);

        ctx.fillStyle = sphereGrad;
        ctx.beginPath();
        ctx.arc(item.sx, item.sy, r, 0, Math.PI * 2);
        ctx.fill();

        ctx.strokeStyle = (isSelected || isHovered) ? tk.text : hexAlpha("#0D0E11", 0.28);
        ctx.lineWidth = (isSelected || isHovered) ? 1.8 : 0.7;
        ctx.stroke();
      });

      /* Pass 3: Front Wireframe Silhouette Edges for Full 3D Volumetric Framing */
      const frontFaces = cubeFaces.slice(3);
      ctx.strokeStyle = hexAlpha(tk.border, 0.55);
      ctx.lineWidth = 0.9;
      frontFaces.forEach(function (face) {
        const pc = face.corners.map(function (c) { return project(c[0], c[1], c[2]); });
        drawQuad(pc[0], pc[1], pc[2], pc[3], null, hexAlpha(tk.border, 0.45), 0.8);
      });

      /* Pass 4: Tri-Planar Laser Crosshairs & Callout Badge on Active Patient */
      if (focusItem) {
        const fp = focusItem.pat;
        const focusCol = fp.primary_classification === "ELIGIBLE"
          ? PALETTE.emerald
          : (fp.primary_classification === "POTENTIAL_NEEDS_LABS" ? PALETTE.amber : PALETTE.rose);
        const wallX = project(backX, focusItem.ey, focusItem.sz);
        const wallZ = project(focusItem.bx, focusItem.ey, backZ);

        ctx.save();
        ctx.strokeStyle = focusCol;
        ctx.lineWidth = 1.2;
        ctx.setLineDash([3, 3]);
        [
          [focusItem.fx, focusItem.fy],
          [wallX.sx, wallX.sy],
          [wallZ.sx, wallZ.sy]
        ].forEach(function (target) {
          ctx.beginPath();
          ctx.moveTo(focusItem.sx, focusItem.sy);
          ctx.lineTo(target[0], target[1]);
          ctx.stroke();

          ctx.setLineDash([]);
          ctx.beginPath();
          ctx.arc(target[0], target[1], 3, 0, Math.PI * 2);
          ctx.stroke();
          ctx.setLineDash([3, 3]);
        });
        ctx.restore();

        /* Target Reticle Halo */
        ctx.strokeStyle = focusCol;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(focusItem.sx, focusItem.sy, 11.5, 0, Math.PI * 2);
        ctx.stroke();

        const tagText = fp.patient_id + " | " + fp.primary_match_protocol + " | " + fp.primary_match_score + "pt";
        drawPillBadge(
          ctx,
          tagText,
          Math.max(85, Math.min(w - 85, focusItem.sx)),
          Math.max(28, focusItem.sy - 20),
          tk.surface,
          focusCol,
          tk.text
        );
      }

      /* Pass 5: Top-Left Scientific Telemetry Overlay */
      const eligN = filteredPats.filter(function (p) { return p.primary_classification === "ELIGIBLE"; }).length;
      const labsN = filteredPats.filter(function (p) { return p.primary_classification === "POTENTIAL_NEEDS_LABS"; }).length;
      const yawDeg = Math.round(((STATE.rot3dYaw * 180) / Math.PI) % 360);
      const pitchDeg = Math.round((STATE.rot3dPitch * 180) / Math.PI);
      ctx.font = '500 10px "IBM Plex Mono", monospace';
      ctx.fillStyle = tk.muted;
      ctx.textAlign = "left";
      ctx.fillText(
        "COHORT: " + filteredPats.length + " (ELIG " + eligN + " / LABS " + labsN + ")  |  CAM " + yawDeg + "deg / " + pitchDeg + "deg  |  " + STATE.zoom3d.toFixed(2) + "x",
        10,
        15
      );
    }

    /* -------------------------------------------------------------------
       3. INTERACTIVE RELATIONSHIP NODE MAP (Bezier Clinical Network Graph)
       ------------------------------------------------------------------- */
    function drawPolygon(ctx, x, y, radius, sides, rotOffset) {
      ctx.beginPath();
      for (let i = 0; i < sides; i++) {
        const a = rotOffset + (i / sides) * Math.PI * 2;
        const px = x + Math.cos(a) * radius;
        const py = y + Math.sin(a) * radius;
        if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      }
      ctx.closePath();
    }

    function drawNodeMapCanvas() {
      const stage = setupCanvas("canvas-nodemap");
      if (!stage) return;
      const ctx = stage.ctx, w = stage.w, h = stage.h;
      const tk = themeTokens();
      const cx = w * 0.50 + STATE.nodePanX;
      const cy = h * 0.50 + STATE.nodePanY;
      const baseScale = Math.min(w, h) / 420;
      const z = STATE.nodeZoom * baseScale;

      const rStudy = 64 * z;
      const rSite = 138 * z;
      const rSig = 186 * z;
      const rPat = 204 * z;

      /* Subtle Concentric Orbital Telemetry Guides */
      ctx.save();
      ctx.strokeStyle = hexAlpha(tk.border, tk.dark ? 0.70 : 0.85);
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 4]);
      [rStudy, rSite, rPat].forEach(function (rad) {
        ctx.beginPath();
        ctx.arc(cx, cy, rad, 0, Math.PI * 2);
        ctx.stroke();
      });
      ctx.restore();

      const nodes = [];
      const edges = [];
      const adj = {};
      function linkAdj(a, b) {
        if (!adj[a]) adj[a] = new Set();
        if (!adj[b]) adj[b] = new Set();
        adj[a].add(b);
        adj[b].add(a);
      }

      const studyPos = {};
      const sitePos = {};
      const sitesByStudy = {};
      WORKSPACE.sites.forEach(function (s) {
        if (!sitesByStudy[s.study_id]) sitesByStudy[s.study_id] = [];
        sitesByStudy[s.study_id].push(s);
      });

      /* Tier 1: Study Protocol Core Hubs (Quadrant Layout with Enrollment Progress Arcs) */
      WORKSPACE.studies.forEach(function (st, sIdx) {
        const ang = (sIdx / WORKSPACE.studies.length) * Math.PI * 2 - Math.PI * 0.75;
        const x = cx + Math.cos(ang) * rStudy;
        const y = cy + Math.sin(ang) * rStudy;
        const stSites = sitesByStudy[st.study_id] || [];
        const enrSum = stSites.reduce(function (acc, s) { return acc + s.enrolled_count; }, 0);
        const tgtSum = Math.max(1, st.target_enrollment);
        const progress = Math.min(1, enrSum / tgtSum);

        studyPos[st.study_id] = { x: x, y: y, ang: ang, code: st.protocol_code };
        nodes.push({
          id: st.study_id,
          label: st.protocol_code,
          sublabel: Math.round(progress * 100) + "% enr",
          kind: "STUDY",
          x: x,
          y: y,
          r: 13.5,
          progress: progress,
          color: PALETTE.indigo,
          detail: st.protocol_code + " (" + st.study_id + ")\\nArea: " + st.therapeutic_area + " | " + st.phase +
            "\\nEnrolled: " + enrSum + " / " + tgtSum + " (" + Math.round(progress * 100) + "%)"
        });

        /* Tier 2: Sector-Aligned Clinical Sites around Parent Study */
        stSites.forEach(function (site, kIdx) {
          const span = 0.92;
          const offset = stSites.length > 1 ? ((kIdx / (stSites.length - 1)) - 0.5) * span : 0;
          const sAng = ang + offset;
          const sx = cx + Math.cos(sAng) * rSite;
          const sy = cy + Math.sin(sAng) * rSite;
          const sCol = site.priority_tier === "Priority Action Required"
            ? PALETTE.rose
            : (site.priority_tier === "Important Signals Next" ? PALETTE.amber : PALETTE.emerald);
          const blocked = site.site_status !== "ACTIVE" || site.unresolved_excursion === 1;

          sitePos[site.site_id] = { x: sx, y: sy, ang: sAng, study_id: st.study_id, color: sCol };
          nodes.push({
            id: site.site_id,
            label: site.site_id,
            sublabel: "R:" + site.risk_score,
            kind: "SITE",
            x: sx,
            y: sy,
            r: 9.5,
            risk_score: site.risk_score,
            blocked: blocked,
            color: sCol,
            detail: site.site_id + " - " + site.site_name + " (" + site.country_code + ")" +
              "\\nTier: " + site.priority_tier + " (Risk " + site.risk_score + "/100)" +
              "\\nSignals: " + site.signal_counts.CRITICAL + " CRIT / " + site.signal_counts.TOTAL + " Total" +
              "\\nStatus: " + site.site_status + (site.unresolved_excursion ? " [COLD-CHAIN EXCURSION]" : "")
          });

          edges.push({
            from: st.study_id,
            to: site.site_id,
            x1: x, y1: y, x2: sx, y2: sy,
            color: sCol,
            width: site.priority_tier === "Priority Action Required" ? 1.9 : 1.3,
            dashed: false,
            curveBend: offset * 0.25
          });
          linkAdj(st.study_id, site.site_id);
        });
      });

      /* Tier 3A: Priority Risk Signal Alert Satellites (Diamond Glyphs) */
      if (STATE.nodeLayerMode === "all" || STATE.nodeLayerMode === "signals") {
        const perSiteCap = STATE.nodeLayerMode === "signals" ? 3 : 2;
        const countBySite = {};
        WORKSPACE.signals.forEach(function (sig) {
          if (sig.disposition === "DISMISS") return;
          if (STATE.nodeLayerMode === "all" && sig.severity !== "CRITICAL" && sig.severity !== "HIGH") return;
          const sp = sitePos[sig.site_id];
          if (!sp) return;
          const c = countBySite[sig.site_id] || 0;
          if (c >= perSiteCap) return;
          countBySite[sig.site_id] = c + 1;

          const sigAng = sp.ang - 0.14 + c * 0.14;
          const rad = (STATE.nodeLayerMode === "signals" ? rPat - 8 * z : rSig) + (c % 2) * 10 * z;
          const gx = cx + Math.cos(sigAng) * rad;
          const gy = cy + Math.sin(sigAng) * rad;
          const sigCol = sig.severity === "CRITICAL" ? PALETTE.rose : PALETTE.amber;

          nodes.push({
            id: sig.signal_id,
            label: sig.signal_id,
            kind: "SIGNAL",
            x: gx,
            y: gy,
            r: 5.6,
            color: sigCol,
            detail: sig.signal_id + " [" + sig.severity + "] " + sig.family +
              "\\n" + sig.title + "\\nSite: " + sig.site_id + " | Click to Audit Evidence"
          });
          edges.push({
            from: sig.site_id,
            to: sig.signal_id,
            x1: sp.x, y1: sp.y, x2: gx, y2: gy,
            color: sigCol,
            width: 1.15,
            dashed: true,
            curveBend: -0.12
          });
          linkAdj(sig.site_id, sig.signal_id);
          linkAdj(sp.study_id, sig.signal_id);
        });
      }

      /* Tier 3B: Eligible & Lab-Refresh Candidate Patient Referral Nodes */
      if (STATE.nodeLayerMode === "all" || STATE.nodeLayerMode === "referrals") {
        const capTotal = STATE.nodeLayerMode === "referrals" ? 48 : 30;
        const candidates = getFilteredPatients().filter(function (p) {
          return (p.primary_classification === "ELIGIBLE" ||
            (STATE.nodeLayerMode === "referrals" && p.primary_classification === "POTENTIAL_NEEDS_LABS")) &&
            p.recommended_site_id;
        });

        /* Ensure currently selected patient is included if eligible/routed */
        const chosen = candidates.slice(0, capTotal);
        const selP = candidates.find(function (p) { return p.patient_id === STATE.selectedPatientId; });
        if (selP && !chosen.some(function (x) { return x.patient_id === selP.patient_id; })) {
          chosen[chosen.length - 1] = selP;
        }

        const patCountBySite = {};
        chosen.forEach(function (p) {
          const sp = sitePos[p.recommended_site_id];
          if (!sp) return;
          const idxAtSite = patCountBySite[p.recommended_site_id] || 0;
          patCountBySite[p.recommended_site_id] = idxAtSite + 1;

          const spread = ((idxAtSite % 5) - 2) * 0.085;
          const ringStep = Math.floor(idxAtSite / 5) * 13 * z;
          const pAng = sp.ang + 0.05 + spread;
          const px = cx + Math.cos(pAng) * (rPat + ringStep);
          const py = cy + Math.sin(pAng) * (rPat + ringStep);
          const pCol = p.primary_classification === "ELIGIBLE" ? PALETTE.teal : PALETTE.amber;

          nodes.push({
            id: p.patient_id,
            label: p.patient_id,
            kind: "PATIENT",
            x: px,
            y: py,
            r: p.patient_id === STATE.selectedPatientId ? 6.5 : 4.6,
            color: pCol,
            detail: p.patient_id + " (" + p.primary_classification + " - " + p.primary_match_score + "pt)" +
              "\\nICD-10: " + p.primary_icd10 + " | Age " + p.age_years + "/" + p.sex +
              "\\nRouted Site: " + p.recommended_site_id + " (" + p.primary_match_protocol + ")"
          });
          edges.push({
            from: p.recommended_site_id,
            to: p.patient_id,
            x1: sp.x, y1: sp.y, x2: px, y2: py,
            color: pCol,
            width: 1.15,
            dashed: false,
            arrow: true,
            curveBend: 0.14
          });
          linkAdj(p.recommended_site_id, p.patient_id);
          linkAdj(sp.study_id, p.patient_id);
        });
      }

      /* Determine Active Spotlight Set (Hovered Node + Connected Neighborhood) */
      const spotlightId = STATE.hoveredNodeId;
      const hasSpotlight = Boolean(spotlightId && adj[spotlightId]);
      function isNodeLit(id) {
        if (!hasSpotlight) return true;
        return id === spotlightId || adj[spotlightId].has(id);
      }

      /* Render Curved Quadratic Bezier Edges & Mid-Curve Flow Chevrons */
      edges.forEach(function (e) {
        const edgeLit = !hasSpotlight || e.from === spotlightId || e.to === spotlightId ||
          (adj[spotlightId].has(e.from) && adj[spotlightId].has(e.to));
        const mx = (e.x1 + e.x2) * 0.5;
        const my = (e.y1 + e.y2) * 0.5;
        const dx = e.x2 - e.x1, dy = e.y2 - e.y1;
        const cpx = mx - dy * (e.curveBend || 0.1);
        const cpy = my + dx * (e.curveBend || 0.1);

        ctx.save();
        ctx.strokeStyle = hexAlpha(e.color, edgeLit ? (hasSpotlight ? 0.85 : 0.44) : 0.09);
        ctx.lineWidth = edgeLit && hasSpotlight ? e.width + 0.8 : e.width;
        if (e.dashed) ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(e.x1, e.y1);
        ctx.quadraticCurveTo(cpx, cpy, e.x2, e.y2);
        ctx.stroke();

        /* Directional Chevron at t = 0.58 on Referral Edges */
        if (e.arrow && edgeLit) {
          const t = 0.58;
          const qx = (1 - t) * (1 - t) * e.x1 + 2 * (1 - t) * t * cpx + t * t * e.x2;
          const qy = (1 - t) * (1 - t) * e.y1 + 2 * (1 - t) * t * cpy + t * t * e.y2;
          const tx = 2 * (1 - t) * (cpx - e.x1) + 2 * t * (e.x2 - cpx);
          const ty = 2 * (1 - t) * (cpy - e.y1) + 2 * t * (e.y2 - cpy);
          const tang = Math.atan2(ty, tx);
          ctx.setLineDash([]);
          ctx.fillStyle = hexAlpha(e.color, hasSpotlight ? 0.95 : 0.70);
          ctx.beginPath();
          ctx.moveTo(qx + Math.cos(tang) * 4, qy + Math.sin(tang) * 4);
          ctx.lineTo(qx + Math.cos(tang + 2.5) * 3.6, qy + Math.sin(tang + 2.5) * 3.6);
          ctx.lineTo(qx + Math.cos(tang - 2.5) * 3.6, qy + Math.sin(tang - 2.5) * 3.6);
          ctx.closePath();
          ctx.fill();
        }
        ctx.restore();
      });

      /* Render Procedural Multi-Layer Node Glyphs */
      nodes.forEach(function (n) {
        const lit = isNodeLit(n.id);
        const isFocus = n.id === spotlightId || n.id === STATE.selectedPatientId;
        ctx.save();
        ctx.globalAlpha = lit ? 1.0 : 0.22;

        if (n.kind === "STUDY") {
          /* Outer Track + Enrollment Progress Ring + Core Hub */
          ctx.fillStyle = tk.surface;
          ctx.beginPath(); ctx.arc(n.x, n.y, n.r + 3.5, 0, Math.PI * 2); ctx.fill();
          ctx.strokeStyle = tk.border;
          ctx.lineWidth = 2.2;
          ctx.stroke();

          ctx.strokeStyle = PALETTE.indigo;
          ctx.lineWidth = 2.6;
          ctx.beginPath();
          ctx.arc(n.x, n.y, n.r + 3.5, -Math.PI / 2, -Math.PI / 2 + n.progress * Math.PI * 2);
          ctx.stroke();

          ctx.fillStyle = PALETTE.indigo;
          ctx.beginPath(); ctx.arc(n.x, n.y, n.r - 1.5, 0, Math.PI * 2); ctx.fill();

          drawPillBadge(ctx, n.label + " " + n.sublabel, n.x, n.y - n.r - 13, tk.surface, PALETTE.indigo, tk.text);
        } else if (n.kind === "SITE") {
          if (n.blocked) {
            /* Hexagonal Containment Node for Paused / Cold-Chain Blocked Sites */
            drawPolygon(ctx, n.x, n.y, n.r + 2.5, 6, Math.PI / 6);
            ctx.fillStyle = tk.surface; ctx.fill();
            ctx.strokeStyle = n.color; ctx.lineWidth = 2.2; ctx.stroke();
            drawPolygon(ctx, n.x, n.y, n.r - 2.5, 6, Math.PI / 6);
            ctx.fillStyle = n.color; ctx.fill();
          } else {
            /* Double-Ring Risk Gauge Node */
            ctx.fillStyle = tk.surface;
            ctx.beginPath(); ctx.arc(n.x, n.y, n.r + 2.2, 0, Math.PI * 2); ctx.fill();
            ctx.strokeStyle = tk.border;
            ctx.lineWidth = 1.8;
            ctx.stroke();

            const riskFrac = Math.max(0.08, (n.risk_score || 0) / 100);
            ctx.strokeStyle = n.color;
            ctx.lineWidth = 2.2;
            ctx.beginPath();
            ctx.arc(n.x, n.y, n.r + 2.2, -Math.PI * 0.75, -Math.PI * 0.75 + riskFrac * Math.PI * 1.5);
            ctx.stroke();

            ctx.fillStyle = n.color;
            ctx.beginPath(); ctx.arc(n.x, n.y, n.r - 2.6, 0, Math.PI * 2); ctx.fill();
          }
          if (z >= 0.72 || isFocus || (hasSpotlight && lit)) {
            ctx.font = '600 9px "IBM Plex Mono", monospace';
            ctx.fillStyle = tk.text;
            ctx.textAlign = "center";
            ctx.fillText(n.label, n.x, n.y - n.r - 6);
          }
        } else if (n.kind === "SIGNAL") {
          /* Rotated Diamond Alert Satellite */
          drawPolygon(ctx, n.x, n.y, isFocus ? n.r + 2.2 : n.r, 4, 0);
          ctx.fillStyle = n.color;
          ctx.fill();
          ctx.strokeStyle = tk.surface;
          ctx.lineWidth = 1.2;
          ctx.stroke();
          if (isFocus) {
            drawPillBadge(ctx, n.label, n.x, n.y - 14, tk.surface, n.color, tk.text);
          }
        } else {
          /* Candidate Referral Orbital Node */
          if (isFocus) {
            ctx.fillStyle = hexAlpha(n.color, 0.24);
            ctx.beginPath(); ctx.arc(n.x, n.y, n.r + 5, 0, Math.PI * 2); ctx.fill();
          }
          ctx.fillStyle = n.color;
          ctx.beginPath(); ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2); ctx.fill();
          ctx.strokeStyle = isFocus ? tk.text : tk.surface;
          ctx.lineWidth = isFocus ? 1.8 : 1.0;
          ctx.stroke();
          if (isFocus) {
            drawPillBadge(ctx, n.label, n.x, n.y - 15, tk.surface, n.color, tk.text);
          }
        }
        ctx.restore();
      });

      /* Bottom-Left Network Summary HUD */
      const sigCnt = nodes.filter(function (n) { return n.kind === "SIGNAL"; }).length;
      const patCnt = nodes.filter(function (n) { return n.kind === "PATIENT"; }).length;
      ctx.font = '500 10px "IBM Plex Mono", monospace';
      ctx.fillStyle = tk.muted;
      ctx.textAlign = "left";
      ctx.fillText(
        "NODES: 4 PROTOCOLS | 16 SITES | " + sigCnt + " SIGNALS | " + patCnt + " REFERRALS   (SCROLL ZOOM / CLICK NODE)",
        10,
        h - 10
      );

      STATE.renderedNodes = nodes;
      STATE.renderedEdges = edges;
    }

    function drawAnalyticsCanvas(res) {
      const stage = setupCanvas("canvas-analytics-chart");
      if (!stage || !res || !res.rows || !res.rows.length) return;
      const ctx = stage.ctx, w = stage.w, h = stage.h;
      const tk = themeTokens();
      const spec = res.chart_spec || {};
      const xKey = spec.x_key || res.columns[0];
      const yKey = spec.y_key || res.columns[1] || res.columns[0];
      const rows = res.rows.slice(0, 14);

      const vals = rows.map(function (r) { return Number(r[yKey]) || 0; });
      const minV = Math.min(0, Math.min.apply(null, vals));
      const maxV = Math.max(1, Math.max.apply(null, vals) * 1.15);
      const span = maxV - minV;

      const padL = 46, padR = 16, padT = 18, padB = 42;
      const pw = w - padL - padR, ph = h - padT - padB;
      const zeroY = padT + ph - ((0 - minV) / span) * ph;

      ctx.strokeStyle = tk.border;
      ctx.font = '10px "IBM Plex Mono", monospace';
      ctx.fillStyle = tk.muted;
      for (let i = 0; i <= 4; i++) {
        const v = minV + (span * i) / 4;
        const y = padT + ph - ((v - minV) / span) * ph;
        ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(padL + pw, y); ctx.stroke();
        ctx.textAlign = "right";
        ctx.fillText(v.toFixed(0), padL - 6, y + 3);
      }

      if (spec.target_line != null && spec.target_line <= maxV) {
        const ty = padT + ph - ((spec.target_line - minV) / span) * ph;
        ctx.strokeStyle = PALETTE.emerald;
        ctx.setLineDash([4, 4]);
        ctx.beginPath(); ctx.moveTo(padL, ty); ctx.lineTo(padL + pw, ty); ctx.stroke();
        ctx.setLineDash([]);
      }

      const slotW = pw / rows.length;
      rows.forEach(function (r, idx) {
        const v = Number(r[yKey]) || 0;
        const barW = Math.min(34, slotW * 0.58);
        const x = padL + idx * slotW + (slotW - barW) / 2;
        const yVal = padT + ph - ((v - minV) / span) * ph;
        const top = Math.min(zeroY, yVal);
        const barH = Math.max(2, Math.abs(zeroY - yVal));

        ctx.fillStyle = spec.color || PALETTE.teal;
        ctx.fillRect(x, top, barW, barH);

        ctx.fillStyle = tk.text;
        ctx.textAlign = "center";
        const lbl = String(r[xKey] || "").slice(0, 10);
        ctx.fillText(lbl, padL + idx * slotW + slotW / 2, padT + ph + 18);
      });
    }

    function redrawAllCanvases() {
      if (STATE.activeView === "portfolio") {
        drawHeatmapCanvas();
        drawSaeTrendCanvas();
      } else if (STATE.activeView === "signal") {
        const sig = WORKSPACE.signals.find(function (s) { return s.signal_id === STATE.selectedSignalId; });
        drawSiteTrendCanvas(sig);
      } else if (STATE.activeView === "selection") {
        draw3DBiomarkerCanvas();
        drawNodeMapCanvas();
      } else if (STATE.activeView === "analytics") {
        drawAnalyticsCanvas(STATE.activeAnalyticsResult);
      }
    }

    function renderActiveView() {
      if (STATE.activeView === "portfolio") renderPortfolio();
      else if (STATE.activeView === "site") renderSingleSite();
      else if (STATE.activeView === "signal") renderSignalDetail();
      else if (STATE.activeView === "selection") renderTrialSelection();
      else if (STATE.activeView === "analytics") renderAnalyticsView();
    }

    /* --- INTERACTIVE CANVAS MOUSE HANDLERS --- */
    const tooltip = document.getElementById("canvas-tooltip");
    function showTip(ev, text) {
      tooltip.style.display = "block";
      const leftPos = Math.min(window.innerWidth - 360, ev.clientX + 14);
      const topPos = Math.min(window.innerHeight - 110, ev.clientY + 14);
      tooltip.style.left = Math.max(8, leftPos) + "px";
      tooltip.style.top = Math.max(8, topPos) + "px";
      tooltip.textContent = text;
    }
    function hideTip() { tooltip.style.display = "none"; }

    /* Heatmap Hover & Click */
    const hmCanvas = document.getElementById("canvas-heatmap");
    hmCanvas.addEventListener("mousemove", function (ev) {
      const rect = hmCanvas.getBoundingClientRect();
      const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
      const hit = heatmapCells.find(function (c) {
        return mx >= c.x && mx <= c.x + c.w && my >= c.y && my <= c.y + c.h;
      });
      if (hit) {
        hmCanvas.style.cursor = "pointer";
        showTip(ev, hit.site_id + " (" + hit.site_name + ")\\nFamily: " + hit.family + " | Signals: " + hit.count + " | Weight: " + hit.weight);
      } else {
        hmCanvas.style.cursor = "default";
        hideTip();
      }
    });
    hmCanvas.addEventListener("mouseleave", hideTip);
    hmCanvas.addEventListener("click", function (ev) {
      const rect = hmCanvas.getBoundingClientRect();
      const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
      const hit = heatmapCells.find(function (c) {
        return mx >= c.x && mx <= c.x + c.w && my >= c.y && my <= c.y + c.h;
      });
      if (hit) {
        STATE.selectedSiteId = hit.site_id;
        switchView("site");
      }
    });

    /* 8-Week Site Signal Trend Scrubber */
    const cTrend = document.getElementById("canvas-site-trend");
    cTrend.addEventListener("mousemove", function (ev) {
      const rect = cTrend.getBoundingClientRect();
      const mx = ev.clientX - rect.left;
      let best = null, bestDx = 999;
      STATE.trendHitPoints.forEach(function (pt) {
        const dx = Math.abs(pt.x - mx);
        if (dx < bestDx) { bestDx = dx; best = pt; }
      });
      if (best && bestDx <= 36) {
        cTrend.style.cursor = "crosshair";
        if (STATE.hoveredTrendIdx !== best.idx) {
          STATE.hoveredTrendIdx = best.idx;
          const sig = WORKSPACE.signals.find(function (s) { return s.signal_id === STATE.selectedSignalId; });
          drawSiteTrendCanvas(sig);
        }
        const diff = (best.val - best.peer).toFixed(1);
        showTip(
          ev,
          "Week Window: " + best.label +
          "\\nSite Active Signals: " + best.val +
          "\\nPortfolio Peer Avg: " + best.peer +
          "\\nVariance vs Peer: " + (Number(diff) >= 0 ? "+" : "") + diff
        );
      } else {
        hideTip();
      }
    });
    cTrend.addEventListener("mouseleave", function () {
      STATE.hoveredTrendIdx = -1;
      hideTip();
      const sig = WORKSPACE.signals.find(function (s) { return s.signal_id === STATE.selectedSignalId; });
      drawSiteTrendCanvas(sig);
    });

    /* 3D Biomarker Cube Orbit, Zoom, Presets, Auto-Orbit & Hover */
    const c3d = document.getElementById("canvas-3d-biomarker");
    let drag3d = null;
    let orbitAnimId = null;

    function syncCamPresetButtons(activeKey) {
      document.querySelectorAll("[data-cam3d]").forEach(function (b) {
        b.classList.toggle("active", b.getAttribute("data-cam3d") === activeKey);
      });
    }

    function stepAutoOrbit() {
      if (!STATE.autoOrbit3d) { orbitAnimId = null; return; }
      if (STATE.activeView === "selection" && !drag3d) {
        STATE.rot3dYaw += 0.0045;
        draw3DBiomarkerCanvas();
      }
      orbitAnimId = window.requestAnimationFrame(stepAutoOrbit);
    }

    c3d.addEventListener("mousedown", function (ev) {
      drag3d = { x: ev.clientX, y: ev.clientY, moved: false };
      syncCamPresetButtons("");
    });
    window.addEventListener("mousemove", function (ev) {
      if (!drag3d) return;
      const dx = ev.clientX - drag3d.x, dy = ev.clientY - drag3d.y;
      if (Math.abs(dx) > 2 || Math.abs(dy) > 2) drag3d.moved = true;
      STATE.rot3dYaw += dx * 0.01;
      STATE.rot3dPitch = Math.max(-1.25, Math.min(1.25, STATE.rot3dPitch + dy * 0.01));
      drag3d.x = ev.clientX; drag3d.y = ev.clientY;
      draw3DBiomarkerCanvas();
    });
    window.addEventListener("mouseup", function () { drag3d = null; });

    c3d.addEventListener("wheel", function (ev) {
      ev.preventDefault();
      STATE.zoom3d = Math.max(0.65, Math.min(2.15, STATE.zoom3d * (ev.deltaY < 0 ? 1.08 : 0.92)));
      draw3DBiomarkerCanvas();
    }, { passive: false });

    c3d.addEventListener("mousemove", function (ev) {
      if (drag3d) return;
      const rect = c3d.getBoundingClientRect();
      const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
      let hit = null, bestD = 11;
      STATE.projected3dPoints.forEach(function (pt) {
        const d = Math.hypot(pt.sx - mx, pt.sy - my);
        if (d <= bestD) { bestD = d; hit = pt; }
      });
      const nextHov = hit ? hit.pat.patient_id : null;
      if (STATE.hovered3dId !== nextHov) {
        STATE.hovered3dId = nextHov;
        draw3DBiomarkerCanvas();
      }
      if (hit) {
        c3d.style.cursor = "pointer";
        const p = hit.pat;
        showTip(
          ev,
          p.patient_id + " [" + p.primary_classification + " | " + p.primary_match_score + "pt]" +
          "\\nProtocol: " + p.primary_match_protocol + " | ICD-10: " + p.primary_icd10 +
          "\\nBiomarker Z: " + hit.bioVal + " | eGFR: " + (hit.egfrVal != null ? hit.egfrVal + " mL/min" : "Missing") +
          "\\nSeverity Index: " + hit.sevVal + " | Site: " + (p.recommended_site_id || "None")
        );
      } else {
        c3d.style.cursor = "grab";
        hideTip();
      }
    });
    c3d.addEventListener("mouseleave", function () {
      if (STATE.hovered3dId !== null) {
        STATE.hovered3dId = null;
        draw3DBiomarkerCanvas();
      }
      hideTip();
    });
    c3d.addEventListener("click", function (ev) {
      const rect = c3d.getBoundingClientRect();
      const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
      let hit = null, bestD = 12;
      STATE.projected3dPoints.forEach(function (pt) {
        const d = Math.hypot(pt.sx - mx, pt.sy - my);
        if (d < bestD) { bestD = d; hit = pt; }
      });
      if (hit) {
        STATE.selectedPatientId = hit.pat.patient_id;
        renderTrialSelection();
      }
    });

    document.querySelectorAll("[data-cam3d]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const preset = btn.getAttribute("data-cam3d");
        syncCamPresetButtons(preset);
        if (preset === "iso") { STATE.rot3dYaw = 0.64; STATE.rot3dPitch = 0.38; }
        else if (preset === "xy") { STATE.rot3dYaw = 0.0; STATE.rot3dPitch = 0.04; }
        else if (preset === "top") { STATE.rot3dYaw = 0.35; STATE.rot3dPitch = 1.18; }
        draw3DBiomarkerCanvas();
      });
    });
    document.getElementById("btn-3d-orbit").addEventListener("click", function (ev) {
      STATE.autoOrbit3d = !STATE.autoOrbit3d;
      ev.currentTarget.classList.toggle("active", STATE.autoOrbit3d);
      if (STATE.autoOrbit3d && !orbitAnimId) stepAutoOrbit();
    });
    document.getElementById("btn-3d-envelope").addEventListener("click", function (ev) {
      STATE.show3dEnvelope = !STATE.show3dEnvelope;
      ev.currentTarget.classList.toggle("active", STATE.show3dEnvelope);
      draw3DBiomarkerCanvas();
    });
    document.getElementById("btn-3d-reset").addEventListener("click", function () {
      STATE.rot3dYaw = 0.64; STATE.rot3dPitch = 0.38; STATE.zoom3d = 1.0;
      syncCamPresetButtons("iso");
      draw3DBiomarkerCanvas();
    });

    /* Node Map Pan, Zoom, Spotlight Hover, Layer Modes & Drill-Down */
    const cNode = document.getElementById("canvas-nodemap");
    let dragNode = null;
    cNode.addEventListener("mousedown", function (ev) {
      dragNode = { x: ev.clientX, y: ev.clientY, moved: false };
    });
    window.addEventListener("mousemove", function (ev) {
      if (!dragNode) return;
      const dx = ev.clientX - dragNode.x, dy = ev.clientY - dragNode.y;
      if (Math.abs(dx) > 2 || Math.abs(dy) > 2) dragNode.moved = true;
      STATE.nodePanX += dx;
      STATE.nodePanY += dy;
      dragNode.x = ev.clientX; dragNode.y = ev.clientY;
      drawNodeMapCanvas();
    });
    window.addEventListener("mouseup", function () { dragNode = null; });
    cNode.addEventListener("wheel", function (ev) {
      ev.preventDefault();
      STATE.nodeZoom = Math.max(0.55, Math.min(2.4, STATE.nodeZoom * (ev.deltaY < 0 ? 1.10 : 0.91)));
      drawNodeMapCanvas();
    }, { passive: false });

    cNode.addEventListener("mousemove", function (ev) {
      if (dragNode && dragNode.moved) return;
      const rect = cNode.getBoundingClientRect();
      const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
      let hit = null, bestD = 999;
      STATE.renderedNodes.forEach(function (n) {
        const d = Math.hypot(n.x - mx, n.y - my);
        if (d <= n.r + 5 && d < bestD) { bestD = d; hit = n; }
      });
      const nextId = hit ? hit.id : null;
      if (STATE.hoveredNodeId !== nextId) {
        STATE.hoveredNodeId = nextId;
        drawNodeMapCanvas();
      }
      if (hit) {
        cNode.style.cursor = "pointer";
        showTip(ev, hit.detail || (hit.kind + ": " + hit.label));
      } else {
        cNode.style.cursor = "grab";
        hideTip();
      }
    });
    cNode.addEventListener("mouseleave", function () {
      if (STATE.hoveredNodeId !== null) {
        STATE.hoveredNodeId = null;
        drawNodeMapCanvas();
      }
      hideTip();
    });
    cNode.addEventListener("click", function (ev) {
      const rect = cNode.getBoundingClientRect();
      const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
      const hit = STATE.renderedNodes.find(function (n) {
        return Math.hypot(n.x - mx, n.y - my) <= n.r + 5;
      });
      if (!hit) return;
      if (hit.kind === "PATIENT") {
        STATE.selectedPatientId = hit.id;
        renderTrialSelection();
      } else if (hit.kind === "SITE") {
        STATE.selectedSiteId = hit.id;
        switchView("site");
      } else if (hit.kind === "SIGNAL") {
        STATE.selectedSignalId = hit.id;
        document.getElementById("signal-view-selector").value = STATE.selectedSignalId;
        switchView("signal");
      } else if (hit.kind === "STUDY") {
        STATE.patientStudy = STATE.patientStudy === hit.id ? "ALL" : hit.id;
        document.getElementById("patient-study-filter").value = STATE.patientStudy;
        renderTrialSelection();
      }
    });

    document.querySelectorAll("[data-nodelayer]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        STATE.nodeLayerMode = btn.getAttribute("data-nodelayer");
        document.querySelectorAll("[data-nodelayer]").forEach(function (b) {
          b.classList.toggle("active", b === btn);
        });
        drawNodeMapCanvas();
      });
    });
    document.getElementById("btn-nodemap-zin").addEventListener("click", function () {
      STATE.nodeZoom = Math.min(2.4, STATE.nodeZoom * 1.18);
      drawNodeMapCanvas();
    });
    document.getElementById("btn-nodemap-zout").addEventListener("click", function () {
      STATE.nodeZoom = Math.max(0.55, STATE.nodeZoom * 0.84);
      drawNodeMapCanvas();
    });
    document.getElementById("btn-reset-nodemap").addEventListener("click", function () {
      STATE.nodePanX = 0; STATE.nodePanY = 0; STATE.nodeZoom = 1.0;
      drawNodeMapCanvas();
    });

    /* --- UI CONTROLS WIRING --- */
    document.getElementById("cra-scope-select").addEventListener("change", function (ev) {
      STATE.craScope = ev.target.value;
      renderActiveView();
    });
    document.getElementById("peer-coverage-toggle").addEventListener("change", function (ev) {
      STATE.includePeer = ev.target.checked;
      renderActiveView();
    });
    document.getElementById("portfolio-search").addEventListener("input", function (ev) {
      STATE.portfolioSearch = ev.target.value;
      renderPortfolio();
    });
    document.getElementById("portfolio-study-filter").addEventListener("change", function (ev) {
      STATE.portfolioStudy = ev.target.value;
      renderPortfolio();
    });
    document.getElementById("portfolio-tier-filter").addEventListener("change", function (ev) {
      STATE.portfolioTier = ev.target.value;
      renderPortfolio();
    });

    document.querySelectorAll("#portfolio-sites-table th[data-sort]").forEach(function (th) {
      th.addEventListener("click", function () {
        const col = th.getAttribute("data-sort");
        if (STATE.sortCol === col) STATE.sortDesc = !STATE.sortDesc;
        else { STATE.sortCol = col; STATE.sortDesc = true; }
        renderPortfolio();
      });
    });

    document.getElementById("site-view-selector").addEventListener("change", function (ev) {
      STATE.selectedSiteId = ev.target.value;
      STATE.checkedSignalIds = new Set();
      renderSingleSite();
    });
    document.querySelectorAll("[data-site-subtab]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        STATE.siteSubtab = btn.getAttribute("data-site-subtab");
        document.querySelectorAll("[data-site-subtab]").forEach(function (b) {
          b.classList.toggle("active", b === btn);
        });
        renderSingleSite();
      });
    });
    document.getElementById("btn-generate-agenda").addEventListener("click", function () {
      STATE.siteDraftMode = "agenda";
      renderSingleSite();
    });
    document.getElementById("btn-generate-email").addEventListener("click", function () {
      STATE.siteDraftMode = "email";
      renderSingleSite();
    });
    document.getElementById("btn-copy-site-draft").addEventListener("click", function () {
      navigator.clipboard && navigator.clipboard.writeText(document.getElementById("site-draft-output").textContent);
    });

    document.getElementById("signal-view-selector").addEventListener("change", function (ev) {
      STATE.selectedSignalId = ev.target.value;
      renderSignalDetail();
    });
    document.getElementById("btn-back-to-site").addEventListener("click", function () {
      const sig = WORKSPACE.signals.find(function (s) { return s.signal_id === STATE.selectedSignalId; });
      if (sig) { STATE.selectedSiteId = sig.site_id; switchView("site"); }
    });
    document.getElementById("btn-feedback-accept").addEventListener("click", function () { submitFeedback("ACCEPT"); });
    document.getElementById("btn-feedback-modify").addEventListener("click", function () { submitFeedback("MODIFY"); });
    document.getElementById("btn-feedback-dismiss").addEventListener("click", function () { submitFeedback("DISMISS"); });
    document.getElementById("btn-copy-issue-json").addEventListener("click", function () {
      navigator.clipboard && navigator.clipboard.writeText(document.getElementById("signal-issue-json").textContent);
    });

    document.getElementById("patient-search-input").addEventListener("input", function (ev) {
      STATE.patientSearch = ev.target.value;
      renderTrialSelection();
    });
    document.getElementById("patient-study-filter").addEventListener("change", function (ev) {
      STATE.patientStudy = ev.target.value;
      renderTrialSelection();
    });
    document.getElementById("patient-status-filter").addEventListener("change", function (ev) {
      STATE.patientStatus = ev.target.value;
      renderTrialSelection();
    });

    document.getElementById("analytics-preset-select").addEventListener("change", function (ev) {
      const item = WORKSPACE.analytics_catalog[Number(ev.target.value)];
      if (item) {
        document.getElementById("analytics-question-input").value = item.question;
        STATE.activeAnalyticsResult = item;
        renderAnalyticsView();
      }
    });
    document.getElementById("btn-run-analytics").addEventListener("click", function () {
      runClientOrServerAnalytics(document.getElementById("analytics-question-input").value.trim());
    });
    document.getElementById("btn-copy-sql").addEventListener("click", function () {
      navigator.clipboard && navigator.clipboard.writeText(document.getElementById("analytics-sql-output").textContent);
    });

    window.addEventListener("resize", redrawAllCanvases);

    /* --- OPTIONAL LIVE SSE STREAMING (DESIGN.md Section 2) --- */
    if (window.location.protocol.startsWith("http") && typeof EventSource !== "undefined") {
      try {
        const es = new EventSource("/events");
        es.addEventListener("open", function () {
          const b = document.getElementById("telemetry-status-badge");
          b.textContent = "SSE Live Connected";
          b.className = "badge badge-emerald mono";
        });
        es.addEventListener("workspace_update", function (ev) {
          const updated = JSON.parse(ev.data);
          WORKSPACE = updated;
          syncLocalFeedback();
          renderActiveView();
        });
      } catch (e) {}
    }

    populateSelects();
    const urlParams = new URLSearchParams(window.location.search);
    const hashView = window.location.hash.replace(/^#/, "").trim();
    const initTheme = urlParams.get("theme");
    const initView = urlParams.get("view") || hashView;
    applyThemeMode(initTheme === "light" || initTheme === "dark" ? initTheme : STATE.themeMode);
    if (["portfolio", "site", "signal", "selection", "analytics"].indexOf(initView) !== -1) {
      switchView(initView);
    } else {
      renderActiveView();
    }
    window.addEventListener("hashchange", function () {
      const hv = window.location.hash.replace(/^#/, "").trim();
      if (["portfolio", "site", "signal", "selection", "analytics"].indexOf(hv) !== -1) {
        switchView(hv);
      }
    });
  })();
  </script>
</body>
</html>
"""


def build_standalone_dashboard(
    output_path: str | Path | None = None,
    data_dir: str | Path | None = None,
    state_file: str | Path | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Compile workspace bundle and emit standalone dashboard.html."""
    logger = configure_logging()
    out = Path(output_path) if output_path else DEFAULT_DASHBOARD_PATH
    if not out.is_absolute():
        out = (PROJECT_ROOT / out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    bundle = compile_workspace_bundle(
        data_dir=data_dir, state_file=state_file, strict=strict
    )
    # Escape closing script tags safely for inline embedding
    json_payload = json.dumps(bundle, separators=(",", ":")).replace("</", "<\\/")
    html_text = DASHBOARD_HTML_TEMPLATE.replace("__WORKSPACE_JSON_PAYLOAD__", json_payload)
    out.write_text(html_text, encoding="utf-8")

    logger.info("Emitted standalone dashboard to %s (%d bytes)", out, len(html_text))
    return {
        "dashboard_path": str(out),
        "size_bytes": len(html_text),
        "system_theme": bundle["metadata"]["system_theme"],
        "sites_count": len(bundle["sites"]),
        "signals_count": len(bundle["signals"]),
        "patients_count": len(bundle["patient_selection"]["patients"]),
    }
