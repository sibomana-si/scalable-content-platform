# API Versioning & Deprecation Policy

> **Status:** 🟥 Draft

## Versioning Scheme
- URI versioning: `/v1`, `/v2`, …
- Only **breaking** changes bump the major version. Additive changes (new optional fields, new endpoints) ship within the current version.

## What counts as breaking
- Removing/renaming fields or endpoints
- Changing types, required-ness, or default behavior
- Changing error codes/status for existing conditions

## Breaking changes taken before 1.0

The policy above starts at 1.0. Until then a breaking change ships inside `/v1`, and it is
recorded here rather than hidden. The project has no external consumers, so the cost of the
break is small now and large later.

| Date | Change | Reason |
|---|---|---|
| 2026-08-22 | `GET /v1/articles` items no longer carry `body`. Read one article to get its text | A 20-item page was 101,367 bytes, 96.7% of it body text ([bottleneck analysis](../performance/bottleneck-analysis.md), F2) |

## Deprecation Process
1. Announce in [CHANGELOG](../CHANGELOG.md) and via `Deprecation` / `Sunset` headers.
2. Maintain old version for a stated window.
3. Remove after the window; document in release notes.
