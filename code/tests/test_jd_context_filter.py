# Regression tests for JD context filtering (skill false positives)
# Anchor cases: linkedin/forward-deployed-engineer-at-roadzen-4448181752
#               linkedin/forward-deployed-engineer-at-greylabs-ai-4451678103

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analysis.skill_extraction.jd_context_filter import (  # noqa: E402
    filter_skill_matches,
    reject_reason,
)


def _spans(text: str, term: str):
    return [m.span() for m in re.finditer(r"\b" + re.escape(term) + r"\b", text, re.I)]


def _run(text: str, term: str):
    return filter_skill_matches(text, {term: _spans(text, term)})


# --- Class 1: negation -------------------------------------------------------

def test_negated_pre_sales_is_rejected():
    """Roadzen: 'not a pre-sales or support role' must not yield Sales."""
    text = (
        "Responsibilities\n"
        "You will ship under tight deadlines and with incomplete requirements. "
        "This is a hands-on build role, not a pre-sales or support role. "
        "Embed directly with customer teams."
    )
    kept, rejected = _run(text, "Sales")
    assert "Sales" not in kept
    assert rejected["Sales"] == "negated"


def test_no_sales_experience_required_is_rejected():
    text = "Requirements\nNo sales background needed for this position."
    kept, _ = _run(text, "Sales")
    assert "Sales" not in kept


# --- Class 2: other team -----------------------------------------------------

def test_partner_with_sales_department_is_rejected():
    """Roadzen: 'Partner closely with Sales, Solutions Architecture and Product'."""
    text = (
        "Responsibilities\n"
        "Run workshops, war rooms, and go-live support. "
        "Partner closely with Sales, Solutions Architecture, and Product to identify "
        "and close expansion opportunities."
    )
    kept, rejected = _run(text, "Sales")
    assert "Sales" not in kept
    assert rejected["Sales"] == "collab-dept"


def test_leadership_team_is_rejected():
    text = "The role\nYou will present findings to our leadership team each quarter."
    kept, rejected = _run(text, "Leadership")
    assert "Leadership" not in kept
    assert rejected["Leadership"] == "other-team"


# --- Class 3: product domain -------------------------------------------------

def test_product_domain_enumeration_is_rejected():
    """GreyLabs: product serves sales/collections/service - not a candidate skill."""
    text = (
        "About us\n"
        "We help fintechs automate and humanize millions of customer conversations "
        "across sales, collections, customer service, and compliance."
    )
    kept, _ = _run(text, "Sales")
    assert "Sales" not in kept


def test_use_case_enumeration_is_rejected():
    text = (
        "Responsibilities\n"
        "Customize agent behavior by industry and use-case (collections, "
        "customer service, sales, compliance)."
    )
    kept, rejected = _run(text, "Sales")
    assert "Sales" not in kept
    assert rejected["Sales"] == "product-domain"


# --- Class 4: boilerplate sections ------------------------------------------

def test_innovation_in_about_us_is_rejected():
    text = (
        "About Us\n"
        "Our innovation and excellence have earned us numerous recognitions "
        "and industry awards.\n"
    )
    kept, rejected = _run(text, "Innovation")
    assert "Innovation" not in kept
    assert rejected["Innovation"] == "boilerplate-section"


def test_benefits_section_is_rejected():
    text = (
        "Requirements\nStrong Python skills.\n"
        "Benefits\nWe offer leadership development stipends and wellness perks.\n"
    )
    kept, rejected = _run(text, "Leadership")
    assert "Leadership" not in kept
    assert rejected["Leadership"] == "boilerplate-section"


# --- True positives must survive --------------------------------------------

def test_genuine_sales_requirement_is_kept():
    text = (
        "Requirements\n"
        "5+ years of sales engineering experience in enterprise SaaS, "
        "with a track record of driving sales outcomes."
    )
    kept, _ = _run(text, "Sales")
    assert "Sales" in kept


def test_genuine_leadership_skill_is_kept():
    text = (
        "Qualifications\n"
        "Demonstrated leadership in ambiguous, high-stakes technical projects."
    )
    kept, _ = _run(text, "Leadership")
    assert "Leadership" in kept


def test_technical_skill_near_collaboration_verb_is_kept():
    """Non-context-sensitive skills must ignore department/domain rules."""
    text = "Responsibilities\nWork with Python and Kubernetes across our services."
    kept, _ = _run(text, "Python")
    assert "Python" in kept
    kept2, _ = _run(text, "Kubernetes")
    assert "Kubernetes" in kept2


def test_mixed_contexts_keeps_skill_when_any_mention_is_valid():
    """One bad mention must not veto a genuine requirement elsewhere."""
    text = (
        "About us\nWe serve sales teams worldwide.\n"
        "Requirements\nProven sales engineering background required."
    )
    kept, _ = _run(text, "Sales")
    assert "Sales" in kept


