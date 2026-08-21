from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from healthcare_rag.graph.nodes.safety import safety_gate
from healthcare_rag.graph.state import RAGState
from healthcare_rag.processors.privacy import RedactSpan, union_spans
from healthcare_rag.processors.privacy import PrivacyScanError
from healthcare_rag.models.safety import SafetyAssessment
from healthcare_rag.processors.safety import scrub_phi


def test_partial_overlap_unions_the_right_extending_suffix() -> None:
    spans = union_spans(
        10,
        [
            RedactSpan(0, 6, "NAME", "deterministic"),
            RedactSpan(3, 8, "US_ID", "presidio"),
        ],
    )

    assert spans == [RedactSpan(0, 8, "IDENTIFIER", "union")]


def test_llm_broad_clinical_phi_span_cannot_mutate_text() -> None:
    text = "Metformin treats type 2 diabetes."

    clean, kinds = scrub_phi(text, extra_spans=["type 2 diabetes"])

    assert clean == text
    assert kinds == []


def test_documentary_date_is_preserved_without_person_event_cue() -> None:
    text = "The monograph was revised on 2024-06-15."

    clean, kinds = scrub_phi(text)

    assert clean == text
    assert kinds == []


def test_person_event_date_is_redacted_with_explicit_cue() -> None:
    clean, kinds = scrub_phi("Patient discharge date 2024-06-15 after Lipitor review.")

    assert "2024-06-15" not in clean
    assert "EVENT_DATE" in kinds
    assert "Lipitor" in clean


def test_cue_bound_healthcare_admin_ids_preserve_clinical_codes() -> None:
    text = (
        "Patient account AC-77881, claim CLM-44990, prior auth PA-88221; "
        "RxCUI 860975, NDC 00071-0155-23, LOINC 4548-4, device model XR-500."
    )

    clean, kinds = scrub_phi(text)

    assert "AC-77881" not in clean
    assert "CLM-44990" not in clean
    assert "PA-88221" not in clean
    assert {"PATIENT_ACCOUNT", "CLAIM_ID", "PRIOR_AUTH_ID"} <= set(kinds)
    assert "RxCUI 860975" in clean
    assert "NDC 00071-0155-23" in clean
    assert "LOINC 4548-4" in clean
    assert "device model XR-500" in clean


@pytest.mark.parametrize(
    "text",
    [
        "MRN-8842017",
        "MRN: MRN-8842017",
        "Medical record number MRN-8842017",
    ],
)
def test_hyphenated_mrn_variants_are_redacted(text: str) -> None:
    clean, kinds = scrub_phi(text)

    assert "8842017" not in clean
    assert "MRN" in kinds


def test_unicode_full_name_is_redacted() -> None:
    text = "My name is José Łukasz Петров and I take Lipitor."

    clean, kinds = scrub_phi(text)

    assert "José" not in clean
    assert "Łukasz" not in clean
    assert "Петров" not in clean
    assert "NAME" in kinds
    assert "Lipitor" in clean


@pytest.mark.parametrize(
    "kind,text,identifier",
    [
        ("MEMBER_ID", "Member ID MEM-20491", "MEM-20491"),
        ("PRESCRIPTION_ID", "Prescription number RX-88310", "RX-88310"),
        ("REFERRAL_ID", "Referral ID REF-55120", "REF-55120"),
        ("ACCESSION_ID", "Accession ACC-77819", "ACC-77819"),
        ("ACCESSION_ID", "Specimen ID SP-44109", "SP-44109"),
        ("ACCESSION_ID", "Lab order LO-11092", "LO-11092"),
        ("ENCOUNTER_ID", "Encounter ID ENC-92811", "ENC-92811"),
        ("ENCOUNTER_ID", "Visit number VIS-72810", "VIS-72810"),
        ("ENCOUNTER_ID", "Appointment ID APT-99182", "APT-99182"),
        ("DEVICE_SERIAL", "Device serial SN-882910", "SN-882910"),
        ("VEHICLE_ID", "My VIN is JTDBT923771012345", "JTDBT923771012345"),
        ("VEHICLE_ID", "VIN: 1HGCM82633A004352", "1HGCM82633A004352"),
        ("VEHICLE_ID", "Vehicle identification number WBAFR7C57CC387462", "WBAFR7C57CC387462"),
        ("VEHICLE_ID", "License plate BHXK 729", "BHXK 729"),
        ("VEHICLE_ID", "Plate number ABPC-82", "ABPC-82"),
        ("VEHICLE_ID", "Vehicle registration XT-4471-K", "XT-4471-K"),
    ],
)
def test_cue_bound_healthcare_identifier_inventory(
    kind: str,
    text: str,
    identifier: str,
) -> None:
    clean, kinds = scrub_phi(text)

    assert identifier not in clean
    assert kind in kinds


@pytest.mark.parametrize(
    "text",
    [
        "platelet count 250 x10^9/L",
        "patient had hip registration of the prosthetic joint",
        "Aspirin 81 mg once daily",
    ],
)
def test_vehicle_cues_do_not_fire_on_clinical_text(text: str) -> None:
    clean, kinds = scrub_phi(text)

    assert not kinds
    assert clean == text


def test_oversized_input_fails_with_stable_raw_free_code() -> None:
    canary = "My name is Jane Doe. " + "x" * (16 * 1024)

    with pytest.raises(PrivacyScanError) as captured:
        _ = scrub_phi(canary)

    assert str(captured.value) == "PRIVACY_INPUT_TOO_LARGE"
    assert "Jane Doe" not in str(captured.value)


def test_reported_kinds_follow_identifier_text_order() -> None:
    _, kinds = scrub_phi("Call 416-555-0134 or email jane.doe@example.com.")

    assert kinds.index("PHONE") < kinds.index("EMAIL")


async def test_gate_off_still_sanitizes_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HC_RAG_SAFETY_GATE", "false")

    update = await safety_gate({"question": "My name is Jane Doe. Explain Lipitor."})

    scrubbed = update["scrubbed_question"]
    working = update["working_query"]
    assert isinstance(scrubbed, str)
    assert isinstance(working, str)
    assert "Jane Doe" not in scrubbed
    assert working == scrubbed


async def test_safety_prompt_receives_sanitized_current_and_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CapturingGateway:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        async def astructured(
            self,
            _stage: str,
            _model_type: type[SafetyAssessment],
            **variables: str,
        ) -> SafetyAssessment:
            self.calls.append(variables)
            return SafetyAssessment(
                category="in_scope_informational",
                contains_phi=False,
                phi_spans=[],
                drug_mentioned="lipitor",
                rationale="informational",
            )

    gateway = CapturingGateway()
    monkeypatch.setattr("healthcare_rag.graph.nodes.safety.GATEWAY", gateway)
    state: RAGState = {
        "question": "My name is Current Canary. Explain Lipitor.",
        "messages": [
            HumanMessage(content="My name is History Canary"),
            AIMessage(content="Hello History Canary"),
        ],
    }

    _ = await safety_gate(state)

    call = gateway.calls[0]
    assert "Current Canary" not in call["user_query"]
    assert "History Canary" not in call["conversation_context"]
