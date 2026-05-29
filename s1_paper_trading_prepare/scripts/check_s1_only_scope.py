from __future__ import annotations

from pathlib import Path
import sys


PROJECT_DIR = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"data", "output", "logs", "__pycache__", ".git", ".pytest_cache"}


def disallowed_tokens() -> list[str]:
    suffixes = tuple(str(index) for index in range(2, 5))
    tokens = []
    for suffix in suffixes:
        tokens.extend([f"S{suffix}", f"s{suffix}_", f"enable_s{suffix}"])
    tokens.extend(["E" + "D1", "E" + "D2", "R" + "S1", "B" + "20"])
    return tokens


def iter_project_files() -> list[Path]:
    files = []
    for path in PROJECT_DIR.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = set(path.relative_to(PROJECT_DIR).parts[:-1])
        if rel_parts & SKIP_DIRS:
            continue
        if path.name == Path(__file__).name:
            continue
        files.append(path)
    return files


def main() -> int:
    issues = []
    tokens = disallowed_tokens()
    for path in iter_project_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8-sig", errors="ignore")
        for token in tokens:
            if token in text:
                issues.append((path.relative_to(PROJECT_DIR), token))

    if issues:
        for rel, token in issues:
            print(f"SCOPE_ISSUE token={token} path={rel}")
        return 1
    print("S1_ONLY_SCOPE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
