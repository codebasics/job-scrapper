# JD Context Filter - Rejects skill matches that appear in non-requirement context
# Implements the "Layer 5: Context Validation" design from docs/VALIDATION_ARCHITECTURE.md
#
# A regex hit on a skill term does NOT mean the job requires that skill. Four
# recurring false-positive classes are filtered here:
#   1. negated        - "this is NOT a pre-sales role"
#   2. other-team     - "partner closely with Sales, Solutions Architecture and Product"
#   3. product-domain - "conversations across sales, collections, customer service"
#   4. boilerplate    - "our innovation has earned us awards" (About Us / Benefits / EEO)

import re
from typing import Dict, List, Optional, Set, Tuple

# Clause terminators for context look-back. Two scopes are needed:
#
# HARD (negation): also stops at parentheses and dash separators. A negator must
# not escape its own parenthetical - "Strong Signals (not checkboxes) Experience
# in a client-facing role" is a genuine requirement, not a negated one.
#
# SOFT (enumeration/collaboration): parentheses are transparent, because a list
# cue legitimately governs its bracketed items - "use-case (collections,
# customer service, sales, compliance)".
_CLAUSE_HARD = r"(?:[.;:!?\n•()]|\s[-–—]\s)"
_CLAUSE_SOFT = r"(?:[.;:!?\n•]|\s[-–—]\s)"

# 1. Negation: a negator directly governing the matched noun phrase.
# The window is deliberately tight (22 chars): genuine negation sits adjacent
# ("not a pre-sales role", "no sales background"), whereas a negator 30+ chars
# upstream almost always belongs to an unrelated clause.
NEGATION = re.compile(
    r"\b(?:not|isn'?t|aren'?t|no|without|neither|nor|rather\s+than|instead\s+of|"
    r"as\s+opposed\s+to|doesn'?t|don'?t|won'?t|never|excluding|except)\b"
    r"(?:(?!" + _CLAUSE_HARD + r").){0,22}$",
    re.IGNORECASE,
)

# "not just X, but Y" / "not only X but also Y" are INCLUSIVE: X is still
# required, plus more. Treating them as negation drops real requirements
# ("AI components - not just the API call, but the whole product").
INCLUSIVE_NEGATION = re.compile(
    r"\b(?:not|isn'?t|aren'?t)\s+(?:just|only|merely|simply)\b"
    r"(?:(?!" + _CLAUSE_HARD + r").){0,22}$",
    re.IGNORECASE,
)

# 2. Collaboration verbs -> the term names another team, not a candidate skill
# NB: "handoff" is deliberately excluded - in these JDs it names an owned
# deliverable ("deployment, and handoff/documentation"), not another team.
COLLABORATION = re.compile(
    r"\b(?:partner|partners|partnering|collaborate|collaborates|collaborating|"
    r"collaboration\s+with|liaise|liaising|alongside|interface|interfacing|"
    r"coordinate|coordinating|align|aligning|embed\s+with|"
    r"work\s+(?:with|across|closely\s+with)|working\s+(?:with|across)|"
    r"together\s+with|in\s+partnership\s+with)\b"
    r"(?:(?!" + _CLAUSE_SOFT + r").){0,40}$",
    re.IGNORECASE,
)

# 3. Term immediately followed by an org-unit noun -> "Sales team", "leadership team"
ORG_UNIT_SUFFIX = re.compile(
    r"^\W{0,3}(?:team|teams|org|orgs|organi[sz]ations?|department|departments|dept|"
    r"stakeholders?|counterparts?|leaders?|leadership|reps?|representatives?|"
    r"folks|colleagues|function|functions|group|groups|division|cycle|cycles|"
    r"pipeline|quota|quotas)\b",
    re.IGNORECASE,
)

# 4. Enumeration of product domains / verticals the product serves
PRODUCT_DOMAIN = re.compile(
    r"\b(?:across|such\s+as|e\.?g\.?|including|includes|use\s*-?\s*cases?|"
    r"verticals?|industries|industry|domains?|categories|segments?|"
    r"functions\s+like|areas\s+(?:like|such\s+as))\b"
    r"(?:(?!" + _CLAUSE_SOFT + r").){0,60}$",
    re.IGNORECASE,
)

# Section headers that contain company marketing / legal / benefits prose.
# Matches are rejected outright inside these sections.
BOILERPLATE_SECTION = re.compile(
    r"(?:^|\n)[^\n]{0,80}?\b("
    r"about\s+(?:us|the\s+company|the\s+team|our\s+company|the\s+org(?:ani[sz]ation)?)|"
    r"who\s+we\s+are|our\s+(?:mission|story|values|culture|investors)|why\s+join|"
    r"benefits|perks|what\s+we\s+offer|compensation|salary\s+range|pay\s+range|"
    r"equal\s+(?:employment\s+)?opportunity|eeo|e-verify|accommodations?|"
    r"diversity(?:\s+and\s+inclusion)?|follow\s+us|learn\s+more|backed\s+by"
    r")\b[^\n]{0,40}(?=\n|:|$)",
    re.IGNORECASE,
)

