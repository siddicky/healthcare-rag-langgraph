from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ServerConfig:
    graphs: dict[str, str]
    auth_path: str | None
    http_app: str | None
    http_flags: dict[str, object]
    store_index: dict[str, object]
    api_version: str
    storage: str = "memory"
    database_uri: str | None = None
    port: int = 8000
    local_dev: bool = False
    raw: dict[str, object] = field(default_factory=dict, repr=False)


def load_config(path: str | Path = "langgraph.json") -> ServerConfig:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {p}")
    data = json.loads(p.read_text())

    # api_version
    api_version = str(data.get("api_version", "0.1.0"))

    # graphs
    graphs = dict(data.get("graphs", {}))

    # auth
    auth_cfg = data.get("auth", {}) if isinstance(data.get("auth"), dict) else {}
    auth_path = auth_cfg.get("path") if isinstance(auth_cfg.get("path"), str) else None

    # http
    http_cfg = data.get("http", {}) if isinstance(data.get("http"), dict) else {}
    http_app = http_cfg.get("app") if isinstance(http_cfg.get("app"), str) else None
    # include http flags incl disable_studio_auth default false
    http_flags: dict[str, object] = dict(http_cfg)

    # store.index
    store_cfg = data.get("store", {}) if isinstance(data.get("store"), dict) else {}
    store_index = store_cfg.get("index", {}) if isinstance(store_cfg.get("index"), dict) else {}

    # env
    storage = os.environ.get("SERVER_STORAGE", "memory")
    if storage not in ("memory", "postgres"):
        raise ValueError(f"Unknown SERVER_STORAGE={storage!r}; expected 'memory', 'postgres'")

    # database URI resolution (DATABASE_URI canonical, DATABASE_URL fallback for Fly)
    _uri = os.environ.get("DATABASE_URI")
    _url = os.environ.get("DATABASE_URL")
    # treat empty string as unset
    uri = _uri if _uri else None
    url = _url if _url else None
    if uri is not None and url is not None and uri != url:
        raise ValueError(
            "DATABASE_URI and DATABASE_URL are both set but differ; keep exactly one set"
        )
    database_uri: str | None = uri if uri is not None else url
    if storage == "postgres" and database_uri is None:
        raise ValueError("DATABASE_URI is required when SERVER_STORAGE=postgres")

    port_raw = os.environ.get("SERVER_PORT", "8000")
    try:
        port = int(port_raw)
    except ValueError as e:
        raise ValueError(f"Invalid SERVER_PORT={port_raw!r}") from e

    local_dev_raw = os.environ.get("SERVER_LOCAL_DEV", "0")
    local_dev = local_dev_raw not in ("0", "", "false", "False")

    # allow override: SERVER_LOCAL_DEV=1 enables
    if local_dev_raw == "1" or local_dev_raw.lower() == "true":
        local_dev = True

    return ServerConfig(
        graphs=graphs,
        auth_path=auth_path,
        http_app=http_app,
        http_flags=http_flags,
        store_index=dict(store_index),
        api_version=api_version,
        storage=storage,
        database_uri=database_uri,
        port=port,
        local_dev=local_dev,
        raw=dict(data),
    )
