# AGENTS.md

## Sadr

### Build and test
- The repo has no test suite. The check is that the page builds: `uv sync --locked`, then `HV_LAT=32.78 HV_LON=-96.80 HV_CITY=Test HV_TIMEZONE=America/Chicago uv run python build.py` with `ANTHROPIC_API_KEY`, `PUSHOVER_API_KEY` and `PUSHOVER_USER_KEY` unset. It passes when it exits 0 and prints `[build] wrote .../dist/index.html`.
- Without those keys, `build.py` only reads public APIs, skips the AI summary and sends no push notification. With both `PUSHOVER_*` keys set, it sends a push, so never set them in a check.
- `node --check infra/cloudflare/worker.js` checks that the trigger Worker parses.
- Never run `wrangler deploy` without `--dry-run`, or `scripts/trigger-build.sh`. Both start a production deploy.

### Toolchain
- uv manages the Python project. `pyproject.toml` sets `exclude-newer = "7 days"` under `[tool.uv]`, and `uv.lock` is committed.
- uv installs a managed CPython for the `3.15` in `.python-version`. The uv release decides the patch version: uv 0.13.1 installs 3.15.0, and uv 0.13.5 installs 3.15.1.

### Deploys outside CI
- Cloudflare Workers Builds builds and deploys the `reveille` Worker on every push to main and on the deploy hook. The README gives the build command as `uv sync --frozen && uv run python build.py` and the deploy command as `npx wrangler deploy`. Cloudflare's own uv and npm run them, at versions set in the Cloudflare dashboard.
- Cloudflare Workers Builds deploys the `reveille-trigger` Worker from root directory `/infra/cloudflare`. docs/scheduling.md gives the deploy command as `npx wrangler deploy --tag "$WORKERS_CI_COMMIT_SHA"`.
