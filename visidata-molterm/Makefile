# molterm for VisiData: a plugin that draws molecules in spreadsheets, and a demo of it
#
#   make dev               build + run the demo against the dev molterm server -> http://molterm-vd-dev.localhost
#   make test              run the plugin's tests in the container
#   make record            record the demo's session again
#   make vd FILE=demo/drugs.csv   run VisiData with the plugin in this terminal, inside the container
#   make <target> ENV=prod          against env/prod.env
APP     := molterm-vd
SERVICE := molterm-vd
ENV     ?= dev
export APP_TAG := $(ENV)
export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)
COMPOSE := docker compose -p $(APP)-$(ENV) --env-file env/$(ENV).env -f docker-compose.yml $(if $(wildcard docker-compose.$(ENV).yml),-f docker-compose.$(ENV).yml)
FILE    ?= demo/drugs.csv

.PHONY: help build up down restart logs ps login test record vd dev

help: ## list targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "  %-9s %s\n",$$1,$$2}'

build: ## build the image for ENV
	@mkdir -p data
	$(COMPOSE) build

up: ## start ENV in the background
	@mkdir -p data
	$(COMPOSE) up -d
	@echo "-> http://$$(grep ^APP_HOST env/$(ENV).env | cut -d= -f2)"

down: ## stop and remove ENV's containers
	$(COMPOSE) down

restart: ## restart ENV's containers
	$(COMPOSE) restart

logs: ## follow ENV's logs
	$(COMPOSE) logs -f

ps: ## show ENV's containers
	$(COMPOSE) ps

login: ## open a shell in the ENV container
	$(COMPOSE) exec $(SERVICE) bash

test: ## run the tests inside the ENV container
	$(COMPOSE) exec $(SERVICE) python -m pytest -q tests

record: ## record the demo's session again
	$(COMPOSE) exec $(SERVICE) python demo/record.py

vd: ## run VisiData with the plugin on FILE, in this terminal
	$(COMPOSE) exec -it -e TERM=$(if $(filter xterm-ghostty,$(TERM)),ghostty,$(TERM)) -e TERM_PROGRAM=$(TERM_PROGRAM) $(SERVICE) vd --config demo/visidatarc $(FILE)

dev: ## build + run the dev environment
	$(MAKE) build up ENV=dev
