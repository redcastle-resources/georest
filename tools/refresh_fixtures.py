"""Re-capture tests/fixtures/layer_cache.json from the live EDW catalog.

The fixture holds every service's real MapServer layer tree, letting the
layer-role tests run offline against genuine data. Refresh it when EDW
publishes or restructures services.

    pip install -e .          # georest must be importable
    python tools/refresh_fixtures.py

Services that cannot be fetched keep their existing cached entry, so a flaky
run degrades the fixture's freshness rather than destroying it.

Repo-only: this writes into tests/fixtures/, a path that does not exist in an
installed distribution, so it is deliberately not a console-script entry point.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from georest.restesri import edw
from georest.restesri._http import fetch_json

#: Repo root is the parent of tools/; the fixture lives under tests/.
TARGET = (Path(__file__).resolve().parent.parent
          / "tests" / "fixtures" / "layer_cache.json")


def main() -> int:
    existing = json.loads(TARGET.read_text(encoding="utf-8")) if TARGET.exists() else {}
    print(f"existing fixture: {len(existing)} services")

    for attempt in range(6):
        try:
            names = [s["name"] for s in edw.search_edw_services()]
            break
        except Exception as exc:  # noqa: BLE001
            print(f"  catalog attempt {attempt + 1} failed ({str(exc)[:60]})")
            time.sleep(3)
    else:
        print("could not reach the EDW catalog", file=sys.stderr)
        return 2

    print(f"live catalog: {len(names)} services")
    fetched = kept = 0
    result: dict[str, list] = {}

    for index, name in enumerate(names, 1):
        for attempt in range(3):
            try:
                data = fetch_json(f"{edw.EDW_BASE_URL}/{name}/MapServer",
                                  {"f": "pjson"})
                if "error" in data:
                    raise RuntimeError(data["error"].get("message"))
                layers = data.get("layers")
                if not layers:
                    # EDW intermittently answers 200 with no layers; treat it
                    # as a failed fetch rather than caching an empty tree.
                    raise RuntimeError("empty layers array")
                result[name] = layers
                fetched += 1
                break
            except Exception:  # noqa: BLE001
                time.sleep(2)
        else:
            if name in existing and existing[name]:
                result[name] = existing[name]
                kept += 1
                print(f"  kept cached copy of {name}")
            else:
                print(f"  MISS {name}")
        if index % 25 == 0:
            print(f"  ...{index}/{len(names)}")

    TARGET.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"\nwrote {TARGET}")
    print(f"  {fetched} refreshed, {kept} kept from the previous fixture, "
          f"{len(names) - fetched - kept} missing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
