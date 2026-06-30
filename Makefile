.PHONY: run test sync nltk-data

sync:
	uv sync

# g2p-en tags parts of speech with NLTK; without this data it downloads
# at first request. Pull it once, up front; the stamp skips re-downloads.
NLTK_PACKAGES := averaged_perceptron_tagger averaged_perceptron_tagger_eng cmudict
NLTK_STAMP := .nltk-data.stamp

nltk-data: $(NLTK_STAMP)

# Needs nltk installed first, so sync before downloading.
$(NLTK_STAMP): | sync
	uv run python -c "import nltk; [nltk.download(p) for p in '$(NLTK_PACKAGES)'.split()]"
	@touch $@

run: sync nltk-data
	uv run granian --interface asgi --reload --port 8765 app:api

test: sync nltk-data
	uv run pytest -q
