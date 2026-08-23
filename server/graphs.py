from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from server.config import ServerConfig
from server.storage import Storage


def _load_from_import_string(import_string: str):
    # import_string like "./healthcare_rag/graph/__init__.py:graph"
    if ":" in import_string:
        file_part, attr = import_string.split(":", 1)
    else:
        file_part, attr = import_string, None
    # Resolve path relative to project root
    p = Path(file_part)
    if p.suffix == ".py":
        # load via spec
        spec_name = f"_server_graph_{p.stem}_{attr or 'mod'}"
        spec = importlib.util.spec_from_file_location(spec_name, p)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load spec for {import_string}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec_name] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        if attr:
            return getattr(mod, attr)
        return mod
    else:
        # dotted import
        mod = importlib.import_module(file_part)
        if attr:
            return getattr(mod, attr)
        return mod


def load_raw_graphs(config: ServerConfig) -> dict[str, object]:
    out: dict[str, object] = {}
    for name, imp in config.graphs.items():
        out[name] = _load_from_import_string(imp)
    return out


def attach_graphs(raw_graphs: dict[str, object], storage: Storage) -> dict[str, object]:
    attached: dict[str, object] = {}
    for name, g in raw_graphs.items():
        builder = getattr(g, "builder", None)
        if builder is None:
            raise RuntimeError(f"Graph {name!r} has no .builder attribute")
        # Recompile from builder with checkpointer and store
        recompiled = builder.compile(checkpointer=storage.saver, store=storage.store, name=name)
        attached[name] = recompiled
    return attached
