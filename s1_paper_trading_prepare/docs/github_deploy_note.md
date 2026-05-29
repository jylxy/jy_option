# GitHub deploy note

The company GitHub repository may be cleared and replaced by this S1
paper-trading preparation project.

Deploy only:

```text
s1_paper_trading_prepare/
```

Do not deploy the old research tree, historical outputs, side-strategy code,
or local data extracts.

Confirmed deployment target for this cleanup:

- Repository URL: `https://github.com/jylxy/jy_option.git`
- Target branch: `main`
- Payload: `s1_paper_trading_prepare/` plus root `.gitignore`

Expected clean-repo policy:

- Keep S1 config snapshots, S1 order-generation code, S1 diagnostics, and S1
  daily Toolkit data refresh code.
- Drop every non-S1 side-strategy branch, notebook, exploratory research
  output, physical compatibility file, and any code not required by the locked
  S1 mainline.
- Ignore `output/`, `data/`, `logs/`, credentials, local databases, and large
  CSV/parquet artifacts.
