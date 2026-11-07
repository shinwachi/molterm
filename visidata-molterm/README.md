# molterm for VisiData

A plugin for [VisiData](https://www.visidata.org/), the terminal spreadsheet, that draws molecules in its cells. A column of SMILES
gains a structure column beside it, and every row grows tall enough to show its molecule: as Kitty graphics in Kitty and Ghostty, and as
coloured Braille in any other terminal, over SSH and in tmux. A molecule opens on its own sheet, large, with its properties, and
properties can be added as typed columns, to sort and filter by.

A sub-project of molterm: a molterm server draws everything, so the plugin is one file that needs only VisiData and Python's standard
library. Author: Shinichiro Wachi.

## Use

Put `molterm_visidata.py` where Python finds it (or add its folder to `sys.path`), and in `~/.visidatarc`:

```python
import molterm_visidata
options.molterm_url = "http://molterm.localhost"   # or set $MOLTERM_URL
```

Then open any file with a column of SMILES (`vd drugs.csv`), put the cursor on it, and:

| key | command | does |
|---|---|---|
| `Alt+m` | `addcol-molecule` | adds a structure column for the current column's SMILES, and shows every row at full height |
| `Alt+y` | `addcol-molprops` | adds columns computed from the structures: formula, MW, cLogP, TPSA, HBD, HBA, RotB, QED, Ro5 violations, PAINS and Brenk alerts |
| `Alt+o` | `open-molecule` | opens the current row's molecule on its own sheet, large, with its properties; `q` comes back |
| `Alt+Shift+E` | `edit-molecule` | draws the current row's molecule in molterm's editor, in a box over the sheet; Done (`Ctrl+Q`) puts the new SMILES in its cell, Cancel (`Ctrl+C`) leaves it, and `U` undoes it |
| `Alt+t` | `toggle-molterm-mode` | switches between pictures and Braille |
| `Alt+g` | `plot-molecules` | charts cLogP against MW, a dot for each row, coloured by a category column (such as `series` or `class`) with a legend; adds the properties first if need be |
| `Alt+n`, `Alt+b` | `go-next-molecule`, `go-prev-molecule` | on a chart, put the cursor on the next or previous dot, in the sheet's order |
| `Alt+Shift+D` | `addcol-molecule-3d` | adds a column of the molecules in 3D, from a [molterm-3d](../molterm-3d/) server: put in 3D by molterm, drawn by Mol* in its illustrative style (Kitty graphics only) |
| `Alt+Shift+V` | `view-molecule-3d` | opens the current row's molecule in molterm-3d's viewer, in a box over the sheet: drag to turn it, the wheel to zoom, `s`, `c` and `l` for styles, colours and lightings, `q` to close it |

The chart opens below its sheet on a split screen (`molterm_chart_split`, 55 percent of it; 0 for a chart on its own), and the two
cursors follow each other: the dot under the chart's cursor puts the sheet's on its row, and moving the sheet's cursor, after `Tab`,
puts the chart's on that row's dot. Stepping goes in the sheet's order as it stands, so after a sort it runs from the top. Pictures are
placed in both panes. Closing the chart closes the split.

On a chart, a card in the lower right shows the molecule under the cursor: its name, its structure (a picture, or Braille), its x and
y values, and how many more dots share the place. It follows the cursor however it moves, and `Enter` opens the rows under it as a sheet
of their own, structures and all. This works on any VisiData chart of a sheet with a structure or SMILES column.

They're also in the menus: Column > Add column > molterm, Edit > molterm and View > molterm. `Alt+y`, `Alt+o` and `Alt+Shift+E` find
the structure column from anywhere in the row. The structure column's value is its SMILES, so copying, saving and sorting by it work as for the SMILES column.

Options: `molterm_mode` (`auto`, `kitty` or `text`; auto picks pictures in Kitty or Ghostty outside tmux and screen), `molterm_rows`
(7) and `molterm_cols` (26) for the structure column's size, `molterm_theme` for the pictures' colours (molterm's `-t` names:
`dark`, `light`, `nord`, ...), `molterm_timeout`, and `molterm_auto`, which adds the structure column as soon as a sheet loads from a
file with a column named like SMILES, and `molterm_auto_props`, which then adds the properties too.

After a resize or `Ctrl+L` the plugin sends its pictures again: a terminal may drop them when its window changes size, and VisiData's
redraw clears the screen window by window, some of it after the pictures are placed. So it waits until the screen has settled
(0.4 s) and then sends everything under new ids, removing the old ones only after the new are placed.

### Editing a molecule

`Alt+Shift+E` (`Alt+e` is VisiData's Edit menu) opens molterm's editor, the same one as `molterm edit`, in a box in the middle of the
screen, up to 110 columns by 34 rows, titled with the row's first column. It edits the SMILES column the row's structure is drawn from,
wherever the cursor is in the row; an empty cell opens an empty canvas. The structure, its picture and its properties follow the new
SMILES straight away. The editor runs on the molterm server, as everything else does: while it's open the plugin reads the terminal
itself, sends each key and mouse report to `/api/v1/edit` and prints the answer, as molterm's client does with `-b`, so it needs no
client installed. Its pictures are removed when it closes, and VisiData redraws the screen and places its own pictures again.

The editor needs Kitty graphics. Unlike the cells, it works inside tmux too, as Unicode placeholders, when the terminal outside is Kitty
or Ghostty and tmux has `allow-passthrough on`; elsewhere the command says what's missing. The terminal needs at least 42 columns and
14 rows.

### In 3D

The 3D column and the viewer come from molterm-3d (`options.molterm3d_url` or `$MOLTERM3D_URL`, http://molterm-3d.localhost by
default), which asks the molterm server for 3D coordinates and draws them with Mol*.

molterm-3d is optional: nothing else in the plugin uses it. Without it (nothing answering, or a server without its endpoints) the 3D
column's cells say "3D unavailable" and stay empty, molterm-3d isn't asked again for 30 s rather than once a row, and the viewer says
it's unavailable without taking over the screen, having asked `/health` with a 3-second limit.

The column's pictures fill their cells, with twice the pixels of a 2D picture, without hydrogens; `molterm3d_style` and
`molterm3d_lighting` (illustrative by default) choose how. In Braille the column says it needs Kitty graphics. The viewer works as the editor does, by molterm-3d's `/api/v1/view` with `box=`, so
it needs no client: the plugin reads the terminal while it's open and prints what comes back, inside a frame titled with the row's
first column, up to 140 by 44 cells. From 0.4 s, a spinner on the frame says the server is drawing. Inside tmux it uses placeholders,
as the editor does.

Keys or mouse reports that arrive while the editor or the viewer is still asking the terminal its cell size are kept as their first
input; they used to be dropped, so a drag started at once did nothing. And after either closes, the sheet's pictures come back without
a key: VisiData stops drawing a second after the last key, and the pictures, placed again 0.4 s after a redraw, waited for the next one.
The plugin now keeps VisiData drawing while pictures are still to come.

### How it draws

- **Braille.** VisiData wraps each piece of a cell onto a line of its own, which would split a drawing's colours across lines, so the
  plugin hands VisiData a drawing already cut into lines, with the element labels coloured within them. It replaces VisiData's internal
  `_splitcell` for that, which is why `requirements.txt` pins VisiData 3.5.
- **Pictures.** The cells are left blank, and after VisiData has drawn the screen the plugin places each picture over its cell with
  Kitty's graphics protocol, below the text (z = -1) so that menus and dialogs cover it. A picture is sent once, and placed again only
  when the layout changes: scrolling, sorting, resizing or switching sheets. A row cut off at the bottom shows the top of its picture.
- Drawings are fetched in the background, four at a time, and cached for the session; a cell says "drawing…" until its drawing comes,
  and shows the server's message for a SMILES it can't read.

- `Alt+y` asks for properties 250 structures at a time (`POST /api/v1/properties`), so a sheet of a thousand takes a few seconds.
- At most 64 pictures are kept in the terminal; the least recently shown are freed and sent again if they come back, so scrolling through
  a thousand rows doesn't fill the terminal's memory, or leave blank cells where a terminal has dropped pictures on its own.
- `Alt+m` on a column that isn't SMILES (by its name) uses the sheet's column whose name contains "smiles". A molecule's own sheet
  enlarges its picture to fill the room, up to three times, drawn with three times the pixels.

It uses three molterm endpoints that are newer than molterm v0.1.1: `POST /api/v1/render` with `"properties": true`,
`POST /api/v1/properties` and `GET /api/v1/render.png`. An older server gets the message "… has no /api/v1/render.png: it needs a
newer molterm server", and `Alt+y` falls back to one request per structure.

## The demo

http://molterm-vd.localhost (dev: http://molterm-vd-dev.localhost) plays a real VisiData session in a web terminal that shows Kitty graphics. `demo/record.py`
runs VisiData with the plugin in a pseudo-terminal of 116 by 34 cells, against the molterm server, presses the keys in its script with
`TERM=xterm-kitty`, and keeps the terminal's output between one key and the next. The page prints those frames back at that size, with
the key and a caption. It shows `demo/drugs.csv` gaining structures, scrolling, Braille and back, properties, a sort by cLogP, a
molecule on its own sheet, a chart of cLogP against MW coloured by class, stepping through its dots with the card naming each and the table following, `Tab` to the table and down it with the
chart following, opening the rows under the chart's cursor, the 3D column beside the SMILES, the 3D viewer turned by mouse reports, relit and restyled, and Braille once more.

The service records the session when it starts, and again whenever the plugin, the script, the data or `demo/visidatarc` is newer than
the recording (`data/recording.json`, git-ignored); the page waits meanwhile. `demo/visidatarc` turns off the help sidebar, uses `│`
inside tall rows (the web terminal's font has no `⁝`), and a dark grey cursor row, since reversed colours would fade light pictures.

### For the main showcase

The main app's `/showcase` ends with a short VisiData scene (`SHOWCASE_STEPS` in `demo/record.py`, with `demo/showcase.visidatarc`:
VisiData's light theme, structures and properties from the start, the split chart and its linked cursors, and quitting at the end).
`GET /api/v1/recording?script=showcase&cols=&rows=` records it at that size, 80 to 240 by 24 to 80, in about ten seconds the first
time, and keeps the 24 most recent sizes in `data/`; it answers 503 while recording, one recording at a time.

## The live demo

http://molterm-vd.localhost/live is VisiData itself, for anyone with a browser: each visitor gets their own VisiData with the
plugin, on a library of 1000 compounds, in the same web terminal. The library is every amide of 40 carboxylic acids and 25 amines
(`demo/library.py`), made up but real structures, written to `/tmp` when the service starts.

The structures and properties are there from the start (`molterm_auto`, `molterm_auto_props`), and the page has buttons for the
chart, stepping from dot to dot, opening a molecule and switching to Braille, which send what
Alt and the letter would, for keyboards whose Alt doesn't reach the terminal. The page and its script are served with
`Cache-Control: no-cache`, so a browser never runs an old script on a new page.

`demo/live.py` (aiohttp) bridges each WebSocket to a VisiData in a pseudo-terminal, speaking restty's own protocol: JSON input and
resize messages in, the terminal's bytes out. The page measures the cell, sets the grid to fill the box, and connects; resizing the
window resizes VisiData. It sends Alt and a letter as Escape and the letter, as terminals do, since a browser may hand restty the letter
alone. The terminal's grid is restty's own fit to the box, sent once the font has loaded and the canvas has stopped changing, and up
to 400 by 160 cells. Sessions use the terminal type `molterm-kitty`, built into the image: `xterm-kitty` with SGR mouse reports, since the older
reports break past column 95.

VisiData can run Python and shell commands and read and write files, so a session is fenced in four ways:

1. **Commands.** `demo/lockdown.py`, loaded last, keeps an allowlist: moving, sorting, selecting and searching by regex, column types,
   frequency, describe and pivot sheets, plots, editing cells in memory, undo, help, and the plugin's commands. The rest (263 of 638)
   are removed from the keys, the menus and the command palette alike: expressions, Python, the shell, the editor, opening and saving
   files, the system clipboard, options, macros, replay, suspend and the sheet of all sheets.
2. **Whatever reaches them anyway.** Expression evaluation and the editor fail, the process can't start another (`subprocess`,
   `os.system`, `fork`, the `exec` and `spawn` families all refuse), and the plugin's server address is fixed, so no option can turn it
   towards another host.
3. **The session.** `demo/sandbox.py` caps memory (1.5 GB), CPU time (20 min), file size (4 MB) and open files before VisiData starts,
   in a fresh home folder under `/tmp` that is removed with the session. At most 4 sessions run at once and 2 from one address (the one
   Traefik adds), and a session ends after 10 minutes without a key or 30 in all; closing the page ends it at once. A WebSocket from
   another site's page is refused.
4. **The container.** `molterm-vd-live` is separate from the recorded demo: its filesystem is read-only apart from a 256 MB `/tmp`,
   it has no Linux capabilities and can't gain privileges, and it is capped at 3 GB, 2 CPUs and 400 processes.

## Run

Docker only, from this folder, with the main molterm dev service running:

```sh
make dev        # build and start -> http://molterm-vd-dev.localhost, against the dev molterm server
make build up ENV=prod   # production -> http://molterm-vd.localhost, against http://molterm.localhost
make test       # the tests, in the container
make record     # record the demo again now
make vd         # VisiData with the plugin on demo/drugs.csv, in this terminal (FILE=... for another)
```

The image is built from the repository's root, to share the house stylesheet, and fetches restty and the DejaVu fonts with checksums,
as the main app does. Dev bind-mounts this folder and draws with the dev molterm server, `molterm-dev-molterm-1`, on the `traefik`
network; `ENV=prod` uses `molterm-vd.localhost` and the production server.

| path | job |
|---|---|
| `molterm_visidata.py` | the plugin |
| `demo/record.py` | runs VisiData through the demo's keys and writes the recording |
| `demo/app.py`, `demo/templates/index.html`, `demo/static/vd.js` | the demo's page, which plays the recording in restty |
| `demo/drugs.csv`, `demo/visidatarc` | the demo's data and VisiData settings |
| `demo/live.py`, `demo/templates/live.html`, `demo/static/live.js` | the live demo: a VisiData per WebSocket, and its page |
| `demo/sandbox.py`, `demo/lockdown.py`, `demo/live.visidatarc` | how a live session starts and what it's allowed |
| `demo/library.py` | the 1000 compounds |
| `tests/test_plugin.py` | the plugin against a stand-in server: cells, colours, picture placement, commands |
| `tests/test_demo.py` | VisiData itself in a pseudo-terminal against the real server, read back with pyte; the page |
| `tests/test_live.py` | live sessions over a WebSocket: VisiData on the library, refused keys, the lockdown, limits, endings |

## Verified

- `make test`: 65 tests, eight of them for 3D: without molterm-3d (a closed port) the column says "3D unavailable" and asks once, the
  2D column still draws, and the viewer refuses without touching the screen; with it, the column's request and placement, the
  viewer's box, frame, spinner, failures and command, and a press during the cell-size question kept as input. The session tests run real VisiData 3.5 against the dev molterm
  server and read the screens back with a terminal emulator: pictures transmitted once and placed in their cells, Braille with
  labels, the molecule's sheet and `q` back. They skip when no server with the new endpoints is reachable.
- The live tests open real sessions over a WebSocket: VisiData on the 1000 compounds; `=`, `g=`, `Ctrl+X`, `g Ctrl+X`, `Ctrl+S`, `o`,
  `!`, `z|`, `Ctrl+Z` and `Ctrl+O` leave the sheet as it was, with anything they asked for answered by a command that would have
  created a file; the lockdown removes what it should and processes, expressions and a changed server address all fail; the session
  limits, a foreign page and the idle ending; structures and properties for all 1000 in under 30 seconds.
- The split was used in the live demo in Chrome: stepping through dots moved the table's cursor to each compound's row, and after
  `Tab`, moving down the table moved the chart's card to that row's compound, with pictures in both panes.
- The chart was used in the live demo in Chrome: 1000 dots coloured by series with a legend, the card showing each compound's
  structure and values as the cursor stepped through them, and Enter opening those rows. Losing the pictures (all removed from the
  terminal, as a resize does) and pressing `Ctrl+L` brought them back; before the fix they stayed gone, because the screen was cleared
  after they were placed.
- The live demo was used in Chrome: structures, properties for the thousand, sorting, paging deep into the sheet with pictures for each
  page, the molecule's sheet, `=` refused, New session, and closing the page ending the session, its process and its folder.
- Four sessions at once, each adding structures and properties and paging through: about 170 MB and 10 processes in the container,
  half a CPU, and back to nothing when they ended.
- The demo was watched in Chrome: pictures in their cells following the cursor and the scroll, Braille, the properties and the sort, the
  molecule's sheet in both modes, with no console errors.
- The editor was used in the live demo in Chrome: `Alt+Shift+E` on a row opened it over the sheet with the row's molecule, an O changed
  to S was put back in the cell on Done with its picture, formula and properties, `U` undid it, Cancel left the cell as it was, and
  VisiData's mouse and pictures came back afterwards. Against the dev server with a scripted terminal, everything it printed stayed
  inside the box. Inside tmux it has only been tested with a stand-in for the server and tmux.
- The production command (gunicorn) serves the page and its vendored files from the built image.
- 3D, in the recorded demo (watched in Chrome): the 3D column beside the 2D one for the drugs, the viewer over the sheet turned by
  mouse reports, relit and restyled, and the sheet's pictures back after `q`. In the live demo (Chrome, a real mouse): Alt+Shift+V
  and the 3D viewer button opened the row's compound, a drag turned it, `q` closed it, and the 3D column button drew 13 compounds.

## Open issues

- Pictures have only been seen in restty, the web terminal, not in a real Kitty or Ghostty. Inside tmux the plugin draws Braille: it
  doesn't use molterm's Unicode placeholders yet.
- The production demo needs molterm released with the new endpoints first: the production server is v0.1.1, which lacks them.
- The plugin replaces `visidata.sheets._splitcell`, an internal function, so a VisiData upgrade may need a change; it is pinned to 3.5.
- Pictures assume cells twice as tall as wide, as molterm does, so they come out a little squeezed or stretched in other fonts.
- The editor's box is fixed while it's open: it can't be moved or resized as in `molterm tui`, though it follows the terminal's size.
- Kitty images placed by the plugin don't survive VisiData's suspend (`Ctrl+Z`) or a shell-out until the layout next changes.
- Large sheets fetch a drawing for each row shown, as it's shown; there is no batch request for drawings, only for properties.
- The live demo's lockdown is an allowlist over VisiData 3.5's commands and a block on the paths I found to code (expressions, processes,
  the editor). It hasn't had an outside security review; until then it's safest kept to the dev host. The live container is still on
  the `traefik` network, so if something did break out it could reach the other stacks there.
- An idle VisiData redraws all the time, about an eighth of a CPU per session.
- Alt keys are only verified through the page's own handler with the browser tool's key events, not with every browser and keyboard.
- 3D pictures sit small in their cells: molterm-3d fits a molecule's whole sphere to the cell's height, and a drug is long and flat.
  Illustrative outlines are black, so on a dark sheet they don't show. The first click on the live page's 3D viewer button, while the
  session was still adding its properties, opened VisiData's capture prompt instead; once loaded the button worked.
- SMILES in 3D need molterm 0.1.3 on the server molterm-3d asks: production has 0.1.2 until it's deployed.
