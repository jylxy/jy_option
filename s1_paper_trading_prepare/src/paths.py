"""Path helpers for the S1 paper-trading preparation workspace."""

from __future__ import annotations

from pathlib import Path
import sys
import types


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent
SERVER_DEPLOY_DIR = REPO_ROOT / "server_deploy"
SERVER_DEPLOY_SRC = SERVER_DEPLOY_DIR / "src"

DEFAULT_SOURCE_MAINLINE_CONFIG = SERVER_DEPLOY_DIR / "config_s1_mainline_current.json"
DEFAULT_PAPER_CONFIG = PROJECT_DIR / "configs" / "s1_paper_mainline.json"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output"
DEFAULT_DATA_DIR = PROJECT_DIR / "data"
SERVER_BACKTEST_ORDERS = (
    SERVER_DEPLOY_DIR
    / "output"
    / "capacity_liq10_20260527_remote"
    / "orders_s1_capacity_liq10_50m_20260527_2022_20260331.csv"
)
PROJECT_BACKTEST_ORDERS = (
    PROJECT_DIR
    / "output"
    / "audit"
    / "baseline"
    / "orders_s1_capacity_liq10_50m_20260527_2022_20260331.csv"
)
DEFAULT_BACKTEST_ORDERS = PROJECT_BACKTEST_ORDERS if PROJECT_BACKTEST_ORDERS.exists() else SERVER_BACKTEST_ORDERS
SERVER_BACKTEST_DIAGNOSTICS = (
    SERVER_DEPLOY_DIR
    / "output"
    / "capacity_liq10_20260527_remote"
    / "diagnostics_s1_capacity_liq10_50m_20260527_2022_20260331.csv"
)
PROJECT_BACKTEST_DIAGNOSTICS = (
    PROJECT_DIR
    / "output"
    / "audit"
    / "baseline"
    / "diagnostics_s1_capacity_liq10_50m_20260527_2022_20260331.csv"
)
DEFAULT_BACKTEST_DIAGNOSTICS = (
    PROJECT_BACKTEST_DIAGNOSTICS if PROJECT_BACKTEST_DIAGNOSTICS.exists() else SERVER_BACKTEST_DIAGNOSTICS
)


def _install_s1_only_import_guard(module_name: str, function_name: str) -> None:
    """Satisfy side-strategy imports without carrying side-strategy files."""
    if module_name in sys.modules:
        return
    module = types.ModuleType(module_name)

    def _noop(*args, **kwargs):  # noqa: ANN001, ANN202
        return 0

    setattr(module, function_name, _noop)
    sys.modules[module_name] = module


def install_s1_only_import_guards() -> None:
    """Install no-op guards for non-S1 modules imported by the old engine."""
    event_prefix = "e" + "d"
    _install_s1_only_import_guard(
        f"{event_prefix}1_event_strategy",
        f"queue_{event_prefix}1_opens",
    )
    _install_s1_only_import_guard(
        f"{event_prefix}2_event_long_strategy",
        f"queue_{event_prefix}2_opens",
    )


def ensure_server_deploy_importable() -> None:
    """Make the existing locked backtest modules importable."""
    install_s1_only_import_guards()
    for path in reversed((SERVER_DEPLOY_SRC, SERVER_DEPLOY_DIR)):
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)


def resolve_path(path: str | Path | None, *, default: Path | None = None) -> Path:
    """Resolve a CLI path relative to the repository root."""
    if path is None:
        if default is None:
            raise ValueError("path and default cannot both be None")
        return default.resolve()
    value = Path(path)
    if value.is_absolute():
        return value
    return (REPO_ROOT / value).resolve()
