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

# Swarm swaps traffic only once the new container is actually serving —
# boot includes model warmup, so give it a generous start period
HEALTHCHECK --interval=10s --timeout=3s --start-period=180s --retries=3 \
  CMD ["python3", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status==200 else 1)"]

CMD ["uv", "run", "--no-sync", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
