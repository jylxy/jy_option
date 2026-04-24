# Development Sync Workflow

This repository is the deployable codebase for the option backtest engine.

Local path:

```text
D:\工作实验\期权卖权策略相关研究\server_deploy
```

GitHub remote:

```text
https://github.com/jylxy/jy_option.git
```

Server path:

```text
/macro/home/lxy/jy_option
```

## Source Of Truth

GitHub is the version source of truth. The server should run code that has been
committed and pushed to GitHub whenever possible.

Generated data, logs, caches, and large backtest outputs should not be committed.

## Normal Change Flow

1. Work locally on a branch using the `codex/` prefix.
2. Commit code, configs, docs, and tests only.
3. Push the branch to GitHub.
4. On the server, fetch the same branch and run smoke/backtest jobs.

Local commands:

```powershell
cd D:\工作实验\期权卖权策略相关研究\server_deploy
git switch -c codex/<topic>
git add src config*.json docs tests .gitignore
git commit -m "<message>"
git push origin codex/<topic>
```

Server commands:

```bash
cd /macro/home/lxy/jy_option
git fetch origin
git switch codex/<topic>
python -m py_compile src/*.py
```

## Remotes

`origin` points to GitHub and is used for normal push/pull.

`prod` points to the server repository and is fetch-only from the local side.
Do not push directly to `prod`; use GitHub plus server-side fetch instead.

## Repository Hygiene

Keep these out of Git:

- Database files.
- Raw market data.
- Backtest output CSV/PNG/Markdown files.
- Runtime caches.
- Log files.
- One-off analysis notebooks or scratch scripts unless they become reusable tools.

When a scratch script becomes reusable, move it under `src/tools/` or `tools/`
with a short docstring and stable arguments.
