# Molterm TUI Renderer — generated from the flask template on 2026-10-31
#
#   make dev               run the dev environment   -> http://molterm-dev.localhost
#   make prod              run VERSION in production -> http://molterm.localhost
#   make release [BUMP=minor|major]   bump VERSION, commit, tag vX.Y.Z, build the prod image
#   make screenshots       the README's hero shots, from the dev services
#   make agent-gif         the agent's demo GIF, from the dev agent
#   make <target> ENV=<env>           any target against env/<env>.env (+ docker-compose.<env>.yml if present)
APP     := molterm
SERVICE := molterm
VERSION := $(shell cat VERSION)
ENV     ?= dev
BUMP    ?= patch

# prod runs the image built for the current VERSION; other environments use a moving tag
export APP_TAG := $(if $(filter prod,$(ENV)),$(VERSION),$(ENV))
COMPOSE := docker compose -p $(APP)-$(ENV) --env-file env/$(ENV).env $(if $(wildcard env/$(ENV).local.env),--env-file env/$(ENV).local.env) -f docker-compose.yml $(if $(wildcard docker-compose.$(ENV).yml),-f docker-compose.$(ENV).yml)

.PHONY: help init build up down restart logs ps login test dev prod release screenshots agent-gif

help: ## list targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "  %-9s %s\n",$$1,$$2}'
	@echo "  ENV=$(ENV)  VERSION=$(VERSION)  APP_TAG=$(APP_TAG)"

init: ## create credentials_norepo.env from the example
	@test -f credentials_norepo.env || sed "s/change-me/$$(openssl rand -hex 16)/g" credentials_norepo.env.example > credentials_norepo.env

build: init ## build the image for ENV
	$(COMPOSE) build

up: init ## start ENV in the background
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
	$(COMPOSE) exec  $(SERVICE) bash

test: ## run the tests inside the ENV container
	$(COMPOSE) exec $(SERVICE) python -m pytest -q tests

dev: ## build + run the dev environment
	$(MAKE) build up ENV=dev

prod: ## build + run VERSION in production (clean tree only)
	@test -z "$$(git status --porcelain)" || (echo "commit or stash your changes first"; exit 1)
	$(MAKE) build up ENV=prod

release: ## bump VERSION, commit, tag, build the prod image (clean tree only)
	@test -z "$$(git status --porcelain)" || (echo "commit or stash your changes first"; exit 1)
	@v=$$(awk -F. -v b=$(BUMP) '{ if (b=="major") {$$1++; $$2=0; $$3=0} else if (b=="minor") {$$2++; $$3=0} else {$$3++}; print $$1"."$$2"."$$3 }' VERSION); \
	    echo $$v > VERSION; git commit -q -am "v$$v"; git tag -a v$$v -m "v$$v"; echo "released v$$v (git push --follow-tags to publish)"
	$(MAKE) build ENV=prod

PLAYWRIGHT := mcr.microsoft.com/playwright/python:v1.49.1-noble
screenshots: ## the README's hero shots in docs/images, from the running dev services (SHOTS="a.png b.png" for some)
	docker run --rm --network traefik --user $$(id -u):$$(id -g) -e HOME=/tmp -v $(CURDIR)/docs:/docs $(PLAYWRIGHT) \
		sh -c 'pip install -q --target /tmp/pw playwright==1.49.1 && PYTHONPATH=/tmp/pw python /docs/screenshots/shoot.py $(SHOTS)'

agent-gif: ## the agent's demo, docs/images/molterm-agent.gif, recorded from the running dev agent
	docker run --rm --network traefik --user $$(id -u):$$(id -g) -e HOME=/tmp -v $(CURDIR)/docs:/docs $(PLAYWRIGHT) \
		sh -c 'pip install -q --target /tmp/pw playwright==1.49.1 imageio-ffmpeg && PYTHONPATH=/tmp/pw python /docs/screenshots/agent_gif.py'
