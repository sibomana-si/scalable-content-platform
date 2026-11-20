#!/usr/bin/env python
"""Export the app's OpenAPI schema to ``docs/api/openapi.json``, or check that it is current.

The committed file is the API contract. A route or schema change then shows up as a diff that a
reviewer reads, instead of reaching clients unseen. Regenerate it after any change to a route,
a request or response model, or the app version:

```bash
python scripts/export_openapi.py
```

CI runs ``python scripts/export_openapi.py --check`` in the ``lint`` job. The check fails when
the committed file differs from the schema the app serves, and it prints the diff.

The output has sorted keys, two-space indentation and one trailing newline, so it is the same
on every run and every machine. The check compares bytes, so a hand-edited or reformatted file
fails too.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

# Run as `python scripts/export_openapi.py`, sys.path[0] is `scripts/`, so `import app` fails.
# pytest hides this, because `pythonpath = ["."]` puts the repository root on the path for it.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.main import create_app  # noqa: E402

DEFAULT_PATH = REPO_ROOT / "docs" / "api" / "openapi.json"
FIX_COMMAND = "python scripts/export_openapi.py"


def render_schema() -> str:
    """Return the app's OpenAPI schema as the exact text of the committed file."""

    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


def check(path: Path) -> int:
    """Return 0 when ``path`` holds the rendered schema. Otherwise print the diff and return 1."""

    expected = render_schema()
    if not path.exists():
        print(f"{path} does not exist. Run `{FIX_COMMAND}` and commit the file.")
        return 1

    committed = path.read_text(encoding="utf-8")
    if committed == expected:
        print(f"{path} matches the app schema.")
        return 0

    diff = difflib.unified_diff(
        committed.splitlines(keepends=True),
        expected.splitlines(keepends=True),
        fromfile=f"{path} (committed)",
        tofile="app schema",
    )
    sys.stdout.writelines(diff)
    print(f"\n{path} differs from the app schema.")
    print(f"Run `{FIX_COMMAND}`, review the diff, and commit.")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare the file with the app schema and exit 1 on any difference",
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=DEFAULT_PATH,
        help="the schema file (default: docs/api/openapi.json)",
    )
    args = parser.parse_args(argv)

    if args.check:
        return check(args.path)

    args.path.write_text(render_schema(), encoding="utf-8")
    print(f"Wrote {args.path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
