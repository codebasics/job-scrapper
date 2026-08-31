#!/usr/bin/env python3
"""
Lightweight Regex-Based Skill Extractor - EMD Compliant
NO heavy packages (SkillNER, spaCy) - Pure regex + JSON
Uses skills_reference_2026.json with compiled patterns for speed

Author: Job Scrapper Team
Created: 2025-10-11
EMD Compliance: ≤80 lines
"""

import re

from .regex.pattern_loader import load_skill_patterns
from .regex.skill_matcher import match_skills_in_text

def extract_skills_from_text(
    text: str,
    skill_patterns: dict[str, list[re.Pattern[str]]] | None = None
) -> list[str]:
    """
    Extract skills from text using compiled regex patterns
    
    Args:
        text: Job description text
        skill_patterns: Pre-compiled patterns (loaded once for speed)
    
    Returns:
        List of unique skill names found
    """
    
    if not text:
        return []
    
    # Load patterns if not provided (cache externally for best performance)
    if skill_patterns is None:
        skill_patterns = load_skill_patterns()
    
    return match_skills_in_text(text, skill_patterns)
