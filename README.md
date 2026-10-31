# Molterm TUI Renderer

TUI renderer for chemical structures using RDKit and advanced text graphics

Created 2026-10-31 from a Flask project template.

## Environments

| | command | URL | image |
|---|---|---|---|
| dev | `make dev` | http://molterm-dev.localhost | `molterm:dev`, source bind-mounted, live reload |
| prod | `make prod` | http://molterm.localhost | `molterm:$(cat VERSION)` |
| other | `make up ENV=staging` | from `env/staging.env` | `molterm:staging` |

Each environment is its own compose project (`molterm-dev`, `molterm-prod`), so they can run side by side.
Add an environment by creating `env/<name>.env` (and optionally `docker-compose.<name>.yml` for overrides).

## Status

**Step 1 of 3 done: environment.** RDKit and Flask are installed in a virtual environment inside the image
(`/opt/venv`, on `PATH`), never on the host. `/health` reports the RDKit version.

Verified with `make dev` and `make test`: the container runs from the venv, RDKit parses SMILES, rejects bad input and
draws a PNG with `MolDraw2DCairo`, and `/health` answers through Traefik.

Next: the molecule parser, then the Braille/Unicode mapping.

Open issues: the page at `/` is still the template's starter page; it gets replaced when there is something to render.

## Versioning

`VERSION` holds the semantic version. `make release` (or `BUMP=minor` / `BUMP=major`) bumps it, commits
`vX.Y.Z`, tags it and builds the prod image; then `git push --follow-tags` and `make prod`.

## Other targets

`make help` lists them: `logs`, `login`, `test`, `ps`, `down`, ...

Secrets go in `credentials_norepo.env` (created from `credentials_norepo.env.example` by `make init`, never committed).
