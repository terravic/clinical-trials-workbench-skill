"""Clinical Trials System of Action and Selection Workbench core package.

Provides shared relative path resolution, configuration helpers, structured
logging configuration, and explicit domain exception classes.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR: Path = PROJECT_ROOT / "assets" / "synthetic_data"
DEFAULT_SCHEMAS_DIR: Path = PROJECT_ROOT / "assets" / "schemas"
DEFAULT_TEMPLATES_DIR: Path = PROJECT_ROOT / "assets" / "templates"
DEFAULT_STATE_FILE: Path = PROJECT_ROOT / "assets" / "workbench_feedback_state.json"
DEFAULT_DASHBOARD_PATH: Path = PROJECT_ROOT / "dashboard.html"


class WorkbenchError(Exception):
    """Base exception for all Clinical Trials Workbench errors."""

    exit_code: int = 1


class MissingDataSourceError(WorkbenchError):
    """Raised when a required synthetic or operational source file is absent."""

    exit_code: int = 2


class MalformedRecordError(WorkbenchError):
    """Raised when a CSV, JSON, or NDJSON input violates structural formatting."""

    exit_code: int = 2


class PIIComplianceViolationError(WorkbenchError):
    """Raised when input records violate HCLS / HIPAA Safe Harbor guardrails."""

    exit_code: int = 3


class ClinicalRangeValidationError(WorkbenchError):
    """Raised when physiological lab or clinical metrics fall outside valid bounds."""

    exit_code: int = 2


class SQLSafetyError(WorkbenchError):
    """Raised when an analytics query attempts non-read-only or unsafe SQL execution."""

    exit_code: int = 4


class EntityNotFoundError(WorkbenchError):
    """Raised when a requested study, site, patient, or signal ID does not exist."""

    exit_code: int = 5


class StateFileError(WorkbenchError):
    """Raised when the human-in-the-loop feedback state file is unreadable in strict mode."""

    exit_code: int = 6


def resolve_data_dir(override: str | Path | None = None) -> Path:
    """Resolve the active data directory using parameter, environment, or default."""
    if override is not None:
        path = Path(override)
        return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()
    env_val = os.environ.get("CTW_DATA_DIR")
    if env_val:
        path = Path(env_val)
        return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()
    return DEFAULT_DATA_DIR


def resolve_state_file(override: str | Path | None = None) -> Path:
    """Resolve the human-in-the-loop feedback state file path."""
    if override is not None:
        path = Path(override)
        return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()
    env_val = os.environ.get("CTW_STATE_FILE")
    if env_val:
        path = Path(env_val)
        return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()
    return DEFAULT_STATE_FILE


def configure_logging(level: str | None = None) -> logging.Logger:
    """Configure deterministic, clean structured stderr logging."""
    resolved_level = (level or os.environ.get("CTW_LOG_LEVEL") or "WARNING").upper()
    numeric_level = getattr(logging, resolved_level, logging.WARNING)
    logger = logging.getLogger("clinical_trials_workbench")
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(numeric_level)
    return logger
