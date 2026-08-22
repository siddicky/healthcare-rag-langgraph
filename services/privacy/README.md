# healthcare-rag privacy service

The Presidio/spaCy identifier analyzer that used to be baked into the LangGraph
deployment image, served over HTTP so the graph image no longer carries the
model. The graph-side client lives in `healthcare_rag/processors/privacy.py`;
everything policy-shaped (deterministic healthcare patterns, clinical-code
preservation, span union, `[REDACTED_*]` replacement) stays there. This service
only answers "which presidio spans are in this text".

## Contract

| route | auth | purpose |
|---|---|---|
| `GET /ok` | none | liveness for the platform probe |
| `GET /health` | bearer | readiness, pinned versions, entity inventory |
| `POST /analyze` | bearer | `{text, entities?, score_threshold?}` → `{results: [{start, end, entity_type, score}]}` |

The pins (`presidio-analyzer==2.2.364`, `spacy==3.8.15`, `en_core_web_sm==3.8.0`)
are the contract. The service refuses to start if the installed versions differ
or the sentinel scan fails; the graph-side client refuses to use a service whose
`/health` reports anything else. Inputs over 16 KiB are rejected. Request text is
never logged.

## Run locally

```
cd services/privacy
uv sync --extra dev
PRIVACY_SERVICE_TOKEN=dev-token uv run fastapi dev privacy_service/main.py
uv run pytest
```

## Deploy (FastAPI Cloud)

```
cd services/privacy
uv run fastapi login                       # once
uv run fastapi cloud env set --secret PRIVACY_SERVICE_TOKEN "<long random token>"
uv run fastapi deploy
```

Then give the graph deployment the same two values:

```
PRIVACY_SERVICE_URL=https://<app>.fastapicloud.dev
PRIVACY_SERVICE_TOKEN=<the same token>
```
