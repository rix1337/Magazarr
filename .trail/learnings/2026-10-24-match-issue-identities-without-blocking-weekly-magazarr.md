---
title: Match issue identities without blocking weekly releases
author: Magazarr
date: 2026-10-24
branch: main
touches:
  - magazarr/utils.py
  - magazarr/search.py
  - magazarr/db.py
  - magazarr/importer.py
  - magazarr/cover.py
---

## Decided
Remove blanket week/month aliases from dated issues. Keep calendar precision explicit. Infer month-based equivalence only from at least two distinct month-labelled releases in the same family/year; multiple dated issues within any month rule it out. Recognize month names separated from their year by an issue label and number. Keep year-qualified issue identities.

Reevaluate automatic duplicate skips so corrected matching recovers missed issues. Preserve manual/error blockers and duplicate skips backed by an explicitly deleted download of that issue. Use the same rendered first-page hash for import duplicate prevention and library duplicate inspection; retain imported alternate identities in download history.

## Tried and ruled out
A shared calendar month blocks distinct weekly and biweekly releases. It also misses numbered releases when month/year parsing fails. Publication timestamps cannot establish release cadence. Removing stale duplicate blockers without checking deletion history revives deliberately deleted issues.

## Watch out
Validate against current search results and a copied database, suppressing queue writes and notifications. Check repeated search cycles. Confirm first-page identity against actual PDFs. Monthly inference must reject malformed month-like weekly labels and preserve release variants. Tests use synthetic titles and URLs.
