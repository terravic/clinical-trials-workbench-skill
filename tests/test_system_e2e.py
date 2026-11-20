"""End-to-end system tests covering full pipeline, DESIGN.md compliance, SSE server, and HCLS guardrails."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from scripts import PROJECT_ROOT
from scripts.workbench_cli import create_workbench_http_server

EMOJI_REGEX = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "]"
)

EXTERNAL_FONT_PATTERNS = (
    ".".join(("fonts", "googleapis", "com")),
    ".".join(("fonts", "gstatic", "com")),
)

LOCAL_PATH_REGEX = re.compile(
    r"(?:^|[\s\"'`(])(?:/" + "Users" + r"/|/" + "home" + r"/|[A-Za-z]:\\)"
)

SECRET_TOKEN_REGEX = re.compile(
    r"(sk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z\-_]{30,}|ya29\.[0-9A-Za-z\-_]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY)"
)

AI_BOILERPLATE_PHRASES = (
    "it is worth noting that",
    "in conclusion,",
    "let's delve into",
)


class TestEndToEndSystem(unittest.TestCase):
    """Execute full CLI workflow, DESIGN.md UI verification, SSE HTTP endpoints, security, and hygiene."""

    def test_full_cli_pipeline_and_design_md_verification(self) -> None:
        cli = str(PROJECT_ROOT / "scripts" / "workbench_cli.py")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            data_dir = tmp_path / "syn_data"
            state_file = tmp_path / "feedback_state.json"
            dash_file = tmp_path / "dashboard.html"

            # 1. Generate synthetic data
            r_gen = subprocess.run(
                [sys.executable, cli, "generate-data", "--output-dir", str(data_dir), "--seed", "42"],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(r_gen.returncode, 0, r_gen.stderr)

            # 2. Site visit agenda & email draft
            r_site = subprocess.run(
                [
                    sys.executable,
                    cli,
                    "--data-dir",
                    str(data_dir),
                    "--state-file",
                    str(state_file),
                    "site",
                    "--site-id",
                    "SITE-101",
                    "--draft-agenda",
                    "--draft-email",
                    "--format",
                    "json",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(r_site.returncode, 0, r_site.stderr)
            site_out = json.loads(r_site.stdout)
            self.assertIn("Clinical Monitoring Visit Agenda", site_out["monitoring_visit_agenda"])
            self.assertIn("@synthetic-clinical.example", site_out["followup_email_draft"])

            # 3. Record HITL feedback on SIG-1001 and export issue JSON
            r_sig = subprocess.run(
                [
                    sys.executable,
                    cli,
                    "--data-dir",
                    str(data_dir),
                    "--state-file",
                    str(state_file),
                    "signal",
                    "--signal-id",
                    "SIG-1001",
                    "--feedback",
                    "ACCEPT",
                    "--note",
                    "Escalated to PI safety review",
                    "--draft-issue",
                    "--format",
                    "json",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(r_sig.returncode, 0, r_sig.stderr)
            sig_out = json.loads(r_sig.stdout)
            self.assertEqual(sig_out["signal"]["disposition"], "ACCEPT")
            self.assertEqual(sig_out["issue_payload"]["originating_signal_id"], "SIG-1001")

            # 4. Select candidate patients & inspect single patient audit
            r_sel = subprocess.run(
                [
                    sys.executable,
                    cli,
                    "--data-dir",
                    str(data_dir),
                    "select-patients",
                    "--patient-id",
                    "PAT-10001",
                    "--format",
                    "json",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(r_sel.returncode, 0, r_sel.stderr)
            sel_out = json.loads(r_sel.stdout)
            self.assertGreaterEqual(sel_out["count"], 1)

            # 5. Build standalone dashboard.html and validate DESIGN.md Section 6 checklist
            r_dash = subprocess.run(
                [
                    sys.executable,
                    cli,
                    "--data-dir",
                    str(data_dir),
                    "--state-file",
                    str(state_file),
                    "--strict",
                    "build-dashboard",
                    "--output",
                    str(dash_file),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(r_dash.returncode, 0, r_dash.stderr)
            html = dash_file.read_text(encoding="utf-8")

            # Verify 3-button SVG theme selector
            for btn_id in ("theme-btn-light", "theme-btn-dark", "theme-btn-system"):
                self.assertIn(f'id="{btn_id}"', html)
            self.assertIn("prefers-color-scheme: dark", html)
            self.assertIn("localStorage", html)

            # Verify exact Light and Dark hex tokens from DESIGN.md Section 3
            for hex_token in (
                "#FAFAF9",
                "#FFFFFF",
                "#E7E5E4",
                "#1C1917",
                "#787774",
                "#0D0E11",
                "#16171D",
                "#292A30",
                "#ECE8E2",
                "#9A9895",
            ):
                self.assertIn(hex_token, html)

            # Verify semantic data visualization palette from DESIGN.md Section 5
            for viz_hex in ("#0D9488", "#D97706", "#4F46E5", "#059669", "#E11D48"):
                self.assertIn(viz_hex, html)

            # Verify self-hosted typography pairing, zero remote font hosts, and 2D/3D plot elements
            for font_name in ("DM Sans", "Instrument Serif", "IBM Plex Mono"):
                self.assertIn(font_name, html)
            self.assertIn("@font-face", html)
            self.assertIn("assets/fonts/dm-sans-400.woff2", html)
            for ext_host in EXTERNAL_FONT_PATTERNS:
                self.assertNotIn(ext_host, html)

            # Verify CSP meta policy and WCAG 2.2 AA structural landmarks & control contrast tokens
            self.assertIn('http-equiv="Content-Security-Policy"', html)
            self.assertIn('class="skip-link"', html)
            self.assertIn('<footer class="site-footer">', html)
            self.assertIn('<h2 class="pane-title">', html)
            self.assertIn(":focus-visible", html)
            self.assertIn("--control-border:", html)
            self.assertIn('role="grid"', html)
            self.assertIn('aria-controls="site-subtab-container"', html)
            self.assertIn('data-cam3d="iso" aria-pressed="true"', html)
            self.assertIn('id="footer-status"', html)

            for canvas_id in (
                "canvas-heatmap",
                "canvas-sae-trend",
                "canvas-site-trend",
                "canvas-3d-biomarker",
                "canvas-nodemap",
                "canvas-analytics-chart",
            ):
                self.assertIn(f'id="{canvas_id}"', html)
            for viz_feature in (
                'id="btn-3d-orbit"',
                'id="btn-3d-envelope"',
                'data-cam3d="iso"',
                'data-nodelayer="signals"',
                'data-nodelayer="referrals"',
                "traceSmoothSpline",
            ):
                self.assertIn(viz_feature, html)

            # Extract embedded empirical JSON bundle and verify feedback & provenance
            match = re.search(
                r'<script id="workspace-data-bundle" type="application/json">(.*?)</script>',
                html,
                flags=re.DOTALL,
            )
            self.assertIsNotNone(match)
            bundle = json.loads(match.group(1))
            self.assertEqual(len(bundle["studies"]), 4)
            self.assertEqual(len(bundle["sites"]), 16)
            self.assertEqual(len(bundle["patient_selection"]["patients"]), 140)
            self.assertEqual(bundle["provenance"]["files_loaded"], 10)
            sig_1001 = next(s for s in bundle["signals"] if s["signal_id"] == "SIG-1001")
            self.assertEqual(sig_1001["disposition"], "ACCEPT")

    def test_live_http_and_sse_server_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dash_file = tmp_path / "dashboard.html"
            state_file = tmp_path / "state.json"

            # Build quick dashboard into tmp
            cli = str(PROJECT_ROOT / "scripts" / "workbench_cli.py")
            subprocess.run(
                [sys.executable, cli, "--state-file", str(state_file), "build-dashboard", "--output", str(dash_file)],
                cwd=PROJECT_ROOT,
                capture_output=True,
                check=True,
            )

            server = create_workbench_http_server(
                "127.0.0.1", 0, dash_file, state_file=str(state_file)
            )
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{port}"
                # GET /
                with urlopen(f"{base_url}/", timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertIn("text/html", resp.headers.get("Content-Type", ""))
                    self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
                    self.assertEqual(resp.headers.get("X-Frame-Options"), "DENY")
                    self.assertIn("default-src 'self'", resp.headers.get("Content-Security-Policy", ""))
                    self.assertIn("max-age=", resp.headers.get("Strict-Transport-Security", ""))
                    html_resp = resp.read().decode("utf-8")
                    self.assertIn("<h1>Clinical Trials Workbench</h1>", html_resp)

                # GET self-hosted font file & reject path traversal
                with urlopen(f"{base_url}/assets/fonts/dm-sans-400.woff2", timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertEqual(resp.headers.get("Content-Type"), "font/woff2")
                    self.assertEqual(resp.read(4), b"wOF2")

                with self.assertRaises(HTTPError) as ctx_trav:
                    urlopen(f"{base_url}/assets/fonts/../../LICENSE", timeout=5)
                self.assertEqual(ctx_trav.exception.code, 404)

                # GET draft legal routes
                for legal_route in ("/privacy", "/terms", "/refunds", "/cookies"):
                    with urlopen(f"{base_url}{legal_route}", timeout=5) as resp:
                        self.assertEqual(resp.status, 200)
                        body = resp.read().decode("utf-8")
                        self.assertIn("DRAFT — REQUIRES LEGAL REVIEW", body)
                        self.assertIn('class="skip-link"', body)

                # GET /events?once=1 (SSE stream check)
                with urlopen(f"{base_url}/events?once=1", timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertIn("text/event-stream", resp.headers.get("Content-Type", ""))
                    sse_line = resp.readline().decode("utf-8")
                    self.assertIn("event: connected", sse_line)

                # POST /api/ask (valid query)
                req_ok = Request(
                    f"{base_url}/api/ask",
                    data=json.dumps({"question": "Compare SAE cycle times"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(req_ok, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    ans = json.loads(resp.read().decode("utf-8"))
                    self.assertEqual(ans["intent_id"], "SAE_CYCLE_TIME_BY_STUDY")

                # POST /api/ask (unsafe SQL must return 400)
                req_bad = Request(
                    f"{base_url}/api/ask",
                    data=json.dumps({"sql": "DROP TABLE studies"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(req_bad, timeout=5)
                self.assertEqual(ctx.exception.code, 400)

                # POST /api/ask (undefined key must return 400)
                req_extra = Request(
                    f"{base_url}/api/ask",
                    data=json.dumps({"question": "Compare SAE cycle times", "extra_key": True}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as ctx_extra:
                    urlopen(req_extra, timeout=5)
                self.assertEqual(ctx_extra.exception.code, 400)

                # POST /api/ask (invalid UTF-8 body must return 400)
                req_bad_utf8 = Request(
                    f"{base_url}/api/ask",
                    data=b"\xff\xfe\xfd",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as ctx_utf8:
                    urlopen(req_bad_utf8, timeout=5)
                self.assertEqual(ctx_utf8.exception.code, 400)

                # POST /api/feedback
                req_fb = Request(
                    f"{base_url}/api/feedback",
                    data=json.dumps(
                        {"signal_id": "SIG-1002", "disposition": "MODIFY", "note": "Updated timeline"}
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(req_fb, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    fb_res = json.loads(resp.read().decode("utf-8"))
                    self.assertEqual(fb_res["feedback"]["disposition"], "MODIFY")

                # Reject cross-origin, default-port Origin mismatch, and malformed Origin with 403
                for bad_origin in (
                    "https://untrusted.example",
                    "http://127.0.0.1",
                    "http://127.0.0.1:99999",
                ):
                    req_xorigin = Request(
                        f"{base_url}/api/feedback",
                        data=json.dumps({"signal_id": "SIG-1002", "disposition": "ACCEPT"}).encode("utf-8"),
                        headers={
                            "Content-Type": "application/json",
                            "Origin": bad_origin,
                        },
                        method="POST",
                    )
                    with self.assertRaises(HTTPError) as ctx_xo:
                        urlopen(req_xorigin, timeout=5)
                    self.assertEqual(ctx_xo.exception.code, 403)

                # Reject oversized payload with 413
                req_big = Request(
                    f"{base_url}/api/ask",
                    data=(b" " * 70000),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as ctx_big:
                    urlopen(req_big, timeout=5)
                self.assertEqual(ctx_big.exception.code, 413)

                # Unsupported HTTP method returns 405 with security headers
                req_put = Request(f"{base_url}/api/feedback", data=b"{}", method="PUT")
                with self.assertRaises(HTTPError) as ctx_put:
                    urlopen(req_put, timeout=5)
                self.assertEqual(ctx_put.exception.code, 405)
                self.assertIn("default-src 'self'", ctx_put.exception.headers.get("Content-Security-Policy", ""))
            finally:
                server.shutdown()
                server.server_close()

    def test_http_auth_owner_isolation_and_non_loopback_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dash_file = tmp_path / "dashboard.html"
            state_file = tmp_path / "state.json"
            dash_file.write_text("<html><body>ok</body></html>", encoding="utf-8")

            # Non-loopback host without token must fail closed
            with self.assertRaises(ValueError):
                create_workbench_http_server("0.0.0.0", 0, dash_file, state_file=str(state_file), api_token="")

            server = create_workbench_http_server(
                "127.0.0.1",
                0,
                dash_file,
                state_file=str(state_file),
                api_token="ephemeral-test-token",
                authenticated_reviewer="CRA-01",
            )
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{port}"
                auth_headers = {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer ephemeral-test-token",
                }

                # Unauthenticated API and data-rights calls return 401 even without params/valid JSON
                req_noauth = Request(
                    f"{base_url}/api/feedback",
                    data=json.dumps({"signal_id": "SIG-1001", "disposition": "ACCEPT"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as ctx_401:
                    urlopen(req_noauth, timeout=5)
                self.assertEqual(ctx_401.exception.code, 401)

                with self.assertRaises(HTTPError) as ctx_exp_401:
                    urlopen(f"{base_url}/api/user-data/export", timeout=5)
                self.assertEqual(ctx_exp_401.exception.code, 401)

                req_del_401 = Request(
                    f"{base_url}/api/user-data/delete",
                    data=b"not-json",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as ctx_del_401:
                    urlopen(req_del_401, timeout=5)
                self.assertEqual(ctx_del_401.exception.code, 401)

                # Invalid token returns 403
                req_badtok = Request(
                    f"{base_url}/api/feedback",
                    data=json.dumps({"signal_id": "SIG-1001", "disposition": "ACCEPT"}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Authorization": "Bearer wrong-token"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as ctx_403:
                    urlopen(req_badtok, timeout=5)
                self.assertEqual(ctx_403.exception.code, 403)

                # Authenticated CRA-01 records own feedback
                req_own = Request(
                    f"{base_url}/api/feedback",
                    data=json.dumps(
                        {"signal_id": "SIG-1001", "disposition": "ACCEPT", "reviewer_id": "CRA-01"}
                    ).encode("utf-8"),
                    headers=auth_headers,
                    method="POST",
                )
                with urlopen(req_own, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)

                # Non-owner attempt by CRA-01 to export or delete CRA-02 data returns 403
                req_idor_exp = Request(
                    f"{base_url}/api/user-data/export?reviewer_id=CRA-02",
                    headers={"Authorization": "Bearer ephemeral-test-token"},
                    method="GET",
                )
                with self.assertRaises(HTTPError) as ctx_idor1:
                    urlopen(req_idor_exp, timeout=5)
                self.assertEqual(ctx_idor1.exception.code, 403)

                req_idor_del = Request(
                    f"{base_url}/api/user-data/delete",
                    data=json.dumps({"reviewer_id": "CRA-02"}).encode("utf-8"),
                    headers=auth_headers,
                    method="POST",
                )
                with self.assertRaises(HTTPError) as ctx_idor2:
                    urlopen(req_idor_del, timeout=5)
                self.assertEqual(ctx_idor2.exception.code, 403)

                # Owner CRA-01 can export and permanently delete own records
                req_exp = Request(
                    f"{base_url}/api/user-data/export?reviewer_id=CRA-01",
                    headers={"Authorization": "Bearer ephemeral-test-token"},
                    method="GET",
                )
                with urlopen(req_exp, timeout=5) as resp:
                    exp_data = json.loads(resp.read().decode("utf-8"))
                    self.assertEqual(exp_data["record_count"], 1)

                req_del = Request(
                    f"{base_url}/api/user-data/delete",
                    data=json.dumps({"reviewer_id": "CRA-01"}).encode("utf-8"),
                    headers=auth_headers,
                    method="POST",
                )
                with urlopen(req_del, timeout=5) as resp:
                    del_data = json.loads(resp.read().decode("utf-8"))
                    self.assertEqual(del_data["purged_count"], 1)
            finally:
                server.shutdown()
                server.server_close()

            # Without server-pinned reviewer identity, forged client header must fail closed with 403
            server_unpinned = create_workbench_http_server(
                "127.0.0.1",
                0,
                dash_file,
                state_file=str(state_file),
                api_token="ephemeral-test-token",
                authenticated_reviewer="",
            )
            port2 = server_unpinned.server_address[1]
            thread2 = threading.Thread(target=server_unpinned.serve_forever, daemon=True)
            thread2.start()
            try:
                req_forged = Request(
                    f"http://127.0.0.1:{port2}/api/user-data/export?reviewer_id=CRA-01",
                    headers={
                        "Authorization": "Bearer ephemeral-test-token",
                        "X-Authenticated-Reviewer": "CRA-01",
                    },
                    method="GET",
                )
                with self.assertRaises(HTTPError) as ctx_forged:
                    urlopen(req_forged, timeout=5)
                self.assertEqual(ctx_forged.exception.code, 403)
            finally:
                server_unpinned.shutdown()
                server_unpinned.server_close()

    def test_repository_hygiene_no_emojis_no_internal_links_no_ai_markers(self) -> None:
        skip_dirs = {".git", "__pycache__", ".pytest_cache", ".venv", "venv"}
        binary_suffixes = {".pyc", ".pyo", ".png", ".jpg", ".sqlite", ".db", ".woff2"}
        checked_files = 0
        for path in PROJECT_ROOT.rglob("*"):
            if any(part in skip_dirs for part in path.parts) or not path.is_file():
                continue
            if path.suffix in binary_suffixes:
                continue
            text = path.read_text(encoding="utf-8")
            rel = path.relative_to(PROJECT_ROOT)
            checked_files += 1

            emoji_match = EMOJI_REGEX.search(text)
            if emoji_match is not None:
                self.fail(
                    f"Forbidden emoji/pictorial character {emoji_match.group(0)!r} found in {rel}"
                )

            lower = text.lower()
            if rel.as_posix() != "tests/test_system_e2e.py":
                for ext_font in EXTERNAL_FONT_PATTERNS:
                    self.assertNotIn(
                        ext_font, lower, f"External font host '{ext_font}' found in {rel}"
                    )
                self.assertIsNone(
                    LOCAL_PATH_REGEX.search(text),
                    f"Local absolute path detected in {rel}",
                )
                self.assertIsNone(
                    SECRET_TOKEN_REGEX.search(text),
                    f"Possible secret signature found in {rel}",
                )
                for phrase in AI_BOILERPLATE_PHRASES:
                    self.assertNotIn(
                        phrase, lower, f"Forbidden boilerplate '{phrase}' found in {rel}"
                    )
        self.assertGreaterEqual(checked_files, 25)


if __name__ == "__main__":
    unittest.main()
