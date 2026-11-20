FROM node:22-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436

ARG CODEX_VERSION

RUN test -n "$CODEX_VERSION" \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       bash ca-certificates file jq poppler-utils procps python-is-python3 \
       python3 python3-openpyxl ripgrep unzip \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global "@openai/codex@${CODEX_VERSION}"

COPY container-run-worker.sh /usr/local/bin/libgen-run-capability-worker

RUN mkdir -p /run/secrets /root/.codex \
    && chmod 0555 /usr/local/bin/libgen-run-capability-worker

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/libgen-run-capability-worker"]
