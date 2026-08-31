"""Unified scraping orchestrator for Naukri using Playwright.

Supported platforms: naukri only (LinkedIn via JobSpy).
Returns list[JobModel] with url, jd, skills-related fields populated.

PERFORMANCE:
- Naukri: Playwright (headless=False) ~6s for 10 jobs
- NO proxy needed - direct connection

SCALABLE COMPONENTS (10K+ jobs):
- BatchProcessor: Streaming batches with validation (1000 jobs/batch)
- CheckpointManager: Crash recovery with JSON persistence
- ProgressTracker: Real-time ETA with moving average throughput
- Rate Limiters: Platform-specific (Naukri=15)
"""
from __future__ import annotations

from src.analysis.skill_extraction.extractor import AdvancedSkillExtractor
from src.models.models import JobDetailModel

from .naukri_unified import scrape_naukri_jobs_unified

# Scalable components available for 10K+ job operations
from .scalable import (
    BatchProcessor,
    CheckpointManager,
    ProgressTracker,
    get_rate_limiter,
)

# Public API: Main function + scalable components for 10K+ operations
__all__ = [
    "scrape_jobs",
    "BatchProcessor",
    "CheckpointManager",
    "ProgressTracker",
    "get_rate_limiter",
]


def _extract_skills_as_list(extractor: AdvancedSkillExtractor, text: str) -> list[str]:
    """Extract skills and ensure list[str] return type"""
    result = extractor.extract(text, return_confidence=False)
    # Type narrow: when return_confidence=False, returns list[str]
    skills: list[str] = []
    for item in result:
        if isinstance(item, str):
            skills.append(item)
    return skills


async def scrape_jobs(
    platform: str,
    *,
    keyword: str,
    location: str,
    limit: int = 50,
) -> list[JobDetailModel]:
    """Scrape jobs and extract skills using lightweight regex patterns"""
    p: str = platform.lower()

    # Get raw jobs without skills extraction
    jobs: list[JobDetailModel]
    if p == "naukri":
        jobs = await scrape_naukri_jobs_unified(keyword=keyword, location=location, limit=limit)
    else:
        raise ValueError(f"Unsupported platform: {platform}. Supported: naukri only (LinkedIn via JobSpy)")

    # Initialize skill extractor once for batch processing (performance optimization)
    extractor = AdvancedSkillExtractor('src/config/skills_reference_2026.json')

    # Extract skills for each job using advanced 3-layer extraction
    for job in jobs:
        jd: str = getattr(job, 'jd', '') or ''
        if jd:
            skills: list[str] = _extract_skills_as_list(extractor, jd)
            job.skills = ','.join(skills) if skills else ''

    return jobs
