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

INTERNAL_LEAK_PATTERNS = (
    "corp.google.com",
    "http://go/",
    "https://go/",
    "//google3/",
    "file:///google3",
)

AI_BOILERPLATE_PHRASES = (
    "it is worth noting that",
    "in conclusion,",
    "let's delve into",
)


class TestEndToEndSystem(unittest.TestCase):
    """Execute full CLI workflow, DESIGN.md UI verification, SSE HTTP endpoints, and repo hygiene."""

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

            # Verify typography pairing and HTML5 Canvas elements
            for font_name in ("DM Sans", "Instrument Serif", "IBM Plex Mono"):
                self.assertIn(font_name, html)
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
                    html_resp = resp.read().decode("utf-8")
                    self.assertIn("<h1>Clinical Trials Workbench</h1>", html_resp)

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
            finally:
                server.shutdown()
                server.server_close()

    def test_repository_hygiene_no_emojis_no_internal_links_no_ai_markers(self) -> None:
        skip_dirs = {".git", "__pycache__", ".pytest_cache", ".venv", "venv"}
        checked_files = 0
        for path in PROJECT_ROOT.rglob("*"):
            if any(part in skip_dirs for part in path.parts) or not path.is_file():
                continue
            if path.suffix in {".pyc", ".pyo", ".png", ".jpg", ".sqlite", ".db"}:
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
                for leak in INTERNAL_LEAK_PATTERNS:
                    self.assertNotIn(
                        leak, lower, f"Forbidden internal domain/link '{leak}' found in {rel}"
                    )
                for phrase in AI_BOILERPLATE_PHRASES:
                    self.assertNotIn(
                        phrase, lower, f"Forbidden AI boilerplate '{phrase}' found in {rel}"
                    )
        self.assertGreaterEqual(checked_files, 25)


if __name__ == "__main__":
    unittest.main()
