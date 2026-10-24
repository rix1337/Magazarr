---
title: Preserve explicit issue identities and edition boundaries
author: Magazarr
date: 2026-10-24
branch: main
touches:
  - magazarr/search.py
  - magazarr/db.py
  - magazarr/importer.py
  - tests/test_duplicate_adversarial.py
  - tests/test_issue_sorting.py
---

## Decided
Explicit issue numbers own canonical issue keys and do not inherit inferred numbers or calendar-date aliases. Known edition families scope their aliases and storage keys. Generic releases cannot inherit a scoped edition's sequence. Keep dated numbered releases sorted by their parsed release date.

Infer date-only issue numbers only when supported source profiles agree on one number. Common offsets require at least two anchors and a strict majority; isolated malformed historical labels must not poison a well-supported sequence.

Cover comparison is intentionally sufficient for import duplicate detection. Do not add full-document comparison. Preserve library directories when an import source contains the destination.

## Tried and ruled out
Global date aliases merge separate editions. Applying inferred numbers to explicit numbered releases creates conflicting identities. Requiring every historical offset to agree disables duplicate blocking after one malformed source label. A sole scoped profile is not evidence about the generic series.

## Watch out
Exercise duplicate pairs and distinct releases, then repeat the complete search against SQLite state. Test agreeing and conflicting source profiles, scoped-only history, monthly alternate listings, weekly and biweekly dates, and dated-number sorting. Keep unrelated preexisting working-tree edits out of release commits.
