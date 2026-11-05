# molterm 3D

Structures in 3D in the terminal, drawn by [Mol*](https://molstar.org/) on a server and shown with Kitty graphics: turn them with the
mouse, zoom with the wheel, step through Mol*'s styles and colours. And a web page with Mol*'s own viewer on the same structures.

A sub-project of molterm, as `visidata-molterm/` is. Mol* runs headless in Node (`HeadlessPluginContext`), on software OpenGL (Mesa's
llvmpipe on a virtual X display), so the server needs no GPU. 3D coordinates for SMILES and molblocks come from the molterm server's
`/api/v1/conformer.sdf`; PDB entries come from RCSB. Author: Shinichiro Wachi.

## Use

```sh
curl -so molterm3d http://molterm-3d-dev.localhost/molterm3d && chmod +x molterm3d
./molterm3d 1crn                       # a PDB entry
./molterm3d 'CC(=O)Oc1ccccc1C(=O)O'    # anything molterm reads, in 3D
./molterm3d -s spacefill ligand.sdf    # a file: PDB, mmCIF, SDF/MOL, MOL2, XYZ
```

| input | does |
|---|---|
| drag | turns, as a trackball: half a turn across the whole picture |
| right-drag, Shift-drag | moves |
| wheel, middle-drag, `+` `-` | zooms |
| arrows, Shift-arrows | turn 15°, move |
| `s` / `S` | next / previous style: auto, ball-and-stick, spacefill, licorice, cartoon, surface |
| `c` / `C` | next / previous colour: auto, element, chain, secondary, rainbow, residue, b-factor |
| `h` | hydrogens shown or hidden |
| `r` | back to the first view |
| `?` | the keys, on the status line |
| `q`, Esc, Ctrl-C | quit |

Auto draws a protein or nucleic acid as a cartoon, coloured by chain, with its ligands as balls and sticks, and anything else as balls
and sticks with grey carbons. Options: `-s` style, `-c` colour, `-H` hydrogens hidden, `-t light|dark`, `-k` Kitty graphics whatever the
terminal seems to be. The server is `$MOLTERM3D_URL`, or the one the script came from.

It needs Kitty graphics: Kitty or Ghostty, or either inside tmux with `allow-passthrough on`, where the picture goes as Unicode
placeholders, as molterm's pictures do (ids are cleared before they're sent again, for Ghostty). Pictures have transparent backgrounds,
so the terminal's own shows through; the background colour the terminal reports is also given to Mol* for its fog.

### How it works

As `molterm edit` does: the client is a shell script that reads the terminal raw and sends each batch of keys and mouse reports, as hex,
to `POST /api/v1/view` with the state from the last answer. The server applies them to the view (a rotation from Mol*'s own starting
view, a zoom and a shift, all in the state), draws the structure at the window's size in pixels with Mol*, and answers
`<0 go on, 1 finished, 2 failed> <state>` and what to print. Pictures alternate between two ids, the new one placed before the old one
is removed, so nothing flickers. A drag costs about 0.1 s on this server for a small protein; the first view of a structure about a
second, as Mol* builds its geometry.

Three Mol* contexts are kept, each with one structure drawn one way; changing style or colour builds it again. Pictures are capped at
1.6 megapixels and the terminal scales them up.

## Web

http://molterm-3d-dev.localhost: a PDB id or a SMILES in Mol*'s viewer (served from the image, no CDN), with the picture the terminal
would get below it, in a chosen style, colour and turn.

| endpoint | gives |
|---|---|
| `GET /api/v1/structure?input=` | the structure's file; `X-Molterm-Format` (`mmcif`, `sdf`, ...) and `X-Molterm-Name` |
| `GET /api/v1/render.png?input=&style=&colour=&width=&height=&turn=&tilt=&background=RRGGBB&hydrogens=0` | a picture, transparent where empty |
| `POST /api/v1/view` | one step of the terminal viewer (above) |
| `GET /molterm3d` | the client, with this server as its default |
| `GET /health` | 200 once Mol* has drawn a test molecule at start, 503 (with the reason) if it can't |

## Run

Docker only, from this folder, with the molterm dev server running (it supplies 3D coordinates):

```sh
make dev                 # build and start -> http://molterm-3d-dev.localhost, against the dev molterm server
make test                # the tests, in the container, drawing with Mol* for real
make view INPUT=1crn     # the viewer in this terminal, through the dev server
make build up ENV=prod   # production -> http://molterm-3d.localhost, against http://molterm.localhost
```

The image is built from the repository's root, to share the house stylesheet. Node 22 on Debian; headless-gl is compiled in a
build stage, as it has no prebuilt binary for this Node. Versions are pinned in `package-lock.json`. The container runs `xvfb-run`
under an init (`init: true`: as PID 1, xvfb-run never hears that the display is ready). Dev bind-mounts `src/`, `public/`, `client/`
and `tests/` and restarts on changes (`node --watch`). PDB entries are kept in `data/pdb/` (git-ignored). CPU is capped at 4 and memory
at 3 GB, since software rendering can take a machine.

| path | job |
|---|---|
| `src/server.js` | routes, the page, health |
| `src/viewer.js` | the terminal viewer's steps: input, the view, frames |
| `src/renderer.js` | Mol* contexts, styles and colours, the camera, PNGs |
| `src/structures.js` | PDB ids, files, and SMILES via molterm |
| `src/kitty.js` | Kitty graphics and tmux placeholders, as molterm's `server/placeholders.py` |
| `client/molterm3d.sh` | the client |
| `public/` | the web page |

## Verified

- 18 tests (`make test`): Kitty sequences and ids match molterm's; keys, mouse reports, which way drags and arrows turn, zoom bounds and quitting; a file
  opened, drawn, turned, restyled through tmux placeholders and closed; resizing; health, the served client and page, SMILES through
  molterm, PNG sizes, failures.
- Through the dev URL: 1CRN first frame 0.78 s, a drag 0.13 s; a bad SMILES gives molterm's message.
- In Chrome: the page shows 4HHB and caffeine in Mol*'s viewer and the server's picture below, with no console errors.

## Open

- Not yet tried in a real Kitty or Ghostty window, only through the protocol and its tests.
- Production needs a molterm release with `/api/v1/conformer.sdf` (added after 0.1.2) for SMILES; PDB entries and files work without.
- No spinning on its own: the client only asks when something is typed or the window changes.
- One structure at a time per Mol* context and all drawing queued, so many viewers at once will wait on each other.
