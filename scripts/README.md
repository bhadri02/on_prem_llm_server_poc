# scripts/ — Deployment & Local Dev Reference

Scripts for deploying the on-prem LLM platform via **Docker Compose** (the
current, working deployment path — see `docs/DEPLOYMENT.md`) and for
running/testing it locally on Windows during development.

The platform previously had a Kubernetes/Helm deployment path (`deploy.sh`,
`build-and-push-all.ps1`, `test-connectivity.py`, `run-demos.ps1`) — those
scripts and this README's old Kubernetes-specific content have been removed.
Per `CLAUDE.md`: the Helm charts under `llm-platform/` are stale relative to
the current application code and would crash-loop most pods if deployed
as-is (missing required env vars, no Postgres dependency in the chart tree,
no `portal_ui` chart at all). If that path is ever revived, it needs the
charts brought back in sync first — see `CLAUDE.md`'s "Kubernetes / Helm
deployment" section for the specifics.

---

## On-prem server deployment (Docker Compose)

### `deploy-onprem.sh`

Interactive, self-contained bootstrap-and-deploy script for a fresh Linux
server. Automates exactly the manual steps documented in
`docs/DEPLOYMENT.md` — nothing more:

1. Checks prerequisites (Docker, Compose v2, git, openssl, curl, disk space).
2. Clones this repo (prompts for URL/branch/target directory) — designed to
   be copied to a fresh server and run standalone, before a checkout exists
   there.
3. Interactively builds `.env.prod` — prompts for every secret (Enter to
   auto-generate, or type your own) and the admin login password (required,
   typed twice).
4. Detects an NVIDIA GPU and offers to enable passthrough for Ollama
   (uncomments the existing block in `docker-compose.prod.yml`).
5. Builds all images, starts the stack, waits for every container to report
   healthy.
6. Runs the same verification checks documented in `docs/DEPLOYMENT.md`
   Step 5 (portal reachable, a real chat completion, the injection
   guardrail actually blocks a malicious prompt).
7. Prints a summary with the portal URL, admin login, and a reminder to
   review the "Security hardening before going live" checklist in
   `docs/DEPLOYMENT.md` before exposing this to real users.

```bash
chmod +x deploy-onprem.sh
./deploy-onprem.sh                # interactive
./deploy-onprem.sh --no-gpu       # skip GPU auto-detection
./deploy-onprem.sh --skip-build   # images already built/loaded another way
```

For the full manual walkthrough (what to do if you want to run each step
yourself, register a cloud model, day-2 operations, troubleshooting), see
**`docs/DEPLOYMENT.md`**.

### `deploy-onprem-existing-repo.sh`

Same script as above, minus the clone step — for when you've already
pulled the repo yourself (`git clone`/`git pull`, downloaded a zip, etc.)
and want to deploy that exact checkout in place instead of having
`deploy-onprem.sh` clone a fresh copy elsewhere. Run it **from inside**
the checkout:

```bash
cd /path/to/your/checkout
chmod +x scripts/deploy-onprem-existing-repo.sh
./scripts/deploy-onprem-existing-repo.sh                # interactive — pulls latest, rebuilds, restarts
./scripts/deploy-onprem-existing-repo.sh --no-pull      # deploy exactly what's on disk, don't touch git
./scripts/deploy-onprem-existing-repo.sh --no-gpu       # skip GPU auto-detection
./scripts/deploy-onprem-existing-repo.sh --skip-build   # images already built/loaded another way
```

This also doubles as the **redeploy script**: run it again any time new
commits land on origin and it will `git fetch`/`git pull --ff-only` the
current branch before rebuilding and restarting — that's the main reason
this script exists separately from `deploy-onprem.sh`. If the checkout has
local uncommitted changes when you re-run it (most commonly
`docker-compose.prod.yml`, left modified by a previous run's GPU-enable
step), it detects that, shows you what's dirty, and asks whether to stash
them for the pull and re-apply afterward, skip the pull and keep them, or
abort — it never silently discards local changes. Pass `--no-pull` to skip
git entirely and just rebuild/restart whatever is currently checked out.

Aside from step 2 (repo verification instead of cloning) and the new pull
step, this runs the identical `.env.prod` setup/GPU-detection/build/
start/verify/summary flow as `deploy-onprem.sh`. The two scripts
intentionally are NOT refactored into a shared library — `deploy-onprem.sh`'s
whole point is being a single file you can `curl` onto a server that has
no repo yet, so if you change the shared deploy logic, update both files.

---

## Local development (Windows)

### `run-local.ps1`

Starts all services natively (no Docker for the app services themselves —
just Redis/Postgres via `docker-compose.local.yml` and Ollama running
separately), each in its own terminal window. See `CLAUDE.md`'s "Running
the stack locally" section for the full prerequisite steps.

```powershell
.\scripts\run-local.ps1                      # start everything
.\scripts\run-local.ps1 -Service audit_store # start just one
.\scripts\run-local.ps1 -Stop                # kill everything
```

### `build-all-images.ps1`

Builds the Docker images (used for the Docker Compose path, not the native
`run-local.ps1` path). Build context is always the repo root so every
Dockerfile can reach `shared/`.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-all-images.ps1
powershell -ExecutionPolicy Bypass -File scripts\build-all-images.ps1 -Service api-gateway
powershell -ExecutionPolicy Bypass -File scripts\build-all-images.ps1 -Force
```

### `smoke-test.ps1`

Generic end-to-end smoke test against any already-running deployment
(local native, or Docker Compose) — not Kubernetes-specific, just hits
`-BaseUrl` directly with `-ApiKey`.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\smoke-test.ps1
powershell -ExecutionPolicy Bypass -File scripts\smoke-test.ps1 -BaseUrl http://localhost -ApiKey <your-key> -Verbose
```
