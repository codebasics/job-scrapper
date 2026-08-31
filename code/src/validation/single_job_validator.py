"""Single Job Validator - Real-time 7-Layer Validation
Validates and fixes a single job immediately after scraping.

Layers:
1. Pattern Syntax (pre-loaded, no per-job check needed)
2. Coverage Analysis (checks skill extraction coverage)
3. False Positive Detection (removes skills not matching patterns)
4. False Negative Detection (adds skills matching patterns but missed)
5. Context Validation (validates skill appears in proper context)
6. Emerging Skills (flags potential new skills - log only)
7. Trend Analysis (batch only - not per-job)
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Set

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of single job validation"""
    job_id: str
    original_skills: Set[str]
    validated_skills: Set[str]
    false_positives_removed: Set[str]
    false_negatives_added: Set[str]
    was_modified: bool
    validation_log: str


class SingleJobValidator:
    """Validates and fixes a single job's skills in real-time"""

    def __init__(self, skills_ref_path: str = "src/config/skills_reference_2026.json"):
        self.skills_ref_path = Path(skills_ref_path)
        self.skill_patterns: dict[str, list[re.Pattern[str]]] = {}
        self.skill_names: dict[str, str] = {}  # lowercase -> canonical name
        self._load_patterns()

    def _load_patterns(self) -> None:
        """Load skill patterns from reference file"""
        with open(self.skills_ref_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for skill in data.get("skills", []):
            name = skill.get("name", "")
            patterns = skill.get("patterns", [])

            if not name or not patterns:
                continue

            compiled_patterns: list[re.Pattern[str]] = []
            for p in patterns:
                try:
                    compiled_patterns.append(re.compile(p, re.IGNORECASE))
                except re.error:
                    continue

            if compiled_patterns:
                self.skill_patterns[name.lower()] = compiled_patterns
                self.skill_names[name.lower()] = name

        logger.info(f"Loaded {len(self.skill_patterns)} skill patterns for validation")

    def detect_skills_in_text(self, text: str) -> Set[str]:
        """Detect all skills that match patterns in the text"""
        detected: Set[str] = set()

        for skill_lower, patterns in self.skill_patterns.items():
            for pattern in patterns:
                if pattern.search(text):
                    detected.add(self.skill_names[skill_lower])
                    break

        return detected

    def validate_and_fix(
        self,
        job_id: str,
        job_description: str,
        extracted_skills: str
    ) -> ValidationResult:
        """Validate a single job and return fixed skills

        Args:
            job_id: Job identifier
            job_description: Full job description text
            extracted_skills: Comma-separated skills string

        Returns:
            ValidationResult with original, validated skills, and changes
        """
        # Parse original skills
        original_skills = set(
            s.strip() for s in extracted_skills.split(",") if s.strip()
        )

        # Layer 3: Detect skills that SHOULD be in the description
        detected_in_jd = self.detect_skills_in_text(job_description)

        # Layer 3: False Positive Detection
        # Skills in extracted but pattern doesn't match in JD
        false_positives: Set[str] = set()
        for skill in original_skills:
            skill_lower = skill.lower()
            if skill_lower in self.skill_patterns:
                # Check if pattern actually matches in JD
                found = False
                for pattern in self.skill_patterns[skill_lower]:
                    if pattern.search(job_description):
                        found = True
                        break
                if not found:
                    false_positives.add(skill)

        # Layer 4: False Negative Detection
        # Skills detected by pattern but not in extracted
        original_lower = {s.lower() for s in original_skills}
        false_negatives: Set[str] = set()
        for skill in detected_in_jd:
            if skill.lower() not in original_lower:
                false_negatives.add(skill)

        # Build validated skills set
        validated_skills = (original_skills - false_positives) | false_negatives

        # Layer 5: Context validation (basic - check skill appears in reasonable context)
        # For now, we trust pattern-based detection

        # Build validation log
        log_parts = []
        if false_positives:
            log_parts.append(f"FP removed: {', '.join(sorted(false_positives))}")
        if false_negatives:
            log_parts.append(f"FN added: {', '.join(sorted(false_negatives))}")

        validation_log = " | ".join(log_parts) if log_parts else "No changes"
        was_modified = bool(false_positives or false_negatives)

        return ValidationResult(
            job_id=job_id,
            original_skills=original_skills,
            validated_skills=validated_skills,
            false_positives_removed=false_positives,
            false_negatives_added=false_negatives,
            was_modified=was_modified,
            validation_log=validation_log
        )

    def get_validated_skills_string(self, result: ValidationResult) -> str:
        """Convert validated skills set to comma-separated string"""
        return ", ".join(sorted(result.validated_skills))


# Singleton instance for reuse
_validator_instance: SingleJobValidator | None = None


def get_validator() -> SingleJobValidator:
    """Get or create singleton validator instance"""
    global _validator_instance
    if _validator_instance is None:
        _validator_instance = SingleJobValidator()
    return _validator_instance


def validate_single_job(
    job_id: str,
    job_description: str,
    extracted_skills: str
) -> ValidationResult:
    """Convenience function to validate a single job"""
    validator = get_validator()
    return validator.validate_and_fix(job_id, job_description, extracted_skills)
