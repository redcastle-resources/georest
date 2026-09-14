# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-09-14

### Added

- `portal.searchPortal(..., org_scoped=None)`. An ArcGIS Online organization URL
  (`https://<org>.maps.arcgis.com`) searches all of ArcGIS Online unless the query
  names the organization, so searching a city's portal for "evacuation" returned
  other states' layers first. `org_scoped` restricts results to the portal's own
  organization: `None` (default) scopes organization URLs automatically when `raw_q`
  isn't given, `True` always scopes, `False` never does.

### Changed

- **BREAKING:** `searchPortal` on an ArcGIS Online organization URL now returns only
  that organization's items. This includes the built-in `portal="nasa"`. Pass
  `org_scoped=False` for the 0.1.0 results.
- **BREAKING:** passing `q`, `num` or `f` through `searchPortal`'s `**filters` now
  raises `TypeError`. In 0.1.0, `q` silently replaced the assembled query (dropping
  `query` and `data_only`), and `f`/`num` overrode the response format and bypassed the
  100-result cap. Use `raw_q` and `limit` instead.
- Scoped searches make one extra `/sharing/rest/portals/self` request per portal and
  token, cached for the life of the process. If the organization id can't be
  resolved, `searchPortal` raises `RuntimeError` rather than silently searching all
  of ArcGIS Online.
- When scoping, parentheses and quotes that would break the `orgid:` clause are
  removed from a free-text `query`, which is searched as plain words. A `raw_q` that
  would break it raises `ValueError`.

## [0.1.0] - 2026-09-10

First release as an installable distribution. Previously this was a loose
`RESTesri/` directory that imported only because the repository root happened to be
on `sys.path`.

### Changed

- **BREAKING:** `RESTesri` is no longer a top-level package. It is now
  `georest.restesri`, a subpackage of the new `georest` distribution — lowercase,
  because a mixed-case import name resolves on Windows and macOS but fails on Linux.

  ```python
  from RESTesri import edw                  # before
  from georest.restesri import edw          # after — or: from georest import edw
  ```

  There is no compatibility shim: the package was never published, so every user
  installed it by cloning, and a top-level `RESTesri` module inside the wheel would
  squat a global import name.

- The `User-Agent` sent on every outbound request is now `georest/<version>` rather
  than `hostedServiceTools/1.0`, a name left over from a predecessor project.

- `VALID_THEMES` and `theme_drift()` moved from `tests/support.py` into
  `georest.restesri.edw`, so the shipped CLI no longer imports the test suite.
  `tests.support` re-exports both, so existing test imports still work.

- The theme-drift checker moved out of `tests/` and is now installed as the
  `georest-check-themes` console script.

- `refresh_fixtures.py` moved to `tools/`; it writes into `tests/fixtures/` and is
  deliberately repo-only, not an entry point.

### Added

- `pyproject.toml` (hatchling), `__version__`, PEP 561 `py.typed` marker, and an
  Apache-2.0 declaration via PEP 639.
- Lazy top-level re-exports (PEP 562): `from georest import edw, portal, services`.
- `tests/test_packaging.py`, which enforces the re-export identity
  (`georest.edw is georest.restesri.edw`) and the zero-runtime-dependency guarantee.
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

[Unreleased]: https://github.com/redcastle-resources/georest/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/redcastle-resources/georest/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/redcastle-resources/georest/releases/tag/v0.1.0
