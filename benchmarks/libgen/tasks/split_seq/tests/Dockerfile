FROM python:3.12-slim
WORKDIR /tests
RUN python -m pip install --no-cache-dir jsonschema
COPY . /tests
RUN chmod +x /tests/test.sh
