FROM python:3.12-slim
WORKDIR /workspace
RUN apt-get update \
    && apt-get install -y --no-install-recommends file \
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir pymupdf pypdf docling openpyxl pillow
COPY fetch_input.py /workspace/fetch_input.py
COPY protocol_processing_rules.md /workspace/protocol_processing_rules.md
