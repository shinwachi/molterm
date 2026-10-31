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

**Step 2 of 3 done: molecule parser.** RDKit and Flask are installed in a virtual environment inside the image
(`/opt/venv`, on `PATH`), never on the host. `/health` reports the RDKit version.

`server/molecule.py` turns SMILES or a molblock into a sanitized molecule with 2D coordinates. A molblock's own 2D layout
is kept; 3D or all-zero coordinates are replaced by a computed layout. From an SDF, only the first record is read.

```sh
curl -H 'Content-Type: application/json' -d '{"input": "c1ccccc1O phenol"}' http://molterm-dev.localhost/api/v1/parse
```

`format` is optional (`auto`, `smiles` or `molblock`). A success returns `format`, `coords`, `name`, `smiles`, `formula`,
`atoms` and `bonds`. A failure returns HTTP 400 with `{"error": {"code", "message"}}`, where `code` is one of
`bad_request`, `empty`, `format`, `syntax`, `chemistry` or `too_large`.

Limits: 500 atoms, 10,000 SMILES characters, 100,000 molblock characters. Layout time grows steeply with size, so the
atom cap keeps requests well inside gunicorn's 30 s worker timeout.

Verified with `make dev` and `make test`: the container runs from the venv, RDKit draws a PNG with `MolDraw2DCairo`, the
parser's accepted and rejected inputs are covered by tests, and `/api/v1/parse` answers through Traefik. Hard inputs at
the cap (fused rings, lattices, cages, long ring chains) parse in about 1 s or less.

Next: the Braille/Unicode mapping.

Open issues: the page at `/` is still the template's starter page; it gets replaced when there is something to render.
`rdkit` has no upper version limit, so pin it before the renderer depends on its exact drawing output.
gunicorn logs `Control server error: [Errno 13] Permission denied: '/.gunicorn'` at startup because the container user
has no home directory; requests are still served. `make prod` itself has not been run yet.

## Versioning

`VERSION` holds the semantic version. `make release` (or `BUMP=minor` / `BUMP=major`) bumps it, commits
`vX.Y.Z`, tags it and builds the prod image; then `git push --follow-tags` and `make prod`.

## Other targets

`make help` lists them: `logs`, `login`, `test`, `ps`, `down`, ...

Secrets go in `credentials_norepo.env` (created from `credentials_norepo.env.example` by `make init`, never committed).
