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

**Done: environment, molecule parser, and rendering as Braille, octants and Kitty graphics.** RDKit and Flask are installed
in a virtual environment inside the image (`/opt/venv`, on `PATH`), never on the host. `/health` reports the RDKit version.

Open http://molterm-dev.localhost/demo to type a SMILES or pick one of 23 samples and see it drawn in text, with the
`curl` command for the same drawing. http://molterm-dev.localhost also takes molblocks and sets the canvas size.

http://molterm-dev.localhost/terminal shows what the drawings are for: a medicinal chemistry session on 15 known compounds,
in a web terminal. Choose **Play demo** to watch it, **Walk me through it** to run each step yourself, or type `help` and
explore. Results are computed live with RDKit from structure alone, so there is no assay data and nothing about potency.
The terminal is simulated: it only runs the commands in `help`, and never a shell.

Or use a real terminal:

```sh
curl -G --data-urlencode 'input=c1ccccc1O' --data-urlencode 'mode=octant' --data-urlencode 'color=1' \
    http://molterm-dev.localhost/api/v1/render.txt
```

Two modes draw the same dots. `braille` (the default) works almost everywhere and looks speckled. `octant` is solid and
sharper, but needs Unicode 16 (2024) characters that many terminals and fonts lack. Ghostty draws them itself. The page
only offers Braille, since browsers usually have no octant font.

Colour shows elements on bonds as well as labels: O red, N blue, S yellow, P magenta, halogens green. It uses the terminal's
16 colours, so it follows a light or dark theme.

`mode=kitty` sends an image instead of text, for terminals with the Kitty graphics protocol, such as Kitty and Ghostty:

```sh
curl -G --data-urlencode 'input=C[C@H](N)C(=O)O' --data-urlencode 'mode=kitty' \
    http://molterm-dev.localhost/api/v1/render.txt
```

It takes about the same space as the text modes, and double bonds and stereo wedges show properly. The image has a
transparent background and the server cannot detect the terminal's, so add `theme=light` on a light terminal; the default
is `dark`. Molecules without bonds, such as `[Na+].[Cl-]`, stay as text. The output is not text, so it cannot be copied,
and `color` does not apply.

| module | job |
|---|---|
| `server/molecule.py` | SMILES or molblock to a sanitized molecule with 2D coordinates. A molblock's own 2D layout is kept; 3D or all-zero coordinates are replaced. From an SDF, only the first record is read. |
| `server/braille.py` | Boolean bitmap to Braille characters, 2 dots wide and 4 tall per character. |
| `server/octant.py` | The same bitmap to Unicode octants. The table was checked entry by entry against the Unicode 16 names. |
| `server/render.py` | RDKit draws the bonds at 4x, which are reduced to dots and mapped to characters. Each cell takes the colour of the element covering most of it. Atom labels are real letters placed over them. |
| `server/samples.py` | The demo page's samples. Each one's formula, legibility and PubChem identity by InChIKey were checked. |
| `server/medchem.py` | The web terminal's commands and its 15 compounds, each matched to its name on PubChem by InChIKey. |
| `server/story.py` | The guided session. Each finding it states is checked against the real output by `tests/test_medchem.py`. |
| `server/kitty.py` | RDKit draws the whole molecule, labels included, as a transparent PNG cropped to whole columns. It is sent in Kitty escape sequences that give only the width, so the terminal keeps its shape with any cell size. |

| endpoint | input | result |
|---|---|---|
| `POST /api/v1/parse` | JSON `input`, optional `format` (`auto`, `smiles`, `molblock`) | `format`, `coords`, `name`, `smiles`, `formula`, `atoms`, `bonds` |
| `POST /api/v1/render` | as above, plus optional `cols`, `rows`, `mode` (`braille`, `octant`) | as above, plus `render` (`mode`, `text`, `ansi`, `colors`, `labels`, `rows`, `cols`, `bond_dots`, `cramped`) |
| `POST /api/v1/term` | JSON `line`, optional `selection` (the working set), `cols` | `output` (text with ANSI colours), `selection`, `clear` |
| `GET /api/v1/render.txt` | query `input`, `format`, `cols`, `rows`, `mode` (also `kitty`), `color=1`, `theme` (`dark`, `light`; `kitty` only) | plain text, with ANSI colours when asked, or Kitty escape sequences |

Failures return HTTP 400. The JSON endpoints give `{"error": {"code", "message"}}`, where `code` is one of `bad_request`,
`empty`, `format`, `syntax`, `chemistry` or `too_large`.

Limits: 500 atoms, 10,000 SMILES characters, 100,000 molblock characters, 10-240 columns, 5-120 rows. Layout time grows
steeply with size, so the atom cap keeps requests well inside gunicorn's 30 s worker timeout.

`rdkit` is pinned because the output depends on its exact layout and drawing.

Drawings use the smallest readable size, 6 dots per bond (phenol is 11 x 4 characters), and `cols` and `rows` only
set a maximum. A larger canvas does not enlarge them. In the text modes, stereo wedges are not drawn at this size, so
stereochemistry shows only in the SMILES.

Verified with `make dev` and `make test`, through Traefik with `curl`, and in Chrome: light and dark themes, error and
warning states, and phone width. Hard inputs at the atom cap parse in about 1 s or less. The Kitty escape sequences were
checked against the protocol's spec and Ghostty's source, and decoded back to the PNG, which was checked by eye. A 480-atom
molecule on the largest canvas takes about 2 s and 390 KB on the dev server.

Open issues:

- gunicorn logs `Control server error: [Errno 13] Permission denied: '/.gunicorn'` at startup because the container user
  has no home directory; requests are still served. `make prod` itself has not been run yet.
- A canvas too small for 6 dots per bond loses detail. The output is flagged `cramped` and the page warns, but the drawing
  is still returned.
- Atom labels are not protected from each other, so crowded molecules can overwrite a label.
- The renderer page keeps the last good drawing on screen beside an error message. The demo page clears it.
- Octant output has not been confirmed by eye in Ghostty beyond a single test of the characters.
- Kitty output has not been seen in a real terminal yet.
- The web terminal draws in Braille only, since browsers usually have no octant font. On a phone, wide tables scroll sideways.
- Kitty output does not pass through tmux, which needs its own wrapping and `allow-passthrough`. Terminals without the
  protocol may show nothing or stray characters.
- With only the width set, a tall drawing can use a few more rows than `rows` when a terminal's cell is taller than 1:2.

## Versioning

`VERSION` holds the semantic version. `make release` (or `BUMP=minor` / `BUMP=major`) bumps it, commits
`vX.Y.Z`, tags it and builds the prod image; then `git push --follow-tags` and `make prod`.

## Other targets

`make help` lists them: `logs`, `login`, `test`, `ps`, `down`, ...

Secrets go in `credentials_norepo.env` (created from `credentials_norepo.env.example` by `make init`, never committed).
