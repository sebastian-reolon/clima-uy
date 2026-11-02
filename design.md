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

No decorative accent. Colour only marks a state: blue = rising or water,
red = alert. Everything else is ink.

## Typography

- Text: Overpass (400 to 800). Headings 700 to 750, roman, never italic.
- Numbers and title-block values: Overpass Mono, tabular.
- h1: clamp(1.9rem, 3.6vw, 2.9rem), line-height 1.08, max 24ch.
- Section h2: 1.05rem, 750.
- Self-hosted woff2 (latin + latin-ext), OFL licence files next to them.

## Components

- Sheet frame: 2 px ink border, max-width 76rem, on the gridded body.
- Top bar: wordmark left, nav right (Río, El Niño, Datos), 1 px rule under it.
- Cajetín: definition list in a ruled grid, label left in ink-2, value right
  in mono.
- Section: padding 1.5rem, 1 px rule under it, h2 plus one muted line that
  says what the block shows.
- Tables: headers small ink-2 with a 1.5 px ink rule under them, rows with
  grid-2 rules, numbers right-aligned in mono. On phones the city table turns
  into one block per row with labels.
- Profile drawing: x = km, y = percentile, Uruguayan stretch shaded with
  `--water-2`, city callouts under the axis with leader lines and a paper box
  behind each label. A cropped variant (from about Paso de los Libres) on
  phones.
- Notes: ordered list, small, ink-2.
- Footer: credit «Hecho por Sebastián Reolon», repo link, «Hoja n de N».

## Motion

None. Only `<details>` opening.

## Must not

- Cream paper, serif body, warm accent, cards with shadows.
- Uppercase eyebrows, italic headings, em dashes in copy.
- A number nobody measured. Every figure comes from the data or a cited source.
