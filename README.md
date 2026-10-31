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

**Steps 1 to 3 done: environment, molecule parser, Braille rendering.** RDKit and Flask are installed in a virtual
environment inside the image (`/opt/venv`, on `PATH`), never on the host. `/health` reports the RDKit version.

Open http://molterm-dev.localhost to render a molecule, or use a terminal:

```sh
curl -G --data-urlencode 'input=c1ccccc1O' --data-urlencode 'cols=60' --data-urlencode 'color=1' \
    http://molterm-dev.localhost/api/v1/render.txt
```

| module | job |
|---|---|
| `server/molecule.py` | SMILES or molblock to a sanitized molecule with 2D coordinates. A molblock's own 2D layout is kept; 3D or all-zero coordinates are replaced. From an SDF, only the first record is read. |
| `server/braille.py` | Boolean bitmap to Braille characters, 2 dots wide and 4 tall per character. |
| `server/render.py` | RDKit draws the bonds at 4x, which are reduced to dots and mapped to Braille. Atom labels are real letters placed over them. |

| endpoint | input | result |
|---|---|---|
| `POST /api/v1/parse` | JSON `input`, optional `format` (`auto`, `smiles`, `molblock`) | `format`, `coords`, `name`, `smiles`, `formula`, `atoms`, `bonds` |
| `POST /api/v1/render` | as above, plus optional `cols`, `rows` | as above, plus `render` (`text`, `ansi`, `labels`, `rows`, `cols`, `bond_dots`, `cramped`) |
| `GET /api/v1/render.txt` | query `input`, `format`, `cols`, `rows`, `color=1` | plain text, with ANSI colours when asked |

Failures return HTTP 400. The JSON endpoints give `{"error": {"code", "message"}}`, where `code` is one of `bad_request`,
`empty`, `format`, `syntax`, `chemistry` or `too_large`.

Limits: 500 atoms, 10,000 SMILES characters, 100,000 molblock characters, 10-240 columns, 5-120 rows. Layout time grows
steeply with size, so the atom cap keeps requests well inside gunicorn's 30 s worker timeout.

`rdkit` is pinned because the Braille output depends on its exact layout and drawing.

Verified with `make dev` and `make test`, through Traefik with `curl`, and in Chrome: light and dark themes, error and
warning states, and phone width. Hard inputs at the atom cap parse in about 1 s or less.

Open issues:

- gunicorn logs `Control server error: [Errno 13] Permission denied: '/.gunicorn'` at startup because the container user
  has no home directory; requests are still served. `make prod` itself has not been run yet.
- Below about 6 dots per bond, double bonds and zigzags stop being readable. The output is flagged `cramped` and the page
  warns, but the drawing is still returned.
- Atom labels are not protected from each other, so crowded molecules can overwrite a label.
- The page keeps the last good drawing on screen beside an error message.

## Versioning

`VERSION` holds the semantic version. `make release` (or `BUMP=minor` / `BUMP=major`) bumps it, commits
`vX.Y.Z`, tags it and builds the prod image; then `git push --follow-tags` and `make prod`.

## Other targets

`make help` lists them: `logs`, `login`, `test`, `ps`, `down`, ...

Secrets go in `credentials_norepo.env` (created from `credentials_norepo.env.example` by `make init`, never committed).
