"""Unit tests for ingestion validation, PII guardrails, trial selection, signals, and SQL safety."""

from __future__ import annotations

from pathlib import Path
import json
import shutil
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen

from scripts import (
    ClinicalRangeValidationError,
    EntityNotFoundError,
    MalformedRecordError,
    MissingDataSourceError,
    PIIComplianceViolationError,
    SQLSafetyError,
    StateFileError,
)
from scripts.action_drafter import (
    delete_reviewer_data,
    export_reviewer_data,
    load_feedback_state,
    record_signal_feedback,
)
from scripts.analytics_assistant import answer_analytics_question, validate_readonly_sql
from scripts.build_dashboard import compile_workspace_bundle, write_dashboard_from_bundle
from scripts.data_ingestor import PIIGuardrailScanner, ingest_workspace_data
from scripts.generate_synthetic_data import generate_all_synthetic_data
from scripts.signal_engine import SIGNAL_FAMILIES, evaluate_signals_and_sites, load_signal_rules
from scripts.trial_selector import (
    evaluate_selection_criteria_once,
    filter_selection_evaluations,
    finalize_selection_with_site_risks,
    run_trial_selection,
)
from scripts.workbench_cli import create_workbench_http_server


class TestPIIGuardrails(unittest.TestCase):
    """Verify HCLS and HIPAA Safe Harbor compliance scanner."""

    def test_clean_synthetic_record_passes(self) -> None:
        clean = {
            "patient_id": "PAT-10001",
            "age_years": 64,
            "contact": "site-101@synthetic-clinical.example",
            "primary_icd10": "C34.1",
        }
        self.assertEqual(PIIGuardrailScanner.scan_record("ehr.csv", "PAT-10001", clean), [])

    def test_ssn_phone_email_and_age_over_89_detected(self) -> None:
        dirty = {
            "patient_id": "PAT-99999",
            "age_years": 92,
            "notes": "SSN 123-45-6789 or call (415) 555-0199 or user@gmail.com",
            "patient_name": "Forbidden Field",
        }
        violations = PIIGuardrailScanner.scan_record("ehr.csv", "PAT-99999", dirty)
        self.assertGreaterEqual(len(violations), 4)
        joined = " | ".join(violations)
        self.assertIn("Safe Harbor", joined)
        self.assertIn("SSN", joined)
        self.assertIn("telephone", joined)
        self.assertIn("gmail.com", joined)
        self.assertIn("patient_name", joined)

    def test_non_numeric_age_redacts_raw_value(self) -> None:
        bad = {"patient_id": "PAT-99998", "age_years": "SENSITIVE-RAW-TOKEN"}
        violations = PIIGuardrailScanner.scan_record("ehr.csv", "PAT-99998", bad)
        self.assertEqual(len(violations), 1)
        self.assertIn("[REDACTED]", violations[0])
        self.assertNotIn("SENSITIVE-RAW-TOKEN", violations[0])

    def test_fast_path_and_csv_scanner_boundary_parity(self) -> None:
        cases = [
            {"patient_id": "P-1", "age_years": "0", "notes": "123-45-6789"},
            {"patient_id": "P-2", "age_years": "89", "notes": "415-555-0199"},
            {"patient_id": "P-3", "age_years": "90", "notes": "a@b.co"},
            {"patient_id": "P-4", "age_years": "-1", "notes": "ok@demo.example"},
            {"patient_id": "P-5", "age_years": "55", "notes": "ok@example.com ok@example.org ok@example.net"},
            {"patient_id": "P-6", "age_years": "45", "notes": "123-45-678 415-555-019"},
            {"patient_id": "P-7", "  PATIENT_NAME ": "Hidden", "age_years": "40", "notes": "clean"},
            {"patient_id": "P-8", "age_years": "BAD-AGE", "notes": ""},
        ]
        for idx, rec in enumerate(cases, start=1):
            key_meta = PIIGuardrailScanner.prepare_csv_key_meta(list(rec.keys()))
            v_generic = PIIGuardrailScanner.scan_record("test.csv", idx, rec)
            v_csv = PIIGuardrailScanner.scan_csv_record("test.csv", idx, rec, key_meta)
            self.assertEqual(v_csv, v_generic, f"Parity mismatch on case {idx}: {rec}")

        # Verify exact boundary triggers
        self.assertEqual(len(PIIGuardrailScanner.scan_record("t.csv", 1, cases[0])), 1)  # 11-char SSN
        self.assertEqual(len(PIIGuardrailScanner.scan_record("t.csv", 2, cases[1])), 1)  # 12-char phone
        self.assertEqual(len(PIIGuardrailScanner.scan_record("t.csv", 3, cases[2])), 2)  # age 90 + a@b.co
        self.assertEqual(len(PIIGuardrailScanner.scan_record("t.csv", 4, cases[3])), 1)  # negative age, .example ok
        self.assertEqual(PIIGuardrailScanner.scan_record("t.csv", 5, cases[4]), [])      # reserved domains ok
        self.assertEqual(PIIGuardrailScanner.scan_record("t.csv", 6, cases[5]), [])      # near-miss lengths ok
        self.assertEqual(len(PIIGuardrailScanner.scan_record("t.csv", 7, cases[6])), 1)  # forbidden header

        # Verify scalar fast-skip and nested dict/list recursion
        nested = {
            "count": 42,
            "ratio": 1.25,
            "flag": True,
            "meta": {"contact_note": "reach a@b.co", "items": ["ok", "123-45-6789"]},
        }
        v_nested = PIIGuardrailScanner.scan_record("nested.json", "N-1", nested)
        self.assertEqual(len(v_nested), 2)


