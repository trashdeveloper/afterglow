# Afterglow one-command entry points. `make doctor` first.
.PHONY: doctor demo test ci tui install

doctor:
	./afterglow doctor

demo:
	./afterglow demo

test:
	python3 -m unittest discover -s tests

ci:
	bash scripts/ci.sh

tui:
	cd tui-rs && cargo build --release

install:
	bash scripts/install.sh
