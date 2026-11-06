# molterm agent: a Claude Code-like TUI in Python (Pyodide, in restty), and server-side sessions with an emulated agent
#
#   make dev               build + run -> http://molterm-agent-dev.localhost, against the dev molterm server
#   make test              run the tests in the container
#   make <target> ENV=prod          against env/prod.env
APP     := molterm-agent
SERVICE := molterm-agent
ENV     ?= dev
export APP_TAG := $(ENV)
export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)
COMPOSE := docker compose -p $(APP)-$(ENV) --env-file env/$(ENV).env $(if $(wildcard env/$(ENV).local.env),--env-file env/$(ENV).local.env) -f docker-compose.yml $(if $(wildcard docker-compose.$(ENV).yml),-f docker-compose.$(ENV).yml)

.PHONY: help build up down restart logs ps login test dev

help: ## list targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "  %-9s %s\n",$$1,$$2}'

build: ## build the image for ENV
	$(COMPOSE) build

up: ## start ENV in the background
	$(COMPOSE) up -d
	@echo "-> http://$$(grep -h ^APP_HOST env/$(ENV).env $(wildcard env/$(ENV).local.env) | tail -1 | cut -d= -f2)"

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

dev: ## build + run the dev environment
	$(MAKE) build up ENV=dev
