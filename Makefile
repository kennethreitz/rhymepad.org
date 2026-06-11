.PHONY: run test

run:
	uv run uvicorn app:app --reload --port 8765

test:
	uv run pytest -q
