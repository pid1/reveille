#!/usr/bin/env bash
# Start reveille's build from a scheduler whose clock we actually control.
#
# GitHub's `schedule` event is best-effort and it has been deferring this
# repository's whole cron queue by hours (see docs/scheduling.md), so the
# crons in build.yml are the fallback path. This is the primary one: it hits
# the workflow_dispatch API directly, which is dispatched immediately.
#
# Run it from cron on a machine you own, at the time you actually want the
# briefing built:
#
#   17 4 * * *  REVEILLE_DISPATCH_TOKEN=ghp_... /path/to/trigger-build.sh
#
# Auth is a fine-grained personal access token scoped to this repository
# with a single permission, "Actions: read and write". That is enough to
# start a workflow_dispatch run and does not grant reading secrets, writing
# contents, or pushing. The token is passed to curl over stdin rather than
# on the command line so it does not show up in `ps`.
#
# Exit status is 0 on a successful dispatch, 1 if every attempt failed --
# so cron will mail you when the briefing did not get triggered.

set -euo pipefail

REPO="${REVEILLE_REPO:-pid1/reveille}"
WORKFLOW="${REVEILLE_WORKFLOW:-build.yml}"
REF="${REVEILLE_REF:-main}"
ATTEMPTS="${REVEILLE_ATTEMPTS:-5}"

: "${REVEILLE_DISPATCH_TOKEN:?REVEILLE_DISPATCH_TOKEN is not set}"

url="https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches"

attempt=1
while [ "$attempt" -le "$ATTEMPTS" ]; do
  code=$(
    printf 'header = "Authorization: Bearer %s"\n' "$REVEILLE_DISPATCH_TOKEN" |
      curl -sS --config - \
        -o /dev/null -w '%{http_code}' \
        -X POST "$url" \
        -H 'Accept: application/vnd.github+json' \
        -H 'X-GitHub-Api-Version: 2022-11-28' \
        --max-time 30 \
        -d "{\"ref\":\"${REF}\"}"
  ) || code=000

  if [ "$code" = "204" ]; then
    echo "dispatched ${WORKFLOW} on ${REPO}@${REF}"
    exit 0
  fi

  # 401/403/404 are configuration problems -- a bad, expired, or
  # under-scoped token, or the wrong repo. Retrying will not fix them.
  case "$code" in
    401 | 403 | 404)
      echo "HTTP ${code}: check REVEILLE_DISPATCH_TOKEN and its Actions permission on ${REPO}" >&2
      exit 1
      ;;
  esac

  echo "attempt ${attempt}/${ATTEMPTS}: HTTP ${code}" >&2
  if [ "$attempt" -lt "$ATTEMPTS" ]; then
    sleep $((2 ** attempt))
  fi
  attempt=$((attempt + 1))
done

echo "failed to dispatch ${WORKFLOW} after ${ATTEMPTS} attempts" >&2
exit 1
