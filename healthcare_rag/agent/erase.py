from __future__ import annotations

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.store.base import BaseStore

from .memory import authenticated_user_id
from .reminders import cleanup_user_crons, deployment_client, sweep_upload_reservations
from .state import CoachState
from .store_data import coordinator_capability, delete_all_for_user

ERASE_MARKER_NAME = "erase_confirmation_v1"
ERASE_MARKER_CONTENT = "All saved data erased."


async def erase_my_data(
    state: CoachState,
    config: RunnableConfig,
    *,
    store: BaseStore,
) -> CoachState:
    """Coordinate fail-closed remote cleanup and privileged owner-store erasure."""
    del state
    user_id = authenticated_user_id(config)
    gate_namespace = ("users", user_id, "gate")
    await store.aput(gate_namespace, "erasing", {"active": True}, index=False)
    clean_crons = False
    clean_reservations = False
    try:
        async with deployment_client() as client:
            clean_crons = await cleanup_user_crons(store, user_id, client)
            clean_reservations = await sweep_upload_reservations(store, user_id, client)
        await delete_all_for_user(store, user_id, coordinator_capability())
    finally:
        await store.adelete(gate_namespace, "erasing")
    if not clean_crons or not clean_reservations:
        return {"messages": [], "follow_ups": []}
    return {
        "messages": [AIMessage(name=ERASE_MARKER_NAME, content=ERASE_MARKER_CONTENT)],
        "follow_ups": [],
    }


__all__ = ["ERASE_MARKER_CONTENT", "ERASE_MARKER_NAME", "erase_my_data"]
