FROM python:3.12-slim
WORKDIR /workspace
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir pymupdf
COPY fetch_input.py /workspace/fetch_input.py