# Section headers that describe the role / candidate requirements.
ROLE_SECTION = re.compile(
    r"(?:^|\n)[^\n]{0,80}?\b("
    r"responsibilities|what\s+you.?ll\s+do|what\s+you\s+will\s+do|the\s+role|your\s+role|"
    r"day.to.day|in\s+this\s+role|requirements|qualifications|"
    r"what\s+we.?re\s+looking\s+for|what\s+you.?ll\s+need|what\s+you\s+bring|"
    r"must\s+have|nice\s+to\s+have|basic\s+qualifications|preferred\s+qualifications|"
    r"minimum\s+qualifications|skills|tech\s+stack|your\s+profile|about\s+you"
    r")\b[^\n]{0,40}(?=\n|:|$)",
    re.IGNORECASE,
)

# Skills that double as a business-function, department or industry-vertical
# name. ONLY these are subjected to the collaboration + product-domain rules,
# so genuine technical skills are never dropped by them.
#
# Personal competencies (Communication, Documentation, Collaboration, Influence,
# Mentoring, Negotiation ...) are intentionally NOT listed: nobody "partners
# with Documentation", and "ability to work with and influence stakeholders" is
# a real requirement. They still get negation + boilerplate-section filtering,
# which applies to every skill.
CONTEXT_SENSITIVE_SKILLS: Set[str] = {
    "Sales", "Marketing", "Consulting", "Customer Service", "Customer Success",
    "Leadership", "Product Management", "Project Management", "Program Management",
    "Account Management", "Professional Services Delivery", "Data Engineering",
    "Platform Engineering", "Data Science", "Design", "Legal", "Finance",
    "Accounting", "Recruiting", "Operations", "Security", "Compliance",
    "Innovation", "Organization", "Ambition", "Insurance", "Supply Chain",
    "Education", "Healthcare", "Retail", "Banking", "Logistics",
}


def _section_spans(text: str) -> List[Tuple[str, int, int]]:
    """Segment a JD into (kind, start, end) spans; kind in boiler|role|intro."""
    marks: List[Tuple[int, str]] = []
    for match in BOILERPLATE_SECTION.finditer(text):
        marks.append((match.start(), "boiler"))
    for match in ROLE_SECTION.finditer(text):
        marks.append((match.start(), "role"))

    if not marks:
        return [("intro", 0, len(text))]

    marks.sort()
    spans: List[Tuple[str, int, int]] = []
    if marks[0][0] > 0:
        spans.append(("intro", 0, marks[0][0]))
    for i, (pos, kind) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        spans.append((kind, pos, end))
    return spans


def _section_kind(spans: List[Tuple[str, int, int]], index: int) -> str:
    for kind, start, end in spans:
        if start <= index < end:
            return kind
    return "intro"


def reject_reason(
    text: str,
    start: int,
    end: int,
    spans: Optional[List[Tuple[str, int, int]]] = None,
    context_sensitive: bool = True,
) -> Optional[str]:
    """Return a rejection reason for one match, or None if the match is valid.

    Args:
        text: full job description
        start, end: character span of the matched skill term
        spans: cached output of _section_spans(text)
        context_sensitive: apply department/domain rules (business-function terms only)
    """
    if spans is None:
        spans = _section_spans(text)

    # Section rejection is limited to context-sensitive terms on purpose.
    # Scraped LinkedIn descriptions are frequently flattened to a single line,
    # so headers run into body text and segmentation is only approximate. An
    # over-extended "boiler" span must never be able to veto a hard technical
    # requirement ("Strong programming experience in Python"); at worst we keep
    # a rare benefits-section mention, which is far cheaper than losing real
    # requirements. Negation stays universal - it is tight and position-local.
    if context_sensitive and _section_kind(spans, start) == "boiler":
        return "boilerplate-section"

    before = text[max(0, start - 90):start]
    after = text[end:end + 28]

    if NEGATION.search(before) and not INCLUSIVE_NEGATION.search(before):
        return "negated"
    if context_sensitive:
        if ORG_UNIT_SUFFIX.match(after):
            return "other-team"
        if COLLABORATION.search(before):
            return "collab-dept"
        if PRODUCT_DOMAIN.search(before):
            return "product-domain"
    return None


def filter_skill_matches(
    text: str, matches: Dict[str, List[Tuple[int, int]]]
) -> Tuple[Set[str], Dict[str, str]]:
    """Drop skills whose every occurrence sits in non-requirement context.

    Args:
        text: full job description
        matches: skill name -> list of (start, end) spans where it matched

    Returns:
        (kept skill names, rejected skill name -> dominant reason)
    """
    if not text or not matches:
        return set(), {}

    spans = _section_spans(text)
    kept: Set[str] = set()
    rejected: Dict[str, str] = {}

    for skill, positions in matches.items():
        if not positions:
            continue
        sensitive = skill in CONTEXT_SENSITIVE_SKILLS
        reasons: List[str] = []
        for start, end in positions:
            reason = reject_reason(text, start, end, spans, sensitive)
            if reason is None:
                kept.add(skill)
                break
            reasons.append(reason)
        else:
            # every occurrence was rejected
            rejected[skill] = max(set(reasons), key=reasons.count)

    return kept, rejected