class TestIngestionEdgeCases(unittest.TestCase):
    """Verify strict vs quarantine behavior on corrupted, missing, or out-of-range inputs."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        generate_all_synthetic_data(output_dir=self.data_dir, seed=42)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_missing_file_raises_missing_data_source_error(self) -> None:
        (self.data_dir / "safety_sae_events.csv").unlink()
        with self.assertRaises(MissingDataSourceError):
            ingest_workspace_data(data_dir=self.data_dir, strict=True)

    def test_corrupted_ndjson_strict_and_quarantine_modes(self) -> None:
        pd_path = self.data_dir / "edc_protocol_deviations.ndjson"
        with pd_path.open("a", encoding="utf-8") as handle:
            handle.write("{corrupted_json_line\n")

        with self.assertRaises(MalformedRecordError):
            ingest_workspace_data(data_dir=self.data_dir, strict=True)

        conn, report = ingest_workspace_data(data_dir=self.data_dir, strict=False)
        try:
            self.assertEqual(report.quarantined_records, 1)
            q_rows = conn.execute("SELECT * FROM validation_quarantine").fetchall()
            self.assertEqual(len(q_rows), 1)
            self.assertEqual(q_rows[0]["error_type"], "MALFORMED_NDJSON")
        finally:
            conn.close()

    def test_pii_and_physiological_bounds_handling(self) -> None:
        pat_path = self.data_dir / "ehr_patients.csv"
        with pat_path.open("a", encoding="utf-8") as handle:
            handle.write("PAT-99901,94,M,NA,SITE-101,C34.1,Stage IV,1,1,30,NONE,1,50.0\n")

        with self.assertRaises(PIIComplianceViolationError):
            ingest_workspace_data(data_dir=self.data_dir, strict=True)

        # Remove PII row and inject impossible negative lab value instead
        shutil.rmtree(self.data_dir)
        generate_all_synthetic_data(output_dir=self.data_dir, seed=42)
        lab_path = self.data_dir / "lab_biomarkers.csv"
        text = lab_path.read_text(encoding="utf-8").splitlines()
        # Replace first data row eGFR with -15.0
        parts = text[1].split(",")
        parts[2] = "-15.0"
        text[1] = ",".join(parts)
        lab_path.write_text("\n".join(text) + "\n", encoding="utf-8")

        with self.assertRaises(ClinicalRangeValidationError):
            ingest_workspace_data(data_dir=self.data_dir, strict=True)

        conn, report = ingest_workspace_data(data_dir=self.data_dir, strict=False)
        try:
            self.assertEqual(report.quarantined_records, 1)
            row = conn.execute(
                "SELECT egfr_ml_min FROM patient_labs WHERE patient_id = ?", (parts[0],)
            ).fetchone()
            self.assertIsNone(row["egfr_ml_min"])
        finally:
            conn.close()


class TestTrialSelectionAndSignalEngines(unittest.TestCase):
    """Verify patient eligibility classifications, site disqualifications, and 32 signal rules."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.conn, cls.report = ingest_workspace_data(strict=True)
        cls.prelim_sel = run_trial_selection(cls.conn, persist_table=True)
        cls.sig_bundle = evaluate_signals_and_sites(cls.conn)
        risk_map = {s["site_id"]: s["risk_score"] for s in cls.sig_bundle["sites"]}
        cls.sel_bundle = run_trial_selection(
            cls.conn, site_risk_scores=risk_map, persist_table=True
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.conn.close()

    def test_all_32_rules_and_7_families_configured(self) -> None:
        cfg = load_signal_rules()
        self.assertEqual(len(cfg["rules"]), 32)
        families_in_cfg = {r["family"] for r in cfg["rules"]}
        self.assertEqual(families_in_cfg, set(SIGNAL_FAMILIES))
        triggered_families = {s["family"] for s in self.sig_bundle["signals"]}
        self.assertEqual(triggered_families, set(SIGNAL_FAMILIES))

    def test_three_priority_tiers_populated(self) -> None:
        tiers = self.sig_bundle["tier_counts"]
        self.assertGreater(tiers["Priority Action Required"], 0)
        self.assertGreater(tiers["Important Signals Next"], 0)
        self.assertGreater(tiers["Continue to Monitor"], 0)
        self.assertEqual(sum(tiers.values()), 16)

    def test_eligibility_classifications_and_site_safety_blocks(self) -> None:
        patients = self.sel_bundle["patients"]
        statuses = {p["primary_classification"] for p in patients}
        self.assertEqual(statuses, {"ELIGIBLE", "POTENTIAL_NEEDS_LABS", "INELIGIBLE"})

        for p in patients:
            if p["primary_classification"] == "ELIGIBLE":
                self.assertGreaterEqual(p["primary_match_score"], 85)
                # Paused SITE-109 and cold-chain excursion SITE-105 must never receive referrals
                self.assertNotIn(p["recommended_site_id"], ("SITE-105", "SITE-109"))
            elif p["primary_classification"] == "INELIGIBLE":
                self.assertLessEqual(p["primary_match_score"], 49)

    def test_unknown_study_raises_entity_not_found(self) -> None:
        with self.assertRaises(EntityNotFoundError):
            run_trial_selection(self.conn, study_id="NCT-DOES-NOT-EXIST", persist_table=False)

    def test_single_pass_pipeline_matches_two_pass_exactly(self) -> None:
        conn_fast, _ = ingest_workspace_data(strict=True)
        try:
            precompute = evaluate_selection_criteria_once(conn_fast)
            sig_fast = evaluate_signals_and_sites(
                conn_fast,
                local_elig_by_site=precompute.local_elig_by_site,
                persist_tables=False,
            )
            risk_map = {s["site_id"]: s["risk_score"] for s in sig_fast["sites"]}
            sel_fast = finalize_selection_with_site_risks(
                conn_fast,
                precompute,
                site_risk_scores=risk_map,
                persist_table=False,
                include_site_rankings=True,
            )

            self.assertEqual(sig_fast, self.sig_bundle)
            self.assertEqual(sel_fast["patients"], self.sel_bundle["patients"])
            self.assertEqual(sel_fast["evaluations"], self.sel_bundle["evaluations"])
            self.assertEqual(sel_fast["summary_by_study"], self.sel_bundle["summary_by_study"])

            # Verify filtered selection queries match run_trial_selection identically
            filter_cases = [
                {"study_id": "NCT-SYN-0101", "status_filter": "ELIGIBLE"},
                {"status_filter": "POTENTIAL_NEEDS_LABS", "region_filter": "NA"},
                {"site_id_filter": "SITE-101", "min_score": 85},
            ]
            for kwargs in filter_cases:
                expected = run_trial_selection(
                    self.conn,
                    site_risk_scores=risk_map,
                    persist_table=False,
                    **kwargs,
                )["evaluations"]
                actual = filter_selection_evaluations(
                    sel_fast["_all_evaluations"],
                    sel_fast["_patients_by_id"],
                    sel_fast["_best_study_by_patient"],
                    sel_fast["_valid_study_ids"],
                    **kwargs,
                )
                self.assertEqual(actual, expected, f"Filter parity mismatch for {kwargs}")
        finally:
            conn_fast.close()


class TestSQLSandboxingAndFeedbackState(unittest.TestCase):
    """Verify multi-layer read-only SQLite guardrails, HITL recovery, and reviewer data rights."""

    def setUp(self) -> None:
        self.conn, _ = ingest_workspace_data(strict=True)
        run_trial_selection(self.conn, persist_table=True)
        evaluate_signals_and_sites(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def test_destructive_and_injected_sql_blocked(self) -> None:
        unsafe_queries = [
            "DROP TABLE studies",
            "DELETE FROM sites WHERE site_id = 'SITE-101'",
            "UPDATE sites SET enrolled_count = 999",
            "INSERT INTO studies VALUES ('X','X','X','X','X',1,'v1',7,28)",
            "SELECT * FROM studies; DROP TABLE sites",
            "SELECT * FROM studies -- inline comment",
            "PRAGMA writable_schema = 1",
            "ATTACH DATABASE ':memory:' AS aux",
        ]
        for bad_sql in unsafe_queries:
            with self.assertRaises(SQLSafetyError, msg=f"Failed to block: {bad_sql}"):
                validate_readonly_sql(bad_sql)
            with self.assertRaises(SQLSafetyError):
                answer_analytics_question(self.conn, custom_sql=bad_sql)

    def test_authorizer_blocks_catalog_blob_dos_and_recursive_cte(self) -> None:
        dos_and_meta_queries = [
            "SELECT name FROM sqlite_master",
            "SELECT randomblob(1000000)",
            "SELECT zeroblob(1000000)",
            "WITH RECURSIVE cnt(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM cnt LIMIT 1000000) SELECT COUNT(*) FROM cnt",
            "SELECT " + ("1 + " * 1200) + "1",
        ]
        for query in dos_and_meta_queries:
            with self.assertRaises(SQLSafetyError, msg=f"Failed to block: {query[:60]}"):
                answer_analytics_question(self.conn, custom_sql=query)

    def test_valid_nl_and_custom_select_queries_succeed(self) -> None:
        res_nl = answer_analytics_question(
            self.conn, question="Compare SAE reporting cycle times across studies"
        )
        self.assertEqual(res_nl["intent_id"], "SAE_CYCLE_TIME_BY_STUDY")
        self.assertEqual(len(res_nl["rows"]), 4)
        self.assertIn("SELECT", res_nl["sql"])

        res_sql = answer_analytics_question(
            self.conn,
            custom_sql="SELECT study_id, protocol_code FROM studies ORDER BY study_id",
        )
        self.assertEqual(len(res_sql["rows"]), 4)

    def test_corrupted_feedback_state_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text("{not_valid_json", encoding="utf-8")
            with self.assertRaises(StateFileError):
                load_feedback_state(state_path, strict=True)
            recovered = load_feedback_state(state_path, strict=False)
            self.assertEqual(recovered, {})

            saved = record_signal_feedback(
                "SIG-1001", "ACCEPT", note="Verified with PI", state_file=state_path
            )
            self.assertEqual(saved["disposition"], "ACCEPT")
            reloaded = load_feedback_state(state_path, strict=True)
            self.assertEqual(reloaded["SIG-1001"]["note"], "Verified with PI")

    def test_reviewer_data_export_and_erasure_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            record_signal_feedback("SIG-1001", "ACCEPT", note="CRA-01 note", reviewer_id="CRA-01", state_file=state_path)
            record_signal_feedback("SIG-1002", "MODIFY", note="CRA-02 note", reviewer_id="CRA-02", state_file=state_path)

            exp_1 = export_reviewer_data("CRA-01", state_file=state_path)
            self.assertEqual(exp_1["record_count"], 1)
            self.assertEqual(exp_1["feedback_records"][0]["signal_id"], "SIG-1001")

            del_1 = delete_reviewer_data("CRA-01", state_file=state_path)
            self.assertEqual(del_1["purged_count"], 1)
            self.assertEqual(del_1["purged_signal_ids"], ["SIG-1001"])

            remaining = load_feedback_state(state_path, strict=True)
            self.assertNotIn("SIG-1001", remaining)
            self.assertIn("SIG-1002", remaining)


class TestHTTPServerCacheInvalidation(unittest.TestCase):
    """Verify HTTP bundle and SQLite caches stay coherent across mutations and external edits."""

    def test_bundle_and_ask_cache_invalidate_on_feedback_delete_and_external_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dash_file = tmp_path / "dashboard.html"
            state_file = tmp_path / "state.json"

            bundle = compile_workspace_bundle(state_file=str(state_file), strict=True)
            _, raw_json = write_dashboard_from_bundle(output_path=dash_file, bundle=bundle)

            server = create_workbench_http_server(
                "127.0.0.1",
                0,
                dash_file,
                state_file=str(state_file),
                api_token="ephemeral-cache-token",
                authenticated_reviewer="CRA-01",
                initial_bundle_json=raw_json,
            )
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            auth_headers = {
                "Content-Type": "application/json",
                "Authorization": "Bearer ephemeral-cache-token",
            }

            def _get_sig_bundle(sig_id: str) -> str:
                req = Request(
                    f"http://127.0.0.1:{port}/api/bundle",
                    headers={"Authorization": "Bearer ephemeral-cache-token"},
                    method="GET",
                )
                with urlopen(req, timeout=5) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                return next(s["disposition"] for s in payload["signals"] if s["signal_id"] == sig_id)

            def _get_sig_sql(sig_id: str) -> str:
                req = Request(
                    f"http://127.0.0.1:{port}/api/ask",
                    data=json.dumps(
                        {"sql": f"SELECT disposition FROM signals WHERE signal_id = '{sig_id}'"}
                    ).encode("utf-8"),
                    headers=auth_headers,
                    method="POST",
                )
                with urlopen(req, timeout=5) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                return payload["rows"][0]["disposition"]

            try:
                # 1. Initial cached state is PENDING_REVIEW
                self.assertEqual(_get_sig_bundle("SIG-1001"), "PENDING_REVIEW")
                self.assertEqual(_get_sig_sql("SIG-1001"), "PENDING_REVIEW")

                # 2. Mutate via POST /api/feedback -> both bundle and SQL cache update immediately
                req_fb = Request(
                    f"http://127.0.0.1:{port}/api/feedback",
                    data=json.dumps(
                        {
                            "signal_id": "SIG-1001",
                            "disposition": "ACCEPT",
                            "note": "Cache verification",
                            "reviewer_id": "CRA-01",
                        }
                    ).encode("utf-8"),
                    headers=auth_headers,
                    method="POST",
                )
                with urlopen(req_fb, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                self.assertEqual(_get_sig_bundle("SIG-1001"), "ACCEPT")
                self.assertEqual(_get_sig_sql("SIG-1001"), "ACCEPT")

                # 3. Mutate via POST /api/user-data/delete -> both revert to PENDING_REVIEW
                req_del = Request(
                    f"http://127.0.0.1:{port}/api/user-data/delete",
                    data=json.dumps({"reviewer_id": "CRA-01"}).encode("utf-8"),
                    headers=auth_headers,
                    method="POST",
                )
                with urlopen(req_del, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                self.assertEqual(_get_sig_bundle("SIG-1001"), "PENDING_REVIEW")
                self.assertEqual(_get_sig_sql("SIG-1001"), "PENDING_REVIEW")

                # 4. External state file mutation outside HTTP -> stat fingerprint invalidates cache
                record_signal_feedback(
                    "SIG-1001",
                    "DISMISS",
                    note="External CLI update",
                    reviewer_id="CRA-01",
                    state_file=state_file,
                )
                self.assertEqual(_get_sig_bundle("SIG-1001"), "DISMISS")
                self.assertEqual(_get_sig_sql("SIG-1001"), "DISMISS")
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
