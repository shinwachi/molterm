# UI style (House (calm light, dark follows the OS))

The owner's house style, distilled from their recent projects and feedback. `server/static/house.css` implements it;
build on its tokens and classes instead of adding new colors, sizes or frameworks.

## Principles

- **Calm and legible over flashy.** Readability beats saturation: if a theme is hard to read, lighten or mute it.
  No gradient heroes, glows or generic "AI dashboard" looks.
- **Lists and tables, not card grids.** Dense, sortable, filterable rows with a sticky header. Separate rows with
  whitespace and a faint hover tint, not borders. Offer a compact density when lists get long.
- **Whitespace instead of boxes.** Use a panel only where grouping really helps.
- **Subtle cues.** Faint tints with no outline for links, selection and highlights. No dotted lines or heavy borders.
- **Alignment matters.** Every control in a row has the same height (`--control-h`, 28px). Soft corners
  (3–4px; pills only for search fields and filter chips).
- **Small, dense type.** 13px base, 11–12px for secondary text, system sans; monospace for IDs, paths, hashes,
  SMILES, IPs and logs.
- **Light by default, dark available.** Both themes come from the same tokens; dark mode follows the OS and can be
  forced with `data-theme`.
- **One accent color** (blue) for actions and links; green/amber/red only for status, always with a label.
- **Sticky top bar** (44px): brand on the left, pill search and navigation on the right. Side panels for tools.
- **Assets are served locally** (vendored into the image), never from a CDN at runtime.

## Tokens

| token | light | dark | use |
|---|---|---|---|
| `--bg` | `#f6f5f1` | `#161615` | page |
| `--surface` | `#ffffff` | `#1f1f1e` | top bar, panels, inputs |
| `--surface-2` | `#eeede8` | `#2a2a28` | chips, hover |
| `--border` | `#dcdad3` | `#3a3a37` | hairlines |
| `--text` / `--text-2` / `--text-3` | `#1d1d1b` / `#5c5b55` / `#8a8980` | `#f0efec` / `#c3c2b7` / `#8f8e86` | ink levels |
| `--accent` / `--accent-tint` | `#2a78d6` / `#eef2fa` | `#3987e5` / `#1c2a3d` | actions / faint highlight |
| `--ok` `--warn` `--err` | `#2d7a5e` `#b45309` `#c62828` | `#3fae84` `#f0a500` `#ef5350` | status only |

Spacing `--sp-1..6`: 4, 8, 12, 16, 24, 48px. Radius: 3 / 4 / 8px.

## Classes in `server/static/house.css`

`.topbar` `.brand` · `main` · `.toolbar` · `.btn` `.btn.primary` `.btn.quiet` · `input.search` ·
`table.list` (`th[aria-sort]`, `.compact`, `td.num`) · `.chip` (with `<i>` swatch) · `.dot.ok|warn|err` ·
`.panel` · `.term` · `.muted` `.faint` `.mono`
