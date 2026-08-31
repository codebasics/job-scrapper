"""Concurrent Job Detail Scraper - Single Window, Multiple Tabs
Scrapes job details from up to 10 URLs simultaneously in ONE browser window.
Smart rate limiting to avoid LinkedIn blocks without proxies.
"""

import asyncio
import html
import logging
import os
import random
import re
from typing import List, Optional, cast

from playwright.async_api import (
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


# Smart Rate Limiting Configuration
BATCH_DELAY_BY_TABS: dict[int, float] = {
    1: 0.5,  # 1 tab: 0.5s between batches
    2: 0.8,  # 2 tabs: 0.8s
    3: 1.0,  # 3 tabs: 1s
    4: 1.2,  # 4 tabs: 1.2s
    5: 1.5,  # 5 tabs: 1.5s (default)
    6: 2.0,  # 6 tabs: 2s
    7: 2.5,  # 7 tabs: 2.5s
    8: 3.0,  # 8 tabs: 3s
    9: 3.5,  # 9 tabs: 3.5s
    10: 4.0,  # 10 tabs: 4s between batches
}

JITTER_RANGE: float = 1.5  # Random jitter added to delays
COOLDOWN_EVERY_N_BATCHES: int = 10  # Cooldown after every N batches
COOLDOWN_DURATION: float = 8.0  # 8 second pause every 10 batches
RATE_LIMIT_BACKOFF: float = 30.0  # 30 second pause if 429 detected
STAGGER_DELAY: float = 0.3  # 0.3s between each tab start


class SharedState:
    """Thread-safe shared state for concurrent scraping with rate limit detection"""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.processed = 0
        self.expired_count = 0
        self.failed_count = 0
        self.rate_limit_count = 0
        self.job_details: List[JobDetailModel] = []
        self.seen_urls: set[str] = set()
        self.seen_job_ids: set[str] = set()
        self.rate_limit_detected = False

    async def add_job(self, job: JobDetailModel, url: str, job_id: str) -> bool:
        """Add job to results, returns True if added (not duplicate)"""
        async with self.lock:
            if url in self.seen_urls or job_id in self.seen_job_ids:
                return False
            self.seen_urls.add(url)
            self.seen_job_ids.add(job_id)
            self.job_details.append(job)
            self.processed += 1
            return True

    async def increment_expired(self) -> int:
        async with self.lock:
            self.expired_count += 1
            return self.expired_count

    async def increment_failed(self) -> int:
        async with self.lock:
            self.failed_count += 1
            return self.failed_count

    async def report_rate_limit(self) -> int:
        async with self.lock:
            self.rate_limit_count += 1
            self.rate_limit_detected = True
            return self.rate_limit_count

    async def clear_rate_limit_flag(self) -> None:
        async with self.lock:
            self.rate_limit_detected = False


async def scrape_single_job(
    page: Page,
    url_tuple: tuple[str, str, str, str],
    idx: int,
    total: int,
    state: SharedState,
    db_ops: JobStorageOperations,
    skill_extractor: AdvancedSkillExtractor,
    skills_validator: SkillValidator,
    tab_id: int,
) -> Optional[JobDetailModel]:
    """Scrape a single job detail in one browser tab"""

    url = url_tuple[0]
    linkedin_job_id = url.split("/")[-1]
    job_id = linkedin_job_id
    platform = "linkedin"
    actual_role = url_tuple[2]

    logger.info(f"🔄 [Tab {tab_id}] [{idx}/{total}] Processing: {job_id[:35]}")

    try:
        # Rotate user agent
        await page.set_extra_http_headers({"User-Agent": get_random_user_agent()})

        # Navigate and capture response
        response = await page.goto(url, timeout=30000)

        # ===== HTTP STATUS CODE DETECTION =====
        if response:
            status = response.status

            if status == 404:
                logger.info(f"🗑️  [Tab {tab_id}] HTTP 404: {job_id[:35]}")
                await asyncio.to_thread(db_ops.delete_urls, [url])
                await state.increment_expired()
                return None

            if status == 429:
                logger.error(f"🔴 [Tab {tab_id}] RATE LIMITED (429): {job_id[:35]}")
                await state.report_rate_limit()
                await state.increment_failed()
                return None

            if status == 503 or (500 <= status < 600):
                logger.warning(f"⚠️  [Tab {tab_id}] HTTP {status}: {job_id[:35]}")
                await state.increment_failed()
                return None

        # Wait for page load
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)

        # ===== PAGE CONTENT 404 DETECTION =====
        page_title = await page.title()
        page_title_lower = page_title.lower()

        if "404" in page_title_lower or "not found" in page_title_lower:
            logger.info(f"🗑️  [Tab {tab_id}] LinkedIn 404 page: {job_id[:35]}")
            await asyncio.to_thread(db_ops.delete_urls, [url])
            await state.increment_expired()
            return None

        page_text = await page.evaluate("() => document.body.innerText")
        page_text_lower = page_text.lower()

        linkedin_404_messages = [
            "the request was not found",
            "this page doesn't exist",
            "page not found",
            "this job is no longer available",
            "job has been removed",
        ]

        for msg in linkedin_404_messages:
            if msg in page_text_lower:
                logger.info(f"🗑️  [Tab {tab_id}] LinkedIn 404: {job_id[:35]}")
                await asyncio.to_thread(db_ops.delete_urls, [url])
                await state.increment_expired()
                return None

        # ===== RATE LIMIT DETECTION (Page Content) =====
        rate_limit_indicators = [
            "too many requests",
            "rate limit",
            "please slow down",
            "unusual activity",
            "temporarily blocked",
        ]
        for indicator in rate_limit_indicators:
            if indicator in page_text_lower:
                logger.error(f"🔴 [Tab {tab_id}] Rate limit detected: {indicator}")
                await state.report_rate_limit()
                await state.increment_failed()
                return None

        # ===== LOGIN WALL DETECTION =====
        current_url = page.url

        for login_path in LOGIN_WALL_INDICATORS["login_urls"]:
            if login_path in current_url.lower():
                logger.error(f"🔒 [Tab {tab_id}] Login wall: {job_id[:35]}")
                await state.increment_failed()
                return None

        # ===== EXPIRED JOB DETECTION =====
        if "/jobs/view/" not in current_url:
            logger.info(f"🗑️  [Tab {tab_id}] Redirected (expired): {job_id[:35]}")
            await asyncio.to_thread(db_ops.delete_urls, [url])
            await state.increment_expired()
            return None

        # Check expired indicators
        is_generic_title = any(
            generic in page_title
            for generic in EXPIRED_JOB_INDICATORS["generic_titles"]
        )

        for error_selector in EXPIRED_JOB_INDICATORS["error_selectors"]:
            error_elem = await page.query_selector(error_selector)
            if error_elem:
                logger.info(f"🗑️  [Tab {tab_id}] Expired: {job_id[:35]}")
                await asyncio.to_thread(db_ops.delete_urls, [url])
                await state.increment_expired()
                return None

        for error_msg in EXPIRED_JOB_INDICATORS["error_messages"]:
            if error_msg in page_text_lower:
                logger.info(f"🗑️  [Tab {tab_id}] Expired: {job_id[:35]}")
                await asyncio.to_thread(db_ops.delete_urls, [url])
                await state.increment_expired()
                return None

        # ===== EXTRACT JOB DATA =====

        # Job title
        scraped_job_title = ""
        for selector in DETAIL_SELECTORS["job_title"]:
            title_elem = await page.query_selector(selector)
            if title_elem:
                scraped_job_title = (await title_elem.inner_text()).strip()
                break

        if not scraped_job_title:
            scraped_job_title = actual_role

        # Description
        job_description = ""
        for selector in DETAIL_SELECTORS["description"]:
            desc_elem = await page.query_selector(selector)
            if desc_elem:
                job_description = await desc_elem.inner_text()
                job_description = html.unescape(job_description)
                job_description = re.sub(r"<[^>]+>", " ", job_description)
                job_description = re.sub(r"&[a-zA-Z]+;", " ", job_description)
                job_description = " ".join(job_description.split())
                break

        if not job_description.strip():
            if is_generic_title:
                logger.info(f"🗑️  [Tab {tab_id}] No desc (expired): {job_id[:35]}")
                await asyncio.to_thread(db_ops.delete_urls, [url])
                await state.increment_expired()
            else:
                logger.warning(f"⏭️  [Tab {tab_id}] No description: {job_id[:35]}")
                await state.increment_failed()
            return None

        # Company name
        company_name = ""
        for selector in DETAIL_SELECTORS["company_name"]:
            company_elem = await page.query_selector(selector)
            if company_elem:
                company_name = (await company_elem.inner_text()).strip()
                break

        if not company_name:
            logger.warning(f"⏭️  [Tab {tab_id}] No company: {job_id[:35]}")
            await state.increment_failed()
            return None

        # Posted date
        posted_date_str = ""
        for selector in DETAIL_SELECTORS["posted_date"]:
            date_elem = await page.query_selector(selector)
            if date_elem:
                posted_date_str = (await date_elem.inner_text()).strip()
                break

        # ===== SKILL EXTRACTION =====
        extracted_skills_list = cast(
            list[str],
            skill_extractor.extract(job_description, return_confidence=False),
        )

        # Deduplicate skills
        if extracted_skills_list:
            seen_lower: set[str] = set()
            unique_extracted: list[str] = []
            for skill in extracted_skills_list:
                skill_lower = skill.lower()
                if skill_lower not in seen_lower:
                    seen_lower.add(skill_lower)
                    unique_extracted.append(skill)
            extracted_skills_list = unique_extracted[:15]

        extracted_skills = (
            ", ".join(extracted_skills_list) if extracted_skills_list else ""
        )

        # Validate skills
        validated_skills = ""
        if extracted_skills.strip():
            canonical_skills = skills_validator.validate_and_extract(job_description)
            if canonical_skills:
                validated_skills = ", ".join(sorted(canonical_skills))
            else:
                validated_skills = extracted_skills

        # Parse date
        posted_date = parse_linkedin_date(posted_date_str) if posted_date_str else None

        # Create job model
        job = JobDetailModel(
            job_id=job_id,
            platform=platform,
            actual_role=scraped_job_title if scraped_job_title else actual_role,
            url=url,
            job_description=job_description[:5000],
            skills=validated_skills,
            company_name=company_name,
            posted_date=posted_date,
        )

        # ===== VALIDATION =====
        job_validator = JobValidator(min_description_length=100)
        is_valid, validation_reason = job_validator.validate_job(job)

        if not is_valid:
            if "Non-English content" in validation_reason:
                await asyncio.to_thread(db_ops.delete_urls, [url])
                await state.increment_expired()
                logger.debug(f"🌐 [Tab {tab_id}] Non-English: {job_id[:35]}")
            else:
                logger.warning(f"⚠️  [Tab {tab_id}] Validation failed: {job_id[:35]}")
                await state.increment_failed()
            return None

        # Skill precision check
        if validated_skills:
            accuracy_report = skills_validator.calculate_accuracy(
                job.job_description, validated_skills
            )
            precision_val = accuracy_report.get("precision", 0.0)
            precision = (
                float(precision_val) if isinstance(precision_val, (int, float)) else 0.0
            )

            if precision < 0.5:
                canonical_raw = accuracy_report.get("canonical_skills", [])
                canonical = canonical_raw if isinstance(canonical_raw, list) else []
                if canonical:
                    job.skills = ", ".join(canonical)
                else:
                    logger.warning(f"⏭️  [Tab {tab_id}] Low precision: {job_id[:35]}")
                    await state.increment_failed()
                    return None

        # ===== STORE TO DATABASE =====
        stored = await asyncio.to_thread(db_ops.store_details, [job])
        if stored > 0:
            await asyncio.to_thread(db_ops.mark_urls_scraped, [url])
            added = await state.add_job(job, url, job_id)
            if added:
                logger.info(
                    f"✅ [Tab {tab_id}] [{idx}/{total}] {job_id[:30]} @ {company_name[:15]}"
                )
                return job
        else:
            logger.error(f"❌ [Tab {tab_id}] DB failed: {job_id[:35]}")
            await state.increment_failed()

        return None

    except Exception as e:
        error_str = str(e)
        if "429" in error_str:
            logger.error(f"🔴 [Tab {tab_id}] Rate limit: {job_id[:35]}")
            await state.report_rate_limit()
        else:
            logger.error(f"❌ [Tab {tab_id}] Error: {job_id[:35]} - {e}")
        await state.increment_failed()
        return None


