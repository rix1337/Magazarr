---
title: "Failed Quasarr packages are cleaned up by the download sync, not the importer"
author: Magazarr
date: 2026-11-03
branch: main
cost: { minutes: 30, tokens: 506359 }
id: l-d625cbd1
scope: repo
status: active
src: agent
touches:
  - magazarr/downloads.py
  - magazarr/importer.py
  - magazarr/db.py
  - magazarr/utils.py
---

## Decided
The sync that marks failed Quasarr history items as download errors also deletes the package from Quasarr, on every pass where the failed item is still present. A failed release is recorded as skipped, and search skips any release title already downloaded from the same source, whatever its parsed issue key.

## Tried and ruled out
Relying on the importer's failed-history branch: the import check runs the sync first, which flips the row away from snatched, and the importer only reads snatched rows, so that branch never ran and failed packages stayed in Quasarr forever.

## Watch out
The source identity is the last field of the Quasarr payload link (title|url|size|password|imdb|source). Unknown sources fall back to matching on title alone.

In the code: `magazarr/downloads.py` `sync_download_errors` and `delete_finished_package`; `magazarr/db.py` `has_downloaded_release`; `magazarr/utils.py` `release_source`.
