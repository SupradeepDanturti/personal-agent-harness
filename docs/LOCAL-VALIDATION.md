# Local validation and pull request policy

## November 12, 2026

Validated the active Python backend and React frontend. The retired TypeScript
backend under `legacy/` is reference material and is not part of the application.
No real Google, Slack, Mastodon, Spotify, Shopify or Home Assistant connection
was authorized or exercised. Connector unit tests use mocked transports only.

| Check | Result |
| --- | --- |
| Backend unit/component/ASGI suite | 91 run: 90 passed, 1 Windows symlink skip |
| Python source compilation | Passed |
| Frontend TypeScript check | Passed |
| Frontend production build | Passed; Vite reports a bundle-size warning |
| Tracked credential/runtime-file guard | Passed |
| Git whitespace validation | Passed |
| Chromium/HTTP acceptance | Blocked locally by restricted socket-pair creation; added to CI |
| Live GPT-6 Luna call | Not verified in this session; network access is restricted |
| Account connections | Excluded from live testing as requested |

The terminal cannot resolve external hosts: GitHub, npm and PyPI lookups fail,
and direct DNS queries return access denied. Git metadata is read-only under the
task's permission profile, so the CLI cannot create a branch or commit here.
These are execution-policy restrictions, not evidence of an invalid GitHub token.
The browser can reach GitHub separately.

The existing GitHub Actions run also failed before starting either job. GitHub
reports: "The job was not started because your account is locked due to a billing
issue." This is separate from the local terminal restriction. Required checks
will keep merges blocked until the account billing issue is resolved and the
workflow completes successfully. Billing settings were not changed.

The npm lockfile installed successfully from the existing local cache. The
backend suite used installed Python 3.14.2 packages: Agents SDK 0.17.2,
OpenAI 2.37.0, FastAPI 0.136.1, Uvicorn 0.47.0, HTTPX 0.28.1 and
cryptography 48.0.0. This is **not** a successful clean locked Python install:
the lockfile pins OpenAI 2.26.0 and downloads were blocked. CI uses the locked
workspace installation rather than these local packages.

The `.dev.vars.txt` file discovered locally is now ignored along with
`.dev.vars` and its variants; `.dev.vars.example` remains versioned. No secret
values were printed, staged or uploaded. The application expects the exact name
`.dev.vars`; Windows may hide the `.txt` extension in Explorer.

## Reproduce the checks

Run from the repository root in an environment with network and Git write access:

```sh
uv sync --all-packages --locked
npm --prefix frontend ci
python scripts/check-private-files.py
npm --prefix frontend run check
npm --prefix frontend run build
uv run --locked --directory backend python -m compileall -q src
uv run --locked --directory backend python -m unittest discover -s tests -t . -v
uv run --locked --directory backend playwright install chromium
uv run --locked --directory backend python -u -m tests.ui_acceptance
uv build --package personal-assistant-harness-backend --out-dir dist/backend
```

UI acceptance starts its own server with a deterministic injected model and
temporary SQLite/workspace files. It loads no `.dev.vars`, denies external
browser requests, and visits all 15 screens other than Connections. It exercises
chat, memory correction/lineage/forgetting, file writing/reading/listing, goals,
history search, people, workflow completion, procedure approval/replay, scheduled
execution, inbox results, theme persistence and server restart persistence.
This verifies application wiring, not live model reasoning or real account access.

## Main branch policy

All changes go through a non-main branch and a pull request. Never commit or push
directly to `main`. GitHub settings, rather than a local Git hook, enforce this.

Two independent active rulesets target the default branch:

1. **main — required PR and CI**: requires a pull request, resolved conversations,
   an up-to-date branch and successful GitHub Actions `frontend` and `backend`
   checks. Blocks deletion and force pushes. Its bypass list is empty.
2. **main — Supradeep controls merges**: restricts branch updates and requires
   one review, code-owner review and dismissal of stale approvals. Only
   `SupradeepDanturti` can bypass this second rule, and only through a pull request.

Only Supradeep can perform the final PR merge, including approving his own
changes through that explicit merge decision. This avoids GitHub's restriction
on authors approving their own PRs. The independent first rule still prevents
direct pushes and CI bypass by the owner. Other contributors cannot merge on
their own, even after an approval. The CODEOWNERS file assigns every path to
`@SupradeepDanturti` once this PR is merged into the base branch.

Repository administrators can edit rulesets; these settings cannot prevent the
repository owner from deliberately changing the policy later. No PR is merged
as part of this validation work.
