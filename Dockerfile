FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY pyproject.toml ./

RUN touch README.md \
    && mkdir -p src/grok_search \
    && touch src/grok_search/__init__.py \
    && pip install --no-cache-dir --editable .

COPY src ./src

ENTRYPOINT ["grok-search"]
