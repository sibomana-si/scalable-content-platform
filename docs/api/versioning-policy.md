# API Versioning & Deprecation Policy

> **Status:** 🟥 Draft

## Versioning Scheme
- URI versioning: `/v1`, `/v2`, …
- Only **breaking** changes bump the major version. Additive changes (new optional fields, new endpoints) ship within the current version.

## What counts as breaking
- Removing/renaming fields or endpoints
- Changing types, required-ness, or default behavior
- Changing error codes/status for existing conditions

## Deprecation Process
1. Announce in [CHANGELOG](../CHANGELOG.md) and via `Deprecation` / `Sunset` headers.
2. Maintain old version for a stated window.
3. Remove after the window; document in release notes.
