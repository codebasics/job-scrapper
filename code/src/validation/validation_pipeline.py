#!/usr/bin/env python3
"""
Validation Pipeline - Fast FP/FN Detection Layer
Integrates with shell scripts for speed boost
"""

import sqlite3
import subprocess
import json
import re
from operator import itemgetter
from pathlib import Path
from typing import TypedDict


class JobValidationResult(TypedDict):
    true_positives: set[str]
    false_positives: set[str]
    false_negatives: set[str]
    precision: float
    recall: float


class BatchValidationResult(TypedDict):
    total_true_positives: int
    total_false_positives: int
    total_false_negatives: int
    precision: float
    recall: float
    f1_score: float
    top_false_positives: list[tuple[str, int]]
    top_false_negatives: list[tuple[str, int]]

class SkillValidator:
    """Validates skill extraction for False Positives and False Negatives"""

    def __init__(self, db_path: str = "data/jobs.db", skills_ref_path: str = "src/config/skills_reference_2026.json"):
        self.db_path = db_path
        self.skills_ref_path = skills_ref_path
        self._load_skills_reference()

    def _load_skills_reference(self):
        """Load skill patterns from JSON"""
        with open(self.skills_ref_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        self.skill_patterns = {}
        for skill in data['skills']:
            patterns = []
            for p in skill.get('patterns', []):
                try:
                    patterns.append(re.compile(p, re.IGNORECASE))
                except re.error:
                    pass
            if patterns:
                self.skill_patterns[skill['name']] = patterns

    def validate_job(self, job_description: str, extracted_skills: str) -> JobValidationResult:
        """Validate a single job's skill extraction"""
        extracted = set(s.strip() for s in extracted_skills.split(',') if s.strip())
        detected = set()

        # Detect skills using patterns
        for skill_name, patterns in self.skill_patterns.items():
            for pattern in patterns:
                if pattern.search(job_description):
                    detected.add(skill_name)
                    break

        # Calculate FP and FN
        false_positives = extracted - detected  # In extracted but not detected
        false_negatives = detected - extracted  # Detected but not extracted
        true_positives = extracted & detected   # Both

        return {
            'true_positives': true_positives,
            'false_positives': false_positives,
            'false_negatives': false_negatives,
            'precision': len(true_positives) / len(extracted) if extracted else 1.0,
            'recall': len(true_positives) / len(detected) if detected else 1.0
        }

    def validate_batch(self, limit: int = 100) -> BatchValidationResult:
        """Validate a batch of jobs and return aggregate stats"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT job_id, job_description, skills FROM jobs "
            "WHERE job_description IS NOT NULL AND skills IS NOT NULL "
            f"LIMIT {limit}"
        )

        total_tp = 0
        total_fp = 0
        total_fn = 0
        fp_counts = {}
        fn_counts = {}

        for _, desc, skills in cursor.fetchall():
            result = self.validate_job(desc, skills)
            total_tp += len(result['true_positives'])
            total_fp += len(result['false_positives'])
            total_fn += len(result['false_negatives'])

            for fp in result['false_positives']:
                fp_counts[fp] = fp_counts.get(fp, 0) + 1
            for fn in result['false_negatives']:
                fn_counts[fn] = fn_counts.get(fn, 0) + 1

        conn.close()

        precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 1.0
        recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 1.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

        return {
            'total_true_positives': total_tp,
            'total_false_positives': total_fp,
            'total_false_negatives': total_fn,
            'precision': precision,
            'recall': recall,
            'f1_score': f1,
            'top_false_positives': sorted(fp_counts.items(), key=itemgetter(1), reverse=True)[:10],
            'top_false_negatives': sorted(fn_counts.items(), key=itemgetter(1), reverse=True)[:10]
        }

    def run_shell_validation(self) -> str:
        """Run shell script for fast pattern validation"""
        script_path = Path(__file__).parent / "validate_skills.sh"
        if script_path.exists():
            result = subprocess.run(
                ["bash", str(script_path), self.db_path],
                capture_output=True,
                text=True
            )
            return result.stdout
        return "Shell validation script not found"


def main():
    """Run validation pipeline"""
    print("=" * 60)
    print("SKILL EXTRACTION VALIDATION PIPELINE")
    print("=" * 60)

    validator = SkillValidator()

    # Run batch validation
    print("\nValidating skills extraction...")
    results = validator.validate_batch(limit=500)

    print(f"\n--- VALIDATION RESULTS (500 jobs sample) ---")
    print(f"True Positives:  {results['total_true_positives']}")
    print(f"False Positives: {results['total_false_positives']}")
    print(f"False Negatives: {results['total_false_negatives']}")
    print(f"\nPrecision: {results['precision']:.2%}")
    print(f"Recall:    {results['recall']:.2%}")
    print(f"F1 Score:  {results['f1_score']:.2%}")

    if results['top_false_positives']:
        print("\n--- TOP FALSE POSITIVES ---")
        for skill, count in results['top_false_positives']:
            print(f"  {skill}: {count}")

    if results['top_false_negatives']:
        print("\n--- TOP FALSE NEGATIVES ---")
        for skill, count in results['top_false_negatives']:
            print(f"  {skill}: {count}")


if __name__ == "__main__":
    main()
