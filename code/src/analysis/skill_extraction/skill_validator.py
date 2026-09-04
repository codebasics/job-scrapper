# Skill Validator - Reference-Only Extraction (EMD ≤80 lines)
# Validates job descriptions against canonical 557 skills

import json
import re
from typing import Dict, List, Set, Union
from pathlib import Path

from .jd_context_filter import filter_skill_matches

class SkillValidator:
    """Validates and extracts ONLY canonical skills from reference file"""
    
    def __init__(self, reference_path: str):
        self.reference_path = Path(reference_path)
        self.canonical_skills: List[Dict[str, Union[str, List[str]]]] = []
        self.skill_patterns: List[tuple[str, List[re.Pattern[str]]]] = []
        self._load_reference()

    def _load_reference(self) -> None:
        """Load canonical skills and pre-compile their regex patterns.

        Pre-compiling matters: the reference holds ~7000 patterns, far beyond
        re's internal 512-entry cache, so passing raw strings to re.search()
        thrashes that cache and recompiles on every call.
        """
        with open(self.reference_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            self.canonical_skills = data['skills']

        for skill in self.canonical_skills:
            name = str(skill['name'])
            raw = list(skill['patterns']) if isinstance(skill['patterns'], list) else []
            compiled: List[re.Pattern[str]] = []
            for pattern in raw:
                try:
                    compiled.append(re.compile(pattern, re.IGNORECASE))
                except re.error:
                    continue  # Skip invalid patterns
            self.skill_patterns.append((name, compiled))
    
    def validate_and_extract(self, job_description: str) -> Set[str]:
        """Extract canonical skills, rejecting matches in non-requirement context.

        A raw regex hit is not evidence the job requires the skill: it may sit in
        a negation ("not a pre-sales role"), name another team ("partner with
        Sales"), enumerate product domains, or live in About Us / Benefits prose.
        Matches are collected with positions and gated by jd_context_filter.
        """
        if not job_description:
            return set()

        matches = self._collect_matches(job_description)
        kept, _rejected = filter_skill_matches(job_description, matches)
        return kept

    def extract_with_rejections(
        self, job_description: str
    ) -> tuple[Set[str], Dict[str, str]]:
        """Same as validate_and_extract, but also returns why skills were dropped."""
        if not job_description:
            return set(), {}

        matches = self._collect_matches(job_description)
        return filter_skill_matches(job_description, matches)

    def _collect_matches(self, job_description: str) -> Dict[str, List[tuple[int, int]]]:
        """Map each canonical skill to every span where its patterns matched."""
        matches: Dict[str, List[tuple[int, int]]] = {}

        for skill_name, patterns in self.skill_patterns:
            spans: List[tuple[int, int]] = []
            for pattern in patterns:
                spans.extend(m.span() for m in pattern.finditer(job_description))
            if spans:
                matches[skill_name] = spans

        return matches
    
    def calculate_accuracy(self, 
                          job_description: str, 
                          scraped_skills: str) -> Dict[str, Union[List[str], float]]:
        """Calculate false positive/negative rates"""
        canonical = self.validate_and_extract(job_description)
        scraped = set([s.strip() for s in scraped_skills.split(',') if s.strip()])
        
        true_positives = canonical & scraped
        false_positives = scraped - canonical
        false_negatives = canonical - scraped
        
        precision = len(true_positives) / len(scraped) if scraped else 0
        recall = len(true_positives) / len(canonical) if canonical else 0
        
        return {
            'canonical_skills': list(canonical),
            'true_positives': list(true_positives),
            'false_positives': list(false_positives),
            'false_negatives': list(false_negatives),
            'precision': round(precision, 2),
            'recall': round(recall, 2)
        }
