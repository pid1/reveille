#!/usr/bin/env bash
# Start reveille's build from a scheduler whose clock we actually control.
#
# The primary trigger is the Cloudflare Worker in infra/cloudflare/, which
# does exactly what this does on a cron at the edge. This is the same request
# from a shell: for a local box, a VPS, or a one-off manual run.
#
#   17 4 * * *  REVEILLE_DEPLOY_HOOK=https://... /path/to/trigger-build.sh
#
# The deploy hook URL is the credential -- anyone holding it can start a
# build, and nothing else. It replaces the fine-grained GitHub PAT this
# script used to carry, which could start any workflow in the repository and
# expired on a schedule of its own. The URL is passed to curl over stdin
# rather than on the command line so it does not show up in `ps`.
#
# Exit status is 0 on a successful request, 1 if every attempt failed -- so
# cron will mail you when the briefing did not get triggered.

set -euo pipefail

ATTEMPTS="${REVEILLE_ATTEMPTS:-5}"

: "${REVEILLE_DEPLOY_HOOK:?REVEILLE_DEPLOY_HOOK is not set}"

attempt=1
while [ "$attempt" -le "$ATTEMPTS" ]; do
  code=$(
    printf 'url = "%s"\n' "$REVEILLE_DEPLOY_HOOK" |
      curl -sS --config - \
        -o /dev/null -w '%{http_code}' \
        -X POST \
        --max-time 30
  ) || code=000

  case "$code" in
    2??)
      echo "build requested via deploy hook"
      exit 0
      ;;
    # A deleted, mistyped, or revoked hook will be rejected again in two
    # seconds. Retrying will not fix it.
    401 | 403 | 404)
      echo "HTTP ${code}: check REVEILLE_DEPLOY_HOOK -- the hook may have been deleted" >&2
      exit 1
      ;;
  esac

  echo "attempt ${attempt}/${ATTEMPTS}: HTTP ${code}" >&2
  if [ "$attempt" -lt "$ATTEMPTS" ]; then
    sleep $((2 ** attempt))
  fi
  attempt=$((attempt + 1))
done

echo "failed to request a build after ${ATTEMPTS} attempts" >&2
exit 1
