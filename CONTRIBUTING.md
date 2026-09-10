# Contributing to georest

## Setup

```bash
git clone https://github.com/redcastle-resources/georest
cd georest
python -m venv .venv && . .venv/Scripts/activate    # Windows; use bin/activate elsewhere
pip install -e ".[dev]"
```

The editable install is **required**, not a convenience. This is a `src/` layout, so
`georest` is not importable from the repository root without it. That is the point:
the test suite imports the installed artifact, so a packaging mistake — a missing
`__init__.py`, a wrong `packages =` — fails loudly instead of being masked by the
working directory.

## The one rule that matters

**georest has zero third-party runtime dependencies, and must keep it that way.**
Everything is stdlib `urllib` and `json`. This is what lets the package install into
an ArcGIS Pro `arcgispro-py3` clone or a locked-down agency environment without a
resolver fight. `tests/test_packaging.py` enforces it by AST-walking every module in
the package, so adding an import of `requests` fails the suite rather than shipping.

If you genuinely need a third-party library, it belongs in an optional extra with a
lazy import at the call site — never at module scope.

## Tests

```bash
python -m pytest                              # offline, deterministic
python -m unittest discover -s tests -t .     # the same suite, stdlib runner
EDW_LIVE=1 SERVICES_LIVE=1 python -m pytest   # include network tests
ruff check .
```

`ruff format` is **not** enforced yet. The code predates any formatter, so
adopting one reformats 21 files at once; that deserves its own commit rather
than being smuggled into unrelated diffs. The lint ruleset in `pyproject.toml`
is likewise a starter set, with the deferred rules listed there as a backlog —
turn them on one at a time so each diff stays reviewable.

Tests are written as `unittest.TestCase` classes to match the package's own
stdlib-only posture; pytest collects them natively and is what CI runs. Either
runner is fine locally.

Live tests are opt-in and **skip** rather than fail when a server is unreachable.
EDW intermittently answers 404, 500, `Layer not found`, or 200-with-no-layers for
services that work moments later — a red run there says nothing about your change,
which is why CI leaves `EDW_LIVE`/`SERVICES_LIVE` unset and runs them on a weekly
schedule instead.

## Fixtures

`tests/fixtures/layer_cache.json` holds real MapServer layer trees so the layer-role
logic runs offline against genuine data. Refresh it when EDW restructures services:

```bash
python tools/refresh_fixtures.py
```

Services that cannot be fetched keep their existing cached entry, so a flaky run
degrades freshness rather than destroying the fixture.

## The theme table

`edw._SERVICE_THEMES` is hand-curated and rots as EDW publishes and retires
services. Check it with:

```bash
georest-check-themes --emit
```

Paste the emitted entries into `src/georest/restesri/edw.py` under the right theme
heading, replacing `THEME` with the correct category from `edw.VALID_THEMES`.

## Notebooks

Strip outputs before committing, so diffs stay reviewable:

```bash
jupyter nbconvert --clear-output --inplace examples/*.ipynb
```

## Releasing

1. Bump `__version__` in `src/georest/__init__.py` — the single source of truth;
   hatchling reads it by AST and `pyproject.toml` carries no literal version.
2. Move the `## [Unreleased]` section of `CHANGELOG.md` under a new dated heading.
3. Commit, then `git tag -a v0.1.0 -m "v0.1.0"` and push both.
4. Create the GitHub Release from the tag. That fires `publish.yml`, which builds,
   publishes to TestPyPI, installs from TestPyPI and smoke-tests it, and only then
   publishes to PyPI behind an environment approval.

Version numbers are **immutable on PyPI and TestPyPI alike** — a bad release can be
yanked but never replaced. Use `0.1.0.dev1`, `.dev2`, … for rehearsals so the real
number stays available.
