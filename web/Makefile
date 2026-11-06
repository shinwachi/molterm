# molterm in the browser: RDKit as WebAssembly and molterm's renderer in JavaScript, on a static page
#
#   make dev               build + run the page -> http://molterm-web-dev.localhost
#   make test              the renderer's tests, in Node, against the server's own output
#   make fixtures          record that output again from the dev molterm server (site/check.json)
#   make export            the whole site as plain files in data/site, to put on any static host
#   make <target> ENV=prod          against env/prod.env
APP     := molterm-web
SERVICE := molterm-web
ENV     ?= dev
export APP_TAG := $(ENV)
COMPOSE := docker compose -p $(APP)-$(ENV) --env-file env/$(ENV).env $(if $(wildcard env/$(ENV).local.env),--env-file env/$(ENV).local.env) -f docker-compose.yml $(if $(wildcard docker-compose.$(ENV).yml),-f docker-compose.$(ENV).yml)
TESTER  := docker build -q --target test -t $(APP)-test -f Dockerfile .. >/dev/null && docker run --rm --user $$(id -u):$$(id -g) -v $(CURDIR)/site:/web/site -v $(CURDIR)/tests:/web/tests $(APP)-test
MOLTERM_URL ?= http://molterm-dev-molterm-1:8910

.PHONY: help build up down restart logs ps test fixtures export dev

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

test: ## the renderer's tests, in Node
	$(TESTER) node --test tests/molterm.test.mjs

fixtures: ## record the molterm server's renderings of the samples again (MOLTERM_URL, the dev server by default)
	docker build -q --target test -t $(APP)-test -f Dockerfile .. >/dev/null
	docker run --rm --user $$(id -u):$$(id -g) --network traefik -e MOLTERM_URL=$(MOLTERM_URL) \
		-v $(CURDIR)/site:/web/site -v $(CURDIR)/tests:/web/tests $(APP)-test node tests/fixtures.mjs

export: ## the site as plain files in data/site (the page and its libraries), for any static host
	docker build -q -t $(APP)-export -f Dockerfile .. >/dev/null
	rm -rf data/site && mkdir -p data/site
	docker run --rm --user $$(id -u):$$(id -g) -v $(CURDIR)/data/site:/out $(APP)-export sh -c 'cp -r /srv/site/. /out/ && cp -r /srv/vendor /out/vendor'
	@du -sh data/site

dev: ## build + run the dev environment
	$(MAKE) build up ENV=dev
