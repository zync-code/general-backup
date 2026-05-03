.PHONY: help test smoke install clean fmt lint

PYTHON ?= python3
BIN := bin/general-backup

help:
	@echo "Targets:"
	@echo "  make test     Run unit tests"
	@echo "  make smoke    Run capture smoke test (writes to /tmp)"
	@echo "  make install  Symlink bin/general-backup into /usr/local/bin"
	@echo "  make lint     Run pyflakes on lib/"
	@echo "  make clean    Remove build artifacts"

test:
	$(PYTHON) -m unittest discover -s tests -t . -v

smoke:
	bash tests/smoke-capture.sh

install:
	install -m 0755 $(BIN) /usr/local/bin/general-backup

lint:
	$(PYTHON) -m pyflakes lib/ bin/general-backup || true

clean:
	rm -rf dist build __pycache__ */__pycache__ */*/__pycache__
	rm -f general-backup-*.tar.zst checksums.sha256
