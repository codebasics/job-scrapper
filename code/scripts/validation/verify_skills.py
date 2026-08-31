#!/usr/bin/env python3
"""Cross-verify all jobs with updated skill patterns."""

import json
import re
import sqlite3
from collections import defaultdict


def main():
    # Load the updated skills reference
    with open("skills_reference_2026.json", "r") as f:
        skills_data = json.load(f)

    # Build skill patterns - compile once
    skill_patterns = {}
    for skill in skills_data["skills"]:
        name = skill["name"].lower()
        patterns = [re.compile(p, re.IGNORECASE) for p in skill["patterns"]]
        skill_patterns[name] = patterns

    # Connect to DB
    conn = sqlite3.connect("data/jobs.db")
    cursor = conn.cursor()

    # Get ALL jobs with job_description
    cursor.execute(
        "SELECT job_id, job_description FROM jobs WHERE job_description IS NOT NULL"
    )
    rows = cursor.fetchall()

    print(f"Total jobs to analyze: {len(rows)}")

    # Extract skills using NEW patterns for all jobs
    total_skills_extracted = 0
    skill_extraction_counts = defaultdict(int)

    processed = 0
    for job_id, description in rows:
        if not description:
            continue

        processed += 1
        if processed % 500 == 0:
            print(f"Processed {processed} jobs...")

        for skill_name, patterns in skill_patterns.items():
            for pattern in patterns:
                if pattern.search(description):
                    total_skills_extracted += 1
                    skill_extraction_counts[skill_name] += 1
                    break

    print(f"\nTotal skills extracted with NEW patterns: {total_skills_extracted}")
    print(f"Average skills per job: {total_skills_extracted / len(rows):.1f}")

    print("\nTop 30 most extracted skills:")
    for skill, count in sorted(skill_extraction_counts.items(), key=lambda x: -x[1])[
        :30
    ]:
        print(f"  {skill}: {count}")

    # Check Azure/AWS sub-service extractions
    print("\n=== Azure/AWS Sub-service Extraction Check ===")
    azure_subs = [
        "azure functions",
        "azure kubernetes service",
        "azure openai",
        "azure storage",
    ]
    aws_subs = [
        "aws bedrock",
        "aws cloud services",
        "aws codepipeline",
        "aws lambda",
        "aws sagemaker",
    ]

    print("Azure sub-services:")
    for skill in azure_subs:
        print(f"  {skill}: {skill_extraction_counts.get(skill, 0)}")

    print("AWS sub-services:")
    for skill in aws_subs:
        print(f"  {skill}: {skill_extraction_counts.get(skill, 0)}")

    print(f"\nAzure (main): {skill_extraction_counts.get('azure', 0)}")
    print(f"AWS (main): {skill_extraction_counts.get('aws', 0)}")

    # Check for potential remaining FP issues
    print("\n=== Checking for potential FP issues ===")

    # Check HTML5 vs HTML
    print(f"HTML: {skill_extraction_counts.get('html', 0)}")
    print(f"HTML5: {skill_extraction_counts.get('html5', 0)}")

    # Check CSS3 vs CSS
    print(f"CSS: {skill_extraction_counts.get('css', 0)}")
    print(f"CSS3: {skill_extraction_counts.get('css3', 0)}")

    # Check React vs React Native
    print(f"React: {skill_extraction_counts.get('react', 0)}")
    print(f"React Native: {skill_extraction_counts.get('react native', 0)}")

    conn.close()


if __name__ == "__main__":
    main()
