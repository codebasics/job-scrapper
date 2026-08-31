"""Queue-Based Concurrent Scraper - 10 Parallel Workers with Job Queue

Architecture:
- 10 persistent browser tabs (workers)
- Shared job queue - workers pull next job when done
- Staggered start (1s between each worker) to avoid burst
- Per-worker delay after each job (3-5s random)
- 429 handling: exponential backoff per worker
"""

import asyncio
import html
import logging
import os
import random
import re
from dataclasses import dataclass
from typing import List, Optional

from playwright.async_api import (
    BrowserContext,
    Page,
    ProxySettings,
    async_playwright,
)

from src.analysis.skill_extraction.extractor import AdvancedSkillExtractor
from src.analysis.skill_extraction.skill_validator import SkillValidator
from src.db.operations import JobStorageOperations
from src.models.models import JobDetailModel
from src.scraper.unified.linkedin.date_parser import parse_linkedin_date
from src.scraper.unified.linkedin.job_validator import JobValidator
from src.scraper.unified.linkedin.selector_config import (
    DETAIL_SELECTORS,
    EXPIRED_JOB_INDICATORS,
    LOGIN_WALL_INDICATORS,
)
from src.scraper.unified.scalable.user_agent_pool import get_random_user_agent

logger = logging.getLogger(__name__)


@dataclass
class JobTask:

    url: str
    job_id: str
    platform: str
    actual_role: str
    index: int
    total: int


@dataclass
class WorkerStats:
    """Per-worker statistics"""

    worker_id: int
    processed: int = 0
    success: int = 0
    failed: int = 0
    expired: int = 0
    rate_limited: int = 0
    current_delay: float = 3.0  # Start with 3s delay


