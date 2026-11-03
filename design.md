# Design: clima-uy

Locked design system for every page of the site. A page change reads this file
first. If a page needs something this file does not allow, amend this file in
the same commit.

Approved mockup: `docs/design/plano-mockup.html` (screenshot
`docs/design/plano-mockup-1280.png`). Pages port it verbatim: same structure,
same tokens, same copy patterns.

## Idea

A drawing sheet. Each page is a sheet inside a frame, with a title block
(cajetín), sections separated by rules, data in tables aligned on the decimal,
one technical drawing (the river's longitudinal profile) and numbered notes at
the end. Technical and plain: hierarchy comes from order and size, never from
decoration.

## Genre and tone

Editorial engineering. Tone: technical, austere.

## Hierarchy (every page)

1. One sentence that answers the page's question (h1), with one or two
   sentences under it.
2. The table the reader came for.
3. The drawing.
4. Secondary blocks, two side by side on wide screens.
5. Long lists folded in `<details>`.
6. Numbered notes, then the footer.

## Theme

- `--paper`   oklch(97.8% 0.002 250)
- `--grid`    oklch(92.5% 0.006 245)  background grid, 16 px
- `--grid-2`  oklch(88% 0.01 245)     table rules, chart grid
- `--ink`     oklch(21% 0.012 255)
- `--ink-2`   oklch(46% 0.012 255)
- `--line`    oklch(21% 0.012 255)    frame and section rules
- `--water`   oklch(48% 0.13 245)     rising values, Uruguayan stretch
- `--water-2` oklch(48% 0.13 245 / .12)
- `--alert`   oklch(54% 0.2 28)       above an alert level, alert lines
- `--focus`   same as `--water`
- `--hov-0` to `--hov-5`  oklch(96% 0.01 240), oklch(89% 0.035 240),
  oklch(78% 0.07 242), oklch(65% 0.1 245), oklch(50% 0.12 250),
  oklch(35% 0.11 260): the percentile bands of the crecida diagram
  (<50, 50 to 75, 75 to 90, 90 to 95, 95 to 99, 99 or more), a sequential
  blue because every cell is water. Used nowhere else.

No decorative accent. Colour only marks a state: blue = rising or water,
red = alert. Everything else is ink.

## Typography

- One family, no monospace: Barlow (400 to 800) for text, tables and numbers,
  with `font-variant-numeric: tabular-nums` on body so figures align.
- h1: Barlow Semi Condensed 600/700, clamp(1.9rem, 3.6vw, 2.9rem),
  line-height 1.08, max 24ch. Roman, never italic.
- Section h2: 1.05rem, 750.
- Self-hosted woff2 (latin + latin-ext), OFL licence next to them.
- Chosen by the owner on 2026-11-02 over Overpass (read as too "coder"),
  Public Sans and Archivo.

## Components

- Sheet frame: 2 px ink border, max-width 76rem, on the gridded body.
- Top bar: wordmark left, nav right (Río, El Niño, Datos), 1 px rule under it.
- Cajetín: definition list in a ruled grid, label left in ink-2, value right.
- Section: padding 1.5rem, 1 px rule under it, h2 plus one muted line that
  says what the block shows.
- Tables: headers small ink-2 with a 1.5 px ink rule under them, rows with
  grid-2 rules, numbers right-aligned, tabular. On phones the city table turns
  into one block per row with labels.
- Profile drawing: x = km, y = percentile, Uruguayan stretch shaded with
  `--water-2`, city callouts under the axis with leader lines and a paper box
  behind each label. A cropped variant (from about Paso de los Libres) on
  phones.
- Pages without a title block (Datos, Metodología, Fotos diarias): the h1
  block alone, full width (`.cab.uno`).
- Other tables on phones: the El Niño outlook stacks like the city table
  (one block per region, one labelled line per season); long reference
  tables (catalogue, all stations, forecast checks) scroll inside their
  wrapper, with their explanatory line above the table, never in a caption.
- River column (`/rio/`): the river is a 2 px `--water` rule down the left,
  each station a dot on it (filled ink for a Uruguayan city), tributaries a
  1 px branch. Three columns (name, gauge, reading) from 64em; one below.
- Dams: a ruled grid of label and value, no boxes.
- Crecida diagram (`/rio/crecida/`, approved mockup `d-crecida.html` of
  2026-11-02): x = km as in the profile, y = the last 60
  days with the most recent at the bottom, each cell a station's day in
  the `--hov-*` band of its percentile, blank without data. Local peaks
  are paper dots ringed in `--alert`; the Uruguayan stretch is a 2 px
  `--water` bracket on top. A 360-wide variant on phones. Under it, a
  range input labelled «Día» (rows are clickable too) redraws that day's
  profile (on phones its city names take one row each above the plot, with
  leaders); the diagram reads in full without JavaScript, and the selection
  outline and the row pointer only appear with it. The last row is today,
  marked «(hoy, parcial)».
- Plano de ciudad (`/rio/mapa/<city>/`, approved mockup E «Plancheta» of
  2026-11-03): a technical city plan, 10 m per unit, north up, with blocks
  in `--grid`, main streets in ink-2, dashed contours in metres of the
  city's gauge, barrio names in ink with a paper halo, the river in
  `--water-2` ringed in `--water`, a 1 km scale bar, a north arrow and
  1 km ticks on the frame. The ground below the chosen height is hatched
  in `--water` at 45° (an estimate); what the Sentinel-1 radar saw is solid
  `--water` (an observation), so the two never read alike. Beside it, a
  vertical gauge staff (drag it, or tap a mark) and a reading ruled in ink:
  height, mark name, people and hectares below. Buttons for the cited
  marks above the plan; «hoy» is pressed when the page opens. On phones
  (48em and below) the staff gives way to a range input labelled «Río» and
  the plan is a crop of the riverside (480 units wide, at most 32rem),
  labels drawn at 11 px or more at 320 px. Without JavaScript the plan,
  the reading and the zones table show today's height (or the evacuation
  mark) and a noscript line says choosing another height needs it.
  People are always «unas N» (rounded) or «menos de 15», never exact.
- Notes: ordered list, small, ink-2.
- Footer: credit «Hecho por Sebastián Reolon», repo link, «Hoja n de N».

## Motion

None. Only `<details>` opening, the crecida page's day selector and the
city map's height selector, which redraw on input without animating. The old profile's grow-in script
(`static/js/morph.js`) is no longer loaded by `/rio/`; it stays for the frozen
snapshots that link it.

## Must not

- Cream paper, serif body, warm accent, cards with shadows.
- Uppercase eyebrows, italic headings, em dashes in copy.
- A number nobody measured. Every figure comes from the data or a cited source.
