"""Load the protocol modules without pulling in Home Assistant.

``custom_components/dolby_ims3000/__init__.py`` imports Home Assistant, which is
not available when running the tools standalone.  The protocol layer (klv, api)
has no such dependency, so we register a synthetic package and load just those
two modules into it.  Relative imports inside them resolve normally.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

PACKAGE = "_ims3000_standalone"
ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "dolby_ims3000"


def load() -> tuple[types.ModuleType, types.ModuleType]:
    """Return the ``klv`` and ``api`` modules."""
    if PACKAGE not in sys.modules:
        pkg = types.ModuleType(PACKAGE)
        pkg.__path__ = [str(ROOT)]
        sys.modules[PACKAGE] = pkg

    for name in ("klv", "api"):
        full = f"{PACKAGE}.{name}"
        if full in sys.modules:
            continue
        path = ROOT / f"{name}.py"
        if not path.is_file():
            raise SystemExit(f"cannot find {path} - run this from the repository root")
        spec = importlib.util.spec_from_file_location(full, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[full] = module
        spec.loader.exec_module(module)

    return sys.modules[f"{PACKAGE}.klv"], sys.modules[f"{PACKAGE}.api"]
