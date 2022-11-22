all: setup-tango-configs setup-docker-configs

.PHONY: setup-tango-configs
setup-tango-configs:
	@echo "Creating default .env"
	cp -n ./.env.template ./.env
	echo "Creating default Tango/config.py"
	cp -n ./Tango/config.template.py ./Tango/config.py

.PHONY: setup-docker-configs
setup-docker-configs:
#	echo "Creating default nginx/app.conf"
#	cp -n ./nginx/app.conf.template ./nginx/app.conf
#	echo "Creating default nginx/no-ssl-app.conf"
#	cp -n ./nginx/no-ssl-app.conf.template ./nginx/no-ssl-app.conf
