"""Engine configuration loading helpers."""

import json
import os


def load_engine_config(path, defaults, logger=None):
    """Load JSON config and merge it over default parameters."""
    merged = dict(defaults)
    if not os.path.exists(path):
        return merged
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if isinstance(cfg, dict):
            merged.update(cfg)
    except (json.JSONDecodeError, OSError) as exc:
        if logger is not None:
            logger.warning("config.json 读取失败: %s", exc)
    return merged
