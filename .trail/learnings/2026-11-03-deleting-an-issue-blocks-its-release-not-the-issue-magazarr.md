---
title: Deleting an issue blocks its release title, not the whole issue
author: Magazarr
date: 2026-11-03
branch: main
touches:
  - magazarr/db.py
  - magazarr/web.py
  - magazarr/automation.py
---

## Decided
Deleting a library issue records a "Deleted manually" skipped release for its title. That blocks the title from every source, shows under Skipped, and "Download anyway" undoes it. A migration backfills the block for downloads already marked deleted.

## Tried and ruled out
Treating deleted downloads as owned issues, so every release with that issue key is skipped. Real deletions were mostly mismatched releases (books, unrelated titles) that had been given a real issue's date key. An issue-level block would silently stop the real issue from downloading. A separate download status for deleted issues was also dropped for the same reason.

## Watch out
Download status 'deleted' is shared by deleted library issues and by downloads skipped or deleted from the Downloads sheet. Skipping a download must keep other releases of that issue eligible. A deleted issue without a download record has no URL, so unskipping it only unblocks.
