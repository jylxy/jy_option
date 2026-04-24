import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from config_loader import load_engine_config  # noqa: E402


class ConfigLoaderTest(unittest.TestCase):
    def test_missing_config_returns_defaults_copy(self):
        defaults = {"capital": 1, "fee": 2}
        result = load_engine_config("missing-config.json", defaults)
        self.assertEqual(result, defaults)
        self.assertIsNot(result, defaults)

    def test_json_config_overrides_defaults(self):
        defaults = {"capital": 1, "fee": 2}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"capital": 10}, f)
            self.assertEqual(load_engine_config(path, defaults), {"capital": 10, "fee": 2})

    def test_non_dict_config_is_ignored(self):
        defaults = {"capital": 1}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump([1, 2, 3], f)
            self.assertEqual(load_engine_config(path, defaults), defaults)


if __name__ == "__main__":
    unittest.main()
