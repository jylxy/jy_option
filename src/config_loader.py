"""Engine configuration loading helpers."""

import json
import os


def _load_config_tree(path, logger=None, seen=None):
    path = os.path.abspath(path)
    seen = seen or set()
    if path in seen:
        if logger is not None:
            logger.warning("config extends cycle detected: %s", path)
        return {}
    seen.add(path)

    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        if logger is not None:
            logger.warning("config read failed: %s", exc)
        return {}

    if not isinstance(cfg, dict):
        return {}

    parent = cfg.pop("extends", None)
    merged = {}
    if parent:
        parents = parent if isinstance(parent, list) else [parent]
        for parent_path in parents:
            if not parent_path:
                continue
            parent_path = str(parent_path)
            if not os.path.isabs(parent_path):
                parent_path = os.path.join(os.path.dirname(path), parent_path)
            merged.update(_load_config_tree(parent_path, logger=logger, seen=seen))

    merged.update(cfg)
    return merged


def load_engine_config(path, defaults, logger=None):
    """Load JSON config and merge it over default parameters."""
    merged = dict(defaults)
    if not os.path.exists(path):
        return merged

    cfg = _load_config_tree(path, logger=logger)
    if isinstance(cfg, dict):
        merged.update(cfg)
    return merged
