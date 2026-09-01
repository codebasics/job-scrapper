"""Test: Complete LinkedIn Workflow - 1000 jobs
Phase 1: URL Collection (5 tabs) → Phase 2: n+5 Rolling Window
Note: In production, limit will come from Streamlit UI inputs
"""
import asyncio
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.scraper.unified.linkedin.complete_workflow import complete_linkedin_workflow

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('tests/rolling_window_output.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def get_job_count() -> int:
    """Get total jobs in database"""
    conn = sqlite3.connect('data/jobs.db')
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM jobs")
    count = cursor.fetchone()[0]
    conn.close()
    return count


async def test_complete_workflow_1000():
    """Complete workflow: URL Collection → n+5 Rolling Window
    
    Testing with 1000 jobs directly.
    In production: limit comes from Streamlit UI inputs
    """
    # Test parameters (will come from Streamlit UI in production)
    KEYWORD = "AI Engineer"
    LOCATION = ""
    TARGET_JOBS = 1000  # Streamlit slider input
    WINDOW_SIZE = 5
    HEADLESS = False
    
    logger.info("="*80)
    logger.info("🚀 COMPLETE LINKEDIN WORKFLOW TEST")
    logger.info("="*80)
    
    logger.info(f"\n📋 Workflow:")
    logger.info(f"   Phase 1: URL Collection (5 concurrent tabs)")
    logger.info(f"   Phase 2: n+5 Rolling Window Processing")
    logger.info(f"   Pattern: Job n completes → Job n+5 starts")
    
    logger.info(f"\n🎯 Configuration (Test - will come from UI):")
    logger.info(f"   Keyword: {KEYWORD}")
    logger.info(f"   Location: {LOCATION or 'Worldwide'}")
    logger.info(f"   Target Jobs: {TARGET_JOBS}")
    logger.info(f"   Window Size: {WINDOW_SIZE}")
    logger.info(f"   Headless: {HEADLESS}")
    logger.info(f"   Skills Source: skills_reference_2026.json")
    logger.info(f"   Storage: jobs.db (immediate)")
    
    logger.info("\n" + "="*80)
    logger.info("📥 WORKFLOW STARTED")
    logger.info("="*80)
    
    start_time = datetime.now()
    jobs_before = get_job_count()
    logger.info(f"Jobs in DB before: {jobs_before}")
    logger.info(f"Start Time: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Run complete workflow
    jobs = await complete_linkedin_workflow(
        keyword=KEYWORD,
        location=LOCATION,
        target_jobs=TARGET_JOBS,
        window_size=WINDOW_SIZE,
        headless=HEADLESS,
    )
    
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    jobs_after = get_job_count()
    new_jobs = jobs_after - jobs_before
    
    # Results summary
    logger.info("\n" + "="*80)
    logger.info("✅ WORKFLOW COMPLETE")
    logger.info("="*80)
    logger.info(f"End Time: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"NEW Jobs Stored: {new_jobs}")
    logger.info(f"Total in DB: {jobs_after}")
    logger.info(f"Duration: {duration:.1f} seconds ({duration/60:.1f} minutes)")
    logger.info(f"Rate: {new_jobs/duration:.2f} jobs/second")
    logger.info(f"Efficiency: {(new_jobs/1000)*100:.1f}% success rate")
    
    return jobs


if __name__ == "__main__":
    asyncio.run(test_complete_workflow_1000())
