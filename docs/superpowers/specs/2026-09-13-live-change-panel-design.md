# Live Change Panel Design

## Goal

Show Advertpreneur's current file-change evidence directly above the persistent
composer while a task is running. The panel must make activity legible without
moving, obscuring, or redrawing the composer, joke row, or bottom status bar.

## Layout and interaction

The collapsed panel is one clickable row above `Message Advertpreneur`:

```text
⠋ Working · 32.4s · 3 files · +84 -21   [click to expand]
╭─ Message Advertpreneur ─────────────────────────╮
```

Selecting the row with the mouse toggles an expanded view. The expanded view
lists each observed changed file with its own added/removed counts and a small,
bounded summary. A second click collapses it. Keyboard input remains in the
composer and is never consumed by this panel.

## Data and safety

The panel uses only local evidence from the active task checkpoint and Git
diff/checkpoint analysis. It shows no provider reasoning and does not infer
file changes from model text. Before the first observed change it reports `No
file changes observed yet`; non-Git work uses checkpoint paths where available.

For Git projects, a daemon tracker samples the active checkpoint once per
second with read-only Git diff/stat commands and updates the panel only when
the observed rows change. This is intentionally separate from the 5 FPS
renderer, which only repaints already-known UI state. Non-Git projects avoid
repeated full manifests and show their final checkpoint paths when finalized.

The panel is rendered within Prompt Toolkit's live composer surface. Its timer
and spinner continue to repaint at five frames per second through application
invalidation; no background ANSI cursor writes are permitted.

## Boundaries and failure behavior

The expanded list is bounded to keep the composer visible. If diff data is
unavailable, the panel retains known file names and displays an explicit
unavailable state rather than a fabricated line count. It resets at the start
of a new task and is removed on task completion, leaving the durable result
card in scrollback.

## Validation

Tests will cover collapsed summaries, expanded per-file rendering, click
toggle behavior, no-change/unavailable states, and preservation of the joke
and status rows. A manual terminal check will confirm the composer remains
editable while the panel timer changes.
