from __future__ import annotations

import re
from typing import Final, NamedTuple


class PatternHit(NamedTuple):
    start: int
    end: int
    kind: str


_LETTER: Final = r"[^\W\d_]"
_NAME_TOKEN: Final = (
    rf"(?!(?:and|but|who|i|take|taking|uses)\b)(?:{_LETTER}\.|{_LETTER}(?:{_LETTER}|['’\-])*)"
)
_NAME: Final = rf"(?P<value>{_NAME_TOKEN}(?:\s+{_NAME_TOKEN}){{1,4}})"
_VALUE: Final = r"(?P<value>[A-Za-z0-9][A-Za-z0-9._/\-]{2,39})"
_DATE_VALUE: Final = (
    r"(?P<value>(?:\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|"
    r"\d{1,2}\s+[A-Za-z]{3,9},?\s+\d{4}|"
    r"[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}))"
)

_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("HEALTH_CARD", re.compile(r"(?<!\d)\d{4}[-\s]\d{3}[-\s]\d{3}(?:[-\s][A-Z]{2})?(?!\d)")),
    (
        "HEALTH_CARD",
        re.compile(
            r"\b(?:health\s*card|hc|ohip)\s*(?:number|num|no\.?|#)?\s*[:#]?\s*"
            + r"(?P<value>\d[\d\-\s]{6,14}\d)",
            re.IGNORECASE,
        ),
    ),
    ("MRN", re.compile(r"\b(?P<value>MRN-\d{4,10})\b", re.IGNORECASE)),
    (
        "MRN",
        re.compile(
            r"\b(?:mrn|m\.r\.n\.|medical\s+record\s*(?:number|num|no\.?|#)?|"
            + r"chart\s*(?:number|no\.?|#)|hospital\s*(?:number|no\.?|#)|"
            + r"patient\s*(?:id|number|no\.?|#))\s*[:#]?\s*"
            + r"(?P<value>[A-Za-z]{0,3}\d{4,10})\b",
            re.IGNORECASE,
        ),
    ),
    (
        "DOB",
        re.compile(
            r"\b(?:dob|d\.o\.b\.|date\s+of\s+birth|born(?:\s+on)?)\b\s*[:\-]?\s*"
            + _DATE_VALUE,
            re.IGNORECASE,
        ),
    ),
    (
        "EVENT_DATE",
        re.compile(
            rf"\b(?:admission|discharge|appointment|visit|encounter|procedure)\s+"
            + rf"(?:date|on)\s*[:\-]?\s*{_DATE_VALUE}",
            re.IGNORECASE,
        ),
    ),
    ("PHONE", re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)")),
    ("POSTAL_CODE", re.compile(r"\b[A-Za-z]\d[A-Za-z][ -]?\d[A-Za-z]\d\b")),
    (
        "ADDRESS",
        re.compile(
            r"\b\d{1,6}\s+(?:[A-Z][A-Za-z.\-']*\s+){1,3}"
            + r"(?i:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|"
            + r"court|ct|way|crescent|cres|place|pl|terrace|trail|parkway|pkwy)\b\.?"
            + r"(?:\s*,?\s*(?i:apt|apartment|unit|suite|ste)\.?\s*#?\s*\w+)?"
        ),
    ),
    (
        "NAME",
        re.compile(
            rf"\b(?:my\s+name\s+is|i['’]?m|i\s+am|this\s+is|name\s*:|patient|pt\.?|hello[,]?)\s+{_NAME}",
            re.IGNORECASE,
        ),
    ),
    (
        "NAME",
        re.compile(
            rf"\b(?i:save\s+my\s+details(?:\s+for\s+next\s+time)?|"
            + rf"my\s+(?:details|contact\s+info)(?:\s+are)?)\s*[-:,]\s*{_NAME}",
        ),
    ),
    ("PATIENT_ACCOUNT", re.compile(rf"\bpatient\s+account(?:\s+(?:number|no\.?|#))?\s*[:#]?\s*{_VALUE}", re.IGNORECASE)),
    ("MEMBER_ID", re.compile(rf"\b(?:member|beneficiary)\s+(?:id|number|no\.?|#)\s*[:#]?\s*{_VALUE}", re.IGNORECASE)),
    ("CLAIM_ID", re.compile(rf"\bclaim\s+(?:id|number|no\.?|#)?\s*[:#]?\s*{_VALUE}", re.IGNORECASE)),
    ("PRIOR_AUTH_ID", re.compile(rf"\bprior[ -]?auth(?:orization)?\s+(?:id|number|no\.?|#)?\s*[:#]?\s*{_VALUE}", re.IGNORECASE)),
    ("PRESCRIPTION_ID", re.compile(rf"\b(?:prescription|rx)\s+(?:id|number|no\.?|#)\s*[:#]?\s*{_VALUE}", re.IGNORECASE)),
    ("REFERRAL_ID", re.compile(rf"\breferral\s+(?:id|number|no\.?|#)\s*[:#]?\s*{_VALUE}", re.IGNORECASE)),
    ("ACCESSION_ID", re.compile(rf"\b(?:accession|specimen|lab[ -]?order)\s+(?:id|number|no\.?|#)?\s*[:#]?\s*{_VALUE}", re.IGNORECASE)),
    ("ENCOUNTER_ID", re.compile(rf"\b(?:encounter|visit|appointment)\s+(?:id|number|no\.?|#)\s*[:#]?\s*{_VALUE}", re.IGNORECASE)),
    ("DEVICE_SERIAL", re.compile(rf"\bdevice\s+serial(?:\s+(?:id|number|no\.?|#))?\s*[:#]?\s*{_VALUE}", re.IGNORECASE)),
    (
        "VEHICLE_ID",
        re.compile(
            r"\b(?:vin|vehicle\s+identification\s+(?:number|num|no\.?|#)?)\s*(?:is|was)?\s*(?:number|num|no\.?|#)?\s*[:#]?\s*"
            r"(?P<value>[A-HJ-NPR-Z0-9]{8,17})",
            re.IGNORECASE,
        ),
    ),
    (
        "VEHICLE_ID",
        re.compile(
            r"\b(?:license\s+plate|plate\s+(?:number|num|no\.?|#)|vehicle\s+registration(?:\s+(?:number|num|no\.?|#))?)\s*[:#]?\s*"
            r"(?P<value>(?=[A-Za-z0-9\-\u00a0 ]{0,9}\d)[A-Za-z0-9][A-Za-z0-9\-\u00a0 ]{1,9}[A-Za-z0-9])",
            re.IGNORECASE,
        ),
    ),
)

_CLINICAL_CODE: Final = re.compile(
    r"\b(?:RxCUI|NDC|DIN|ATC|LOINC|SNOMED|ICD(?:-10)?|CPT|CCI|HCPCS|device\s+model)\s*[:#]?\s*"
    + r"[A-Za-z0-9][A-Za-z0-9.\-/]*",
    re.IGNORECASE,
)


def deterministic_hits(text: str) -> list[PatternHit]:
    hits: list[PatternHit] = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span("value") if "value" in match.groupdict() else match.span()
            if kind == "NAME" and not all(
                token[0].isupper() for token in match.group("value").split()
            ):
                continue
            hits.append(PatternHit(start, end, kind))
    return hits


def clinical_code_intervals(text: str) -> list[tuple[int, int]]:
    return [match.span() for match in _CLINICAL_CODE.finditer(text)]
