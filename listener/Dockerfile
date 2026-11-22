# reveille nwws-oi listener. same base and uv pins as rally's image.
FROM python:3.15-slim@sha256:533b0b2f9b5d46fd097091cf826f27902f1d6c3bca714b6dc74551356aa97a51 AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY src/ src/
RUN uv sync --no-dev --frozen --no-editable

FROM python:3.15-slim@sha256:533b0b2f9b5d46fd097091cf826f27902f1d6c3bca714b6dc74551356aa97a51

WORKDIR /app
COPY --from=builder /app/.venv .venv
COPY tests/fixtures/ fixtures/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    STATE_DIR=/data

# not root. the mount point is created and chowned before VOLUME, because
# changes to a declared volume path later in the build are discarded.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data && chown 10001 /data

VOLUME /data
USER 10001

# fixtures ship in the image so a replay can check the rules on the nas
# without opening a second nwws connection:
#   docker exec reveille-listener python -m nwws_listener replay /app/fixtures/TORFWD-0.txt
CMD ["python", "-m", "nwws_listener"]
