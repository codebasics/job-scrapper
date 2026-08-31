# Skills Validation Against skills_reference_2026.json
# Filters false positives, validates extracted skills
import json
import logging
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)


class SkillsValidator:
    """Validates extracted skills against reference list"""

    def __init__(self, reference_file: str = "src/config/skills_reference_2026.json"):
        """Load skills reference from JSON file"""
        self.valid_skills: set[str] = set()
        self.reference_file = Path(reference_file)
        self._load_reference()

    def _load_reference(self) -> None:
        """Load valid skills from JSON"""
        try:
            with open(self.reference_file, "r", encoding="utf-8") as f:
                skills_data = json.load(f)

                # Handle structure: {"total_skills": 246, "skills": [...]}
                if isinstance(skills_data, dict) and "skills" in skills_data:
                    skills_list = skills_data["skills"]
                    self.valid_skills = {
                        skill.get("name", "").lower()
                        for skill in skills_list
                        if isinstance(skill, dict) and skill.get("name")
                    }
                # Fallback: plain list
                elif isinstance(skills_data, list):
                    self.valid_skills = {
                        skill.get("name", "").lower()
                        for skill in skills_data
                        if isinstance(skill, dict) and skill.get("name")
                    }

                logger.info(
                    f"✅ Loaded {len(self.valid_skills)} valid skills from reference"
                )
        except Exception as e:
            logger.error(f"Failed to load skills reference: {e}")
            self.valid_skills = set()

    def validate_skills(
        self, extracted_skills: str, min_confidence: float = 0.5
    ) -> Tuple[str, bool]:
        """
        Validate extracted skills, filter false positives

        Args:
            extracted_skills: Comma-separated skills string
            min_confidence: Minimum match ratio (default 0.5 = 50%)

        Returns:
            (validated_skills_string, is_valid)
        """
        if not extracted_skills or not extracted_skills.strip():
            return "", False

        # Parse skills
        skills_list = [s.strip() for s in extracted_skills.split(",") if s.strip()]

        if not skills_list:
            return "", False

        # Validate against reference
        valid_skills = [
            skill for skill in skills_list if skill.lower() in self.valid_skills
        ]

        # Calculate confidence
        confidence = len(valid_skills) / len(skills_list) if skills_list else 0.0

        # Check if meets minimum confidence
        is_valid = confidence >= min_confidence and len(valid_skills) > 0

        validated_str = ", ".join(valid_skills)

        logger.debug(
            f"Skills validation: {len(skills_list)} total, "
            f"{len(valid_skills)} valid, confidence={confidence:.2f}"
        )

        return validated_str, is_valid
