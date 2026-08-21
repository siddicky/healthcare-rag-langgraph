"""Auth-scoped coach memory; the model-free ``cron_wake`` route never calls this module."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, ClassVar, Literal, final, override
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedStore
from langgraph.store.base import BaseStore
from pydantic import BaseModel, ConfigDict, ValidationError

from healthcare_rag.graph.resources import get as get_resources
from healthcare_rag.processors.privacy import PrivacyScanError

MemoryKind = Literal["profile", "episodic"]

_PRIVACY_REFUSAL = "Memory not saved: privacy checks failed."
_STORAGE_REFUSAL = "Memory not saved: storage unavailable."


class _AuthPrincipal(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    identity: str


@final
@dataclass(frozen=True, slots=True)
class MemoryIdentityError(Exception):
    code: str = "MEMORY_AUTH_IDENTITY_REQUIRED"

    @override
    def __str__(self) -> str:
        return self.code


def authenticated_user_id(config: RunnableConfig) -> str:
    principal = config.get("configurable", {}).get("langgraph_auth_user")
    if not isinstance(principal, Mapping):
        raise MemoryIdentityError
    try:
        identity = _AuthPrincipal.model_validate(principal).identity
    except ValidationError:
        raise MemoryIdentityError
    if not identity:
        raise MemoryIdentityError
    return identity
def sanitize_memory_field(value: str) -> str | None:
    """Apply the single scrub-then-rescan policy shared by every memory writer."""
    privacy = get_resources().privacy
    try:
        scrubbed = privacy.scan(value).text
        sweep = privacy.scan(scrubbed)
    except PrivacyScanError:
        return None
    if sweep.kinds:
        return None
    return scrubbed


async def remember_fact_impl(
    fact: str,
    kind: MemoryKind,
    config: RunnableConfig,
    store: Annotated[BaseStore, InjectedStore()],
    user_id: str | None = None,
) -> str:
    """Save one profile or episodic fact after privacy sanitization."""
    del user_id
    user_identity = authenticated_user_id(config)
    clean = sanitize_memory_field(fact)
    if clean is None:
        return _PRIVACY_REFUSAL
    try:
        await store.aput(
            ("users", user_identity, kind),
            str(uuid4()),
            {"fact": clean, "kind": kind},
        )
    except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK - store boundary.
        return _STORAGE_REFUSAL
    return "Memory saved."


remember_fact = tool("remember_fact")(remember_fact_impl)


async def dynamic_prompt(config: RunnableConfig, store: BaseStore) -> str:
    """Build Route B's memory segment for the authenticated user, or return blank."""
    user_id = authenticated_user_id(config)
    profile = await store.asearch(("users", user_id, "profile"), limit=100)
    episodic = await store.asearch(("users", user_id, "episodic"), limit=100)
    facts = [item.value.get("fact") for item in (*profile, *episodic)]
    clean_facts = [fact for fact in facts if isinstance(fact, str)]
    if not clean_facts:
        return ""
    return "## Saved user memories\n" + "\n".join(f"- {fact}" for fact in clean_facts)


__all__ = [
    "MemoryIdentityError",
    "authenticated_user_id",
    "dynamic_prompt",
    "remember_fact",
    "sanitize_memory_field",
]
