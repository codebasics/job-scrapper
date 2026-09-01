#!/usr/bin/env python3
"""One-off script: scrape ~500 combined FDE jobs across LinkedIn (US) + Naukri (India).
Run from the code/ directory: python scrape_fde_500.py
"""
import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(message)s", datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)


async def main():
    from src.scraper.multi_platform_service import scrape_jobs_with_skills

    keyword = "Forward Deployed Engineer"

    print("=" * 60)
    print("FDE SCRAPE: LinkedIn (US, target 175 new) + Naukri (India, target 250 new)")
    print("=" * 60)

    linkedin_jobs = await scrape_jobs_with_skills(
        platforms=["linkedin"],
        keyword=keyword,
        location="United States",
        limit=175,
        headless=False,
        store_to_db=True,
    )
    print(f"\n✅ LinkedIn done: {len(linkedin_jobs)} jobs")

    naukri_jobs = await scrape_jobs_with_skills(
        platforms=["naukri"],
        keyword=keyword,
        location="India",
        limit=250,
        headless=False,
        store_to_db=True,
    )
    print(f"\n✅ Naukri done: {len(naukri_jobs)} jobs")

    print("\n" + "=" * 60)
    print(f"TOTAL NEW: {len(linkedin_jobs) + len(naukri_jobs)}")
    print("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