class QueueBasedScraper:
    """Queue-based scraper with 10 parallel workers"""

    def __init__(
        self,
        num_workers: int = 10,
        base_delay: float = 3.0,
        max_delay: float = 30.0,
        headless: bool = False,
    ):
        self.num_workers = num_workers
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.headless = headless

        # Shared state
        self.job_queue: asyncio.Queue[Optional[JobTask]] = asyncio.Queue()
        self.results: List[JobDetailModel] = []
        self.results_lock = asyncio.Lock()

        # Worker stats
        self.worker_stats: dict[int, WorkerStats] = {}

        # Global counters
        self.total_processed = 0
        self.total_success = 0
        self.total_expired = 0
        self.total_failed = 0

        # Validators
        self.skill_extractor = AdvancedSkillExtractor(
            "src/config/skills_reference_2026.json"
        )
        self.skills_validator = SkillValidator("src/config/skills_reference_2026.json")
        self.job_validator = JobValidator(min_description_length=100)
        self.db_ops = JobStorageOperations()

        # Seen tracking for deduplication
        self.seen_urls: set[str] = set()
        self.seen_job_ids: set[str] = set()

    async def worker(
        self,
        worker_id: int,
        context: BrowserContext,
    ) -> None:
        """Worker coroutine - processes jobs from queue"""

        stats = WorkerStats(worker_id=worker_id)
        self.worker_stats[worker_id] = stats

        # Create dedicated page for this worker
        page = await context.new_page()
        await page.set_extra_http_headers({"User-Agent": get_random_user_agent()})

        logger.info(f"👷 Worker {worker_id} started")

        try:
            while True:
                # Get next job from queue (blocks until available)
                task = await self.job_queue.get()

                # Poison pill - shutdown signal
                if task is None:
                    logger.info(f"👷 Worker {worker_id} shutting down")
                    break

                stats.processed += 1

                try:
                    # Process the job
                    result = await self._process_job(page, task, worker_id, stats)

                    if result:
                        async with self.results_lock:
                            self.results.append(result)
                            self.total_success += 1
                        stats.success += 1
                        logger.info(
                            f"✅ Worker {worker_id} [{task.index}/{task.total}] "
                            f"Success: {task.job_id[:30]}"
                        )
                        # Reset delay on success
                        stats.current_delay = self.base_delay

                except RateLimitError as e:
                    stats.rate_limited += 1
                    # Exponential backoff for this worker
                    stats.current_delay = min(stats.current_delay * 2, self.max_delay)
                    logger.warning(
                        f"⚠️ Worker {worker_id} rate limited, "
                        f"backing off to {stats.current_delay:.1f}s"
                    )
                    # Re-queue the job for another worker
                    await self.job_queue.put(task)
                    # Extra cooldown for this worker
                    await asyncio.sleep(stats.current_delay)

                except ExpiredJobError:
                    stats.expired += 1
                    self.total_expired += 1
                    # Delete from DB
                    self.db_ops.delete_urls([task.url])
                    logger.info(f"🗑️ Worker {worker_id} expired: {task.job_id[:30]}")

                except Exception as e:
                    stats.failed += 1
                    self.total_failed += 1
                    logger.error(
                        f"❌ Worker {worker_id} error: {task.job_id[:30]} - {e}"
                    )

                finally:
                    self.job_queue.task_done()
                    self.total_processed += 1

                    # Random delay before next job (jittered)
                    delay = stats.current_delay + random.uniform(-1.0, 1.0)
                    delay = max(2.0, delay)  # Minimum 2s
                    await asyncio.sleep(delay)

                    # Rotate user agent periodically
                    if stats.processed % 10 == 0:
                        await page.set_extra_http_headers(
                            {"User-Agent": get_random_user_agent()}
                        )

        finally:
            await page.close()
            logger.info(
                f"👷 Worker {worker_id} finished: "
                f"{stats.success} success, {stats.failed} failed, "
                f"{stats.expired} expired, {stats.rate_limited} rate-limited"
            )

    async def _process_job(
        self,
        page: Page,
        task: JobTask,
        worker_id: int,
        stats: WorkerStats,
    ) -> Optional[JobDetailModel]:
        """Process a single job - returns JobDetailModel or raises exception"""

        url = task.url
        job_id = task.job_id

        # Navigate with response status check
        response = await page.goto(url, timeout=30000)

        # HTTP status checks
        if response:
            status = response.status
            if status == 429:
                raise RateLimitError("HTTP 429 Too Many Requests")
            if status == 404:
                raise ExpiredJobError("HTTP 404 Not Found")
            if status == 503:
                raise ServerError("HTTP 503 Service Unavailable")
            if 500 <= status < 600:
                raise ServerError(f"HTTP {status} Server Error")

        # Wait for page load
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)

        # Check for 404 page content
        page_title = await page.title()
        if "404" in page_title.lower() or "not found" in page_title.lower():
            raise ExpiredJobError(f"LinkedIn 404 page: {page_title}")

        # Check for login wall
        current_url = page.url
        for login_path in LOGIN_WALL_INDICATORS["login_urls"]:
            if login_path in current_url.lower():
                raise LoginWallError("LinkedIn login wall detected")

        # Check for expired job indicators
        page_text = await page.evaluate("() => document.body.innerText")
        page_text_lower = page_text.lower()

        for error_msg in EXPIRED_JOB_INDICATORS["error_messages"]:
            if error_msg in page_text_lower:
                raise ExpiredJobError(f"Job expired: {error_msg}")

        # Check if redirected away from job page
        if "/jobs/view/" not in current_url:
            raise ExpiredJobError("Redirected away from job page")

        # Extract job title
        job_title = ""
        for selector in DETAIL_SELECTORS["job_title"]:
            elem = await page.query_selector(selector)
            if elem:
                job_title = (await elem.inner_text()).strip()
                break

        if not job_title:
            job_title = task.actual_role

        # Extract description
        job_description = ""
        for selector in DETAIL_SELECTORS["description"]:
            elem = await page.query_selector(selector)
            if elem:
                job_description = await elem.inner_text()
                # Clean HTML
                job_description = html.unescape(job_description)
                job_description = re.sub(r"<[^>]+>", " ", job_description)
                job_description = re.sub(r"&[a-zA-Z]+;", " ", job_description)
                job_description = " ".join(job_description.split())
                break

        if not job_description.strip():
            raise ValueError("Empty job description")

        # Extract company
        company_name = ""
        for selector in DETAIL_SELECTORS["company_name"]:
            elem = await page.query_selector(selector)
            if elem:
                company_name = (await elem.inner_text()).strip()
                break

        if not company_name:
            raise ValueError("No company name found")

        # Extract posted date
        posted_date_str = ""
        for selector in DETAIL_SELECTORS["posted_date"]:
            elem = await page.query_selector(selector)
            if elem:
                posted_date_str = (await elem.inner_text()).strip()
                break

        posted_date = parse_linkedin_date(posted_date_str) if posted_date_str else None

        # Extract and validate skills
        extracted_result = self.skill_extractor.extract(
            job_description, return_confidence=False
        )
        # Type narrow: when return_confidence=False, returns list[str]
        extracted_skills: list[str] = []
        for item in extracted_result:
            if isinstance(item, str):
                extracted_skills.append(item)

        # Deduplicate skills
        if extracted_skills:
            seen_lower: set[str] = set()
            unique_skills: list[str] = []
            for skill in extracted_skills:
                if skill.lower() not in seen_lower:
                    seen_lower.add(skill.lower())
                    unique_skills.append(skill)
            extracted_skills = unique_skills[:15]

        # Validate skills
        validated_skills = ""
        if extracted_skills:
            canonical = self.skills_validator.validate_and_extract(job_description)
            if canonical:
                validated_skills = ", ".join(sorted(canonical))
            else:
                validated_skills = ", ".join(extracted_skills)

        # Create job model
        job = JobDetailModel(
            job_id=job_id,
            platform=task.platform,
            actual_role=job_title,
            url=url,
            job_description=job_description[:5000],
            skills=validated_skills,
            company_name=company_name,
            posted_date=posted_date,
        )

        # Validate job
        is_valid, reason = self.job_validator.validate_job(job)
        if not is_valid:
            if "Non-English" in reason:
                raise ExpiredJobError(f"Non-English job: {reason}")
            raise ValueError(f"Validation failed: {reason}")

        # Store in database
        stored = self.db_ops.store_details([job])
        if stored > 0:
            self.db_ops.mark_urls_scraped([url])
            self.seen_urls.add(url)
            self.seen_job_ids.add(job_id)
            return job

        raise ValueError("Database storage failed")

    async def scrape(
        self,
        urls: List[tuple[str, str, str, str]],
    ) -> List[JobDetailModel]:
        """Main scraping method - launches workers and processes queue"""

        logger.info(f"🚀 Starting queue-based scraper with {self.num_workers} workers")
        logger.info(f"📋 Total jobs to process: {len(urls)}")

        # Setup proxy if configured
        proxy_url = os.getenv("PROXY_URL")
        proxy_config = None
        if proxy_url:
            parts = proxy_url.replace("http://", "").split("@")
            if len(parts) == 2:
                auth, server = parts
                username, password = auth.split(":")
                proxy_config = ProxySettings(
                    server=f"http://{server}", username=username, password=password
                )

        # Launch browser
        p = await async_playwright().start()
        browser = await p.chromium.launch(headless=self.headless, proxy=proxy_config)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=get_random_user_agent(),
        )

        try:
            # Populate job queue
            for idx, url_tuple in enumerate(urls, 1):
                url, _, actual_role, _ = url_tuple
                job_id = url.split("/")[-1]

                # Skip duplicates
                if url in self.seen_urls or job_id in self.seen_job_ids:
                    continue

                task = JobTask(
                    url=url,
                    job_id=job_id,
                    platform="linkedin",
                    actual_role=actual_role,
                    index=idx,
                    total=len(urls),
                )
                await self.job_queue.put(task)

            logger.info(f"📋 Queue populated with {self.job_queue.qsize()} jobs")

            # Launch workers with staggered start (1s apart)
            workers = []
            for i in range(self.num_workers):
                worker = asyncio.create_task(self.worker(i, context))
                workers.append(worker)
                # Stagger start to avoid burst
                await asyncio.sleep(1.0)

            logger.info(f"👷 All {self.num_workers} workers started")

            # Wait for queue to be empty
            await self.job_queue.join()

            # Send shutdown signal to all workers
            for _ in range(self.num_workers):
                await self.job_queue.put(None)

            # Wait for workers to finish
            await asyncio.gather(*workers)

        finally:
            await context.close()
            await browser.close()
            await p.stop()

        # Print summary
        logger.info("=" * 60)
        logger.info("📊 SCRAPING SESSION SUMMARY")
        logger.info("=" * 60)
        logger.info(f"   Total processed:    {self.total_processed}")
        logger.info(f"   ✅ Successful:       {self.total_success}")
        logger.info(f"   🗑️ Expired/deleted:  {self.total_expired}")
        logger.info(f"   ❌ Failed:           {self.total_failed}")
        logger.info("=" * 60)
        logger.info("👷 Worker Statistics:")
        for wid, stats in sorted(self.worker_stats.items()):
            logger.info(
                f"   Worker {wid}: {stats.success} ok, "
                f"{stats.failed} fail, {stats.expired} expired, "
                f"{stats.rate_limited} rate-limited"
            )
        logger.info("=" * 60)

        return self.results


# Custom exceptions
class RateLimitError(Exception):
    """HTTP 429 rate limit error"""

    pass


class ExpiredJobError(Exception):
    """Job expired or deleted"""

    pass


class ServerError(Exception):
    """Server-side error (5xx)"""

    pass


class LoginWallError(Exception):
    """LinkedIn login wall detected"""

    """LinkedIn login wall detected"""
    pass


async def scrape_job_details_queue_based(
    urls: List[tuple[str, str, str, str]],
    headless: bool = False,
    num_workers: int = 10,
) -> List[JobDetailModel]:
    """Entry point for queue-based scraping"""

    scraper = QueueBasedScraper(
        num_workers=num_workers,
        headless=headless,
        base_delay=3.0,  # 3s base delay between jobs
        max_delay=30.0,  # Max 30s on rate limit backoff
    )

    return await scraper.scrape(urls)
