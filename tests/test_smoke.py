"""Fast CLI smoke tests verifying repository structure, documentation standards, and exit codes."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest

from scripts import PROJECT_ROOT


class TestRepositorySmoke(unittest.TestCase):
    """Verify modular file layout, SKILL.md metadata, PRD sections, and CLI subcommands."""

    def test_required_project_structure_exists(self) -> None:
        required_paths = [
            "SKILL.md",
            "README.md",
            "DESIGN.md",
            "requirements.txt",
            "documents/prd.md",
            "references/clinical_ontology_and_signals.md",
            "references/eligibility_criteria_guide.md",
            "references/data_dictionary.md",
            "assets/images/clinical_trials_workbench_overview.png",
            "assets/schemas/signal_rules_config.json",
            "assets/schemas/workspace_bundle_schema.json",
            "assets/templates/monitoring_visit_agenda.md.tpl",
            "assets/templates/site_followup_email.txt.tpl",
            "assets/templates/issue_capa_record.json.tpl",
            "scripts/workbench_cli.py",
            "scripts/generate_synthetic_data.py",
            "scripts/data_ingestor.py",
            "scripts/trial_selector.py",
            "scripts/signal_engine.py",
            "scripts/action_drafter.py",
            "scripts/analytics_assistant.py",
            "scripts/build_dashboard.py",
        ]
        for rel in required_paths:
            full = PROJECT_ROOT / rel
            self.assertTrue(full.exists(), f"Missing required file: {rel}")

        readme_text = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("assets/images/clinical_trials_workbench_overview.png", readme_text)

    def test_skill_md_frontmatter_and_prd_sections(self) -> None:
        skill_text = (PROJECT_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(skill_text.startswith("---\n"))
        self.assertIn("name: clinical-trials-workbench", skill_text)
        self.assertIn("description:", skill_text)

        prd_text = (PROJECT_ROOT / "documents" / "prd.md").read_text(encoding="utf-8")
        for required_heading in (
            "User Stories and Scope",
            "Technical Architecture",
            "Functional Requirements",
            "Non-Functional Requirements",
            "Error Handling and Edge Cases Matrix",
        ):
            self.assertIn(required_heading, prd_text)

    def test_cli_smoke_commands_exit_zero(self) -> None:
        cli = str(PROJECT_ROOT / "scripts" / "workbench_cli.py")

        help_proc = subprocess.run(
            [sys.executable, cli, "--help"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(help_proc.returncode, 0, help_proc.stderr)

        port_proc = subprocess.run(
            [sys.executable, cli, "portfolio", "--cra", "CRA-01", "--format", "json"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(port_proc.returncode, 0, port_proc.stderr)
        payload = json.loads(port_proc.stdout)
        self.assertIn("portfolio_summary", payload)
        self.assertGreater(len(payload["sites"]), 0)


if __name__ == "__main__":
    unittest.main()
