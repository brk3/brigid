IMAGE    ?= ghcr.io/brk3/brigid
TAG      ?= $(shell git describe --tags --always --dirty)
PLATFORM ?= linux/amd64

.PHONY: venv test build clean

venv:
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt

test:
	.venv/bin/python -m pytest -q

build:
	docker build --platform $(PLATFORM) -t $(IMAGE):$(TAG) .
	@got=$$(docker image inspect --format '{{.Os}}/{{.Architecture}}' $(IMAGE):$(TAG)); \
	  [ "$$got" = "$(PLATFORM)" ] || { echo "ERROR: built $$got, wanted $(PLATFORM)"; exit 1; }
	@echo "built $(IMAGE):$(TAG) for $(PLATFORM)"

clean:
	rm -rf .venv .pytest_cache __pycache__
