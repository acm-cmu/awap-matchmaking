autograde: replay/replay.awap23r 
	@echo "====== BEGIN REPLAY HERE ======"
	@cat replay/replay.awap23r
	@echo "====== END REPLAY HERE ======"

.PHONY:
assets:
	tar xfz engine.tar.gz
	mkdir -p bots replay maps
	cp team1.py team2.py bots

replay/replay.awap23r: assets
	python3 run_game.py -f config.json -sb -sr > /dev/null
	find replay -f "*.awap23r" -exec mv {} replay/replay.awap23r \;
	mv replay/team1-team2.json team

.PHONY:
clean-replays:
	rm -rf replay

.PHONY:
clean: clean-replays
	rm -rf __pycache__ bots maps src run_game.py
