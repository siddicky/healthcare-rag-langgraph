from __future__ import annotations

import pytest

from healthcare_rag.agent.gate import compute_features


@pytest.mark.parametrize(
    ("text", "expected_parse"),
    [
        ("Remind me to log my weight every Monday", "reminder_manage"),
        ("Set a reminder for Thursday", "reminder_manage"),
        ("Nudge me to weigh in", "reminder_manage"),
        ("What nudges do I have?", "reminder_manage"),
        ("What reminders do I have?", "reminder_manage"),
        ("What's on my schedule this month?", "schedule_view"),
        ("How is my weight trending?", "metric_log"),
    ],
)
def test_compute_features_recognizes_coaching_families(
    text: str,
    expected_parse: str,
) -> None:
    # Given/When
    features = compute_features(text)

    # Then
    assert features["coaching_parse"] == expected_parse


@pytest.mark.parametrize(
    "text",
    [
        "Delete my data",
        "Erase my account",
        "Remove my history",
        "Forget my records",
        "Wipe my medication history",
        "Clear everything",
        "Purge my data",
        "Get rid of my data",
        "Could you please delete my account?",
        "Can you help me erase my records?",
        "I want to have my data deleted",
        "How can I have my medication history removed?",
    ],
)
def test_compute_features_recognizes_all_erasure_forms(text: str) -> None:
    # Given/When
    features = compute_features(text)

    # Then
    assert features["is_erase_request"] is True


@pytest.mark.parametrize(
    "text",
    [
        "Log my weight at 190 lb",
        "Record 190 lbs",
        "Track 190 pounds",
        "Log 82 kg",
        "Record 82 kilograms",
        "Track my waist at 95 cm",
        "Log 37 in",
    ],
)
def test_weight_units_are_metric_objects_after_log_verbs(text: str) -> None:
    # Given/When
    features = compute_features(text)

    # Then
    assert features["coaching_parse"] == "metric_log"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("I took 500 mg", True),
        ("The injection was 40 IU", True),
        ("My weight is 190 lb", True),
        ("I have mg tablets", False),
        ("There were 500 reasons", False),
    ],
)
def test_number_unit_requires_a_number_and_clinical_unit(
    text: str,
    expected: bool,
) -> None:
    # Given/When
    features = compute_features(text)

    # Then
    assert features["has_number_unit"] is expected


@pytest.mark.parametrize("drug", ["Lipitor", "atorvastatin", "metformin", "Glucophage"])
def test_in_scope_drugs_are_detected_from_imported_lexicons(drug: str) -> None:
    # Given/When
    features = compute_features(f"Tell me about {drug}")

    # Then
    assert features["has_in_scope_drug"] is True
    assert features["has_oos_drug"] is False


@pytest.mark.parametrize("drug", ["insulin", "warfarin", "Ozempic", "Januvia"])
def test_out_of_scope_drugs_are_detected_from_imported_lexicon(drug: str) -> None:
    # Given/When
    features = compute_features(f"Tell me about {drug}")

    # Then
    assert features["has_oos_drug"] is True


@pytest.mark.parametrize(
    "symptom",
    [
        "dizzy",
        "dizziness",
        "nausea",
        "headache",
        "chest pain",
        "blood sugar",
    ],
)
def test_symptom_lexicon_sets_medical_cue(symptom: str) -> None:
    # Given/When
    features = compute_features(f"I have {symptom}")

    # Then
    assert features["has_medical_cue"] is True


def test_tokenization_normalizes_unicode_and_splits_non_alphanumeric() -> None:
    # Given/When
    features = compute_features("LIPITOR—side effects")

    # Then
    assert features["has_in_scope_drug"] is True
    assert features["has_medical_cue"] is True


def test_attachment_is_a_feature_without_classification() -> None:
    # Given/When
    features = compute_features("Please review this document.", attachment_id="upload-1")

    # Then
    assert features["has_attachment"] is True
    assert features["classifier_category"] == "ambiguous"
    assert features["classifier_failed"] is False


@pytest.mark.parametrize(
    ("text", "expected_parse"),
    [
        ("Log my metformin 500 mg dose", "injection_log"),
        ("Took atorvastatin 40 mg", "injection_log"),
        ("Log my metformin, 500 mg", "injection_log"),
    ],
)
def test_dosage_grammar_inputs_still_have_a_coaching_parse(
    text: str,
    expected_parse: str,
) -> None:
    # Given/When
    features = compute_features(text)

    # Then
    assert features["coaching_parse"] == expected_parse
