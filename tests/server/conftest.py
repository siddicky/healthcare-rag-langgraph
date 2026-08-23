"""Deterministic deployment env for the server suites.

`create_app()` mounts the production custom app (`langgraph.json` ->
`healthcare_rag/agent/http_app.py:app`), whose lifespan validates the dedicated
feedback project. These suites are about server topology, not about the
operator's `.env`, so pin a syntactically valid test project id instead of
depending on one being configured. The probe itself stays offline: the root
conftest clears `LANGSMITH_API_KEY`, and without a platform credential
`validate_feedback_project` skips the LangSmith call. The real validation
contract is asserted in `tests/agent/test_deploy_config.py`.
"""

import os

os.environ.setdefault(
    "LANGSMITH_FEEDBACK_PROJECT_ID", "00000000-0000-4000-8000-000000000fee"
)
