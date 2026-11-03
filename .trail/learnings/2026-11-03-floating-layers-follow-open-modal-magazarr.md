---
title: Toasts and the job panel move into the open modal dialog
author: Magazarr
date: 2026-11-03
branch: main
touches:
  - assets/common.js
  - assets/app.js
  - assets/app.css
  - magazarr/automation.py
---

## Decided
Modal dialogs render in the top layer and make the rest of the page inert, so toasts and the search job panel are re-parented into the topmost open modal (job panel: sheets only, so it never covers a centred modal's buttons; never the small confirm dialog). Manual searches run on one FIFO worker; the client orders its queue list by server job id because parallel requests reach the server out of click order.

## Tried and ruled out
Popover top-layer elements: they stay inert behind a modal dialog, so their buttons cannot be clicked.

## Watch out
Dialog open animations must fill backwards only. A `both` fill keeps a transform, which makes the dialog the containing block of the fixed layers and clips them to its box. Job events are capped at 300 on the server; the client uses `event_count` to find new events.
