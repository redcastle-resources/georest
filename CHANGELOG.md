# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-09-07

First release as an installable distribution. Previously this was a loose
`RESTesri/` directory that imported only because the repository root happened to be
on `sys.path`.

### Changed

- **BREAKING:** `RESTesri` is no longer a top-level package. It is now
  `geoREST.RESTesri`, a subpackage of the new `geoREST` distribution.

  ```python
  from RESTesri import edw                  # before
  from geoREST.RESTesri import edw          # after — or: from geoREST import edw
  ```

  There is no compatibility shim: the package was never published, so every user
  installed it by cloning, and a top-level `RESTesri` module inside the wheel would
  squat a global import name.

- The `User-Agent` sent on every outbound request is now `geoREST/<version>` rather
  than `hostedServiceTools/1.0`, a name left over from a predecessor project.

- `VALID_THEMES` and `theme_drift()` moved from `tests/support.py` into
  `geoREST.RESTesri.edw`, so the shipped CLI no longer imports the test suite.
  `tests.support` re-exports both, so existing test imports still work.

- The theme-drift checker moved out of `tests/` and is now installed as the
  `georest-check-themes` console script.

- `refresh_fixtures.py` moved to `tools/`; it writes into `tests/fixtures/` and is
  deliberately repo-only, not an entry point.

### Added

- `pyproject.toml` (hatchling), `__version__`, PEP 561 `py.typed` marker, and an
  Apache-2.0 declaration via PEP 639.
- Lazy top-level re-exports (PEP 562): `from geoREST import edw, portal, services`.
- `tests/test_packaging.py`, which enforces the re-export identity
  (`geoREST.edw is geoREST.RESTesri.edw`) and the zero-runtime-dependency guarantee.
- CI: offline test matrix, a weekly live/drift run, and PyPI publishing via Trusted
  Publishing.

### Removed

- `scratch/` — both generator scripts hardcoded paths into a temporary directory
  that no longer exists, so neither could run; `georest-check-themes --emit`
  supersedes them.
- `esriToolsTest.ipynb` — a scratch notebook importing `arcgis.gis`, contrary to the
  stdlib-only design.
- All five `sys.path.insert(...)` bootstraps (two CLIs, three notebooks), made
  unnecessary by the `src/` layout plus an editable install.

[Unreleased]: https://github.com/redcastle-resources/geoREST/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/redcastle-resources/geoREST/releases/tag/v0.1.0
