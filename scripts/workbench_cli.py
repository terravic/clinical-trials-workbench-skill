"""Unified CLI Orchestrator and HTTP/SSE Workbench Server.

Primary programmatic interface invoked by Agent Harnesses executing SKILL.md
and by human operators from the terminal. Supports portfolio triage, single-site
monitoring preparation, signal audit & HITL feedback, clinical trial patient
selection, guardrailed SQL analytics, standalone dashboard generation, and live
Server-Sent Events (SSE) streaming.
"""

from __future__ import annotations

import argparse
import hmac
import html as html_lib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import sys
import threading
from typing import Any
from urllib.parse import parse_qs, urlsplit

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import (
    DEFAULT_DASHBOARD_PATH,
    DEFAULT_FONTS_DIR,
    DEFAULT_LEGAL_DIR,
    EntityNotFoundError,
    SQLSafetyError,
    WorkbenchError,
    configure_logging,
    resolve_data_dir,
    resolve_state_file,
)
from scripts.action_drafter import (
    build_portfolio_executive_briefing,
    delete_reviewer_data,
    draft_issue_payload,
    draft_monitoring_visit_agenda,
    draft_site_followup_email,
    export_reviewer_data,
    load_feedback_state,
    record_signal_feedback,
)
from scripts.analytics_assistant import answer_analytics_question
from scripts.build_dashboard import (
    build_standalone_dashboard,
    compile_workspace_bundle,
    serialize_bundle_json,
    write_dashboard_from_bundle,
)
from scripts.data_ingestor import REQUIRED_SOURCE_FILES, ingest_workspace_data
from scripts.generate_synthetic_data import generate_all_synthetic_data
from scripts.signal_engine import evaluate_signals_and_sites, get_signal_by_id
from scripts.trial_selector import (
    evaluate_selection_criteria_once,
    filter_selection_evaluations,
    finalize_selection_with_site_risks,
)

MAX_REQUEST_BODY_BYTES = 65_536
MAX_SSE_SUBSCRIBERS = 32
MAX_QUESTION_LENGTH = 1000
MAX_SQL_PAYLOAD_LENGTH = 4096
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

ALLOWED_FONT_FILES = frozenset(
    {
        "dm-sans-400.woff2",
        "dm-sans-500.woff2",
        "dm-sans-600.woff2",
        "dm-sans-700.woff2",
        "ibm-plex-mono-400.woff2",
        "ibm-plex-mono-500.woff2",
        "ibm-plex-mono-600.woff2",
        "instrument-serif-400.woff2",
        "instrument-serif-400-italic.woff2",
    }
)

LEGAL_ROUTE_FILES: dict[str, tuple[str, str]] = {
    "/privacy": ("Privacy Notice", "privacy.md"),
    "/terms": ("Terms of Service", "terms.md"),
    "/refunds": ("Refund and Cancellation Policy", "refunds.md"),
    "/cookies": ("Cookie and Local Storage Notice", "cookies.md"),
}

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "font-src 'self' data:; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)


def _render_legal_html(title: str, markdown_body: str) -> bytes:
    """Render a draft legal markdown file as an accessible, CSP-safe HTML page."""
    escaped_title = html_lib.escape(title)
    escaped_body = html_lib.escape(markdown_body)
    doc = (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="UTF-8" />\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0" />\n'
        f"  <title>{escaped_title} - Clinical Trials Workbench</title>\n"
        "  <style>\n"
        "    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; "
        "background: #FAFAF9; color: #1C1917; max-width: 860px; margin: 0 auto; padding: 24px; line-height: 1.6; }\n"
        "    header { border-bottom: 1px solid #E7E5E4; padding-bottom: 12px; margin-bottom: 20px; }\n"
        "    a { color: #0F766E; }\n"
        "    a:focus-visible { outline: 2px solid #0F766E; outline-offset: 2px; }\n"
        "    pre { white-space: pre-wrap; word-break: break-word; background: #FFFFFF; "
        "border: 1px solid #E7E5E4; border-radius: 6px; padding: 18px; font-family: ui-monospace, monospace; font-size: 13px; }\n"
        "    footer { margin-top: 24px; padding-top: 12px; border-top: 1px solid #E7E5E4; font-size: 12px; color: #57534E; }\n"
        "  </style>\n"
        "</head>\n"
        "<body>\n"
        "  <header>\n"
        f"    <h1>{escaped_title}</h1>\n"
        '    <nav aria-label="Legal Navigation">'
        '<a href="/">Back to Workbench</a> | '
        '<a href="/privacy">Privacy</a> | '
        '<a href="/terms">Terms</a> | '
        '<a href="/refunds">Refunds</a> | '
        '<a href="/cookies">Cookies</a>'
        "</nav>\n"
        "  </header>\n"
        "  <main>\n"
        f"    <pre>{escaped_body}</pre>\n"
        "  </main>\n"
        "  <footer>DRAFT — REQUIRES LEGAL REVIEW</footer>\n"
        "</body>\n"
        "</html>\n"
    )
    return doc.encode("utf-8")