# --- reject_reason unit behaviour -------------------------------------------

def test_negation_does_not_cross_clause_boundary():
    """A negator in a previous sentence must not suppress a later match."""
    text = "Requirements\nWe do not offer relocation. Sales engineering experience required."
    span = _spans(text, "Sales")[0]
    assert reject_reason(text, span[0], span[1]) is None


def test_negation_does_not_leak_across_bullet_break():
    """GreyLabs regression: 'without ... the answer' must not suppress a later skill.

    These JDs render bullets with no terminating punctuation, so the negator sits
    ~45 chars upstream of an unrelated genuine requirement.
    """
    text = (
        "Requirements\n"
        "Trace data flows and figure out what's broken without someone handing you "
        "the answer Strong communication skills - you can translate between teams"
    )
    kept, _ = _run(text, "Communication")
    assert "Communication" in kept


def test_negation_inside_parenthetical_does_not_suppress():
    """GreyLabs regression: 'Strong Signals (not checkboxes) Experience in a
    client-facing technical role' is a genuine requirement."""
    text = (
        "Strong Signals (not checkboxes)\n"
        "Experience in a client-facing technical role - Solutions Engineer, "
        "Implementation Engineer, or similar"
    )
    kept, _ = _run(text, "client-facing")
    assert "client-facing" in kept


def test_handoff_deliverable_is_not_treated_as_other_team():
    """Roadzen regression: 'handoff/documentation' is an owned deliverable."""
    text = (
        "Responsibilities\n"
        "Own the full delivery lifecycle: scoping, architecture, implementation, "
        "deployment, and handoff/documentation."
    )
    kept, _ = _run(text, "Documentation")
    assert "Documentation" in kept


def test_personal_skills_are_exempt_from_department_rules():
    """Communication/Documentation are not departments - 'work with' must not veto."""
    text = "Responsibilities\nWork with stakeholders; strong documentation habits required."
    kept, _ = _run(text, "Documentation")
    assert "Documentation" in kept


def test_ability_to_influence_stakeholders_is_kept():
    text = "Qualifications\nProven ability to work with and influence senior stakeholders."
    kept, _ = _run(text, "Influence")
    assert "Influence" in kept


def test_technical_skill_survives_flattened_jd_without_newlines():
    """Pragmatike regression: LinkedIn flattens JDs, so headers run into body text.

    Role headers must still register mid-line, otherwise boilerplate spans
    over-extend and swallow genuine requirements like Python.
    """
    text = (
        "About Us We are a YC-backed startup. What We're Looking For 5+ years of "
        "professional software engineering experience. Strong programming "
        "experience in Python, TypeScript, Go, or similar languages."
    )
    kept, _ = _run(text, "Python")
    assert "Python" in kept


def test_technical_skill_never_dropped_on_section_grounds():
    """Section parsing is unreliable on scraped HTML; it must never veto a
    hard technical skill. Only negation may do that."""
    text = "Benefits\nWe sponsor conference travel and Python training courses.\n"
    kept, _ = _run(text, "Python")
    assert "Python" in kept


def test_paebbl_regression_python_development_kept():
    text = (
        "Hands-on with IT systems and software. Experience with, or strong "
        "interest in, AI and agentic augmentation. Python development. "
        "Strong problem-solving skills."
    )
    kept, _ = _run(text, "Python")
    assert "Python" in kept


def test_negated_technical_skill_is_still_rejected():
    """Negation remains the one rule that applies to every skill."""
    text = "Requirements\nThis role does not require Python at all."
    kept, rejected = _run(text, "Python")
    assert "Python" not in kept
    assert rejected["Python"] == "negated"


def test_not_just_is_inclusive_not_exclusionary():
    """'not just X, but Y' means X *and more* - X is still required.

    Live case: "Full-stack engineering with AI components - not just the API
    call, but the whole product".
    """
    text = (
        "Responsibilities\n"
        "Full-stack engineering with AI components - not just the API call, "
        "but the whole product."
    )
    kept, _ = _run(text, "API")
    assert "API" in kept


def test_not_only_is_inclusive():
    text = "Requirements\nYou will own not only Python services but the surrounding tooling."
    kept, _ = _run(text, "Python")
    assert "Python" in kept


def test_plain_negation_still_rejected_after_inclusive_exception():
    """The 'just/only' carve-out must not weaken ordinary negation."""
    text = "Requirements\nThis is not a Python role."
    kept, rejected = _run(text, "Python")
    assert "Python" not in kept
    assert rejected["Python"] == "negated"


def test_empty_inputs_are_safe():
    assert filter_skill_matches("", {"Sales": [(0, 5)]}) == (set(), {})
    assert filter_skill_matches("some text", {}) == (set(), {})
