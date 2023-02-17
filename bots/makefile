autograde: replay/replay.json
	@echo "====== BEGIN REPLAY HERE ======"
	@cat replay/replay.json
	@echo "====== END REPLAY HERE ======"

.PHONY:
assets:
	tar xfz engine.tar.gz
	mkdir -p bots replay maps
	cp team1.py team2.py bots

replay/replay.json: assets
	python3 run_game.py $(shell cat config.txt | xargs) team1 team2 -r -sb -sr > $@

.PHONY:
clean-replays:
	rm -rf replay

.PHONY:
clean: clean-replays
	rm -rf __pycache__ bots maps src run_game.py
