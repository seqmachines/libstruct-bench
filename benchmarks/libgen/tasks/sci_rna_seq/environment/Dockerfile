FROM python:3.12-slim
WORKDIR /workspace
RUN apt-get update \
    && apt-get install -y --no-install-recommends antiword file ripgrep unzip \
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir jsonschema pymupdf pypdf docling openpyxl pillow
COPY fetch_input.py /workspace/fetch_input.py
RUN python /workspace/fetch_input.py
COPY rules.md /workspace/rules.md
COPY input_manifest.json /workspace/input_manifest.json
COPY libstruct_bench /workspace/libstruct_bench
COPY schemas /workspace/schemas
RUN mkdir -p /logs/artifacts
