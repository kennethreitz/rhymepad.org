.PHONY: run test

run:
	uv run granian --interface asgi --reload --port 8765 app:api

test:
	uv run pytest -q
