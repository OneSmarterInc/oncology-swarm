"""Conservative, auditable multi-label classification on individual biography text."""

import re

from .models import CATEGORIES

LAW = re.compile(
    r"\b(attorney|lawyer|counsel|legal practice|practice of law|bar admissions?|admitted to practice|J\.?D\.?)\b",
    re.I,
)
HEALTH = re.compile(
    r"\b(health\s?care|medical|hospitals?|physicians?|medicare|medicaid|patients?|nursing)\b",
    re.I,
)
NONLAW = re.compile(
    r"\b(paralegal|legal assistant|legal nurse consultant|marketing director|recruiting manager)\b",
    re.I,
)
RULES = {
    "plaintiff": [
        r"medical (?:malpractice|negligence).{0,100}(?:plaintiff|patients|families|victims)",
        r"(?:plaintiff|patients|families|victims).{0,100}medical (?:malpractice|negligence)",
        r"birth injury.{0,80}(?:represent|litigat)",
    ],
    "defense": [
        r"(?:defen[cs]e|defend\w*).{0,100}(?:medical malpractice|medical negligence|health\s?care|physicians|hospitals)",
        r"(?:medical malpractice|medical negligence|health\s?care|physicians|hospitals).{0,100}(?:defen[cs]e|defend\w*)",
    ],
    "compliance": [
        r"health\s?care.{0,80}(?:regulatory|compliance)",
        r"(?:regulatory|compliance).{0,80}health\s?care",
        r"HIPAA",
        r"Stark Law",
        r"Anti.Kickback",
    ],
    "fraud": [
        r"health\s?care fraud",
        r"(?:Medicare|Medicaid) fraud",
        r"False Claims Act",
        r"qui tam",
        r"OIG.{0,40}(?:investigat|audit)",
    ],
    "transactional": [
        r"health\s?care.{0,100}(?:transactions|mergers|acquisitions|joint ventures|contracting)",
        r"(?:transactions|mergers|acquisitions|joint ventures).{0,100}health\s?care",
        r"physician practice.{0,50}(?:acquisition|sale|purchase)",
    ],
    "nurse": [
        r"nurse.attorney",
        r"registered nurse",
        r"\bR\.?N\.?\s*[,/]?\s*J\.?D\.?",
        r"\bJ\.?D\.?\s*[,/]?\s*R\.?N\.?",
    ],
    "public_health": [
        r"public health law",
        r"(?:counsel|attorney).{0,100}(?:department of health|health department|health and human services)",
        r"(?:department of health|health department|health and human services).{0,100}(?:counsel|attorney)",
    ],
    "bioethics": [
        r"bioethics",
        r"clinical ethics",
        r"informed consent",
        r"end.of.life",
        r"patient rights",
        r"(?:health\s?care|hospital|clinical).{0,50}risk management",
    ],
}


def classify(text: str, title: str = "") -> dict:
    text = re.sub(r"\s+", " ", text)
    attorney = bool(LAW.search(text))
    # A staff job title takes precedence over a footer mentioning attorneys.
    excluded = bool(NONLAW.search(title)) or bool(
        re.search(r"\bnot (?:an? )?(?:attorney|lawyer)\b", text, re.I)
    )
    evidence = {}
    for category, patterns in RULES.items():
        if category in {"fraud", "bioethics"} and not HEALTH.search(text):
            continue
        for pattern in patterns:
            m = re.search(pattern, text, re.I)
            if m:
                before = text[max(0, m.start() - 35) : m.start()]
                if re.search(
                    r"(?:does not|never|no longer|not a|not an)\s.{0,20}$", before, re.I
                ):
                    continue
                evidence[category] = text[
                    max(0, m.start() - 45) : min(len(text), m.end() + 70)
                ]
                break
    cats = sorted(evidence) if not excluded else []
    side = []
    if "plaintiff" in cats:
        side.append("patient/plaintiff")
    if "defense" in cats:
        side.append("provider/defense")
    if "fraud" in cats:
        if re.search(
            r"represent\w*.{0,60}whistleblower|whistleblower.{0,60}(?:represent|counsel)",
            text,
            re.I,
        ):
            side.append("whistleblower")
        if re.search(
            r"defend\w*.{0,90}(?:fraud|investigat)|defen[cs]e.{0,90}False Claims",
            text,
            re.I,
        ):
            side.append("provider/defense")
    status = (
        "excluded" if excluded else "confirmed" if attorney and cats else "needs_review"
    )
    return {
        "categories": cats,
        "classification_evidence": evidence,
        "attorney_evidence": attorney,
        "client_side": sorted(set(side)) or ["unclear"],
        "status": status,
        "reason": "Non-attorney role"
        if excluded
        else "Biography supports attorney role and specialty"
        if status == "confirmed"
        else "Attorney role or healthcare specialty needs review",
    }


def discovery_queries(category: str, location: str) -> list[str]:
    """Search links for human source discovery; no dependency on a search API."""
    term = CATEGORIES[category]
    return [
        f'"{term.split(" / ")[0]}" attorney {location} biography email',
        f'"{term.split(" / ")[0]}" law firm {location} attorneys',
    ]
