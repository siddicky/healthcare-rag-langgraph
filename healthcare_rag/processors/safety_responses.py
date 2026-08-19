"""
Templated safety responses. Plain strings — **no LLM call, no retrieval**.

Why templates and not a "be careful" instruction in the answer prompt: a generated
refusal is a probability, not a guarantee. The baseline (journey F13) shows the model
happily reciting a dosing table for "should I double my metformin tonight". These
strings are the part of the behaviour we can actually promise.

Hard rule for every template in this module: **no specific numbers with a clinical
unit**. ``evals.evaluators.numeric_advice_leak`` treats any "<number> mg / tablets /
times a day / hours" in a refuse-expected answer as a failure, and it is right to —
reciting the relevant monograph numbers back at someone who asked a personal question
is the failure mode a phrase blocklist cannot see. ``tests/test_safety_gate.py``
asserts this over every template.
"""

from __future__ import annotations

SCOPE = "the Lipitor (atorvastatin) and metformin product monographs"

#: Prefixed (as its own line) when identifiers were found and removed from the message.
PHI_NOTICE = (
    "I've disregarded the personal identifiers in your message; please don't share them here."
)

#: Prefixed when an instruction-override attempt was detected but a legitimate question remained.
INJECTION_NOTICE = (
    "I can't change the instructions I operate under or take on a different persona, "
    "so I'll answer your question as the monograph assistant."
)

#: Heading used when a de-personalised version of a refused question can still be answered.
ADDENDUM_HEADING = "General information from the monograph (not personal advice):"


def emergency_response(*, overdose: bool = False) -> str:
    """Urgent-care redirect for red-flag symptoms.

    Deliberately contains no monograph content at all: someone describing chest pain or
    cola-coloured urine needs to be assessed by a person, and anything else in the reply
    competes with that instruction for their attention.
    """
    poison = (
        "\n\nIf you think you have taken too much of a medicine, call your local poison "
        "control centre on your way — they can advise while you travel."
        if overdose
        else ""
    )
    return (
        "What you're describing needs to be assessed by a person, not by a document assistant.\n\n"
        "Please seek urgent medical care now: call your local emergency number (911 in Canada "
        "and the US) or go to your nearest emergency department. If you cannot reach either, "
        "call your prescriber or a nurse line straight away." + poison + "\n\n"
        "Please don't start, stop or change any medication on my say-so — tell the clinician "
        "which medicines you take and when the symptoms started, and let them decide.\n\n"
        "Once you have been seen, I'm glad to go through what "
        f"{SCOPE} say about these symptoms in general."
    )


def personal_advice_response() -> str:
    """Decline a personal dosing/treatment decision and say *why*."""
    return (
        "I can't tell you what to do with your own dose or treatment. That decision depends on "
        "things I can't see and am not qualified to weigh: your other conditions and medicines, "
        "your kidney and liver function, your recent bloodwork, and how you have responded so "
        "far. A general document can't make an individual decision.\n\n"
        "Please take this to the person who prescribed it — your doctor or nurse practitioner, "
        "your pharmacist, or your diabetes nurse — and tell them exactly what you've told me. "
        "Pharmacists can usually answer the same day without an appointment. If you feel unwell "
        "or things are getting worse, seek care today rather than waiting.\n\n"
        f"What I can do is explain what {SCOPE} say in general terms."
    )


def out_of_scope_response() -> str:
    """Helpful decline for questions outside the two monographs."""
    return (
        f"I can only answer from two documents: {SCOPE}. Your question falls outside them, so I "
        "have no source I can stand behind — and guessing is exactly what I shouldn't do here.\n\n"
        "A pharmacist is the best next stop for questions about other medicines, including "
        "over-the-counter ones; for anything about your own health or a change of therapy, your "
        "doctor or nurse practitioner. For non-medical questions I'm simply the wrong tool.\n\n"
        "If you do have a question about Lipitor or metformin — what they're for, how they're "
        "dosed in general, side effects, interactions, warnings, monitoring — I can help with that."
    )


def identifier_recall_response() -> str:
    """Refuse to read personal identifiers back. There is nothing to read back: we scrub on input."""
    return (
        "I don't hold personal identifiers. Names, health card numbers, medical record numbers, "
        "dates of birth and contact details are stripped out of a message before it is used and "
        "are not kept in this conversation, so there is nothing for me to repeat back to you — "
        "not in full and not in part.\n\n"
        "Those details live on the record they came from: the card itself, the patient's chart, "
        "or your clinic's system. Please take them from there.\n\n"
        f"I'm happy to keep going with questions about {SCOPE}."
    )


def injection_response() -> str:
    """Refuse an instruction-override attempt that carries no legitimate question."""
    return (
        "I can't change the instructions I operate under, adopt a different persona, or print "
        "those instructions for you — that holds however the request is phrased, and framing it "
        "as fiction, a test mode or a developer request doesn't change it.\n\n"
        f"What I can do is answer questions from {SCOPE}. Ask me one of those and I'll help."
    )


#: Every templated body, for the "no numbers in a refusal" regression test.
ALL_TEMPLATES = (
    emergency_response(),
    emergency_response(overdose=True),
    personal_advice_response(),
    out_of_scope_response(),
    identifier_recall_response(),
    injection_response(),
    PHI_NOTICE,
    INJECTION_NOTICE,
    ADDENDUM_HEADING,
)
