# Contributing

Start with the [setup instructions](README.md#run-locally). Run the commands
below from the repository root.

## Check a change

For backend changes, run the unit and component suite:

```bash
uv run --directory backend python -m unittest discover -s tests -t . -v
```

For frontend changes, check TypeScript and build the production bundle:

```bash
npm --prefix frontend run check
npm --prefix frontend run build
```

These checks do not establish live model, browser or provider connectivity.
The smoke tests documented in the [backend guide](backend/README.md) and
[project README](README.md#dedicated-browser) use real services; model calls
may incur charges. Report which checks you actually ran when sharing a change.

## Keep related files together

- When changing Python dependencies, commit `backend/pyproject.toml` and the
  root `uv.lock` together.
- When changing frontend dependencies, commit `frontend/package.json` and
  `frontend/package-lock.json` together.
- After updating documentation embedded in the interactive project guide,
  regenerate it with
  `uv run --directory backend python ../scripts/build-project-guide.py`.
- Keep credentials and local assistant data out of commits. Use
  `.dev.vars.example` to document configuration without secret values.

Active backend code lives in `backend/src/harness`; `legacy/typescript` is
retired reference code. See the [backend](backend/README.md) and
[frontend](frontend/README.md) guides for directory maps and development details.
