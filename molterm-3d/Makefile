# molterm-3d: Mol* drawing structures in 3D, in terminals and on a web page
#
#   make dev               build + run against the dev molterm server -> http://molterm-3d-dev.localhost
#   make test              run the tests in the container
#   make view INPUT=1crn   view a structure in this terminal
#   make <target> ENV=prod          against env/prod.env
APP     := molterm-3d
SERVICE := molterm-3d
ENV     ?= dev
export APP_TAG := $(ENV)
export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)
COMPOSE := docker compose -p $(APP)-$(ENV) --env-file env/$(ENV).env -f docker-compose.yml $(if $(wildcard docker-compose.$(ENV).yml),-f docker-compose.$(ENV).yml)

.PHONY: help build up down restart logs ps login test view dev

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

test: ## run the tests inside the ENV container (a second X display, beside the server's)
	$(COMPOSE) exec $(SERVICE) xvfb-run -a node --test --test-concurrency=1 'tests/*.test.js'

dev: ## build + run the dev environment
	$(MAKE) build up ENV=dev

view: ## view INPUT (a SMILES, a PDB id, ...) in this terminal, through ENV's server
	MOLTERM3D_URL=http://$$(grep ^APP_HOST env/$(ENV).env | cut -d= -f2) sh client/molterm3d.sh '$(INPUT)'
