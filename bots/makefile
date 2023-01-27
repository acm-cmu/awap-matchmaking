.NOTPARALLEL:
autograde: replays
	@echo "====== BEGIN REPLAY HERE ======"
	@cat replay/replay.json
	@echo "====== END REPLAY HERE ======"

assets:
	tar xfz engine.tar.gz
	mkdir -p bots replay maps
	cp team1.py team2.py bots

replays: assets
	python3 run_game.py $(shell cat config.txt | xargs) team1 team2 -r > replay/replay.json

clean-replays:
	rm -rf replay

clean: clean-replays
	rm -rf __pycache__ bots maps src run_game.py
