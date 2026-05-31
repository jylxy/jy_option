"""Path helpers for the S1 paper-trading preparation workspace."""

from __future__ import annotations

from pathlib import Path
import sys
import types


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent
SERVER_DEPLOY_DIR = REPO_ROOT / "server_deploy"
SERVER_DEPLOY_SRC = SERVER_DEPLOY_DIR / "src"

DEFAULT_PAPER_CONFIG = PROJECT_DIR / "configs" / "s1_paper_mainline.json"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output"
DEFAULT_DATA_DIR = PROJECT_DIR / "data"


def _install_noop_module(module_name: str, function_name: str) -> None:
    if module_name in sys.modules:
        return
    module = types.ModuleType(module_name)

    def _noop(*args, **kwargs):  # noqa: ANN001, ANN202
        return 0

    setattr(module, function_name, _noop)
    sys.modules[module_name] = module


def install_s1_only_import_guards() -> None:
    """Keep server imports narrow when Toolkit loads optional side modules."""
    _install_noop_module("ed1_event_strategy", "queue_ed1_opens")
    _install_noop_module("ed2_event_long_strategy", "queue_ed2_opens")


def ensure_server_deploy_importable() -> None:
    install_s1_only_import_guards()
    for path in reversed((SERVER_DEPLOY_SRC, SERVER_DEPLOY_DIR)):
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)


def resolve_path(path: str | Path | None, *, default: Path | None = None) -> Path:
    if path is None:
        if default is None:
            raise ValueError("path and default cannot both be None")
        return default.resolve()
    value = Path(path)
    if value.is_absolute():
        return value.resolve()
    return (REPO_ROOT / value).resolve()
