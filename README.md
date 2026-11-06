# Molterm TUI Renderer

TUI renderer for chemical structures using RDKit and advanced text graphics

![A text interface of structures, drawn as Kitty graphics](docs/images/molterm-tui.png)

| | |
|---|---|
| ![Imatinib as a Kitty picture beside the same drawing in Braille](docs/images/molterm-braille.png) | ![Caffeine as a Kitty picture beside plain ASCII](docs/images/molterm-ascii.png) |
| Kitty graphics and coloured Braille | Kitty graphics and plain ASCII, for anywhere text goes |

![molterm in a shell: each command's picture prints below it and scrolls with the text](docs/images/molterm-shell.png)

| | |
|---|---|
| ![VisiData with a column of structures beside their SMILES](docs/images/visidata.png) | ![VisiData with a molecule open in 3D, drawn by Mol* on molterm-3d](docs/images/visidata-3d.png) |
| The VisiData plugin: structures in a spreadsheet | ...and a molecule in 3D, turned with the mouse |

![molterm in the browser: RDKit as WebAssembly, no server](docs/images/web.png)

The shots come from the dev services' own demos: `make screenshots` takes them again, in headless Chromium.

Created 2026-10-31 from a Flask project template.

## Environments

| | command | URL | image |
|---|---|---|---|
| dev | `make dev` | http://molterm-dev.localhost | `molterm:dev`, source bind-mounted, live reload |
| prod | `make prod` | http://molterm.localhost | `molterm:$(cat VERSION)` |
| other | `make up ENV=staging` | from `env/staging.env` | `molterm:staging` |

Each environment is its own compose project (`molterm-dev`, `molterm-prod`), so they can run side by side.

`visidata-molterm/` is a separate sub-project: a [VisiData](https://www.visidata.org/) plugin that draws molecules in a spreadsheet's
cells, as Kitty pictures or coloured Braille, with a recorded demo at http://molterm-vd.localhost and a live one, where anyone
can use VisiData on 1000 compounds in a locked-down session, at http://molterm-vd.localhost/live (dev: `molterm-vd-dev`). It has its own Makefile,
containers and README, and uses this server's `/api/v1/render` (with `properties`), `/api/v1/properties` and `/api/v1/render.png`.
With molterm-3d it also shows molecules in 3D: a column of them, drawn by Mol*, and a viewer over the sheet to turn one with the mouse.

`molterm-3d/` is a second sub-project: structures in 3D, drawn by [Mol*](https://molstar.org/) running headless in Node, in the
terminal (`molterm3d 1crn`: drag to turn, wheel to zoom, Kitty graphics, through tmux too) and on a web page with Mol*'s own viewer,
at http://molterm-3d-dev.localhost (prod: `molterm-3d`). It puts SMILES and molblocks in 3D with this server's
`/api/v1/conformer.sdf` (RDKit's ETKDG, then MMFF94, the same shape every time; a molblock already in 3D keeps its coordinates), and
fetches PDB entries from RCSB itself. Mol*'s illustrative lighting is its default, and `molterm3d -p` prints a picture into the shell
that scrolls with the text. http://molterm-3d-dev.localhost/showcase is an automated demo of it in the same web terminal as
`/showcase` here. See its README.

`web/` is a third: molterm in the browser with no server behind it, at http://molterm-web-dev.localhost (prod: `molterm-web`). RDKit
compiled to WebAssembly (RDKit.js, the same RDKit release as this server) lays out and draws the molecule, and this server's text
renderer, ported to JavaScript, turns the drawing into Braille or ASCII, or shows RDKit's picture through Kitty graphics, in restty.
nginx only serves the files, and `make -C web export` writes them out for any static host. Its ASCII matches this server's character for
character; its Braille differs only by single dots where the browser's antialiasing differs from Cairo's (`/check.html`). See its README.

Add an environment by creating `env/<name>.env` (and optionally `docker-compose.<name>.yml` for overrides).

Every service is published through a Traefik on the external `traefik` Docker network, by container labels. The hosts in `env/*.env`
are `*.localhost` names, which browsers send to this machine. To serve your own domain, put the settings that differ in a git-ignored
`env/<name>.local.env` next to the tracked one (in this folder, `molterm-3d/` and `visidata-molterm/` alike): the Makefiles load it
after `env/<name>.env`, so it wins. For example, `APP_HOST=molterm.example.org`, and for the VisiData demo also
`MOLTERM_PUBLIC_URL=http://molterm.example.org`, where its page links to molterm.

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

http://molterm-dev.localhost/showcase is an automated demo in the same web terminal, black on white. It sets molecules as Kitty
graphics beside plain ASCII, with Braille for a larger drug, and shows the fog setting and four colour themes. It types six `molterm`
commands into a shell, whose pictures print inline and scroll with the text, and scrolls the view back up through them and down again,
as a mouse wheel would. Then it works through a real
edit: it copies a SMILES, copies a structure as SMILES, SVG and PNG from its right-click menu, clicks a structure in a list, edits it in
the windowed editor, highlights an atom and adds a note to another, resizes, moves and minimizes that window, chooses Done, copies the result, pastes it
into a command and gets the list back. The server makes every frame with the same code as `molterm tui`, and the page only prints
them and moves a pointer to show where the input went. It plays by itself, with **Stop** and **Start again**, and doesn't take
input. It won't overwrite your clipboard on its own: it shows what the terminal was asked to copy below the screen, with a **Copy text**
button, and for a copy with pictures a preview of the PNG and **Save PNG** and **Save SVG** links.

It ends with a VisiData scene: the plugin from `visidata-molterm/`, with each drug's structure and properties in the spreadsheet,
then the chart of cLogP against MW below it, stepping through the dots with the card naming each molecule and the table's cursor
following, and `Tab` to the table with the chart following back. The scene is real VisiData output, recorded by the visidata-molterm
service at the showcase terminal's own size the first time that size is asked for (about ten seconds) and kept for later visitors;
`GET /api/v1/showcase/visidata?cols=&rows=` passes it on from `$MOLTERM_VD_URL` (set in `env/dev.env` and `env/prod.env`). The
page asks for it while the rest plays and adds it to the end once it's ready; without the service the showcase plays without it.

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
| `kitty` | Kitty, Ghostty | answers a Kitty graphics query, or `$TERM_PROGRAM`/`$TERM` names it; inside tmux, tmux's `#{client_termname}` names Kitty or Ghostty and `allow-passthrough` is on |
| `iterm` | iTerm2, WezTerm, mintty | reports that name to an XTVERSION query, or `$TERM_PROGRAM` names it |
| `sixel` | xterm `-ti vt340`, others | device attributes include 4; inside tmux, `#{client_termfeatures}` includes `sixel` |
| `braille` | everything else | the fallback, and always used when output is piped |

### Kitty graphics in tmux

tmux passes Kitty graphics on only when they're wrapped for passthrough and `allow-passthrough` is on, and even then it doesn't know
a picture is there, so it would land wherever the outer cursor happened to be. So inside tmux, in Kitty or Ghostty, molterm uses Kitty's
Unicode placeholders (`server/placeholders.py`): each picture is sent once, wrapped, as a virtual placement, and the cells it covers
are printed as U+10EEEE with marks for their row and column, in a colour that names the picture. tmux keeps those as text, so pictures
stay in place through redraws, scrolling, its history and switching panes. This works for a drawing, the guided session, `molterm
edit` and `molterm tui`; the client asks for it with `placeholders=1`. Turn passthrough on once:

```sh
tmux set -g allow-passthrough on      # and add `set -g allow-passthrough on` to ~/.tmux.conf to keep it
```

Without it, the client says so and falls back to Braille (and `edit` and `tui` stop with that message).

A placeholder names its picture by its colour, and tmux turns 24-bit colours into 256 when it doesn't know the outer terminal has
them, as with Ghostty's terminfo: an id sent that way pointed at the wrong picture, and nothing showed. So the id's low byte is a
256-colour index, which tmux passes on as it is, and its high byte a third combining mark on each cell, as Kitty's protocol allows.

tmux passes picture commands on at once but redraws the cells that show them a little later, so a picture that changes is never
removed through tmux: each layer of the editor alternates between two ids, and a new picture goes under the one its cells don't name
yet. Removing the old one would leave the cells naming nothing until tmux redrew them, a flicker. Only the
cells of the picture that changed are printed again (all of them after a resize or a closed menu), so that tmux has less to redraw.

The id a new picture goes under is cleared just before it is sent (`a=d,d=I`). Ghostty 1.3, sent a picture under an id it already
has, replaces the picture but keeps that id's placements and adds another; its placeholders take their size from whichever it finds
first. So a placement left from an earlier frame or run of another size, such as the 19-button palette before the chain and R tools,
squeezed the picture into the wrong number of cells and centred it. In a tmux pane of Ghostty the palette then shrank and grew as
the two ids alternated on hover, and the drawing sat off the cells the mouse is read in, so a dragged bond ended away from the pointer.
Outside tmux the editor clears the new id before placing too, in case a run that ended without tidying up left a picture under it;
the list's tiles already did, and VisiData's pictures use their own placement ids, which replace rather than add.

If pictures still don't show in tmux, `molterm -v` says what the client found: the mode, tmux's version, the terminal tmux says is
outside it, `allow-passthrough`, and whether pictures go as placeholders. They need tmux 3.3 or later, the client from a server with
this support (the dev server's `/molterm`; v0.1.1's has none), and Kitty, or Ghostty 1.0 or later, outside. In the editor a cell can show
only one picture, so through tmux the selection marks are drawn into the structure rather than over it, and the boxed editor has no
backing picture: its cells cover the list below anyway.

`-m octant` and `-m ascii` are only used if you ask for them. ASCII draws each bond as characters, at a larger size, so it survives
anything that carries text, but it's coarse: it suits small and mid-sized molecules.

Fog diffuses the lines a little, as if the drawing were seen through fogged glass: a faint halo and a wider, slightly patchy haze, from
0 (none) to 100. It's subtle even at 100, and the lines stay crisp. It works in the graphics modes for one structure; text modes ignore it,
so a fallback to Braille still works.

```sh
./molterm -t mono -f 50 'CCO'   # black on white, softly diffused
```

Fog takes the theme's ink, so on `dark` and `light` the halo keeps the element colours. The drawing stays within `-c` and `-r`, with the
molecule a little smaller to leave room for the haze. Fog isn't available in `molterm demo`, `edit` or `tui`.

### Colour themes

Text modes use the terminal's 16 colours and follow its theme by themselves. Pictures, the editor's bars, menus and palette, and the list's
labels carry their own colours, which `-t` picks: `dark`, `light`, `mono` (black on white), `mono-dark`, `solarized-dark`,
`solarized-light`, `gruvbox-dark`, `gruvbox-light`, `nord`, `dracula`, `catppuccin-mocha`, `catppuccin-latte` or `tokyo-night`. Each
names an ink, element colours in the order of ANSI red, green, yellow, blue and magenta, a highlight, and the editor's bars and accent;
they are kept in `server/themes.py`.

Without `-t`, the client asks the terminal for its background and text colours (OSC 11 and 10), picks `dark` or `light` by the
background's brightness, and sends both colours with it. The server then draws in the terminal's own ink, gives the boxed editor the
terminal's background, and shades the bars between the two, so the editor fits whatever theme the terminal has. Element colours stay the
base theme's. Over HTTP the same is `theme=dark&background=1e1e2e&foreground=cdd6f4`.

```sh
./molterm -t nord 'CN1C=NC2=C1C(=O)N(C(=O)N2C)C'
./molterm -t gruvbox-light tui
```

### Drawing a molecule

`molterm edit` is a molecular editor inside the terminal, for Kitty and Ghostty, laid out like a desktop drawing program such as
ChemDraw. Press `?` in it for every control.

```sh
./molterm edit                                 # start empty
./molterm edit 'CC(=O)Oc1ccccc1C(=O)O'         # start from a SMILES
smiles=$(./molterm edit)                       # Ctrl-Q prints the SMILES on stdout, so it can be captured
./molterm edit 'CCO |atomProp:2.atomNote.hydroxyl|'   # CXSMILES brings its annotations
```

A drawing with annotations is printed as CXSMILES: the SMILES, then the annotations as atom properties, which RDKit reads back.
RDKit's own `atomNote` holds an atom's note, so RDKit draws it unchanged. Highlights, bond annotations and what is shown are
molterm's own properties, `molterm_highlight`, `molterm_bond_<other atom>_highlight` and `_note`, and `molterm_show`, since CXSMILES has no
place for bond properties: other programs keep them as atom properties without knowing what they mean.

- **Tool palette, on the left:** selection, eraser, pan, text, bonds, charges and rings, drawn as icons. A tool stays chosen until you
  pick another.
- **Drawing:** click for a whole bond, click an atom to grow one, click a bond to change it, or drag, in steps of 15 degrees or onto
  another atom. Ring tools fuse onto a bond, share an atom (a spiro link on a ring atom; Alt+click sprouts instead) or land in empty space, and show where they will go before you click.
- **Keys follow the atom, bond and generic tables in chapter 5 of the ChemDraw 17.1 User Guide.** They act on the hotspot, the atom or
  bond under the pointer, marked as there with a light blue circle and green dot or a light blue bar. A key means what the table says
  for what is there: on an atom `3` sprouts a phenyl and `n` makes nitrogen, on a bond `3` makes it triple and `a` fuses a benzene, and
  over nothing keys only choose tools, such as `u` for cyclohexane. After a sprout the hotspot moves to the new atom, so `1` `1` `o`
  builds a chain. After a ring or group it clears until the pointer moves, so a held key can't scatter copies. Arrows move the hotspot
  instead, atom to bond to atom, so a mouse is optional. What a sprout adds meets the chain at even angles: a group or ring that takes
  in the end of a chain goes on straight along its last bond, so `2` on the end makes the acetyl's three bonds 120 degrees apart
  (it used to be turned 60 degrees, folding the methyl or the oxygen back onto the chain). Only a fourth bond on a carbon that has
  three, or a ring or two methyls on one with two, has to fit into the room left, at 60 degrees, as in ChemDraw.
  `tests/test_editor.py` checks the angles of every sprouting key on each kind of carbon.
- **Text tool, or Enter on an atom:** a box opens at the atom for an element or a group such as OMe, CF3, CO2H, NO2, Ph, Boc or Ts.
  Groups become real atoms. A group replaces an atom at the end of a chain, and is added as a substituent to a ring atom.
- **Selection:** drag across empty space to select, drag to move, Delete to remove, and arrows to nudge.
- **Menus:** a menu bar (also F10), and right-click for the menu of what is under the pointer. Right-click a selection for its own menu,
  to copy just those atoms and the bonds between them, annotate them or delete them.
- **Annotations, drawn by RDKit:** highlights in four colours and short notes on atoms and bonds, from an atom's or bond's menu, a
  selection's menu or the **Annotate** menu, which also shows atom numbers and CIP stereo labels (R, S, E, Z). They are undone like any
  other change, follow their atoms through Clean Up, and go when their atom or bond goes.
- **Copying:** **File** copies the drawing as SMILES, SVG or PNG, or all three at once; see [Copying](#copying) below.
- The SMILES, formula, MW and cLogP update below the drawing. Atoms RDKit can't accept, such as an oxygen with three bonds, get a red
  box, so a drawing can be invalid on its way to being finished. Ctrl-Q then stays in the editor and says why. Ctrl-C leaves anyway, printing
  the problem and exiting with 1.

### Markush structures, queries and Ketcher's other tools

The editor also has the small-molecule tools of EPAM's Ketcher that it lacked, macromolecules aside, with Markush structures first.
Right-click an atom for **Properties and Markush**, a bond for **More Bonds and Queries**, and a selection for **Markush and Stereo**; the
**Structure** menu has the rest.

- **R-groups.** `r` on an atom (or the **R** tool in the palette) makes it the lowest free R-group, and again the next number; the label
  box takes `R1` to `R32`. Draw a definition apart from the structure, select it and choose **R-group Definition...** with its number:
  it's drawn with `R1 =` before it. `.` on an atom steps its attachment point through first, second and both, drawn as Ketcher draws
  them, with a short bond ending in a wave; without one a definition attaches by its first atom. **Structure > R-group Logic...** takes
  Ketcher's occurrence, rest-H and if-then, written as `R1 >0 H if R2`.
- **Generic atoms and lists.** A, AH, Q, QH, X, XH, M, MH and `*` from the label box (`x` makes X), or the atom's menu; an atom list as
  `C,N,O` and a NOT list as `!F,Cl` or `NOT F,Cl`.
- **Variable attachment (position variation).** Select the atoms a substituent may go on, as in a ring, then right-click its bond to one
  of them and choose **Attach to Selected Atoms**. The bond is drawn to the middle of them, which RDKit shades.
- **Repeating units and link nodes.** **Repeating Unit...** on a selection brackets it with `n` or a range such as `1-3`; **Link Node...**
  on an atom with two bonds repeats it, as `1-4`.
- **Atoms and bonds.** Isotopes (`13C`, and `d` or `D` for deuterium), radicals, enhanced stereo groups (`abs`, `and1`, `or1`...), wavy
  (`y`), aromatic, dative and crossed double bonds, and the query bonds any, single or double, single or aromatic, double or
  aromatic, each in a ring or a chain.
- **Tools and commands.** A chain tool (`X`), flips and a 90 degree turn of the selection or the drawing (a mirror image swaps its wedges, so
  the stereochemistry stays), Aromatize and Dearomatize, adding and removing explicit hydrogens, Check Structure, Calculated Values
  (formula, MW, exact mass and elemental analysis) and **File > Copy Molfile (V3000)**.

It all travels as ChemAxon's extended SMILES, which RDKit reads and Marvin writes: `_R1` and `Q_e` labels on star atoms, `m:` for a
variable attachment, `Sg:` for a repeating unit, `LN:` for a link node, `a:`/`&1:`/`o1:` for stereo groups, and the definitions and
logic after `RG:` and `LOG=`, which `server/markush.py` adds and takes off, since RDKit doesn't read them:

```sh
./molterm edit '*C1CCCCC1* |$_R1;;;;;;;_R2$,RG:_R1={CCC},{N},_R2={O},LOG={_R1:;;>0._R2:_R1;H;0,1}|'
./molterm edit 'Cl*.c1ccccc1 |m:1:2.3.4|'      # Cl on any of three ring atoms
```

A definition that bonds to the core twice marks its ends with atom maps 1 and 2, as `{[CH2:1]C[CH2:2]}`; ChemAxon's `_AP1` stars are read
and written for attachment points outside a definition. An atom list or a query bond has no place in SMILES, so a drawing with one is
written as CXSMARTS, such as `[#6]-,=[#6]-[#6,#7,#8]`, which the editor and the renderers read back. The renderers draw a Markush
structure's definitions beside its core, and the list (`molterm tui`) keeps the text as it came.

The client only passes raw input to the server, and the server keeps no sessions: the drawing travels with each request and is checked
when it comes back. So every action needs a round trip. The palette, molecule and marks are separate images, and only those that
changed are sent: hovering costs 1 to 9 KB, and a change to the molecule about 60 KB. It needs the same tools as the rest of the client,
plus `od`.

### Editing inside a text interface

`molterm tui` is a text interface with structures in it: a grid of drawings, where hovering marks a tile and clicking an editable one opens
the editor in a window over the rest. Done (or Ctrl-Q) puts the edit back in the list and redraws that tile, and Cancel (or Ctrl-C) leaves it as it was.

The window works like one on a desktop. Drag its title bar to move it, and its right or bottom edge, or the ◢ grip at the corner, to resize it;
it won't go smaller than the editor needs, nor off the screen. **□** on its top edge fills the screen and a second click puts it back, and **_**
minimizes it into the list's top bar, edit and all, where a click brings it back. While it's minimized the list can be browsed and copied from,
but opening another structure or quitting says to finish that edit first. A smaller terminal pulls the window inside it.

```sh
./molterm tui                                                   # a sample list
./molterm tui 'ro:CC(=O)Oc1ccccc1C(=O)O Aspirin' 'CCO Ethanol'  # "SMILES name"; ro: means read-only
./molterm tui 'CCO |atomProp:2.atomNote.OH| Ethanol'            # a CXSMILES keeps its annotations
./molterm tui 'CCO Ethanol' > edited.smi                        # q prints the list as it now stands
```

Each structure keeps its annotations, draws them on its tile, opens with them in the editor and gets back what the editor saves.
The list prints each line as `SMILES name`, or `CXSMILES name` for one with annotations.
Right-click a structure, or press `/` or `m` with the pointer on it, for its menu: **Edit**, the four kinds of copy, and for an editable one
**Show Atom Numbers**, **Show Stereo Labels** and **Clear Annotations**. Arrows and Enter work in it, and anything else closes it.

### Copying

Everything that copies takes the annotations along. In the list, point at a structure and press `c`, or click **copy** on its frame, for
its SMILES; its right-click menu also copies an SVG, a PNG or all three. Read-only structures can be copied too. In the editor, **Copy
SMILES** is on the window's bottom edge, the **File** menu and the empty canvas's menu copy the drawing in each format, and a selection's
menu copies only the selection; these work in the full-screen `molterm edit` too.

- **SMILES** goes as text, through the terminal's clipboard sequence (OSC 52), so it reaches your clipboard even over SSH. A terminal can
  refuse it without saying so: Kitty and Ghostty allow it by default, while tmux needs `set -g set-clipboard on`.
- **SVG and PNG** are drawn by RDKit, 600 by 450 on white, and go through Kitty's clipboard protocol (OSC 5522), which can put several
  types on the clipboard at once: a paste into a document gets the picture, and a paste into a text field gets the text. For SVG that text
  is the SVG itself, and for PNG it's the SMILES. The text goes by OSC 52 first, so terminals without Kitty's protocol, Ghostty among
  them, still get it, and only it. Kitty answers whether it took the copy, and the status line says so.

A drawing left as it was keeps its SMILES exactly, an empty drawing isn't saved, and an invalid one stays open with the reason.
Clicks outside the box do nothing, so a stray one can't lose work.

To open the same box from another text interface, run the client with the area to use and read the result:

```sh
new=$(molterm -b 2,4,26,100 -T 'Compound 3' edit "$old")   # row,col,rows,cols from 0 at the top left, counting the border
case $? in
    0) ;;             # Done: $new is the SMILES
    3) new=$old ;;    # Cancel
    *) ;;             # it failed, and said why on stderr
esac
```

The box stays inside that area and doesn't change screens, but the host has work to do around it:

- Stop reading the terminal while the client runs.
- Afterwards, redraw what the box covered, turn your own mouse reporting on again, and hide the cursor again if you hid it. The client can't
  know what they were.
- Keep your own Kitty images at z -1073741834 or below while the box is open, or delete them first, as anything above that would show over it.
  Don't use image ids 7001 to 7021.
- The box needs at least 38 columns and enough rows for four of canvas, counting its border. Otherwise the client exits with 1.

A host that talks HTTP can skip the client: `POST /api/v1/edit` takes `box` and `title`, and answers `3` in its first line on Cancel.
`server/board.py`, behind `/api/v1/tui`, shows one way to use the editor from Python.

The client also reads the terminal's background and text colours, to fit `theme` to them and blend Sixel's edges, and its cell size, because Sixel's
pixels are the screen's and can't be scaled. It needs only `sh`, `curl`, `stty` and `dd` (the editor also `od`).

The same modes work on `render.txt` directly, with `theme=light` on a light terminal, since `curl` alone can't ask. Molecules
without bonds, such as `[Na+].[Cl-]`, stay as text. Images can't be copied as text, and `color` does not apply to them.

| module | job |
|---|---|
| `server/molecule.py` | SMILES or molblock to a sanitized molecule with 2D coordinates. A molblock's own 2D layout is kept; 3D or all-zero coordinates are replaced. From an SDF, only the first record is read. |
| `server/braille.py` | Boolean bitmap to Braille characters, 2 dots wide and 4 tall per character. |
| `server/octant.py` | The same bitmap to Unicode octants. The table was checked entry by entry against the Unicode 16 names. |
| `server/asciiart.py` | Bonds as plain ASCII lines, from the atoms' positions rather than from a bitmap. |
| `server/render.py` | RDKit draws the bonds at 4x, which are reduced to dots and mapped to characters. Each cell takes the colour of the element covering most of it. Atom labels are real letters placed over them. |
| `server/samples.py` | The demo page's samples. Each one's formula, legibility and PubChem identity by InChIKey were checked. |
| `server/medchem.py` | The web terminal's commands and its 15 compounds, each matched to its name on PubChem by InChIKey. |
| `server/story.py` | The guided session. Each finding it states is checked against the real output by `tests/test_medchem.py`. |
| `server/graphics.py` | RDKit draws the whole molecule, labels included, as a transparent image cropped to whole columns. Kitty and iTerm2 get a PNG with only its width given, so the terminal keeps its shape with any cell size. Sixel is encoded here, at the terminal's real cell size. |
| `server/client/molterm.sh` | The terminal client, served at `/molterm`: asks the terminal what it supports and chooses a mode, then draws one molecule or runs the guided session. |
| `server/editor_icons.py` | The editor's tool palette, drawn as one image with Pillow. |
| `server/editor.py` | The molecular editor: all of its input handling, geometry and drawing. The drawing is kept as plain atoms and bonds, since it often isn't valid chemistry while it's being made, and RDKit reads it as a molblock for each frame. |
| `server/annotate.py` | Annotations: highlights, notes, atom numbers and stereo labels, as CXSMILES, drawn by RDKit, and copied as SMILES, SVG and PNG. |
| `server/board.py` | `molterm tui`: the grid of structures, which hosts the editor in a box and carries its state inside its own. |
| `server/static/kitty.js` | The `/kitty` page: a line editor and the guided session, written for restty. It uses the same commands and story as the client, but not its code. |
| `server/placeholders.py` | Kitty graphics through tmux: pictures as Unicode placeholder cells, which tmux keeps as text. |
| `server/themes.py` | The colour themes: named ones, and ones fitted to the terminal's own background and text colours. |
| `server/showcase.py`, `server/static/showcase.js` | The `/showcase` demo: the script, which drives the list and editor as the client would, and the page that plays its frames. |

| endpoint | input | result |
|---|---|---|
| `POST /api/v1/parse` | JSON `input`, optional `format` (`auto`, `smiles`, `molblock`) | `format`, `coords`, `name`, `smiles`, `formula`, `atoms`, `bonds` |
| `POST /api/v1/render` | as above, plus optional `cols`, `rows`, `mode` (`braille`, `octant`), and `properties: true` | as above, plus `render` (`mode`, `text`, `ansi`, `colors`, `labels`, `rows`, `cols`, `bond_dots`, `cramped`), and if asked `properties` (`mw`, `logp`, `tpsa`, `hbd`, `hba`, `rotb`, `qed`, `ro5`, `pains`, `brenk`) |
| `POST /api/v1/properties` | JSON `{"inputs": [...]}`, at most 1000 SMILES or molblocks | `{"results": [...]}` in the same order, each `formula` and `properties` as above, or `error` |
| `GET /api/v1/render.png` | query `input`, `format`, `cols`, `rows`, `theme`, `background`, `foreground`, `cell_width` (pixels per column) | a transparent PNG within cols by rows cells (a cell taken as twice as tall as wide), cropped to its drawing; `X-Molterm-Cols` gives the columns it takes. For programs that place pictures themselves |
| `POST /api/v1/term` | JSON `line`, optional `selection` (the working set), `cols`, `display` (`text`, `kitty`, `iterm`, `sixel`), `cell_width` | `output` (text with ANSI colours, and images if asked), `selection`, `clear` |
| `GET /api/v1/term.txt` | query `line`, optional `selection` (comma-separated), `cols`, `display`, `theme`, `background` and `foreground` (`RRGGBB`, the terminal's, to fit the theme to), and for Sixel `cell_width` | a first line of `<1 to clear, or 0> <selection>`, then the output |
| `GET /api/v1/story.txt` | | the guided session, one step per line: title, command, before and after, separated by tabs |
| `GET /api/v1/showcase/visidata` | query `cols`, `rows` | `frames` of the VisiData scene (`caption`, `key`, `output`, `pause`), or 503 with `code` `recording` (ask again) or `unavailable` |
| `GET /api/v1/showcase` | query `cols`, `rows`, `cell_width`, `cell_height` | `frames`, each with `caption`, `output`, `pointer` (`[col, row]` or null), `click`, `key`, `pause` (ms) and `scroll` (lines to move the view, up if negative) |
| `GET /api/v1/render.txt` | query `input`, `format`, `cols`, `rows`, `mode` (`braille`, `octant`, `ascii`, also `kitty`, `iterm`, `sixel`), `color=1`; for images `theme` (see Colour themes), `background` and `foreground` (`RRGGBB`) and `fog` (0-100); for Sixel `cell_width` (pixels, 4-40) | plain text, with ANSI colours when asked, or image escape sequences |
| `POST /api/v1/edit` | form fields `cols`, `rows`, `events` (raw input, as hex), `state` (from the last answer) or `smiles` to begin, optional `cell_width`, `cell_height`, `pixels=1`, `theme`, `background`, `foreground` | a first line of `<0 to go on, 1 finished, 2 failed> <state>`, then what to print |
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
xterm with Sixel on dark and light backgrounds, xterm without Sixel (Braille), tmux in xterm (Sixel) and tmux in Kitty (Braille,
before placeholders). Placeholders were checked with a real tmux 3.5a: it passed the wrapped commands on unwrapped and drew the
placeholder cells, colour and all, and `molterm edit` was run inside a real tmux 3.5a under a terminal claiming to be Ghostty: both
pictures passed through and every placeholder named one of them; `tests/test_placeholders.py` checks the sequences, cells and order (pictures before menus) for
drawings, the editor and the list. In real Ghostty 1.3.1 under tmux 3.6, screenshots of the editor showed the palette drawn at 91%
of its cells, then at full size while hovering, which the clearing of ids described above fixes; the fix itself hasn't been seen
there yet.
`tests/test_client.py` runs the client under a fake terminal for the rest.

`molterm demo` was run the same way, against the dev server: in Kitty through `curl | sh`, with Enter, typed commands, `clear` and
`q`; in xterm with Sixel; and in xterm without it, where it fell back to Braille. `molterm play` has only run under the fake terminal.
`molterm edit` was run in real Kitty with a real pointer: palette tips and choices, ring previews, fusing, hotkeys on the atom under the
pointer, the label box, a marquee, moving a selection, undo, menus and a right-click menu covering the drawing, help, Clean Up, and quitting
with stdout redirected. 
It was then run in real Ghostty 1.4 on Linux, on a virtual display: the pointer landing on atoms and bonds, the hotspot, ring previews replacing
each other, ring keys doing nothing over nothing, six fast presses of `6` giving one ring, sprouting a chain, labels, a key over nothing changing
nothing, help covering the drawing, and quitting.
`tests/test_editor.py` drives it with raw input, including random input, tampered states, and checks that atoms are drawn where clicks find them.
`molterm tui` was run in the same Ghostty: hovering and clicking tiles, the box hiding the pictures below it, the hotspot and keys landing correctly
inside the box, its menus and help, a click outside doing nothing, Done redrawing the tile, Cancel, scrolling, resizing with the box open, and the
printed list. `-b` was run there from a shell script standing in for a host, with text all around the box. `tests/test_board.py` checks that nothing
from the box lands outside it, and refuses tampered states. Moving, resizing, maximizing and minimizing the window are checked there with raw
mouse input, including that the drawing still answers where it is shown after a move; they haven't been tried in a real terminal yet.
A drag step takes about 35 ms on the server, as the editor is drawn again at each step.

`/kitty` was checked in Chrome: a full automated run at normal speed, typing with Backspace, Tab, Up and a mistake, the scrollback,
no console errors, and no page overflow at 320, 375 and 640 px.

The Markush and Ketcher features are checked by `tests/test_markush.py`: each kind of text read into the editor and written back as it came,
the label box, the keys and the R-group tool, each menu and prompt, a variable attachment found where it's drawn, a definition made from a
selection and its logic undone, the chain tool's length, the Structure commands, a flip keeping a stereocentre, Clean Up keeping the
Markush parts, the renderers and the list, and tampered properties refused. The drawing of R-groups, attachment points, a repeating unit,
a stereo group, a generic atom and a bridging definition was checked by eye, and in the live VisiData demo in Chrome the new palette icons,
`r`, the bond menu, the atom's submenu, an atom list typed into the prompt and a dragged chain were used.

Annotations and copying are checked by `tests/test_annotate.py`: CXSMILES written and read back by RDKit with awkward note text, notes
following atoms through undo, delete, Clean Up and RDKit's reordering on save, the menus of atoms, bonds and selections, copying part of a
drawing, the list's menu from the mouse and the keys, and that what a copy sends decodes to the SMILES, a PNG and an SVG.
Their drawings were checked by eye: highlights, notes, atom numbers and an (S) label in the editor, on a tile and in an export.

`/showcase` was watched in Chrome: a full run with no console errors, the side-by-side screens, the window opening over the list, the
palette and hotspot in it, Done, the redrawn tile and the printed list. The edit's result, `CCOc1ccc(NC(C)=O)cc1F`, is checked by tests.
Its fog frames, all three ways of copying and the pasted drawing were watched there too, with the page's Copy button checked for selecting the right text.
The window being resized, moved, minimized into the top bar with every thumbnail of the list still showing, and brought back, was watched there as well,
and so were the copy in all three formats with its PNG preview, the highlight and note in the editor and on the tile, and the printed CXSMILES.
The themes frame, and the shell scene with its pictures kept in the scrollback while scrolling up to the first command and down to the prompt,
were watched there too, with no console errors. `tests/test_themes.py` checks every theme for legible ink, the original three for their old colours,
fitted themes, the endpoints, and the editor, list and session captions drawing in a theme; `tests/test_client.py` checks the client
sends the terminal's colours only when no `-t` is given.
Actual clipboard writes haven't been tested: not OSC 52 or Kitty's OSC 5522 in a real terminal, nor the page's browser copy. Fog has not been run in a real terminal; its images were checked by eye and by tests.

Open issues:

- Markush: a definition's own annotations, generic atoms and lists are written as plain SMILES inside `RG:`, so they don't survive; Copy
  Molfile leaves the definitions out (V3000 has RGROUP blocks for them); reactions, Ketcher's S-groups other than repeating units (data,
  multiple, superatom) and its templates library aren't there. ChemAxon's syntax for two attachment points in a definition isn't
  documented, so the atom maps are this editor's own. Markush drawing hasn't been seen in a real Kitty or Ghostty, only in restty.
- The atom's and the selection's right-click menus are 19 lines each, so a box needs at least 23 rows, counting its border, to show them whole; the Markush
  items are in submenus to keep them that short.

- The named themes haven't been seen in a real terminal with that theme, only on their own backgrounds in `/showcase` and by tests. A fitted
  theme keeps the base theme's element colours, which may sit poorly on an unusual background (for example dark red on a deep red one).
- `/showcase` scrolls restty's view by sending its canvas wheel events, which relies on restty treating them like a real wheel.

- `/showcase` needs at least 90 columns by 30 rows at the smallest font, so it says it can't start on a phone. It sizes itself once, so
  resizing the window needs a reload. The hotspot stays light blue in its black-on-white theme, as it's what shows where the keys act.
- restty paints cell backgrounds under the box's backing image, so grey bars and highlights inside the editor's box don't show there, only their text.
  Menus over a drawing show the drawing through them there, as they do over the list's tiles.
  The black-on-white theme's highlight is underlined for that reason. Real terminals aren't expected to do this.
- restty keeps about 10 MB of decoded images and drops the oldest, so `/showcase` asks for images at 7 pixels per column, and the editor's are a
  little soft there. In a real terminal they're sharp.

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
- `molterm edit` hasn't been seen in Ghostty on macOS, where a Retina display could change how pixel mouse reports line up with cells.
- `molterm edit` takes its keys from ChemDraw 17.1's guide, and versions differ: Shift+F for CF3 comes from a later ChemDraw guide. ChemDraw itself
  wasn't available to compare with, so where the guide is unclear these are readings of it: `0` curling round, what a "primary" carbon's sprout
  replaces, and the wedge on a tertiary carbon, which goes into the widest gap rather than vertically. `9` on an aromatic carbon gives an isopropyl
  group, which the table doesn't cover. Beyond the table, Delete on a plain carbon or a bond removes it.
- Keys in ChemDraw's tables for what `molterm edit` lacks say so and change nothing: R, X and D labels, attachment points, atom numbers, dashed, bold
  and wavy bonds, double bond position, chair rings, and the chain, bracket, arrow, symbol, pen and query tools. `/` and `?` on an atom or bond open
  its menu rather than a properties dialog. It has no captions, rotation handles or copy and paste, and terminals can't report Shift+Ctrl+Z, so
  redo is Ctrl-Y, and Clean Up is Ctrl-K.
- Each action in `molterm edit` is a request to the server, so a slow connection makes it lag. Inside tmux it needs Kitty or Ghostty
  outside and `allow-passthrough` on; through tmux every change reprints the picture cells, a few tens of KB a frame for a large
  window. With the mouse in use, selecting text needs the terminal's own override, usually Shift.
- `molterm tui` and `-b` have only been seen in Ghostty 1.4 on Linux, not in Kitty or on macOS, and `-b` only from that shell script, not from a
  real TUI framework. A framework may need suspending around the call so that it stops reading the terminal, and Kitty images it places at z 0
  would show over the box.
- `molterm tui` opens, edits and prints a list. It can't add or remove structures or read a file. It holds at most 60 structures, and cuts names
  at 60 characters. A very large drawing gives up its oldest undo steps to keep the state small enough to send.
- In a window under 64 columns or 16 rows, the box takes the whole window. In a narrow box the help text is cut at the edge.
- Pictures reach the clipboard only in Kitty: Ghostty, iTerm2 and others don't take OSC 5522 and get the text alone, and say nothing.
  The SVG and PNG are drawn at one size, with RDKit's own colours rather than the terminal's theme.
- Notes are cut at 40 characters and a drawing holds at most 400 annotations. Atom numbers in the editor follow the drawing's order, which
  after saving becomes the order of the written SMILES, so the numbers can change on Done. A bond note sits in RDKit's colour for notes.
- Annotation needs a menu: there are no keys for it, as ChemDraw's tables have none.
- The window can only be resized from its right and bottom edges, not its left or top. There is one window at a time, and the minimize and
  maximize buttons act on press, not on release as desktop buttons do. Only `molterm tui` moves it: a host using `-b` places the box itself.
- `molterm edit` keeps elements, charges, Kekulé bonds, wedges and 2D positions. A loaded SMILES with isotopes or unusual bonds loses
  them, and says so. It holds at most 200 atoms and 30 undo steps.
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
