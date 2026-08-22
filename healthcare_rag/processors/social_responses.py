from __future__ import annotations

from dataclasses import dataclass
from typing import Final, assert_never

from healthcare_rag.models.safety import SocialIntent
from healthcare_rag.services.models import QueryResponseArm


@dataclass(frozen=True, slots=True)
class SocialArmOutput:
    safety_response: str
    direct_response: str | None
    response_action: str | None
    safety_kind: str
    response_kind: str
    short_circuited: bool
    route_kind: str

    def for_social_turn(
        self,
        arm: QueryResponseArm,
        intent: SocialIntent | None,
        benign_social: bool,
    ) -> SocialArmOutput:
        if not benign_social or intent is None:
            return self
        return social_arm_output(arm, intent, self)


def default_social_arm_output(
    safety_response: str,
    safety_kind: str,
    short_circuited: bool,
) -> SocialArmOutput:
    return SocialArmOutput(
        safety_response=safety_response,
        direct_response=None,
        response_action=None,
        safety_kind=safety_kind,
        response_kind=safety_kind,
        short_circuited=short_circuited,
        route_kind=safety_kind,
    )


def social_response(intent: SocialIntent) -> str:
    match intent:
        case "greeting":
            return (
                "Hello. I can help with questions grounded in the Lipitor "
                "(atorvastatin) and metformin product monographs."
            )
        case "thanks":
            return (
                "You're welcome. I'm here if you have another question about the "
                "Lipitor or metformin product monographs."
            )
        case "goodbye":
            return "Goodbye. Take care."
        case "capability":
            return (
                "I can explain information from the Lipitor (atorvastatin) and metformin "
                "product monographs, including uses, dosing in general, side effects, "
                "interactions, warnings, and monitoring."
            )
        case unreachable:
            assert_never(unreachable)


def social_arm_output(
    arm: QueryResponseArm,
    intent: SocialIntent,
    current: SocialArmOutput,
) -> SocialArmOutput:
    match arm:
        case "current":
            return current
        case "deterministic":
            return SocialArmOutput(
                safety_response="",
                direct_response=social_response(intent),
                response_action="direct",
                safety_kind="benign_social",
                response_kind="benign_social",
                short_circuited=False,
                route_kind="benign_social",
            )
        case "tool":
            return SocialArmOutput(
                safety_response="",
                direct_response=None,
                response_action="query_or_respond",
                safety_kind="benign_social",
                response_kind="benign_social",
                short_circuited=False,
                route_kind="benign_social",
            )
        case unreachable:
            assert_never(unreachable)


ALL_SOCIAL_RESPONSES: Final = tuple(
    social_response(intent)
    for intent in ("greeting", "thanks", "goodbye", "capability")
)
