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

**Done: environment, molecule parser, rendering as Braille, octants and terminal graphics, and the guided session in web and real terminals.** RDKit and Flask are installed
in a virtual environment inside the image (`/opt/venv`, on `PATH`), never on the host. `/health` reports the RDKit version.

Open http://molterm-dev.localhost/demo to type a SMILES or pick one of 23 samples and see it drawn in text, with the
`curl` command for the same drawing. http://molterm-dev.localhost also takes molblocks and sets the canvas size.

http://molterm-dev.localhost/terminal shows what the drawings are for: a medicinal chemistry session on 15 known compounds,
in a web terminal. Choose **Play demo** to watch it, **Walk me through it** to run each step yourself, or type `help` and
explore. Results are computed live with RDKit from structure alone, so there is no assay data and nothing about potency.
The terminal is simulated: it only runs the commands in `help`, and never a shell.

http://molterm-dev.localhost/compare shows both displays side by side, with its own play and walkthrough modes. Each
command goes to two real terminal emulators, [xterm.js](https://xtermjs.org) in the page: one gets Braille, the other RDKit's
image as the same iTerm2 or Sixel sequences a real terminal receives. xterm.js has no Kitty support, so that protocol isn't
offered there. Tables and plots are text in both. The library is fetched and checksummed when the image is built, and
served from `/vendor`, so changing it needs `make dev`, not only a reload.

http://molterm-dev.localhost/kitty runs the same session as `/terminal` in [restty](https://github.com/wiedymi/restty), a web
terminal built on Ghostty's parser, with drawings sent as real Kitty graphics. You type in the terminal itself, or choose
**Play demo**. [ghostty-web](https://github.com/coder/ghostty-web) was the first choice, but version 0.4.0 parses text only and
has no image drawing. restty and a font are fetched and checksummed when the image is built, because restty's default fonts come
from a CDN. Its page downloads 4.2 MB of script.

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

### Images in the terminal

Terminals with a graphics protocol can show RDKit's real drawing, so double bonds and stereo wedges appear properly, at about
the size of the text modes. The server cannot ask a terminal what it supports, so a small client does:

```sh
curl -s http://molterm-dev.localhost/molterm | sh -s -- 'C[C@H](N)C(=O)O'

# or keep it, with options: -m mode, -t theme, -c cols, -r rows, -v to say which mode it chose
curl -so molterm http://molterm-dev.localhost/molterm && chmod +x molterm
./molterm -v 'C[C@H](N)C(=O)O'

# the guided session: Enter runs each step, or type another command, and q quits. Afterwards the prompt is yours.
./molterm demo
# the same session, playing by itself
./molterm play
```

Either session draws with the best mode the terminal supports. What you type is sent to the server, which runs only the commands
in `help`; the client never runs it. The prompt has the terminal's plain line editing, so no Tab completion or history.

It chooses the first of these that the terminal supports. `-m` or `$MOLTERM_MODE` overrides the choice.

| mode | terminals | how it's chosen |
|---|---|---|
| `kitty` | Kitty, Ghostty | answers a Kitty graphics query, or `$TERM_PROGRAM`/`$TERM` names it |
| `iterm` | iTerm2, WezTerm, mintty | reports that name to an XTVERSION query, or `$TERM_PROGRAM` names it |
| `sixel` | xterm `-ti vt340`, others | device attributes include 4; inside tmux, `#{client_termfeatures}` includes `sixel` |
| `braille` | everything else | the fallback, and always used when output is piped |

The client also reads the terminal's background, to pick `theme` and blend Sixel's edges, and its cell size, because Sixel's
pixels are the screen's and can't be scaled. It needs only `sh`, `curl`, `stty` and `dd`.

The same modes work on `render.txt` directly, with `theme=light` on a light terminal, since `curl` alone can't ask. Molecules
without bonds, such as `[Na+].[Cl-]`, stay as text. Images can't be copied as text, and `color` does not apply to them.

| module | job |
|---|---|
| `server/molecule.py` | SMILES or molblock to a sanitized molecule with 2D coordinates. A molblock's own 2D layout is kept; 3D or all-zero coordinates are replaced. From an SDF, only the first record is read. |
| `server/braille.py` | Boolean bitmap to Braille characters, 2 dots wide and 4 tall per character. |
| `server/octant.py` | The same bitmap to Unicode octants. The table was checked entry by entry against the Unicode 16 names. |
| `server/render.py` | RDKit draws the bonds at 4x, which are reduced to dots and mapped to characters. Each cell takes the colour of the element covering most of it. Atom labels are real letters placed over them. |
| `server/samples.py` | The demo page's samples. Each one's formula, legibility and PubChem identity by InChIKey were checked. |
| `server/medchem.py` | The web terminal's commands and its 15 compounds, each matched to its name on PubChem by InChIKey. |
| `server/story.py` | The guided session. Each finding it states is checked against the real output by `tests/test_medchem.py`. |
| `server/graphics.py` | RDKit draws the whole molecule, labels included, as a transparent image cropped to whole columns. Kitty and iTerm2 get a PNG with only its width given, so the terminal keeps its shape with any cell size. Sixel is encoded here, at the terminal's real cell size. |
| `server/client/molterm.sh` | The terminal client, served at `/molterm`: asks the terminal what it supports and chooses a mode, then draws one molecule or runs the guided session. |
| `server/static/kitty.js` | The `/kitty` page: a line editor and the guided session, written for restty. It uses the same commands and story as the client, but not its code. |

| endpoint | input | result |
|---|---|---|
| `POST /api/v1/parse` | JSON `input`, optional `format` (`auto`, `smiles`, `molblock`) | `format`, `coords`, `name`, `smiles`, `formula`, `atoms`, `bonds` |
| `POST /api/v1/render` | as above, plus optional `cols`, `rows`, `mode` (`braille`, `octant`) | as above, plus `render` (`mode`, `text`, `ansi`, `colors`, `labels`, `rows`, `cols`, `bond_dots`, `cramped`) |
| `POST /api/v1/term` | JSON `line`, optional `selection` (the working set), `cols`, `display` (`text`, `kitty`, `iterm`, `sixel`), `cell_width` | `output` (text with ANSI colours, and images if asked), `selection`, `clear` |
| `GET /api/v1/term.txt` | query `line`, optional `selection` (comma-separated), `cols`, `display`, `theme`, and for Sixel `cell_width`, `background` | a first line of `<1 to clear, or 0> <selection>`, then the output |
| `GET /api/v1/story.txt` | | the guided session, one step per line: title, command, before and after, separated by tabs |
| `GET /api/v1/render.txt` | query `input`, `format`, `cols`, `rows`, `mode` (also `kitty`, `iterm`, `sixel`), `color=1`; for images `theme` (`dark`, `light`); for Sixel `cell_width` (pixels, 4-40), `background` (`RRGGBB`) | plain text, with ANSI colours when asked, or image escape sequences |
| `GET /molterm` | | the terminal client, set to call back to this server |

Failures return HTTP 400. The JSON endpoints give `{"error": {"code", "message"}}`, where `code` is one of `bad_request`,
`empty`, `format`, `syntax`, `chemistry` or `too_large`.

Limits: 500 atoms, 10,000 SMILES characters, 100,000 molblock characters, 10-240 columns, 5-120 rows. Layout time grows
steeply with size, so the atom cap keeps requests well inside gunicorn's 30 s worker timeout.

`rdkit` is pinned because the output depends on its exact layout and drawing.

Drawings use the smallest readable size, 6 dots per bond (phenol is 11 x 4 characters), and `cols` and `rows` only
set a maximum. A larger canvas does not enlarge them. In the text modes, stereo wedges are not drawn at this size, so
stereochemistry shows only in the SMILES.

Verified with `make dev` and `make test`, through Traefik with `curl`, and in Chrome: light and dark themes, error and
warning states, and phone width. Hard inputs at the atom cap parse in about 1 s or less.

The client and image modes were run in real terminals under a virtual display, and the screenshots checked by eye: Kitty,
xterm with Sixel on dark and light backgrounds, xterm without Sixel (Braille), tmux in xterm (Sixel) and tmux in Kitty (Braille).
`tests/test_client.py` runs the client under a fake terminal for the rest.

`molterm demo` was run the same way, against the dev server: in Kitty through `curl | sh`, with Enter, typed commands, `clear` and
`q`; in xterm with Sixel; and in xterm without it, where it fell back to Braille. `molterm play` has only run under the fake terminal.
`/kitty` was checked in Chrome: a full automated run at normal speed, typing with Backspace, Tab, Up and a mistake, the scrollback,
no console errors, and no page overflow at 320, 375 and 640 px.

Open issues:

- gunicorn logs `Control server error: [Errno 13] Permission denied: '/.gunicorn'` at startup because the container user
  has no home directory; requests are still served. `make prod` itself has not been run yet.
- A canvas too small for 6 dots per bond loses detail. The output is flagged `cramped` and the page warns, but the drawing
  is still returned.
- Atom labels are not protected from each other, so crowded molecules can overwrite a label.
- The renderer page keeps the last good drawing on screen beside an error message. The demo page clears it.
- Octant output has not been confirmed by eye in Ghostty beyond a single test of the characters.
- Image output has not been seen in Ghostty, iTerm2, WezTerm or mintty by the checks above. Kitty's protocol was seen in Kitty
  itself and in restty. iTerm2's was seen in xterm.js and checked against its documentation.
- restty (0.3.0, which calls itself early-release) keeps about 10 MB of decoded images. A larger image never shows, so `/kitty`
  draws at 10 pixels per column instead of 20, and each new image pushes out the oldest: in a long session, drawings far back in
  the scrollback become blank. A real terminal has much more room.
- xterm cuts a Sixel image off at 1000 x 1000 pixels by default, so `grid` is clipped in `molterm demo` there.
- Below 40 columns, as on a small phone, the commands still draw for 40, so a wide drawing can run past the edge of `/kitty`.
  Typing on a phone's on-screen keyboard has not been tried on a real device.
- xterm does not report its cell size by default, so Sixel assumes 10 px wide cells there, and the drawing may take more columns.
- Over a very slow connection, a terminal's replies can arrive after the client stops waiting (0.5 s). It then uses Braille,
  and the late replies can appear at the shell prompt. `-m` skips the questions.
- Text modes need a UTF-8 terminal: in another locale, Braille comes out garbled.
- `/terminal` draws in Braille only, since browsers usually have no octant font. On a phone, wide tables scroll sideways.
- In `/compare`, a page of images takes about 2 s to draw as iTerm2 (0.6 s as Sixel), and the text pane answers first.
- With only the width set, a tall drawing can use a few more rows than `rows` when a terminal's cell is taller than 1:2.

## Versioning

`VERSION` holds the semantic version. `make release` (or `BUMP=minor` / `BUMP=major`) bumps it, commits
`vX.Y.Z`, tags it and builds the prod image; then `git push --follow-tags` and `make prod`.

## Other targets

`make help` lists them: `logs`, `login`, `test`, `ps`, `down`, ...

Secrets go in `credentials_norepo.env` (created from `credentials_norepo.env.example` by `make init`, never committed).
