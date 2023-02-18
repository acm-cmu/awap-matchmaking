autograde: test-red test-blue out.awap23r 
	@echo "====== BEGIN REPLAY HERE ======"
	@cat out.awap23r
	@echo ""
	@echo "====== END REPLAY HERE ======"

test-red:
	@python3 team1_*.py > /dev/null 2> /dev/null || echo "===== RED BROKEN ====="

test-blue:
	@python3 team2_*.py > /dev/null 2> /dev/null || echo "===== BLUE BROKEN ====="

.PHONY:
assets:
	tar xfz engine.tar.gz
	mkdir -p bots replays maps
	cp team1_*.py team2_*.py bots

out.awap23r: assets
	python3 run_game.py -f config.json -sb -sr
	find replays -type f -name "*.awap23r" -exec mv {} $@ \;

.PHONY:
clean-replays:
	rm -rf replays

.PHONY:
clean: clean-replays
	rm -rf __pycache__ bots maps src run_game.py
