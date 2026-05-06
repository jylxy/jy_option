import os
import subprocess
import sys
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class S1CliTest(unittest.TestCase):
    def test_list_outputs_registered_commands(self):
        proc = subprocess.run(
            [sys.executable, "scripts/s1_cli.py", "--list"],
            cwd=ROOT,
            check=True,
            text=True,
            encoding="utf-8",
            capture_output=True,
        )

        self.assertIn("analyze-backtest", proc.stdout)
        self.assertIn("report-s1", proc.stdout)
        self.assertIn("autoresearch", proc.stdout)

    def test_unknown_command_exits_nonzero(self):
        proc = subprocess.run(
            [sys.executable, "scripts/s1_cli.py", "not-a-command"],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
        )

        self.assertEqual(proc.returncode, 2)
        self.assertIn("未知命令", proc.stdout)


if __name__ == "__main__":
    unittest.main()
