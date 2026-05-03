.PHONY: help test smoke git-sync docker-restore install clean fmt lint

PYTHON ?= python3
BIN := bin/general-backup

help:
	@echo "Targets:"
	@echo "  make test           Run unit tests"
	@echo "  make smoke          Run capture smoke test (writes to /tmp)"
	@echo "  make git-sync       Test git-sync phase semantics"
	@echo "  make docker-restore Round-trip restore test in Docker (requires docker)"
	@echo "  make install        Symlink bin/general-backup into /usr/local/bin"
	@echo "  make lint           Run pyflakes on lib/"
	@echo "  make clean          Remove build artifacts"

test:
	$(PYTHON) -m unittest discover -s tests -t . -v

smoke:
	bash tests/smoke-capture.sh

git-sync:
	bash tests/git-sync.sh

docker-restore:
	bash tests/restore-in-docker.sh

install:
	install -m 0755 $(BIN) /usr/local/bin/general-backup

lint:
	$(PYTHON) -m pyflakes lib/ bin/general-backup || true

clean:
	rm -rf dist build __pycache__ */__pycache__ */*/__pycache__
	rm -f general-backup-*.tar.zst checksums.sha256
