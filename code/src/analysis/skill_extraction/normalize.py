"""
Skill normalization and synonym handling
"""
from __future__ import annotations

from typing import TypedDict


# REMOVED: All hardcoded SKILL_SYNONYMS to prevent hallucinations
# ALL normalization must come from skills_reference_2026.json patterns
# This ensures single source of truth and eliminates duplicate/conflicting mappings


class SkillDict(TypedDict):
    """Type for skill dict with at least a 'skill' key"""
    skill: str


def normalize_skill(skill: str) -> str:
    """
    Normalize skill name - preserve exact canonical name from extraction
    All mapping handled by skills_reference_2026.json, no transformation needed
    """
    # Return exact skill name as extracted (already canonical from layer3_direct)
    return skill.strip()


def deduplicate_skills(skills: list[SkillDict]) -> list[str]:
    """
    Extract skill names, normalize, and deduplicate
    Handles overlapping patterns (e.g., CI/CD contains CI and CD)
    """
    skill_names: list[str] = [s['skill'] for s in skills]
    normalized: list[str] = [normalize_skill(name) for name in skill_names]

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_skills: list[str] = []
    for skill in normalized:
        if skill.lower() not in seen:
            seen.add(skill.lower())
            unique_skills.append(skill)

    # Remove overlapping duplicates:
    # "Continuous Integration/Continuous Deployment" supersedes individual CI/CD components
    cicd_long: str = 'Continuous Integration/Continuous Deployment'
    if cicd_long in unique_skills:
        unique_skills = [s for s in unique_skills if s.upper() not in ['CI', 'CD', 'CI/CD']]

    return unique_skills
