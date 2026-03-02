ROOT?=.
VENV=$(ROOT)/.venv
PIP=$(VENV)/bin/pip

.PHONY: setup run-server run-worker run-retention check clean

setup:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

run-server:
	scripts/run_pi.sh server

run-worker:
	scripts/run_pi.sh worker

run-retention:
	scripts/run_pi.sh retention

check:
	scripts/run_pi.sh check

clean:
	rm -rf $(VENV)
