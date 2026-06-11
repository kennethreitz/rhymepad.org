FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Dependencies only (rhymepad is a single-module app, not a packaged project)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app.py rhymes.py ./
COPY static ./static

# Pre-warm NLTK data so g2p-en and WordNet never download at request time
# (--no-sync: use the env built above; don't try to install the project)
RUN uv run --no-sync python -c "import nltk;\
[nltk.download(p, quiet=True) for p in ('averaged_perceptron_tagger','averaged_perceptron_tagger_eng','cmudict','wordnet','omw-1.4')]" || true

EXPOSE 8000

CMD ["uv", "run", "--no-sync", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
