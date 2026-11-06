"""stdlib unittest suite. run from the repo root: `uv run python -m unittest`.

config.py reads the location at import time, so pin test values here,
before any test module imports it. the api keys are cleared so no test
can reach a real service even if the shell running it has them set.
"""

import os

os.environ.update(
    HV_LAT="33.09",
    HV_LON="-97.05",
    HV_CITY="Highland Village",
    HV_TIMEZONE="America/Chicago",
)
for _key in (
    "ANTHROPIC_API_KEY",
    "GRIDSTATUS_API_KEY",
    "PUSHOVER_API_KEY",
    "PUSHOVER_USER_KEY",
    "GITHUB_TOKEN",
):
    os.environ.pop(_key, None)
