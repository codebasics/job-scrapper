#!/usr/bin/env python3
"""
Cross-verify all jobs: Compare skills column vs job_description
using patterns from skills_reference_2026.json

Find False Positives (skills in DB but not in description)
Find False Negatives (skills in description but not extracted)
"""

import json
import re
import sqlite3
from collections import defaultdict


def load_skills_patterns(filepath):
    """Load and compile skill patterns from JSON file."""
    with open(filepath, "r", encoding="utf-8") as f:
        skills_data = json.load(f)

    skill_patterns = {}
    for skill in skills_data["skills"]:
        name = skill["name"].lower().strip()
        patterns = []
        for p in skill["patterns"]:
            try:
                patterns.append(re.compile(p, re.IGNORECASE))
            except re.error as e:
                print(f"  Warning: Invalid regex for {name}: {p} - {e}")
        skill_patterns[name] = patterns

    return skill_patterns, skills_data["total_skills"]


def extract_skills_from_description(description, skill_patterns):
    """Extract skills from job description using patterns."""
    found_skills = set()
    for skill_name, patterns in skill_patterns.items():
        for pattern in patterns:
            if pattern.search(description):
                found_skills.add(skill_name)
                break
    return found_skills


def parse_skills_from_db(skills_str):
    """Parse comma-separated skills string from database."""
    if not skills_str:
        return set()
    return set(s.strip().lower() for s in skills_str.split(",") if s.strip())


def main():
    # Load skills patterns from the config file
    skills_file = "src/config/skills_reference_2026.json"
    print(f"Loading skills patterns from: {skills_file}")
    skill_patterns, total_skills = load_skills_patterns(skills_file)
    print(f"Loaded {total_skills} skills with patterns")

    # Connect to database
    conn = sqlite3.connect("data/jobs.db")
    cursor = conn.cursor()

    # Get all jobs with both description and skills
    cursor.execute("""
        SELECT job_id, job_description, skills
        FROM jobs
        WHERE job_description IS NOT NULL
        AND job_description != ''
        AND skills IS NOT NULL
        AND skills != ''
    """)
    rows = cursor.fetchall()
    print(f"\nTotal jobs to analyze: {len(rows)}")

    # Track statistics
    total_tp = 0  # True Positives
    total_fp = 0  # False Positives (in DB but not in description)
    total_fn = 0  # False Negatives (in description but not in DB)

    fp_by_skill = defaultdict(int)
    fn_by_skill = defaultdict(int)

    # Sample FP/FN examples
    fp_examples = []
    fn_examples = []

    processed = 0
    for job_id, description, db_skills in rows:
        processed += 1
        if processed % 500 == 0:
            print(f"  Processed {processed} jobs...")

        # Parse skills from DB
        db_skills_set = parse_skills_from_db(db_skills)

        # Extract skills from description using patterns
        extracted_skills = extract_skills_from_description(description, skill_patterns)

        # Calculate TP, FP, FN
        for skill in db_skills_set:
            if skill in extracted_skills:
                total_tp += 1
            else:
                total_fp += 1
                fp_by_skill[skill] += 1
                if len(fp_examples) < 5:
                    fp_examples.append(
                        {
                            "job_id": job_id,
                            "skill": skill,
                            "description_snippet": description[:200] + "..."
                            if len(description) > 200
                            else description,
                        }
                    )

        for skill in extracted_skills:
            if skill not in db_skills_set:
                total_fn += 1
                fn_by_skill[skill] += 1
                if len(fn_examples) < 5:
                    fn_examples.append(
                        {
                            "job_id": job_id,
                            "skill": skill,
                            "db_skills": list(db_skills_set)[:10],
                        }
                    )

    conn.close()

    # Calculate metrics
    precision = (
        total_tp / (total_tp + total_fp) * 100 if (total_tp + total_fp) > 0 else 0
    )
    recall = total_tp / (total_tp + total_fn) * 100 if (total_tp + total_fn) > 0 else 0
    f1 = (
        2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    )

    # Print results
    print("\n" + "=" * 60)
    print("CROSS-VERIFICATION RESULTS")
    print("=" * 60)
    print(f"\nTotal Jobs Analyzed: {len(rows)}")
    print(f"\nMetrics:")
    print(f"  True Positives (correct extractions): {total_tp}")
    print(f"  False Positives (in DB but NOT in description): {total_fp}")
    print(f"  False Negatives (in description but NOT in DB): {total_fn}")
    print(f"\n  Precision: {precision:.2f}%")
    print(f"  Recall: {recall:.2f}%")
    print(f"  F1 Score: {f1:.2f}%")

    print("\n" + "-" * 60)
    print("TOP 25 FALSE POSITIVES (skills in DB but NOT in description):")
    print("-" * 60)
    for skill, count in sorted(fp_by_skill.items(), key=lambda x: -x[1])[:25]:
        print(f"  {skill}: {count} FPs")

    print("\n" + "-" * 60)
    print("TOP 25 FALSE NEGATIVES (skills in description but NOT in DB):")
    print("-" * 60)
    for skill, count in sorted(fn_by_skill.items(), key=lambda x: -x[1])[:25]:
        print(f"  {skill}: {count} FNs")

    # Print sample FP examples
    if fp_examples:
        print("\n" + "-" * 60)
        print("SAMPLE FALSE POSITIVE EXAMPLES:")
        print("-" * 60)
        for ex in fp_examples[:3]:
            print(f"\n  Job ID: {ex['job_id']}")
            print(f"  FP Skill: {ex['skill']}")
            print(f"  Description: {ex['description_snippet']}")

    # Print sample FN examples
    if fn_examples:
        print("\n" + "-" * 60)
        print("SAMPLE FALSE NEGATIVE EXAMPLES:")
        print("-" * 60)
        for ex in fn_examples[:3]:
            print(f"\n  Job ID: {ex['job_id']}")
            print(f"  FN Skill: {ex['skill']}")
            print(f"  DB Skills (sample): {ex['db_skills']}")


if __name__ == "__main__":
    main()