def _validate_exact_keys(
    payload: dict[str, Any],
    allowed_keys: frozenset[str],
    required_keys: frozenset[str] = frozenset(),
) -> str | None:
    """Validate that a JSON object contains only allowlisted keys and all required keys."""
    unknown = sorted(set(payload.keys()) - allowed_keys)
    if unknown:
        return f"Undefined payload keys rejected: {', '.join(unknown)}"
    missing = sorted(required_keys - set(payload.keys()))
    if missing:
        return f"Missing required payload keys: {', '.join(missing)}"
    return None


def _workspace_fingerprint(
    data_dir: str | None, state_file: str | None
) -> tuple[Any, ...]:
    """Compute a fast stat-based fingerprint across all 10 data feeds and the HITL state file."""
    resolved_data = resolve_data_dir(data_dir)
    resolved_state = resolve_state_file(state_file)
    sig: list[Any] = [str(resolved_data), str(resolved_state)]
    for filename, _ in REQUIRED_SOURCE_FILES:
        fpath = resolved_data / filename
        try:
            st = fpath.stat()
            sig.append((st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append(None)
    try:
        st_s = resolved_state.stat()
        sig.append((st_s.st_mtime_ns, st_s.st_size))
    except OSError:
        sig.append(None)
    return tuple(sig)


def _prepare_runtime_context(
    data_dir: str | None,
    state_file: str | None,
    strict: bool,
    *,
    persist_tables: bool = True,
    need_selection_pass2: bool = True,
    include_site_rankings: bool = False,
    check_same_thread: bool = True,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """Ingest feeds, run single-pass criteria + signals + live site ranking, and return context."""
    conn, _ = ingest_workspace_data(
        data_dir=data_dir, strict=strict, check_same_thread=check_same_thread
    )
    try:
        feedback = load_feedback_state(state_file=state_file, strict=strict)
        precompute = evaluate_selection_criteria_once(conn)
        signal_bundle = evaluate_signals_and_sites(
            conn,
            feedback_map=feedback,
            local_elig_by_site=precompute.local_elig_by_site,
            persist_tables=persist_tables,
        )
        if not need_selection_pass2:
            return conn, signal_bundle, {}
        risk_map = {s["site_id"]: s["risk_score"] for s in signal_bundle["sites"]}
        selection_bundle = finalize_selection_with_site_risks(
            conn,
            precompute,
            site_risk_scores=risk_map,
            persist_table=persist_tables,
            include_site_rankings=include_site_rankings,
        )
        return conn, signal_bundle, selection_bundle
    except Exception:
        conn.close()
        raise


def _format_portfolio_markdown(
    briefing: dict[str, Any], sites: list[dict[str, Any]]
) -> str:
    lines = [
        "# Level 1: Clinical Portfolio Workbench Summary",
        "",
        briefing["executive_briefing"],
        "",
        "## Priority Tier Distribution",
        f"- **Priority Action Required:** {briefing['tier_counts']['Priority Action Required']} sites",
        f"- **Important Signals Next:** {briefing['tier_counts']['Important Signals Next']} sites",
        f"- **Continue to Monitor:** {briefing['tier_counts']['Continue to Monitor']} sites",
        f"- **Closed SAE Median Turnaround:** {briefing['submitted_sae_median_days']} days (Target: {briefing['sae_target_days']} days; Legacy Baseline: {briefing['sae_baseline_days']} days)",
        f"- **Eligible Trial Candidates:** {briefing['eligible_patient_count']} ready | {briefing['potential_needs_labs_count']} awaiting lab refresh",
        "",
        "## Clinical Sites Ranked by Risk Score",
        "| Site ID | Protocol | Region | CRA | Priority Tier | Risk Score | Crit/Total Signals | Enrolled | Local Eligible |",
        "|---|---|---|---|---|---:|---:|---:|---:|",
    ]
    for s in sorted(sites, key=lambda x: (-x["risk_score"], x["site_id"])):
        lines.append(
            f"| {s['site_id']} | {s['protocol_code']} | {s['region']} | {s['assigned_cra_id']} | "
            f"{s['priority_tier']} | {s['risk_score']} | {s['signal_counts']['CRITICAL']}/{s['signal_counts']['TOTAL']} | "
            f"{s['enrolled_count']}/{s['target_enrollment']} | {s['local_eligible_candidates']} |"
        )
    return "\n".join(lines)


def _format_selection_markdown(
    evaluations: list[dict[str, Any]],
    patients_by_id: dict[str, dict[str, Any]],
    summary_by_study: list[dict[str, Any]],
    single_patient_audit: dict[str, Any] | None = None,
) -> str:
    lines = [
        "# Clinical Trial Selection Workbench Report",
        "",
        "## Study Eligibility Funnel Summary",
        "| Study ID | Protocol | ELIGIBLE | POTENTIAL_NEEDS_LABS | INELIGIBLE |",
        "|---|---|---:|---:|---:|",
    ]
    for row in summary_by_study:
        lines.append(
            f"| {row['study_id']} | {row['protocol_code']} | {row['ELIGIBLE']} | "
            f"{row['POTENTIAL_NEEDS_LABS']} | {row['INELIGIBLE']} |"
        )

    lines.extend(
        [
            "",
            f"## Matched Candidate Evaluations ({len(evaluations)} shown)",
            "| Patient ID | Age/Sex | Region | ICD-10 | Study | Classification | Score | Recommended Site | Missing / Failed Criteria |",
            "|---|---|---|---|---|---|---:|---|---|",
        ]
    )
    for ev in evaluations:
        p = patients_by_id[ev["patient_id"]]
        notes = ev["failed_criteria"] or ev["missing_criteria"] or ["All passed"]
        lines.append(
            f"| {ev['patient_id']} | {p['age_years']}/{p['sex']} | {p['region']} | {p['primary_icd10']} | "
            f"{ev['protocol_code']} | {ev['classification']} | {ev['match_score']} | "
            f"{ev['recommended_site_id'] or 'None'} | {', '.join(notes[:3])} |"
        )

    if single_patient_audit:
        lines.extend(
            [
                "",
                f"## Criterion-by-Criterion Audit: {single_patient_audit['patient_id']} ({single_patient_audit['protocol_code']})",
                "| Criterion | Category | Status | Observed | Requirement |",
                "|---|---|---|---|---|",
            ]
        )
        for c in single_patient_audit["criteria_audit"]:
            lines.append(
                f"| {c['criterion_id']} | {c['category']} | {c['status']} | {c['observed']} | {c['requirement']} |"
            )
    return "\n".join(lines)


def create_workbench_http_server(
    host: str,
    port: int,
    dashboard_path: Path,
    data_dir: str | None = None,
    state_file: str | None = None,
    api_token: str | None = None,
    authenticated_reviewer: str | None = None,
    initial_bundle_json: str | None = None,
) -> ThreadingHTTPServer:
    """Create a hardened, thread-safe local HTTP + Server-Sent Events (SSE) workbench server."""
    effective_token = api_token if api_token is not None else os.environ.get("CTW_API_TOKEN")
    effective_reviewer = (
        authenticated_reviewer
        if authenticated_reviewer is not None
        else os.environ.get("CTW_AUTHENTICATED_REVIEWER")
    )
    if host.lower() not in LOOPBACK_HOSTS and not effective_token:
        raise ValueError(
            "Binding to a non-loopback host requires CTW_API_TOKEN to be configured."
        )

    allowed_hosts = (
        set(LOOPBACK_HOSTS) | {host.lower()}
        if host.lower() in LOOPBACK_HOSTS
        else {host.lower()}
    )

    subscribers: list[queue.Queue[str]] = []
    sub_lock = threading.Lock()
    state_lock = threading.Lock()
    cache_lock = threading.Lock()
    ask_lock = threading.Lock()

    state_version = 0
    bundle_cache: dict[str, Any] = {"key": None, "json_bytes": None}
    if initial_bundle_json is not None:
        bundle_cache["key"] = (_workspace_fingerprint(data_dir, state_file), state_version)
        bundle_cache["json_bytes"] = initial_bundle_json.encode("utf-8")

    ask_conn_cache: dict[str, Any] = {"key": None, "conn": None}
    font_cache: dict[str, bytes] = {}
    legal_cache: dict[str, tuple[int, int, bytes]] = {}
    dash_cache: dict[str, Any] = {"sig": None, "bytes": b""}

    def _current_cache_key() -> tuple[Any, int]:
        fp = _workspace_fingerprint(data_dir, state_file)
        with cache_lock:
            return (fp, state_version)

    def get_bundle_json_bytes() -> bytes:
        key = _current_cache_key()
        with cache_lock:
            if bundle_cache["key"] == key and bundle_cache["json_bytes"] is not None:
                return bundle_cache["json_bytes"]
        bundle = compile_workspace_bundle(data_dir=data_dir, state_file=state_file)
        encoded = serialize_bundle_json(bundle).encode("utf-8")
        with cache_lock:
            if state_version == key[1]:
                bundle_cache["key"] = key
                bundle_cache["json_bytes"] = encoded
        return encoded

    def _invalidate_ask_conn() -> None:
        with ask_lock:
            old_conn = ask_conn_cache["conn"]
            ask_conn_cache["conn"] = None
            ask_conn_cache["key"] = None
            if old_conn is not None:
                try:
                    old_conn.close()
                except Exception:
                    pass

    def execute_cached_ask(question: str | None, custom_sql: str | None) -> dict[str, Any]:
        key = _current_cache_key()
        with ask_lock:
            conn = ask_conn_cache["conn"]
            if conn is None or ask_conn_cache["key"] != key:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
                conn, _, _ = _prepare_runtime_context(
                    data_dir,
                    state_file,
                    strict=False,
                    persist_tables=True,
                    need_selection_pass2=True,
                    include_site_rankings=False,
                    check_same_thread=False,
                )
                ask_conn_cache["conn"] = conn
                ask_conn_cache["key"] = key
            return answer_analytics_question(
                conn,
                question=question,
                custom_sql=custom_sql,
            )

    def broadcast_payload(payload: str) -> None:
        with sub_lock:
            if not subscribers:
                return
            targets = list(subscribers)
        for q in targets:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass

    def _refresh_after_state_mutation() -> str:
        """Rebuild bundle and dashboard.html once, refresh caches, and return serialized JSON."""
        nonlocal state_version
        bundle = compile_workspace_bundle(data_dir=data_dir, state_file=state_file)
        _, raw_json = write_dashboard_from_bundle(dashboard_path, bundle)
        new_fp = _workspace_fingerprint(data_dir, state_file)
        encoded = raw_json.encode("utf-8")
        with cache_lock:
            state_version += 1
            bundle_cache["key"] = (new_fp, state_version)
            bundle_cache["json_bytes"] = encoded
        _invalidate_ask_conn()
        return raw_json

    class WorkbenchRequestHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return  # Keep terminal output quiet unless structured logger enabled

        def _send_security_headers(self) -> None:
            self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Permissions-Policy", "geolocation=(), microphone=(), camera=()")

        def _send_json_bytes(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self._send_security_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        def _send_json(self, status: int, data: dict[str, Any]) -> None:
            self._send_json_bytes(status, json.dumps(data, separators=(",", ":")).encode("utf-8"))

        def _validate_host_and_origin(self) -> bool:
            raw_host = (self.headers.get("Host") or "").strip()
            if raw_host:
                if raw_host.startswith("["):
                    host_only = raw_host.split("]")[0].lstrip("[").lower()
                else:
                    host_only = raw_host.split(":")[0].lower()
                if host_only not in allowed_hosts:
                    self._send_json(HTTPStatus.FORBIDDEN, {"error": "Invalid Host header"})
                    return False

            raw_origin = (self.headers.get("Origin") or "").strip()
            if raw_origin:
                parsed_origin = urlsplit(raw_origin)
                origin_host = (parsed_origin.hostname or "").lower()
                bound_port = self.server.server_address[1]
                origin_port = parsed_origin.port
                if (
                    parsed_origin.scheme not in ("http", "https")
                    or origin_host not in allowed_hosts
                    or (origin_port is not None and origin_port != bound_port)
                ):
                    self._send_json(HTTPStatus.FORBIDDEN, {"error": "Cross-origin request rejected"})
                    return False
            return True

        def _check_bearer_auth(self, mandatory: bool = False) -> bool:
            if not effective_token:
                if mandatory:
                    self._send_json(
                        HTTPStatus.UNAUTHORIZED,
                        {"error": "Authentication required: configure CTW_API_TOKEN"},
                    )
                    return False
                return True
            auth_header = (self.headers.get("Authorization") or "").strip()
            if not auth_header:
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "Missing Authorization header"})
                return False
            parts = auth_header.split(" ", 1)
            if len(parts) != 2 or parts[0].lower() != "bearer":
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "Invalid Authorization scheme"})
                return False
            if not hmac.compare_digest(parts[1].strip(), effective_token):
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "Invalid authentication token"})
                return False
            return True

        def _authorize_reviewer_owner(self, target_reviewer_id: str) -> bool:
            if not self._check_bearer_auth(mandatory=True):
                return False
            owner = effective_reviewer or (self.headers.get("X-Authenticated-Reviewer") or "").strip()
            if not owner:
                self._send_json(
                    HTTPStatus.FORBIDDEN,
                    {"error": "Authenticated reviewer identity required"},
                )
                return False
            if not hmac.compare_digest(owner, target_reviewer_id):
                self._send_json(
                    HTTPStatus.FORBIDDEN,
                    {"error": "Forbidden: cannot access another reviewer's records"},
                )
                return False
            return True

        def _read_json_body(self) -> dict[str, Any] | None:
            raw_len = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_len)
            except ValueError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid Content-Length header"})
                return None
            if length < 0:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Negative Content-Length header"})
                return None
            if length > MAX_REQUEST_BODY_BYTES:
                self._send_json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    {"error": f"Request body exceeds {MAX_REQUEST_BODY_BYTES} byte limit"},
                )
                return None
            raw_body = self.rfile.read(length).decode("utf-8", errors="strict") if length > 0 else "{}"
            try:
                parsed = json.loads(raw_body)
            except json.JSONDecodeError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON body"})
                return None
            if not isinstance(parsed, dict):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "JSON payload must be an object"})
                return None
            return parsed

        def do_GET(self) -> None:  # noqa: N802
            if not self._validate_host_and_origin():
                return
            parsed = urlsplit(self.path)
            route = parsed.path
            query = parse_qs(parsed.query)

            if route in ("/", "/dashboard.html"):
                st = dashboard_path.stat()
                sig = (st.st_mtime_ns, st.st_size)
                with cache_lock:
                    if dash_cache["sig"] == sig:
                        html_bytes = dash_cache["bytes"]
                    else:
                        html_bytes = dashboard_path.read_bytes()
                        dash_cache["sig"] = sig
                        dash_cache["bytes"] = html_bytes
                self.send_response(HTTPStatus.OK)
                self._send_security_headers()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html_bytes)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    self.wfile.write(html_bytes)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                return

            if route.startswith("/assets/fonts/"):
                font_name = route[len("/assets/fonts/"):]
                if font_name in ALLOWED_FONT_FILES:
                    font_bytes = font_cache.get(font_name)
                    if font_bytes is None:
                        font_path = DEFAULT_FONTS_DIR / font_name
                        if font_path.is_file():
                            font_bytes = font_path.read_bytes()
                            font_cache[font_name] = font_bytes
                    if font_bytes is not None:
                        self.send_response(HTTPStatus.OK)
                        self._send_security_headers()
                        self.send_header("Content-Type", "font/woff2")
                        self.send_header("Content-Length", str(len(font_bytes)))
                        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
                        self.end_headers()
                        try:
                            self.wfile.write(font_bytes)
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            pass
                        return
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Endpoint not found"})
                return

            if route in LEGAL_ROUTE_FILES:
                title, filename = LEGAL_ROUTE_FILES[route]
                legal_path = DEFAULT_LEGAL_DIR / filename
                if legal_path.is_file():
                    st_l = legal_path.stat()
                    cached_l = legal_cache.get(route)
                    if cached_l is not None and cached_l[0] == st_l.st_mtime_ns and cached_l[1] == st_l.st_size:
                        body = cached_l[2]
                    else:
                        body = _render_legal_html(title, legal_path.read_text(encoding="utf-8"))
                        legal_cache[route] = (st_l.st_mtime_ns, st_l.st_size, body)
                    self.send_response(HTTPStatus.OK)
                    self._send_security_headers()
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    try:
                        self.wfile.write(body)
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        pass
                    return

            if route == "/api/bundle":
                if not self._check_bearer_auth(mandatory=False):
                    return
                self._send_json_bytes(HTTPStatus.OK, get_bundle_json_bytes())
                return

            if route == "/api/user-data/export":
                req_reviewer = (query.get("reviewer_id") or [""])[0].strip()
                if not req_reviewer:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Missing reviewer_id query parameter"})
                    return
                if not self._authorize_reviewer_owner(req_reviewer):
                    return
                try:
                    exported = export_reviewer_data(req_reviewer, state_file=state_file)
                    self._send_json(HTTPStatus.OK, exported)
                except ValueError as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return

            if route == "/events":
                if not self._check_bearer_auth(mandatory=False):
                    return
                is_once = (query.get("once") or [""])[0] == "1"
                client_q: queue.Queue[str] = queue.Queue(maxsize=8)
                with sub_lock:
                    if len(subscribers) >= MAX_SSE_SUBSCRIBERS:
                        self._send_json(
                            HTTPStatus.SERVICE_UNAVAILABLE,
                            {"error": "Maximum concurrent event streams reached"},
                        )
                        return
                    subscribers.append(client_q)
                self.send_response(HTTPStatus.OK)
                self._send_security_headers()
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close" if is_once else "keep-alive")
                self.end_headers()
                try:
                    self.wfile.write(b"event: connected\ndata: {\"status\":\"ok\"}\n\n")
                    self.wfile.flush()
                    if is_once:
                        self.close_connection = True
                        return
                    while True:
                        try:
                            msg = client_q.get(timeout=15.0)
                            frame = f"event: workspace_update\ndata: {msg}\n\n".encode("utf-8")
                            self.wfile.write(frame)
                            self.wfile.flush()
                        except queue.Empty:
                            self.wfile.write(b": heartbeat\n\n")
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with sub_lock:
                        if client_q in subscribers:
                            subscribers.remove(client_q)
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Endpoint not found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._validate_host_and_origin():
                return
            route = urlsplit(self.path).path

            if route == "/api/ask":
                if not self._check_bearer_auth(mandatory=False):
                    return
                req = self._read_json_body()
                if req is None:
                    return
                schema_err = _validate_exact_keys(req, frozenset({"question", "sql"}))
                if schema_err:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": schema_err})
                    return
                question = req.get("question")
                custom_sql = req.get("sql")
                if question is not None and (not isinstance(question, str) or len(question) > MAX_QUESTION_LENGTH):
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid 'question' field"})
                    return
                if custom_sql is not None and (not isinstance(custom_sql, str) or len(custom_sql) > MAX_SQL_PAYLOAD_LENGTH):
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid 'sql' field"})
                    return
                try:
                    ans = execute_cached_ask(question=question, custom_sql=custom_sql)
                    self._send_json(HTTPStatus.OK, ans)
                except (SQLSafetyError, ValueError) as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return

            if route == "/api/feedback":
                if not self._check_bearer_auth(mandatory=False):
                    return
                req = self._read_json_body()
                if req is None:
                    return
                schema_err = _validate_exact_keys(
                    req,
                    allowed_keys=frozenset({"signal_id", "disposition", "note", "reviewer_id"}),
                    required_keys=frozenset({"signal_id", "disposition"}),
                )
                if schema_err:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": schema_err})
                    return
                for field_name in ("signal_id", "disposition", "note", "reviewer_id"):
                    if field_name in req and not isinstance(req[field_name], str):
                        self._send_json(
                            HTTPStatus.BAD_REQUEST,
                            {"error": f"Field '{field_name}' must be a string"},
                        )
                        return
                target_reviewer = req.get("reviewer_id", "CRA-01")
                if effective_reviewer and not hmac.compare_digest(effective_reviewer, target_reviewer):
                    self._send_json(
                        HTTPStatus.FORBIDDEN,
                        {"error": "Forbidden: reviewer_id does not match authenticated reviewer"},
                    )
                    return
                try:
                    with state_lock:
                        entry = record_signal_feedback(
                            signal_id=req["signal_id"],
                            disposition=req["disposition"],
                            note=req.get("note", ""),
                            reviewer_id=target_reviewer,
                            state_file=state_file,
                        )
                        raw_json = _refresh_after_state_mutation()
                    broadcast_payload(raw_json)
                    self._send_json(HTTPStatus.OK, {"status": "saved", "feedback": entry})
                except (KeyError, ValueError) as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return

            if route == "/api/user-data/delete":
                req = self._read_json_body()
                if req is None:
                    return
                schema_err = _validate_exact_keys(
                    req,
                    allowed_keys=frozenset({"reviewer_id"}),
                    required_keys=frozenset({"reviewer_id"}),
                )
                if schema_err or not isinstance(req.get("reviewer_id"), str):
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": schema_err or "Field 'reviewer_id' must be a string"},
                    )
                    return
                target_reviewer = req["reviewer_id"].strip()
                if not self._authorize_reviewer_owner(target_reviewer):
                    return
                try:
                    with state_lock:
                        deletion = delete_reviewer_data(target_reviewer, state_file=state_file)
                        raw_json = _refresh_after_state_mutation()
                    broadcast_payload(raw_json)
                    self._send_json(HTTPStatus.OK, {"status": "deleted", **deletion})
                except ValueError as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Endpoint not found"})

    class _WorkbenchHTTPServer(ThreadingHTTPServer):
        def server_close(self) -> None:
            _invalidate_ask_conn()
            super().server_close()

    return _WorkbenchHTTPServer((host, port), WorkbenchRequestHandler)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench_cli.py",
        description="Clinical Trials System of Action and Selection Workbench CLI.",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Path to clinical data feeds (default: assets/synthetic_data or CTW_DATA_DIR).",
    )
    parser.add_argument(
        "--state-file",
        default=None,
        help="Path to HITL feedback JSON file (default: assets/workbench_feedback_state.json).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Abort with non-zero exit code on any malformed or out-of-range record instead of quarantining.",
    )

    sub_common = argparse.ArgumentParser(add_help=False)
    sub_common.add_argument("--data-dir", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub_common.add_argument("--state-file", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub_common.add_argument(
        "--strict", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # generate-data
    p_gen = sub.add_parser("generate-data", parents=[sub_common], help="Generate deterministic 10-feed synthetic clinical data.")
    p_gen.add_argument("--output-dir", default=None, help="Target synthetic data folder.")
    p_gen.add_argument("--seed", type=int, default=42, help="Random seed (default: 42).")

    # portfolio
    p_port = sub.add_parser("portfolio", parents=[sub_common], help="Level 1 Portfolio triage and executive summary.")
    p_port.add_argument("--cra", default="ALL", help="CRA scope filter (e.g., CRA-01 or ALL).")
    p_port.add_argument("--no-peer", action="store_true", help="Exclude peer-coverage sites.")
    p_port.add_argument("--tier", default=None, help="Filter sites by priority tier.")
    p_port.add_argument("--format", choices=["markdown", "json"], default="markdown")

    # site
    p_site = sub.add_parser("site", parents=[sub_common], help="Level 2 Single-Site view and visit/email drafting.")
    p_site.add_argument("--site-id", required=True, help="Target clinical site ID (e.g., SITE-101).")
    p_site.add_argument("--signals", default=None, help="Comma-separated signal IDs to include in drafts.")
    p_site.add_argument("--draft-agenda", action="store_true", help="Include Monitoring Visit Agenda draft.")
    p_site.add_argument("--draft-email", action="store_true", help="Include Site Follow-Up Email draft.")
    p_site.add_argument("--format", choices=["markdown", "json"], default="markdown")

    # signal
    p_sig = sub.add_parser("signal", parents=[sub_common], help="Level 3 Signal evidence audit, issue export, and HITL feedback.")
    p_sig.add_argument("--signal-id", required=True, help="Signal identifier (e.g., SIG-1001).")
    p_sig.add_argument("--draft-issue", action="store_true", help="Include exportable Issue/CAPA JSON.")
    p_sig.add_argument("--feedback", choices=["ACCEPT", "MODIFY", "DISMISS"], default=None)
    p_sig.add_argument("--note", default="", help="Reviewer rationale note for HITL feedback.")
    p_sig.add_argument("--reviewer", default="CRA-01", help="Reviewer role ID.")
    p_sig.add_argument("--format", choices=["markdown", "json"], default="markdown")

    # select-patients
    p_sel = sub.add_parser("select-patients", parents=[sub_common], help="Clinical Trial Patient Selection and Site Matcher.")
    p_sel.add_argument("--study-id", default=None, help="Filter by study ID (e.g., NCT-SYN-0101).")
    p_sel.add_argument("--status", choices=["ELIGIBLE", "POTENTIAL_NEEDS_LABS", "INELIGIBLE"], default=None)
    p_sel.add_argument("--site-id", default=None, help="Filter by recommended site ID.")
    p_sel.add_argument("--region", choices=["NA", "EMEA", "APAC"], default=None)
    p_sel.add_argument("--patient-id", default=None, help="Inspect specific patient ID (e.g., PAT-10001).")
    p_sel.add_argument("--min-score", type=int, default=None, help="Minimum composite match score (0-100).")
    p_sel.add_argument("--limit", type=int, default=25, help="Max rows in output (default: 25).")
    p_sel.add_argument("--format", choices=["markdown", "json"], default="markdown")

    # ask
    p_ask = sub.add_parser("ask", parents=[sub_common], help="Conversational Analytics & Guarded Read-Only SQL Explorer.")
    p_ask.add_argument("--question", default=None, help="Natural-language clinical/operational question.")
    p_ask.add_argument("--sql", default=None, help="Direct read-only SQLite SELECT query.")
    p_ask.add_argument("--format", choices=["markdown", "json"], default="markdown")

    # build-dashboard
    p_dash = sub.add_parser("build-dashboard", parents=[sub_common], help="Build standalone interactive dashboard.html.")
    p_dash.add_argument("--output", default=None, help="Output HTML file path (default: dashboard.html).")

    # serve
    p_srv = sub.add_parser("serve", parents=[sub_common], help="Serve dashboard.html over HTTP with live SSE /events streaming.")
    p_srv.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1).")
    p_srv.add_argument("--port", type=int, default=8765, help="TCP port (default: 8765).")
    p_srv.add_argument("--output", default=None, help="Dashboard path (default: dashboard.html).")

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "generate-data":
            out_dir = args.output_dir or args.data_dir
            res = generate_all_synthetic_data(output_dir=out_dir, seed=args.seed)
            print(json.dumps(res, indent=2))
            return 0

        if args.command == "build-dashboard":
            res = build_standalone_dashboard(
                output_path=args.output,
                data_dir=args.data_dir,
                state_file=args.state_file,
                strict=args.strict,
            )
            print(json.dumps(res, indent=2))
            return 0

        if args.command == "serve":
            bundle = compile_workspace_bundle(
                data_dir=args.data_dir,
                state_file=args.state_file,
                strict=args.strict,
            )
            dash_info, raw_json = write_dashboard_from_bundle(
                output_path=args.output,
                bundle=bundle,
            )
            dash_path = Path(dash_info["dashboard_path"])
            server = create_workbench_http_server(
                args.host,
                args.port,
                dash_path,
                data_dir=args.data_dir,
                state_file=args.state_file,
                initial_bundle_json=raw_json,
            )
            print(f"Serving Clinical Trials Workbench at http://{args.host}:{args.port} (SSE /events active)")
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0

        need_persist = args.command == "ask"
        need_pass2 = args.command != "signal" and not (
            args.command == "site" and not args.draft_agenda
        )
        need_site_rankings = (
            args.command == "select-patients" and args.format == "json"
        )
        conn, signal_bundle, selection_bundle = _prepare_runtime_context(
            args.data_dir,
            args.state_file,
            args.strict,
            persist_tables=need_persist,
            need_selection_pass2=need_pass2,
            include_site_rankings=need_site_rankings,
        )
        try:
            if args.command == "portfolio":
                briefing = build_portfolio_executive_briefing(
                    signal_bundle,
                    selection_bundle,
                    cra_filter=args.cra,
                    include_peer_sites=not args.no_peer,
                )
                sites = signal_bundle["sites"]
                if args.cra and args.cra.upper() != "ALL":
                    cu = args.cra.upper()
                    sites = [
                        s
                        for s in sites
                        if s["assigned_cra_id"] == cu or (not args.no_peer and s["peer_cra_id"] == cu)
                    ]
                if args.tier:
                    sites = [s for s in sites if s["priority_tier"].lower() == args.tier.lower()]

                if args.format == "json":
                    print(json.dumps({"portfolio_summary": briefing, "sites": sites}, indent=2))
                else:
                    print(_format_portfolio_markdown(briefing, sites))
                return 0

            if args.command == "site":
                site = next(
                    (s for s in signal_bundle["sites"] if s["site_id"] == args.site_id), None
                )
                if site is None:
                    raise EntityNotFoundError(
                        f"Unknown site_id '{args.site_id}'. Valid sites: {[s['site_id'] for s in signal_bundle['sites']]}"
                    )
                sig_filter = (
                    [x.strip() for x in args.signals.split(",") if x.strip()]
                    if args.signals
                    else None
                )
                agenda = (
                    draft_monitoring_visit_agenda(
                        args.site_id, signal_bundle, selection_bundle, sig_filter
                    )
                    if args.draft_agenda
                    else None
                )
                email = (
                    draft_site_followup_email(args.site_id, signal_bundle, sig_filter)
                    if args.draft_email
                    else None
                )
                site_sigs = [s for s in signal_bundle["signals"] if s["site_id"] == args.site_id]

                if args.format == "json":
                    print(
                        json.dumps(
                            {
                                "site": site,
                                "signals": site_sigs,
                                "monitoring_visit_agenda": agenda,
                                "followup_email_draft": email,
                            },
                            indent=2,
                        )
                    )
                else:
                    blocks = [
                        f"# Level 2: Single-Site Summary — {site['site_id']} ({site['site_name']})",
                        f"- **Protocol:** {site['protocol_code']} ({site['study_id']})",
                        f"- **Priority Tier:** {site['priority_tier']} (Risk Score: {site['risk_score']}/100)",
                        f"- **Status / Last Visit:** {site['site_status']} / {site['last_monitoring_date']} ({site['days_since_monitoring']}d ago)",
                        f"- **Summary:** {site['site_summary']}",
                    ]
                    if agenda:
                        blocks.extend(["", "---", "", agenda])
                    if email:
                        blocks.extend(["", "---", "", "## Follow-Up Email Draft", "```text", email, "```"])
                    print("\n".join(blocks))
                return 0

            if args.command == "signal":
                sig = get_signal_by_id(signal_bundle["signals"], args.signal_id)
                feedback_entry = None
                if args.feedback:
                    feedback_entry = record_signal_feedback(
                        signal_id=args.signal_id,
                        disposition=args.feedback,
                        note=args.note,
                        reviewer_id=args.reviewer,
                        state_file=args.state_file,
                    )
                    sig["disposition"] = feedback_entry["disposition"]
                    sig["reviewer_note"] = feedback_entry["note"]

                issue_json = (
                    draft_issue_payload(args.signal_id, signal_bundle)
                    if args.draft_issue
                    else None
                )
                if args.format == "json":
                    print(
                        json.dumps(
                            {
                                "signal": sig,
                                "feedback_recorded": feedback_entry,
                                "issue_payload": issue_json,
                            },
                            indent=2,
                        )
                    )
                else:
                    ev = sig["evidence"]
                    md = [
                        f"# Level 3: Signal Detail & Evidence — {sig['signal_id']}",
                        f"- **Title:** {sig['title']} (`{sig['rule_id']}` | `{sig['family']}`)",
                        f"- **Severity / Weight:** {sig['severity']} ({sig['weight']})",
                        f"- **Clinical Site / Study:** {sig['site_id']} ({sig['site_name']}) / {sig['protocol_code']}",
                        f"- **Disposition:** {sig['disposition']}",
                        f"- **Provenance Citation:** `{ev['source_file']}` -> record `{ev['source_record_id']}` (`{ev['metric_name']} = {ev['observed_value']}`, threshold `{ev['threshold_value']}`)",
                        f"- **Historical 8-Week Site Trend:** {sig['site_weekly_trend']}",
                        f"- **Rationale:** {sig['rationale']}",
                        f"- **Recommended Action:** {sig['recommended_action']}",
                    ]
                    if issue_json:
                        md.extend(["", "## Issue / CAPA Export Payload", "```json", json.dumps(issue_json, indent=2), "```"])
                    print("\n".join(md))
                return 0

            if args.command == "select-patients":
                evals = filter_selection_evaluations(
                    selection_bundle["_all_evaluations"],
                    selection_bundle["_patients_by_id"],
                    selection_bundle["_best_study_by_patient"],
                    selection_bundle["_valid_study_ids"],
                    study_id=args.study_id,
                    status_filter=args.status,
                    site_id_filter=args.site_id,
                    region_filter=args.region,
                    min_score=args.min_score,
                )
                single_audit = None
                if args.patient_id:
                    evals = [e for e in evals if e["patient_id"] == args.patient_id]
                    if not evals:
                        raise EntityNotFoundError(
                            f"No matching evaluations found for patient_id '{args.patient_id}'."
                        )
                    single_audit = evals[0]

                evals_limited = evals[: max(1, args.limit)]
                pat_map = selection_bundle["_patients_by_id"]
                if args.format == "json":
                    print(
                        json.dumps(
                            {
                                "summary_by_study": selection_bundle["summary_by_study"],
                                "count": len(evals),
                                "evaluations": evals_limited,
                            },
                            indent=2,
                        )
                    )
                else:
                    print(
                        _format_selection_markdown(
                            evals_limited, pat_map, selection_bundle["summary_by_study"], single_audit
                        )
                    )
                return 0

            if args.command == "ask":
                ans = answer_analytics_question(
                    conn, question=args.question, custom_sql=args.sql
                )
                if args.format == "json":
                    print(json.dumps(ans, indent=2))
                else:
                    lines = [
                        "# Conversational Analytics Result",
                        f"**Question:** {ans['question']}",
                        "",
                        f"**Synthesis:** {ans['narrative']}",
                        "",
                        "## Generated Read-Only SQL",
                        "```sql",
                        ans["sql"],
                        "```",
                        "",
                        f"## Underlying Data ({len(ans['rows'])} rows; Sources: {', '.join(ans['provenance_sources'])})",
                    ]
                    if ans["columns"] and ans["rows"]:
                        lines.append("| " + " | ".join(ans["columns"]) + " |")
                        lines.append("|" + "|".join(["---"] * len(ans["columns"])) + "|")
                        for r in ans["rows"]:
                            lines.append("| " + " | ".join(str(r[c]) for c in ans["columns"]) + " |")
                    print("\n".join(lines))
                return 0

        finally:
            conn.close()

    except WorkbenchError as exc:
        print(f"ERROR [{exc.__class__.__name__}]: {exc}", file=sys.stderr)
        return exc.exit_code
    except ValueError as exc:
        print(f"ERROR [ValueError]: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
