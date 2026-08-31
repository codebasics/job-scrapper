"""
Batch Re-extraction Script
Re-extracts skills for all jobs using updated skills reference
Optimized for FP/FN reduction with type-safe implementation
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import sys
from pathlib import Path
from typing import Final, NamedTuple, TypedDict

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
DEGREE_CONTEXT_WINDOW: Final[int] = 80
DEFAULT_BATCH_SIZE: Final[int] = 100
DEFAULT_DB_PATH: Final[str] = 'data/jobs.db'
DEFAULT_SKILLS_REF_PATH: Final[str] = 'src/config/skills_reference_2026.json'

# Type definitions
class ReextractionStats(TypedDict):
    """Type-safe statistics dictionary."""
    total_jobs: int
    processed: int
    updated: int
    skills_added: int
    skills_removed: int
    dry_run: bool


class CompiledPattern(NamedTuple):
    """Pre-compiled regex pattern with associated skill name."""
    regex: re.Pattern[str]
    skill_name: str


# Pre-compiled degree context patterns (compiled once at module load)
_DEGREE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE) for p in (
        r"bachelor'?s?\s+(?:degree\s+)?(?:in|of)",
        r"master'?s?\s+(?:degree\s+)?(?:in|of)",
        r"(?:bs|ba|ms|ma|phd|mba)\s+(?:in|of)?",
        r"degree\s+in",
        r"diploma\s+in",
        r"major\s+in",
        r"studied\s+",
        r"background\s+in",
    )
)

# Default skills to check for degree context
DEFAULT_DEGREE_CHECK_SKILLS: Final[frozenset[str]] = frozenset({
    'Computer Science', 'Data Science', 'Mathematics',
    'Statistics', 'Information Technology', 'Physics',
    'Economics', 'Business Administration'
})


def load_skills_reference(path: str) -> list[CompiledPattern]:
    """
    Load skills reference and pre-compile all patterns.

    Patterns are sorted by length (longest first) for greedy matching.
    Invalid regex patterns are logged and skipped.

    Args:
        path: Path to skills reference JSON file

    Returns:
        List of pre-compiled patterns sorted by length (longest first)

    Raises:
        FileNotFoundError: If skills reference file not found
        ValueError: If JSON is invalid
    """
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Skills reference file not found: {path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in skills reference: {e}")

    # Build pattern list
    patterns: list[tuple[str, str]] = []
    for skill in data.get('skills', []):
        name = skill.get('name', '')
        for pattern_str in skill.get('patterns', []):
            patterns.append((pattern_str, name))

    # Sort by pattern length (longest first) for greedy matching
    patterns.sort(key=lambda x: len(x[0]), reverse=True)

    # Compile patterns, logging any errors
    compiled: list[CompiledPattern] = []
    invalid_count = 0

    for pattern_str, name in patterns:
        try:
            compiled.append(CompiledPattern(
                regex=re.compile(pattern_str, re.IGNORECASE),
                skill_name=name
            ))
        except re.error as e:
            logger.warning(f"Invalid regex pattern for skill '{name}': {pattern_str} - {e}")
            invalid_count += 1

    if invalid_count > 0:
        logger.warning(f"Skipped {invalid_count} invalid patterns")

    logger.info(f"Loaded {len(compiled)} compiled patterns from {path}")
    return compiled


def is_degree_context(text: str, match_start: int, window: int = DEGREE_CONTEXT_WINDOW) -> bool:
    """
    Check if match is in degree/education context.

    Uses pre-compiled patterns for efficiency.

    Args:
        text: Full job description text
        match_start: Start position of the skill match
        window: Characters to look back for context

    Returns:
        True if the skill appears in a degree context (should be filtered)
    """
    context_start = max(0, match_start - window)
    context = text[context_start:match_start].lower()

    return any(pattern.search(context) for pattern in _DEGREE_PATTERNS)


def extract_skills_optimized(
    text: str,
    compiled_patterns: list[CompiledPattern],
    degree_check_skills: frozenset[str] | None = None
) -> list[str]:
    """
    Extract skills with optimized FP/FN handling using pre-compiled patterns.

    Args:
        text: Job description text
        compiled_patterns: Pre-compiled and pre-sorted patterns
        degree_check_skills: Skills to check for degree context

    Returns:
        Sorted list of extracted skill names
    """
    if not text or not text.strip():
        return []

    if degree_check_skills is None:
        degree_check_skills = DEFAULT_DEGREE_CHECK_SKILLS

    found_skills: set[str] = set()
    consumed: list[tuple[int, int]] = []

    for pattern, skill_name in compiled_patterns:
        for match in pattern.finditer(text):
            start, end = match.span()

            # Skip if region already consumed
            if any(s <= start < e or s < end <= e for s, e in consumed):
                continue

            # FP Filter: Check degree context for specific skills
            if skill_name in degree_check_skills:
                if is_degree_context(text, start):
                    continue  # Skip - false positive

            found_skills.add(skill_name)
            consumed.append((start, end))

    return sorted(found_skills)


def reextract_all_jobs(
    db_path: str = DEFAULT_DB_PATH,
    skills_ref_path: str = DEFAULT_SKILLS_REF_PATH,
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False
) -> ReextractionStats:
    """
    Re-extract skills for all jobs in database.

    Args:
        db_path: Path to SQLite database
        skills_ref_path: Path to skills reference JSON
        batch_size: Number of jobs to process per batch
        dry_run: If True, don't update database

    Returns:
        Type-safe statistics dictionary
    """
    print(f"Loading skills reference from {skills_ref_path}...")
    compiled_patterns = load_skills_reference(skills_ref_path)
    print(f"Loaded {len(compiled_patterns)} compiled patterns")

    # Use context manager for guaranteed cleanup
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        # Get total count with null check
        cursor.execute("SELECT COUNT(*) FROM jobs WHERE job_description IS NOT NULL")
        result = cursor.fetchone()
        if result is None:
            raise RuntimeError("Failed to count jobs - query returned None")
        total_jobs: int = result[0]
        print(f"Total jobs to process: {total_jobs}")

        stats: ReextractionStats = {
            'total_jobs': total_jobs,
            'processed': 0,
            'updated': 0,
            'skills_added': 0,
            'skills_removed': 0,
            'dry_run': dry_run
        }

        offset = 0
        while offset < total_jobs:
            cursor.execute("""
                SELECT job_id, job_description, skills
                FROM jobs
                WHERE job_description IS NOT NULL
                LIMIT ? OFFSET ?
            """, (batch_size, offset))

            jobs = cursor.fetchall()
            if not jobs:
                break

            for row in jobs:
                job_id: str = row[0]
                description: str = row[1]
                old_skills: str | None = row[2]

                # Extract with new reference
                new_skills_list = extract_skills_optimized(description, compiled_patterns)
                new_skills = ', '.join(new_skills_list)

                # Compare
                old_set = set(s.strip() for s in (old_skills or '').split(',') if s.strip())
                new_set = set(new_skills_list)

                added = len(new_set - old_set)
                removed = len(old_set - new_set)

                stats['skills_added'] += added
                stats['skills_removed'] += removed

                # Update if changed
                if new_skills != (old_skills or ''):
                    if not dry_run:
                        cursor.execute(
                            "UPDATE jobs SET skills = ? WHERE job_id = ?",
                            (new_skills, job_id)
                        )
                    stats['updated'] += 1

                stats['processed'] += 1

            if not dry_run:
                conn.commit()

            offset += batch_size
            progress = (offset / total_jobs) * 100
            print(f"Progress: {min(offset, total_jobs)}/{total_jobs} ({progress:.1f}%)")

    print("\n" + "=" * 60)
    print("RE-EXTRACTION COMPLETE")
    print("=" * 60)
    print(f"Total jobs processed: {stats['processed']}")
    print(f"Jobs updated: {stats['updated']}")
    print(f"Skills added (FN recovered): {stats['skills_added']}")
    print(f"Skills removed (FP eliminated): {stats['skills_removed']}")
    if dry_run:
        print("\n[DRY RUN - No changes made to database]")

    return stats


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Re-extract skills for all jobs')
    parser.add_argument('--db', default=DEFAULT_DB_PATH, help='Database path')
    parser.add_argument('--ref', default=DEFAULT_SKILLS_REF_PATH,
                        help='Skills reference path')
    parser.add_argument('--batch-size', type=int, default=DEFAULT_BATCH_SIZE, help='Batch size')
    parser.add_argument('--dry-run', action='store_true', help='Preview only')

    args = parser.parse_args()

    reextract_all_jobs(
        db_path=args.db,
        skills_ref_path=args.ref,
        batch_size=args.batch_size,
        dry_run=args.dry_run
    )