def get_batch_delay(concurrent_tabs: int) -> float:
    """Get appropriate delay between batches based on concurrency level"""
    base_delay = BATCH_DELAY_BY_TABS.get(concurrent_tabs, BATCH_DELAY_BY_TABS[10])
    jitter = random.uniform(0, JITTER_RANGE)
    return base_delay + jitter


async def scrape_job_details_concurrent(
    urls: List[tuple[str, str, str, str]],
    headless: bool = False,
    max_concurrent: int = 5,
) -> List[JobDetailModel]:
    """
    Scrape job details using multiple concurrent tabs in ONE browser window.

    Smart rate limiting automatically adjusts delays based on concurrency:
    - 5 tabs: ~1.5s between batches (safe)
    - 10 tabs: ~4s between batches + staggered starts (safe without proxy)

    The browser window stays open until ALL jobs are processed.

    Args:
        urls: List of (url, job_id, actual_role, platform) tuples
        headless: Run browser in headless mode (default False - visible)
        max_concurrent: Number of concurrent tabs (default 5, max 10)

    Returns:
        List of JobDetailModel objects
    """

    if not urls:
        return []

    # Cap at 10 concurrent tabs for safety
    max_concurrent = min(max_concurrent, 10)

    db_ops = JobStorageOperations()
    state = SharedState()
    skill_extractor = AdvancedSkillExtractor("src/config/skills_reference_2026.json")
    skills_validator = SkillValidator("src/config/skills_reference_2026.json")

    # Proxy config (optional - not required for rate limit protection)
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
        else:
            proxy_config = ProxySettings(server=f"http://{parts[0]}")

    total = len(urls)

    logger.info(f"\n{'=' * 60}")
    logger.info(f"🚀 CONCURRENT DETAIL SCRAPER - SMART RATE LIMITING")
    logger.info(f"{'=' * 60}")
    logger.info(f"📊 Total URLs: {total}")
    logger.info(f"📑 Concurrent Tabs: {max_concurrent}")
    logger.info(
        f"⏱️  Base batch delay: {BATCH_DELAY_BY_TABS.get(max_concurrent, 4.0)}s + jitter"
    )
    logger.info(f"🔄 Cooldown every {COOLDOWN_EVERY_N_BATCHES} batches")
    logger.info(f"🛡️  No proxy required - smart delays protect against rate limits")
    logger.info(f"{'=' * 60}\n")

    async with async_playwright() as p:
        # Add timeouts to browser/context creation to prevent hanging
        try:
            browser = await asyncio.wait_for(
                p.chromium.launch(headless=headless, proxy=proxy_config),
                timeout=30.0  # 30s timeout for browser launch
            )
        except asyncio.TimeoutError:
            logger.error("❌ Browser launch timed out after 30s")
            return []

        try:
            context = await asyncio.wait_for(
                browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                ),
                timeout=10.0  # 10s timeout for context
            )
        except asyncio.TimeoutError:
            logger.error("❌ Context creation timed out after 10s")
            await browser.close()
            return []

        try:
            # Create persistent tabs with staggered creation
            persistent_pages: List[Page] = []
            logger.info(f"📑 Opening {max_concurrent} tabs (staggered)...")

            for tab_num in range(max_concurrent):
                try:
                    page = await asyncio.wait_for(context.new_page(), timeout=10.0)
                except asyncio.TimeoutError:
                    logger.error(f"❌ Tab {tab_num + 1} creation timed out")
                    # Close already created pages and return
                    for pg in persistent_pages:
                        try:
                            await pg.close()
                        except Exception:
                            pass
                    await context.close()
                    await browser.close()
                    return []
                persistent_pages.append(page)
                logger.info(f"   Tab {tab_num + 1}/{max_concurrent} ready")
                if tab_num < max_concurrent - 1:
                    await asyncio.sleep(0.2)  # Small stagger between tab creation

            logger.info(f"✅ All {max_concurrent} tabs created\n")

            # Process URLs in batches
            batch_num = 0

            for i in range(0, total, max_concurrent):
                batch = urls[i : i + max_concurrent]
                batch_num += 1
                batch_end = min(i + max_concurrent, total)

                # Check for rate limit and apply backoff
                if state.rate_limit_detected:
                    logger.warning(
                        f"🛑 Rate limit detected! Pausing {RATE_LIMIT_BACKOFF}s..."
                    )
                    await asyncio.sleep(RATE_LIMIT_BACKOFF)
                    await state.clear_rate_limit_flag()

                # Cooldown every N batches
                if batch_num > 1 and batch_num % COOLDOWN_EVERY_N_BATCHES == 0:
                    logger.info(
                        f"😴 Cooldown pause: {COOLDOWN_DURATION}s (every {COOLDOWN_EVERY_N_BATCHES} batches)"
                    )
                    await asyncio.sleep(COOLDOWN_DURATION)

                logger.info(f"{'=' * 55}")
                logger.info(
                    f"📦 Batch {batch_num}: Jobs {i + 1}-{batch_end} of {total}"
                )
                logger.info(f"{'=' * 55}")

                # Create tasks with staggered starts
                tasks = []
                for j in range(len(batch)):
                    # Stagger each tab start slightly
                    if j > 0:
                        await asyncio.sleep(STAGGER_DELAY)

                    task = scrape_single_job(
                        page=persistent_pages[j],
                        url_tuple=batch[j],
                        idx=i + j + 1,
                        total=total,
                        state=state,
                        db_ops=db_ops,
                        skill_extractor=skill_extractor,
                        skills_validator=skills_validator,
                        tab_id=j + 1,
                    )
                    tasks.append(task)

                # Run all tabs concurrently
                await asyncio.gather(*tasks)

                # Progress update
                progress_pct = batch_end / total * 100
                logger.info(
                    f"📈 Progress: {progress_pct:.1f}% | "
                    f"✅ {state.processed} | "
                    f"🗑️ {state.expired_count} | "
                    f"❌ {state.failed_count} | "
                    f"🔴 {state.rate_limit_count} rate limits"
                )

                # Delay between batches (with jitter)
                if batch_end < total:
                    delay = get_batch_delay(max_concurrent)
                    logger.info(f"⏳ Waiting {delay:.1f}s before next batch...")
                    await asyncio.sleep(delay)

            # All batches complete
            logger.info(f"\n🏁 All {total} URLs processed!")
            logger.info(f"📑 Closing {max_concurrent} tabs...")

            for page in persistent_pages:
                try:
                    await asyncio.wait_for(page.close(), timeout=5.0)
                except Exception:
                    pass  # Ignore cleanup errors

        finally:
            try:
                await asyncio.wait_for(context.close(), timeout=10.0)
            except Exception:
                pass
            try:
                await asyncio.wait_for(browser.close(), timeout=10.0)
            except Exception:
                pass
            logger.info("🔒 Browser window closed.")

    # Session Summary
    success_rate = (state.processed / total * 100) if total > 0 else 0

    logger.info("\n" + "=" * 60)
    logger.info("📊 SESSION SUMMARY")
    logger.info("=" * 60)
    logger.info(f"   Total URLs processed:    {total}")
    logger.info(
        f"   ✅ Successfully scraped:  {state.processed} ({state.processed / total * 100:.1f}%)"
        if total > 0
        else "   ✅ Successfully scraped:  0"
    )
    logger.info(
        f"   🗑️  Expired/404 removed:  {state.expired_count} ({state.expired_count / total * 100:.1f}%)"
        if total > 0
        else "   🗑️  Expired/404 removed:  0"
    )
    logger.info(
        f"   ❌ Failed (other):        {state.failed_count} ({state.failed_count / total * 100:.1f}%)"
        if total > 0
        else "   ❌ Failed (other):        0"
    )
    logger.info(f"   🔴 Rate limits hit:       {state.rate_limit_count}")
    logger.info(f"   📈 Success rate:          {success_rate:.1f}%")
    logger.info("=" * 60)

    return state.job_details


# For direct testing
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

    concurrent = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20

    print(f"Testing with {concurrent} tabs, {limit} URLs")

    db = JobStorageOperations("data/jobs.db")
    urls = db.get_unscraped_urls("linkedin", "Data Analyst", limit=limit)
    print(f"Found {len(urls)} unscraped URLs")

    if urls:
        jobs = asyncio.run(
            scrape_job_details_concurrent(
                urls, headless=False, max_concurrent=concurrent
            )
        )
        print(f"Scraped {len(jobs)} jobs")
