"""
Layer 3: Direct pattern matching ONLY from skills_reference_2026.json
All patterns centralized in single source of truth
"""
from __future__ import annotations

import re
from typing import TypedDict


class SkillReferenceData(TypedDict, total=False):
    """Type for skill reference data from JSON"""
    name: str
    patterns: list[str]
    category: str


class Layer3SkillMatch(TypedDict):
    """Type for layer 3 skill match result"""
    skill: str
    start: int
    end: int
    layer: int


def layer3_extract_direct(
    text: str,
    consumed: list[tuple[int, int]],
    skills_reference: list[SkillReferenceData]
) -> list[Layer3SkillMatch]:
    """
    Layer 3: Extract using ONLY patterns from skills_reference_2026.json
    Returns canonical skill names, not pattern text
    """
    skills: list[Layer3SkillMatch] = []

    # Build pattern -> canonical_name mapping ONLY from skills_reference_2026.json
    pattern_to_skill: dict[str, str] = {}

    # All patterns come from skills_reference parameter (loaded from JSON)
    for skill_data in skills_reference:
        canonical_name: str = skill_data.get('name', '')
        patterns_list: list[str] = skill_data.get('patterns', [])
        for pattern_str in patterns_list:
            pattern_to_skill[pattern_str] = canonical_name

    # Sort patterns by length (longest first) to prioritize specific matches
    # This ensures "React Native" matches before "React", "Google Cloud Platform" before "GCP"
    sorted_patterns: list[tuple[str, str]] = sorted(
        pattern_to_skill.items(), key=lambda x: len(x[0]), reverse=True
    )

    # Extract using patterns and return canonical names
    for pattern_str, canonical_name in sorted_patterns:
        try:
            pattern = re.compile(pattern_str, re.IGNORECASE)

            for match in pattern.finditer(text):
                start, end = match.span()

                # Skip if region already consumed
                if any(s <= start < e or s < end <= e for s, e in consumed):
                    continue

                skills.append({
                    'skill': canonical_name,  # Use canonical name from JSON
                    'start': start,
                    'end': end,
                    'layer': 3
                })
        except re.error:
            # Skip invalid patterns
            continue

    return skills
