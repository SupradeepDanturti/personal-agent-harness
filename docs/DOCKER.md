# Local Docker development and verification

The application now has a Docker build and Compose definition. Cloudflare is a
later deployment stage. The runtime image contains the real Python service,
built React UI and dedicated Chromium. It is separate from the optional
Docker-based tool for executing scripts, which remains disabled.

## Prerequisites and present verification boundary

Install a Docker-compatible CLI, a running Linux-container Engine and Docker
Compose v2 with support for optional `env_file` entries. On Windows, a normal
route is [Docker Desktop with its WSL 2 backend](https://docs.docker.com/desktop/setup/install/windows-install/).
Follow its current Windows/virtualization/WSL requirements. Installation, OS
feature changes and any required reboot are owner actions; this setup does not
perform them automatically. Run these checks before application setup:

```powershell
docker version
docker compose version
```

`docker version` must show both Client and Server information for Linux
containers. A CLI alone is insufficient. During implementation the development
terminal had no Docker command on PATH, so no successful local container run
is claimed. The PR workflow builds and exercises the images on its Linux Docker
runner. The workflow must actually pass before treating the container gate as
verified; source/config tests do not substitute for it.

## Run the real application locally

From the repository root:

```powershell
Copy-Item .docker.env.example .docker.env
# Fill .docker.env locally, using your editor.
docker compose build app
docker compose up -d app
docker compose ps
docker compose logs --tail 100 app
```

Set `OPENAI_API_KEY`, a random `HARNESS_TOKEN` of at least 24 characters and a
different stable random `CREDENTIAL_KEY` of at least 24 characters. Keep those
values out of chat, Git and image layers. Add Google/provider configuration only
for accounts you actually intend to connect. The real app refuses to start
without valid configuration; it never silently substitutes the synthetic test
runner. `.docker.env` is ignored by Git and rejected by the tracked-private-file
guard. `.dockerignore` allows only explicit build inputs, excluding live state,
credentials, local tools and local configuration.

Open `http://127.0.0.1:8787` and enter the harness access token. The Google OAuth
callback remains `http://127.0.0.1:8787/oauth/google/callback`; register that
exact URI with Google. The Compose mapping intentionally uses the same port
inside and outside the container. Stop an existing host service on that port
first. Do not change just one side of the mapping without updating the public
callback and Host configuration.

```powershell
docker compose stop app
docker compose start app
# Recreate the container while retaining its named state volume:
docker compose up -d --force-recreate app
```

The named `assistant-state` volume stores `/data/.assistant`, including SQLite,
encrypted credentials, workspace files and the dedicated browser profile.
Normal container recreation retains that volume. Deleting the volume loses
state; no cleanup command deleting application volumes is part of this guide.
Do not mount the everyday browser profile or the owner's home directory.

## Network and runtime boundaries

- Host mode still binds only `127.0.0.1`. Only explicit `CONTAINER_MODE=true`
  changes the internal listen address to `0.0.0.0`.
- Compose publishes the port on host `127.0.0.1` only. The app remains a local,
  single-owner service; this is not hosted login or multiuser authentication.
- Request guards retain exact loopback Host authorities. Optional
  `HARNESS_ALLOWED_HOSTS` is a comma-separated list of exact `host:port`
  authorities, accepted only in container mode. Wildcards, URLs and out-of-range
  ports are rejected. Forwarded host/protocol headers are not trusted.
- `MCP_TRUSTED_LOCAL_URLS` accepts comma-separated owner-configured endpoints;
  the MCP registry validates literal loopback URLs. Inside a container,
  `127.0.0.1` refers to that container, not the host. This does not turn arbitrary
  LAN addresses or remote servers into trusted local services.
- The image runs as UID/GID 10001 with a read-only root filesystem in Compose,
  a writable state volume, bounded temporary space and shared memory, dropped
  capabilities and resource limits. No Docker socket is mounted.
- Chromium is installed from the locked Playwright version and uses only the
  dedicated application profile. Headless navigation/approval policy remains
  enforced. A visible Linux browser window is not automatically displayed on
  the Windows desktop; manual-login/takeover support requires a separate
  deliberate design. No unauthenticated display/CDP/VNC port is published.
- Script execution stays off. Installing the app in Docker does not grant the
  model host-shell access or nested Docker administration. A future isolated
  execution broker must remain a separate authorization boundary.

The image pins Node/Python image-index digests and the uv bootstrap version;
application dependencies come from the committed npm/uv lockfiles. Refresh image
digests and OS packages deliberately with image scans and CI. A digest pin is
reproducibility, not proof that its contents have no vulnerabilities.

## Synthetic test profile

The `acceptance` build target adds test code and Node for the historical
Node-to-Python SQLite migration fixture. Neither the tests nor the synthetic
server entrypoint is present in the `runtime` image.

```powershell
docker compose --profile test build acceptance
docker compose --profile test run --rm acceptance
```

This profile receives no `.docker.env`, real credentials, network access or
application volume. It runs unit/component/ASGI tests, then all included
`ui_*acceptance.py` scenarios using Chromium, synthetic models and mocked
providers. Its temporary SQLite/workspace state is discarded with the test
container. The `/test-work` tmpfs is explicitly writable by the non-root UID.

For the complete deployment-image gate, run:

```powershell
python scripts/container-smoke.py --build
```

That host-side script requires Python 3 and a working Docker Engine. It builds
both image targets, checks that the runtime image is non-root and excludes test
fixtures/local config, runs the acceptance image without network, then starts a
uniquely named synthetic fixture container with its own named volume. It checks
API authentication and Host rejection, chat, exact procedure approval/replay,
scheduled completion, dedicated browser launch/private-destination rejection,
process restart and full container recreation over the same test volume. It
removes only fixture resources generated in that invocation. Existing app
containers and volumes are not targets of cleanup.

PR CI invokes this same image gate under the existing required backend check;
the frontend check also runs the local, Google and MCP UI scenarios. CI remains
triggered only by pull-request events, including updates to an open PR.

## Backup and restore

Container persistence is not a backup. Stop the app cleanly or use SQLite's
backup API when exporting live data; copying only `assistant.sqlite` while
ignoring WAL state is not a reliable backup. Preserve the credential encryption
key separately and test restoration into a separate empty volume. A rollback
to an app version that predates dcrypt envelopes also needs its matching
pre-upgrade vault backup. An automated production backup/restore policy is
still a separate delivery requirement.

## Cloudflare boundary

This local Docker setup is the execution/test baseline, not a deployment of the
current local disk to Workers. A final Workers-based deployment still needs its
durable state/lease/scheduler adapters, authenticated edge boundary, managed
runner/container wiring, artifact/credential persistence, external callback
configuration, observability and restoration checks. Temporary cloud container
disk cannot be the sole home of the SQLite database or credentials.
