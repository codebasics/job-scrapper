"""ROUND-ROBIN Queue Scraper with Adaptive Rate Limiting + Real-time Progress

Architecture:
- N SLOTS (tabs) reused in round-robin order
- Central dispatcher assigns jobs to slots sequentially
- ADAPTIVE delay: starts at 2.0s, reduces to 1.5s after consecutive successes
- Exponential backoff on 429 errors
- Real-time progress events via JSON stdout

Flow:
    Job 1  → Slot 0 → wait delay (2.0s initial)
    Job 2  → Slot 1 → wait delay
    ...
    After 10 successes → delay reduces to 1.75s
    After 20 successes → delay reduces to 1.5s (minimum)
    On 429 error → delay resets to 2.0s base
"""

import asyncio
import html
import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass
from typing import List, Optional, Set, Union

from playwright.async_api import (
    Page,
    ProxySettings,
    async_playwright,
)

from src.analysis.skill_extraction.extractor import AdvancedSkillExtractor
from src.analysis.skill_extraction.skill_validator import SkillValidator
from src.validation.single_job_validator import SingleJobValidator, ValidationResult
from src.db.operations import JobStorageOperations
from src.models.models import JobDetailModel
from src.scraper.unified.linkedin.date_parser import parse_linkedin_date
from src.scraper.unified.linkedin.job_validator import (
    JobValidator,
    detect_non_english_language,
)
from src.scraper.unified.linkedin.selector_config import (
    DETAIL_SELECTORS,
    EXPIRED_JOB_INDICATORS,
)
from src.scraper.unified.scalable.user_agent_pool import get_random_user_agent

logger = logging.getLogger(__name__)


def emit_progress(event_type: str, data: dict[str, Union[str, int, float, bool, None, dict[str, int]]]) -> None:
    """Emit progress event as JSON line to stdout for real-time UI updates"""
    event = {"event": event_type, "timestamp": time.time(), **data}
    # Print to stdout with flush for immediate visibility
    print(f"PROGRESS:{json.dumps(event)}", flush=True)


@dataclass
class JobTask:
    """Job task for processing"""

    url: str
    job_id: str
    platform: str
    actual_role: str
    index: int
    total: int
    retry_count: int = 0


class TokenBucket:
    """Token Bucket Algorithm for smooth rate limiting.

    DSA: Classic rate limiting pattern
    - Tokens regenerate at fixed rate (tokens_per_second)
    - Each request consumes 1 token
    - Allows controlled bursts up to bucket capacity
    - Smooths out request patterns to avoid rate limits

    Time Complexity: O(1) for all operations
    Space Complexity: O(1)
    """

    def __init__(
        self,
        capacity: float = 5.0,        # Max burst size
        tokens_per_second: float = 0.5,  # Refill rate (1 token per 2 seconds)
        initial_tokens: float = 3.0   # Start with some tokens
    ):
        self.capacity = capacity
        self.tokens_per_second = tokens_per_second
        self.tokens = min(initial_tokens, capacity)
        self.last_refill = time.time()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        """Refill tokens based on elapsed time - O(1)"""
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.tokens_per_second)
        self.last_refill = now

    async def acquire(self, timeout: float = 30.0) -> bool:
        """Acquire a token, waiting if necessary.

        Returns True if token acquired, False if timeout.
        Time Complexity: O(1) amortized

        CRITICAL: Does NOT hold lock while sleeping - allows parallel workers!
        """
        start_time = time.time()

        while True:
            # Quick lock to check/update tokens
            async with self._lock:
                self._refill()

                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True

                # Calculate wait time for 1 token
                wait_time = (1.0 - self.tokens) / self.tokens_per_second

            # Check timeout OUTSIDE lock
            elapsed = time.time() - start_time
            if elapsed + wait_time > timeout:
                return False

            # Sleep OUTSIDE lock - allows other workers to proceed!
            await asyncio.sleep(min(wait_time, 0.5))  # Sleep in small chunks

            # Check if we've been waiting too long
            if time.time() - start_time > timeout:
                return False

    def available(self) -> float:
        """Check available tokens without consuming - O(1)"""
        self._refill()
        return self.tokens

    async def penalize(self, penalty_seconds: float = 5.0) -> None:
        """Reduce tokens as penalty (e.g., on 429 error) - O(1)
        THREAD-SAFE: Uses async lock to prevent race conditions.
        """
        async with self._lock:
            self.tokens = max(0, self.tokens - penalty_seconds * self.tokens_per_second)

    async def boost(self, boost_amount: float = 0.5) -> None:
        """Add tokens as reward (e.g., after success streak) - O(1)
        THREAD-SAFE: Uses async lock to prevent race conditions.
        """
        async with self._lock:
            self.tokens = min(self.capacity, self.tokens + boost_amount)

    async def set_rate(self, tokens_per_second: float) -> None:
        """Dynamically adjust rate - O(1)
        THREAD-SAFE: Uses async lock to prevent race conditions.
        """
        async with self._lock:
            self._refill()  # Settle current tokens first
            self.tokens_per_second = tokens_per_second


class RoundRobinScraper:
    """Round-robin slot assignment with adaptive rate limiting.

    DSA Optimizations:
    - Token Bucket: O(1) rate limiting with burst support
    - Free Slots Set: O(1) slot availability lookup
    - Deque: O(1) retry queue operations

    SIMPLE MODE (sequential=True):
    - Single page, simple loop
    - No queue/workers
    - More reliable, slightly slower
    """

    def __init__(
        self,
        num_slots: int = 4,  # Default to 4 tabs (safer for LinkedIn)
        delay_between_jobs: float = 5.0,  # INCREASED: Base delay - LinkedIn safe
        headless: bool = False,
        max_retries: int = 3,
        rate_limit_backoff_base: float = 60.0,  # INCREASED: Base backoff for 429
        sequential: bool = False,  # Use simple sequential mode (more reliable)
    ):
        # Validate and clamp num_slots to 1-10
        self.num_slots = max(1, min(10, num_slots))
        self.delay_between_jobs = delay_between_jobs
        self.headless = headless
        self.max_retries = max_retries
        self.rate_limit_backoff_base = rate_limit_backoff_base
        self.sequential = sequential  # Simple sequential mode (more reliable)

        # Slots (tabs)
        self.slots: dict[int, Page] = {}
        self.slot_busy: dict[int, bool] = {}  # Track if slot is processing
        self.slot_locks: dict[int, asyncio.Lock] = {}

        # DSA: Free slots Set for O(1) lookup instead of O(n) linear scan
        self.free_slots: Set[int] = set(range(self.num_slots))

        # LinkedIn-safe rate limiting: scale delay with number of tabs
        # More tabs = need longer delays to avoid rate limits
        # Formula: effective_delay = base_delay * (1 + 0.1 * (num_slots - 1))
        # 1 tab: 3.0s, 4 tabs: 3.9s, 10 tabs: 5.7s
        self.slot_delay_factor = 1.0 + 0.1 * (self.num_slots - 1)
        effective_delay = delay_between_jobs * self.slot_delay_factor

        # DSA: Token Bucket for smooth rate limiting
        # Rate = num_slots / effective_delay to support parallel workers
        # Example: 4 slots, 3.9s delay = 4/3.9 = 1.03 tokens/sec
        # This allows all workers to proceed at ~1 job per effective_delay total throughput
        self.token_bucket = TokenBucket(
            capacity=float(self.num_slots * 2),  # Allow burst of 2x workers
            tokens_per_second=float(self.num_slots) / effective_delay,  # Parallel throughput
            initial_tokens=float(self.num_slots)  # All workers can start immediately
        )

        # Adaptive rate limiting
        self.global_dispatch_lock = asyncio.Lock()
        self.last_dispatch_time = 0.0
        self.consecutive_429_count = 0
        self.rate_limit_until = 0.0  # Global pause until this time

        # ═══════════════════════════════════════════════════════════════════
        # GLOBAL JOB START COORDINATOR: Ensures minimum interval between ANY
        # job starts across ALL slots, even when jobs complete quickly
        # ═══════════════════════════════════════════════════════════════════
        self.job_start_lock = asyncio.Lock()
        self.last_job_start_time = 0.0
        # Minimum interval between any two job starts (across all slots)
        # This prevents bursts when jobs are skipped quickly
        # FIX: Use configured delay divided by num_slots for parallel throughput
        # Example: 10s delay, 5 slots → 2s min interval = 5 requests per 10s
        self.min_interval_between_jobs = max(2.0, effective_delay / self.num_slots)

        # Adaptive throttling - reduces delay after consecutive successes
        self.consecutive_success_count = 0
        self.current_delay = effective_delay  # Start with scaled delay
        # Min delay scales with tabs: 1 tab = 4s min, 10 tabs = 6s min (INCREASED)
        self.min_delay = max(4.0, 4.0 + 0.2 * (self.num_slots - 1))
        self.delay_reduction_step = 0.1  # REDUCED: Smaller reduction steps
        self.success_threshold = 25  # INCREASED: More successes needed before reducing delay

        # Results
        self.results: List[JobDetailModel] = []
        self.results_lock = asyncio.Lock()

        # Stats lock - protects all counters from race conditions
        # CRITICAL: Use this lock for any check-then-act patterns on counters
        self.stats_lock = asyncio.Lock()

        # Stats
        self.total_jobs = 0
        self.total_processed = 0
        self.total_success = 0
        self.total_expired = 0
        self.total_failed = 0
        self.total_retried = 0

        # Authwall detection - track consecutive authwall hits
        self.consecutive_authwall_count = 0
        self.authwall_threshold = 50  # Alert only after many consecutive authwall hits (jobs are auto-skipped)
        self.cookies_expired_alert_sent = False

        # 429 retry tracking - prevent infinite retry loops
        self.total_429_retries = 0
        self.max_total_429_retries = 50  # Stop retrying after 50 total 429 retries

        # Validators
        self.skill_extractor = AdvancedSkillExtractor(
            "src/config/skills_reference_2026.json"
        )
        self.skills_validator = SkillValidator("src/config/skills_reference_2026.json")
        self.job_validator = JobValidator(min_description_length=100)
        self.db_ops = JobStorageOperations()

        # 7-Layer Single Job Validator - validates and fixes skills after each job
        self.single_job_validator = SingleJobValidator("src/config/skills_reference_2026.json")

    async def _wait_for_rate_limit(self) -> float:
        """Check if we're in a rate limit backoff period.

        Returns remaining wait time in seconds (0.0 if no wait needed).
        Does NOT block - just returns time remaining.
        """
        now = time.time()
        if self.rate_limit_until > now:
            return self.rate_limit_until - now
        return 0.0

    async def _acquire_job_start_slot(self, slot_id: int) -> None:
        """GLOBAL JOB START COORDINATOR: Ensures minimum interval between job starts.

        This prevents multiple slots from firing requests simultaneously when jobs
        complete quickly (e.g., skipped due to language/authwall).

        Algorithm:
        1. Acquire lock briefly to check time and reserve slot
        2. Calculate wait time and update timestamp immediately (reserve slot)
        3. Release lock BEFORE sleeping (allows other slots to reserve their slots)
        4. Sleep outside the lock (parallel waits, staggered starts)

        This ensures staggered job starts without blocking other slots.
        """
        wait_time = 0.0

        # Quick lock to check and reserve our time slot
        async with self.job_start_lock:
            now = time.time()
            time_since_last = now - self.last_job_start_time

            if time_since_last < self.min_interval_between_jobs:
                wait_time = self.min_interval_between_jobs - time_since_last
                # Reserve our slot by advancing the timestamp
                self.last_job_start_time = now + wait_time
            else:
                # No wait needed, just update timestamp
                self.last_job_start_time = now

        # Sleep OUTSIDE the lock - other slots can now reserve their slots
        if wait_time > 0:
            logger.info(f"⏳ Slot {slot_id}: Enforcing {wait_time:.1f}s delay (min_interval={self.min_interval_between_jobs:.1f}s)")
            emit_progress("slot_waiting", {
                "slot_id": slot_id,
                "wait_time": round(wait_time, 1),
                "reason": f"Rate limit delay ({self.min_interval_between_jobs:.1f}s interval)"
            })
            await asyncio.sleep(wait_time)

    async def validate_session(self, page: Page) -> tuple[bool, str]:
        """Pre-flight check: Validate LinkedIn session is active.

        Returns (is_valid, message)
        """
        try:
            logger.info("🔍 Validating LinkedIn session...")

            # Navigate to LinkedIn feed (requires login)
            await asyncio.wait_for(
                page.goto("https://www.linkedin.com/feed/", timeout=10000),
                timeout=15.0
            )

            current_url = page.url.lower()

            # Check if we're on the feed page (logged in)
            if "feed" in current_url and "login" not in current_url and "authwall" not in current_url:
                logger.info("✅ LinkedIn session is valid")
                return True, "Session valid"

            # Check if redirected to login
            if "login" in current_url or "authwall" in current_url:
                logger.error("🔴 LinkedIn session expired - redirected to login")
                return False, "Session expired - please refresh cookies"

            # Check page content for login indicators
            try:
                page_text = await asyncio.wait_for(
                    page.evaluate("() => document.body.innerText.toLowerCase().slice(0, 500)"),
                    timeout=3.0
                )
                if "sign in" in page_text or "join now" in page_text:
                    logger.error("🔴 LinkedIn session expired - login page detected")
                    return False, "Session expired - login page detected"
            except Exception:
                pass

            logger.warning("⚠️ Session validation inconclusive, proceeding anyway")
            return True, "Session validation inconclusive"

        except Exception as e:
            logger.warning(f"⚠️ Session validation failed: {e}")
            return True, f"Validation error: {e}"  # Proceed anyway

    async def _enforce_dispatch_delay(self) -> None:
        """Enforce rate limiting using Token Bucket algorithm (DSA).

        Token Bucket provides:
        - O(1) token check and acquisition
        - Natural burst handling (up to capacity)
        - Smooth rate limiting without fixed delays
        - Adaptive to traffic patterns
        """
        # DSA: Token Bucket handles rate limiting - blocks until token available
        # Rate = num_slots / delay to support parallel workers
        if self.consecutive_429_count > 0:
            # Temporarily slow down the bucket refill rate
            base_rate = float(self.num_slots) / self.current_delay
            penalty_rate = base_rate / (1.5 ** self.consecutive_429_count)
            penalty_rate = max(0.5, penalty_rate)  # Don't go below 0.5 tokens/sec
            await self.token_bucket.set_rate(penalty_rate)
        else:
            # Normal rate: support all parallel workers
            normal_rate = float(self.num_slots) / self.current_delay
            await self.token_bucket.set_rate(normal_rate)

        # Acquire token - this will wait if no tokens available (O(1) amortized)
        # FIX: Use delay-based timeout instead of hardcoded 2s
        # Timeout should be at least the current_delay to allow proper rate limiting
        bucket_timeout = max(5.0, self.current_delay * 1.5)
        acquired = await self.token_bucket.acquire(timeout=bucket_timeout)

        if not acquired:
            logger.warning(f"⚠️ Token bucket timeout after {bucket_timeout:.1f}s - forcing dispatch")

        # Add small jitter for anti-detection (±0.2s)
        jitter = random.uniform(-0.2, 0.2)
        if jitter > 0:
            await asyncio.sleep(jitter)

    async def _process_job_in_slot(
        self, slot_id: int, page: Page, task: JobTask
    ) -> tuple[bool, Optional[str]]:
        """Process a single job in the specified slot

        Returns: (success, error_type) where error_type is None on success
        """
        logger.info(
            f"📥 Slot {slot_id} [{task.index}/{task.total}]: "
            f"Processing {task.job_id[:30]}..."
        )

        try:
            # RESET PAGE STATE: Navigate to about:blank to clear any stuck state
            try:
                await asyncio.wait_for(
                    page.goto("about:blank", timeout=2000),
                    timeout=3.0
                )
                emit_progress("slot_reset", {
                    "slot_id": slot_id,
                    "message": "Cleared for new job"
                })
            except Exception:
                pass  # Ignore errors on reset - we'll try the real navigation anyway

            # PRE-CHECK: Skip authwall URLs immediately (don't even load)
            url_lower = task.url.lower()
            if any(x in url_lower for x in ["authwall", "login", "signin", "checkpoint", "uas/login"]):
                logger.info(f"🚫 Slot {slot_id}: Authwall URL - skipping: {task.url[:50]}")
                emit_progress("slot_authwall", {
                    "slot_id": slot_id,
                    "job_id": task.job_id[:20],
                    "reason": "Authwall URL detected"
                })
                raise ExpiredJobError("Authwall URL")

            # Emit navigation start event
            emit_progress("slot_navigate", {
                "slot_id": slot_id,
                "job_id": task.job_id[:20],
                "url": task.url[:60]
            })

            # Navigate with VERY AGGRESSIVE timeout - fail fast!
            response = None  # Initialize response variable
            try:
                response = await asyncio.wait_for(
                    page.goto(task.url, timeout=8000, wait_until="commit"),  # 8s Playwright timeout
                    timeout=10.0  # 10s hard limit
                )
            except (asyncio.TimeoutError, asyncio.CancelledError) as e:
                logger.warning(f"⏱️ Slot {slot_id}: Navigation timeout - {task.job_id[:20]}")
                emit_progress("slot_timeout", {
                    "slot_id": slot_id,
                    "job_id": task.job_id[:20],
                    "timeout": 10,
                    "phase": "navigation"
                })
                raise ExpiredJobError("Navigation timeout")
            except Exception as e:
                if "timeout" in str(e).lower() or "Target closed" in str(e):
                    logger.warning(f"⏱️ Slot {slot_id}: Playwright error - {task.job_id[:20]}")
                    emit_progress("slot_error", {
                        "slot_id": slot_id,
                        "job_id": task.job_id[:20],
                        "error": str(e)[:50]
                    })
                    raise ExpiredJobError("Playwright error")
                raise

            # Get current URL for later checks (but DON'T fail yet - data might still be accessible)
            current_url = page.url

            # NOTE: We no longer immediately fail on authwall URLs!
            # LinkedIn may show sign-in dialogs but job data is often still accessible behind them.
            # We will check authwall ONLY IF data extraction fails.

            # ONLY fail on complete redirect away from job page (like to /feed/ with no job ID)
            if "/jobs/view/" not in current_url and task.job_id not in current_url:
                # Complete redirect - no job page at all
                if "/feed" in current_url or "/login" in current_url:
                    logger.info(f"🚫 Slot {slot_id}: Complete redirect to non-job page - {current_url[:50]}")
                    raise ExpiredJobError("Redirected away from job")

            # Check HTTP status
            if response:
                status = response.status
                if status == 429:
                    return False, "rate_limit"
                if status == 404:
                    return False, "expired"
                if status >= 500:
                    return False, "server_error"

            # Wait for load - use domcontentloaded only (faster, don't wait for networkidle)
            try:
                await asyncio.wait_for(
                    page.wait_for_load_state("domcontentloaded"),
                    timeout=3.0  # Reduced to 3 seconds
                )
            except asyncio.TimeoutError:
                pass  # Continue anyway - we'll check content

            # NOTE: We no longer check for authwall/login indicators here!
            # Sign-in dialogs may appear as overlays, but job data is often still accessible.
            # We will attempt extraction first, and only check authwall if extraction fails.

            # Emit extraction start event
            emit_progress("slot_extracting", {
                "slot_id": slot_id,
                "job_id": task.job_id[:20],
                "phase": "Parsing job details"
            })

            # Extract data with overall timeout (AGGRESSIVE: 10s)
            try:
                job = await asyncio.wait_for(
                    self._extract_job_data(page, task),
                    timeout=10.0  # 10 second hard limit for entire extraction
                )
            except (asyncio.TimeoutError, asyncio.CancelledError) as e:
                logger.warning(f"⏱️ Slot {slot_id}: Extraction timeout/cancelled - {task.job_id[:20]}")
                emit_progress("slot_timeout", {
                    "slot_id": slot_id,
                    "job_id": task.job_id[:20],
                    "timeout": 10,
                    "phase": "extraction"
                })
                raise ExpiredJobError("Extraction timeout")
            except Exception as e:
                error_str = str(e).lower()
                if "timeout" in error_str or "target closed" in error_str or "context" in error_str:
                    logger.warning(f"⏱️ Slot {slot_id}: Playwright error - {task.job_id[:20]}: {str(e)[:50]}")
                    emit_progress("slot_error", {
                        "slot_id": slot_id,
                        "job_id": task.job_id[:20],
                        "error": str(e)[:50]
                    })
                    raise ExpiredJobError("Playwright error")
                raise

            # Store - CRITICAL: Run in thread to avoid blocking event loop
            # SQLite can block if database is locked by another process (e.g., Streamlit)
            stored = await asyncio.to_thread(self.db_ops.store_details, [job])
            if stored > 0:
                await asyncio.to_thread(self.db_ops.mark_urls_scraped, [task.url])

                # ═══════════════════════════════════════════════════════════════════
                # 7-LAYER VALIDATION: Validate and fix skills after storing
                # Layers: FP detection, FN detection, context validation
                # ═══════════════════════════════════════════════════════════════════
                validation_result: ValidationResult = await asyncio.to_thread(
                    self.single_job_validator.validate_and_fix,
                    task.job_id,
                    job.job_description or "",
                    job.skills or ""
                )

                if validation_result.was_modified:
                    # Update skills in database with validated skills
                    validated_skills_str = self.single_job_validator.get_validated_skills_string(validation_result)
                    await asyncio.to_thread(
                        self.db_ops.update_job_skills,
                        task.job_id,
                        validated_skills_str
                    )
                    job.skills = validated_skills_str  # Update local object too

                    # Emit validation event for UI
                    emit_progress("job_validated", {
                        "slot_id": slot_id,
                        "job_id": task.job_id[:20],
                        "fp_removed": len(validation_result.false_positives_removed),
                        "fn_added": len(validation_result.false_negatives_added),
                        "log": validation_result.validation_log[:80]
                    })
                    logger.info(f"🔧 Slot {slot_id}: Validated - {validation_result.validation_log}")

                async with self.results_lock:
                    self.results.append(job)
                    self.total_success += 1

                # CRITICAL: Use stats_lock for all counter modifications to prevent race conditions
                # This protects the check-then-act pattern in adaptive throttling
                async with self.stats_lock:
                    # Reset 429 count on success
                    self.consecutive_429_count = max(0, self.consecutive_429_count - 1)

                    # Reset authwall counter on success - cookies are working!
                    self.consecutive_authwall_count = 0

                    # ADAPTIVE THROTTLING: Track consecutive successes
                    self.consecutive_success_count += 1
                    delay_changed = False
                    should_boost_milestone = False

                    if self.consecutive_success_count >= self.success_threshold:
                        # Reduce delay by step amount (but not below min_delay)
                        old_delay = self.current_delay
                        self.current_delay = max(
                            self.min_delay,
                            self.current_delay - self.delay_reduction_step
                        )
                        self.consecutive_success_count = 0  # Reset counter
                        if self.current_delay < old_delay:
                            delay_changed = True
                            should_boost_milestone = True
                            logger.info(
                                f"⚡ Adaptive throttle: {old_delay:.2f}s → {self.current_delay:.2f}s "
                                f"(after {self.success_threshold} successes)"
                            )

                # Token bucket operations OUTSIDE the stats_lock (they have their own lock)
                await self.token_bucket.boost(boost_amount=0.3)  # Small reward per success
                if should_boost_milestone:
                    await self.token_bucket.boost(boost_amount=1.0)  # Bigger boost on milestone

                logger.info(f"✅ Slot {slot_id}: Done - {task.job_id[:30]}")

                # Emit detailed slot success event
                emit_progress("slot_success", {
                    "slot_id": slot_id,
                    "job_id": task.job_id[:20],
                    "company": job.company_name[:25] if job.company_name else "",
                    "title": job.actual_role[:30] if job.actual_role else "",
                    "skills_count": len(job.skills.split(",")) if job.skills else 0
                })

                emit_progress("job_complete", {
                    "slot_id": slot_id,
                    "job_id": task.job_id,
                    "job_index": task.index,
                    "total_jobs": task.total,
                    "status": "success",
                    "company": job.company_name,
                    "title": job.actual_role[:50] if job.actual_role else "",
                    "skills_count": len(job.skills.split(",")) if job.skills else 0,
                    "current_delay": self.current_delay,
                    "delay_changed": delay_changed,
                    "stats": {
                        "success": self.total_success,
                        "expired": self.total_expired,
                        "failed": self.total_failed,
                        "processed": self.total_processed + 1
                    }
                })
                return True, None
            else:
                return False, "storage_failed"

        except ExpiredJobError as e:
            # Run in thread to avoid blocking event loop
            await asyncio.to_thread(self.db_ops.delete_urls, [task.url])
            error_msg = str(e)
            error_lower = error_msg.lower()

            # Track consecutive authwall hits - PROTECTED by stats_lock
            is_authwall = any(x in error_lower for x in ["authwall", "login", "redirect"])
            authwall_count = 0
            should_alert_cookies = False

            async with self.stats_lock:
                self.total_expired += 1
                if is_authwall:
                    self.consecutive_authwall_count += 1
                    authwall_count = self.consecutive_authwall_count

                    # Check if cookies are likely expired
                    if self.consecutive_authwall_count >= self.authwall_threshold and not self.cookies_expired_alert_sent:
                        self.cookies_expired_alert_sent = True
                        should_alert_cookies = True
                else:
                    # Non-authwall expiry (job deleted, non-English, etc.) - reset counter
                    self.consecutive_authwall_count = 0

            # Logging and events OUTSIDE the lock
            if is_authwall:
                logger.warning(f"🔐 Authwall #{authwall_count} - {error_msg[:40]}")

                # Emit authwall event for slot monitor
                emit_progress("slot_authwall", {
                    "slot_id": slot_id,
                    "job_id": task.job_id[:20],
                    "reason": error_msg[:50],
                    "consecutive_count": authwall_count
                })

                if should_alert_cookies:
                    logger.error(f"🔴 {authwall_count} consecutive authwall hits - COOKIES MAY BE EXPIRED!")
                    emit_progress("cookie_expired", {
                        "message": f"🔴 {authwall_count}+ consecutive authwall hits detected. Please refresh LinkedIn cookies!",
                        "consecutive_count": authwall_count
                    })
            else:
                # Emit expired event for slot monitor (non-authwall)
                emit_progress("slot_expired", {
                    "slot_id": slot_id,
                    "job_id": task.job_id[:20],
                    "reason": error_msg[:50]
                })

            logger.info(f"🗑️ Slot {slot_id}: Skipped - {task.job_id[:30]} ({error_msg[:40]})")
            emit_progress("job_complete", {
                "slot_id": slot_id,
                "job_id": task.job_id,
                "job_index": task.index,
                "total_jobs": task.total,
                "status": "expired",
                "error": error_msg,  # Include reason (Non-English, expired, etc.)
                "is_authwall": is_authwall,
                "stats": {
                    "success": self.total_success,
                    "expired": self.total_expired,
                    "failed": self.total_failed,
                    "processed": self.total_processed + 1
                }
            })
            return False, "authwall" if is_authwall else "expired"

        except asyncio.CancelledError:
            # Task was force-cancelled due to timeout
            logger.warning(f"⚠️ Slot {slot_id}: Cancelled - {task.job_id[:30]}")
            emit_progress("job_complete", {
                "slot_id": slot_id,
                "job_id": task.job_id,
                "job_index": task.index,
                "total_jobs": task.total,
                "status": "error",
                "error": "Task cancelled (timeout)",
                "stats": {
                    "success": self.total_success,
                    "expired": self.total_expired,
                    "failed": self.total_failed + 1,
                    "processed": self.total_processed + 1
                }
            })
            return False, "cancelled"

        except Exception as e:
            logger.error(f"❌ Slot {slot_id}: Error - {task.job_id[:30]} - {e}")
            emit_progress("job_complete", {
                "slot_id": slot_id,
                "job_id": task.job_id,
                "job_index": task.index,
                "total_jobs": task.total,
                "status": "error",
                "error": str(e)[:100],
                "stats": {
                    "success": self.total_success,
                    "expired": self.total_expired,
                    "failed": self.total_failed + 1,
                    "processed": self.total_processed + 1
                }
            })
            return False, "error"

    async def _extract_job_data(self, page: Page, task: JobTask) -> JobDetailModel:
        """Extract job data from loaded page - DATA-FIRST approach

        SMART AUTHWALL DETECTION:
        - TRY to extract job data FIRST (title, description)
        - ONLY IF data is missing, THEN check for authwall indicators
        - Sign-in dialogs may appear as overlays but data is often still accessible
        """

        current_url = page.url

        # Check for 404 page FIRST (with SHORT timeout) - this is always reliable
        try:
            page_title = await asyncio.wait_for(page.title(), timeout=2.0)
        except asyncio.TimeoutError:
            page_title = ""

        if "404" in page_title.lower() or "not found" in page_title.lower():
            raise ExpiredJobError("404 page")

        # Get page text for later checks
        try:
            page_text = await asyncio.wait_for(
                page.evaluate("() => document.body.innerText"),
                timeout=3.0  # Reduced to 3 seconds
            )
        except asyncio.TimeoutError:
            raise ExpiredJobError("Page content timeout")

        page_text_lower = page_text.lower()

        # Check for expired job messages (these are reliable indicators)
        for error_msg in EXPIRED_JOB_INDICATORS["error_messages"]:
            if error_msg in page_text_lower:
                raise ExpiredJobError(f"Expired: {error_msg}")

        # NOTE: Language check moved AFTER description extraction
        # We only reject if the JOB DESCRIPTION is non-English
        # This allows collecting English job descriptions from non-English country sites
        # (e.g., German LinkedIn with English job postings)

        # Helper for safe element extraction with SHORT timeout
        async def safe_query_text(selectors: list[str], timeout: float = 2.0, max_total: float = 8.0) -> str:
            start = time.time()
            for selector in selectors:
                # Bail out if total time exceeded (prevent 10 selectors × 4s = 40s)
                if time.time() - start >= max_total:
                    return ""
                try:
                    elem = await asyncio.wait_for(
                        page.query_selector(selector),
                        timeout=timeout
                    )
                    if elem:
                        text = await asyncio.wait_for(
                            elem.inner_text(),
                            timeout=timeout
                        )
                        return text.strip()
                except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                    continue
            return ""

        # Extract job title (with timeout)
        job_title = await safe_query_text(DETAIL_SELECTORS["job_title"])
        if not job_title:
            job_title = task.actual_role

        # Extract description (with timeout - slightly longer for description)
        job_description = await safe_query_text(DETAIL_SELECTORS["description"], timeout=3.0)
        if job_description:
            job_description = html.unescape(job_description)
            job_description = re.sub(r"<[^>]+>", " ", job_description)
            job_description = re.sub(r"&[a-zA-Z]+;", " ", job_description)
            job_description = " ".join(job_description.split())

            # ═══════════════════════════════════════════════════════════════════
            # LANGUAGE CHECK: Only check the JOB DESCRIPTION, not the page
            # This allows collecting English jobs from non-English country sites
            # (e.g., German/French/Spanish LinkedIn with English job postings)
            # ═══════════════════════════════════════════════════════════════════
            desc_sample = job_description[:1500] if len(job_description) > 1500 else job_description
            is_non_english, detected_lang = detect_non_english_language(desc_sample.lower())
            if is_non_english:
                logger.info(f"🌐 Skipping non-English JOB DESCRIPTION ({detected_lang}): {task.url[:50]}...")
                raise ExpiredJobError(f"Non-English job description ({detected_lang})")

        # Extract company (with timeout)
        company_name = await safe_query_text(DETAIL_SELECTORS["company_name"])

        # ═══════════════════════════════════════════════════════════════════
        # SMART AUTHWALL DETECTION: Only check if data is MISSING
        # ═══════════════════════════════════════════════════════════════════
        # If we have BOTH title and description, the data is accessible!
        # Sign-in dialogs are just overlays - ignore them if data is present.
        #
        # Only if data is missing, THEN check for authwall indicators.
        # ═══════════════════════════════════════════════════════════════════

        data_is_accessible = bool(job_description.strip()) and bool(company_name.strip())

        if not data_is_accessible:
            # Data is missing - NOW check if it's due to authwall
            logger.debug(f"📋 Data missing (desc={bool(job_description.strip())}, company={bool(company_name.strip())}) - checking for authwall")

            # Check URL for authwall indicators
            authwall_url_indicators = ["authwall", "login", "signin", "checkpoint", "uas/login", "session_redirect"]
            is_authwall_url = any(indicator in current_url.lower() for indicator in authwall_url_indicators)

            # Check page content for login screen indicators
            login_content_indicators = [
                "sign in", "join now", "forgot password", "create account",
                "sertai linkedin", "daftar masuk", "iniciar sesión", "s'inscrire",
                "anmelden", "registrieren", "entrar", "cadastre-se",
                "ログイン", "登录", "로그인"
            ]
            login_matches = sum(1 for indicator in login_content_indicators if indicator in page_text_lower)

            # It's authwall ONLY if: (authwall URL OR 2+ login indicators) AND no job content
            has_job_content = "responsibilities" in page_text_lower or "requirements" in page_text_lower or "experience" in page_text_lower
            is_authwall = (is_authwall_url or login_matches >= 2) and not has_job_content

            if is_authwall:
                logger.info(f"🔐 Authwall confirmed (URL={is_authwall_url}, login_matches={login_matches}) - data not accessible")
                raise ExpiredJobError("Authwall - data not accessible")

            # Not authwall, just missing data
            if not job_description.strip():
                raise ValueError("Empty description")
            if not company_name.strip():
                raise ValueError("No company")
        else:
            # Data IS accessible - log success even if sign-in dialog present
            if "sign in" in page_text_lower or "join now" in page_text_lower:
                logger.debug(f"✅ Sign-in dialog present but data accessible - continuing with extraction")

        # Extract posted date (with timeout)
        posted_date_str = await safe_query_text(DETAIL_SELECTORS["posted_date"])
        posted_date = parse_linkedin_date(posted_date_str) if posted_date_str else None

        # Extract skills (return_confidence=False returns list[str])
        extracted_skills_raw = self.skill_extractor.extract(
            job_description, return_confidence=False
        )
        # Convert to list[str] - when return_confidence=False, items are strings
        extracted_skills: list[str] = [
            str(s) if isinstance(s, str) else s[0]
            for s in extracted_skills_raw
        ] if extracted_skills_raw else []

        if extracted_skills:
            seen_lower: set[str] = set()
            unique_skills: list[str] = []
            for skill in extracted_skills:
                if skill.lower() not in seen_lower:
                    seen_lower.add(skill.lower())
                    unique_skills.append(skill)
            extracted_skills = unique_skills[:15]

        validated_skills = ""
        if extracted_skills:
            canonical = self.skills_validator.validate_and_extract(job_description)
            validated_skills = (
                ", ".join(sorted(canonical))
                if canonical
                else ", ".join(extracted_skills)
            )

        # Create job model
        job = JobDetailModel(
            job_id=task.job_id,
            platform=task.platform,
            actual_role=job_title,
            url=task.url,
            job_description=job_description[:5000],
            skills=validated_skills,
            company_name=company_name,
            posted_date=posted_date,
        )

        # Validate
        is_valid, reason = self.job_validator.validate_job(job)
        if not is_valid:
            if "Non-English" in reason:
                raise ExpiredJobError(f"Non-English: {reason}")
            raise ValueError(f"Invalid: {reason}")

        return job

    async def _scrape_sequential(
        self, urls: List[tuple[str, str, str, str]]
    ) -> List[JobDetailModel]:
        """SIMPLE SEQUENTIAL MODE - One job at a time, most reliable.

        This mode:
        - Uses a single page (no complex multi-tab management)
        - Simple for loop (no queues, workers, or locks)
        - Simple delay between jobs (no token bucket)
        - Emits the same progress events for UI compatibility
        """
        initial_count = len(urls)

        logger.info("=" * 60)
        logger.info("🚀 SEQUENTIAL SCRAPER (Simple Mode)")
        logger.info("=" * 60)
        logger.info(f"   Mode: SEQUENTIAL (1 job at a time)")
        logger.info(f"   Delay: {self.delay_between_jobs}s between jobs")
        logger.info(f"   Input URLs: {initial_count}")
        logger.info("=" * 60)

        # Build task list
        tasks: List[JobTask] = []
        for idx, url_tuple in enumerate(urls, 1):
            url, _, actual_role, _ = url_tuple
            job_id = url.split("/")[-1]
            tasks.append(
                JobTask(
                    url=url,
                    job_id=job_id,
                    platform="linkedin",
                    actual_role=actual_role,
                    index=idx,
                    total=initial_count,
                )
            )

        self.total_jobs = len(tasks)

        # Setup proxy
        proxy_url = os.getenv("PROXY_URL")
        proxy_config = None
        if proxy_url:
            parts = proxy_url.replace("http://", "").split("@")
            if len(parts) == 2:
                auth, server = parts
                username, password = auth.split(":")
                proxy_config = ProxySettings(
                    server=f"http://{server}",
                    username=username,
                    password=password,
                )

        # Launch browser WITH TIMEOUT to prevent hanging on browser issues
        p = await async_playwright().start()
        try:
            browser = await asyncio.wait_for(
                p.chromium.launch(headless=self.headless, proxy=proxy_config),
                timeout=30.0  # 30s timeout for browser launch
            )
        except asyncio.TimeoutError:
            logger.error("❌ Browser launch timed out after 30s")
            await p.stop()
            return []

        try:
            context = await asyncio.wait_for(
                browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    user_agent=get_random_user_agent(),
                ),
                timeout=10.0  # 10s timeout for context creation
            )
        except asyncio.TimeoutError:
            logger.error("❌ Context creation timed out after 10s")
            await browser.close()
            await p.stop()
            return []

        page: Page | None = None  # Initialize to None for cleanup safety

        try:
            # Create single page WITH TIMEOUT
            page = await asyncio.wait_for(context.new_page(), timeout=10.0)
            await asyncio.wait_for(
                page.set_extra_http_headers({"User-Agent": get_random_user_agent()}),
                timeout=5.0
            )
            self.slots[0] = page
            logger.info("✅ Single page created for sequential processing")

            # Emit start event
            emit_progress("scraper_start", {
                "total_jobs": self.total_jobs,
                "num_slots": 1,  # Sequential = 1 slot
                "delay_between_jobs": self.delay_between_jobs,
                "mode": "sequential"
            })

            # Simple sequential loop with robust error handling
            consecutive_errors = 0
            MAX_CONSECUTIVE_ERRORS = 10  # Stop after 10 consecutive errors

            for task in tasks:
                # Check if we've had too many consecutive errors
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.error(f"🛑 {MAX_CONSECUTIVE_ERRORS} consecutive errors - stopping scraper")
                    emit_progress("scraper_error", {
                        "message": f"Stopped after {MAX_CONSECUTIVE_ERRORS} consecutive errors",
                        "processed": self.total_processed
                    })
                    break

                # Emit dispatch event
                emit_progress("job_dispatch", {
                    "slot_id": 0,
                    "job_id": task.job_id,
                    "job_index": task.index,
                    "total_jobs": task.total,
                    "is_retry": task.retry_count > 0
                })

                logger.info(f"📌 Processing Job {task.index}/{task.total}: {task.job_id[:30]}")

                # Process job with hard timeout and catch-all exception handling
                success = False
                error_type: str | None = None

                try:
                    success, error_type = await asyncio.wait_for(
                        self._process_job_in_slot(0, page, task),
                        timeout=30.0  # 30s per job timeout
                    )
                except asyncio.TimeoutError:
                    success, error_type = False, "timeout"
                    logger.warning(f"⏱️ Job timeout after 30s: {task.job_id[:30]}")
                    emit_progress("slot_timeout", {
                        "slot_id": 0,
                        "job_id": task.job_id[:20],
                        "timeout": 30
                    })
                except asyncio.CancelledError:
                    # Task was cancelled - stop the loop
                    logger.warning(f"⚠️ Scraper cancelled at job {task.index}")
                    break
                except Exception as e:
                    # CATCH-ALL: Any other exception
                    success, error_type = False, "error"
                    logger.error(f"❌ Unexpected error for {task.job_id[:30]}: {e}")
                    emit_progress("slot_error", {
                        "slot_id": 0,
                        "job_id": task.job_id[:20],
                        "error": str(e)[:100]
                    })

                # Try to reset page after any error to prevent stuck state
                if not success:
                    consecutive_errors += 1
                    try:
                        await asyncio.wait_for(
                            page.goto("about:blank", timeout=2000),
                            timeout=3.0
                        )
                    except Exception:
                        # Page might be corrupted - try to recreate it
                        logger.warning("⚠️ Page reset failed - recreating page")
                        try:
                            await asyncio.wait_for(page.close(), timeout=2.0)
                        except Exception:
                            pass
                        try:
                            page = await asyncio.wait_for(
                                context.new_page(), timeout=10.0
                            )
                            await asyncio.wait_for(
                                page.set_extra_http_headers({"User-Agent": get_random_user_agent()}),
                                timeout=5.0
                            )
                            self.slots[0] = page
                            logger.info("✅ Page recreated successfully")
                        except (asyncio.TimeoutError, Exception) as e:
                            logger.error(f"❌ Failed to recreate page: {e}")
                            # Can't continue without a page
                            break
                else:
                    consecutive_errors = 0  # Reset on success

                # Handle result
                if not success:
                    if error_type == "rate_limit":
                        self.consecutive_429_count += 1
                        self.total_failed += 1
                        # Backoff before next job
                        backoff = min(30.0, 10.0 * self.consecutive_429_count)
                        logger.warning(f"⚠️ 429 Rate limit - waiting {backoff}s")
                        await asyncio.sleep(backoff)
                    elif error_type not in ("expired", "authwall"):
                        self.total_failed += 1

                self.total_processed += 1

                # Emit completion event
                status = "success" if success else error_type or "error"
                emit_progress("job_complete", {
                    "slot_id": 0,
                    "job_id": task.job_id,
                    "job_index": task.index,
                    "total_jobs": task.total,
                    "status": status,
                    "error": "" if success else (error_type or "unknown"),
                    "stats": {
                        "success": self.total_success,
                        "expired": self.total_expired,
                        "failed": self.total_failed,
                        "processed": self.total_processed
                    }
                })

                # Simple delay between jobs (with LARGE jitter to look human)
                if task.index < task.total:  # Don't wait after last job
                    # Random delay between 80%-150% of base delay (more human-like)
                    jitter = random.uniform(-0.2, 0.5) * self.delay_between_jobs
                    wait_time = max(4.0, self.delay_between_jobs + jitter)  # Min 4s
                    logger.debug(f"⏳ Waiting {wait_time:.1f}s before next job")
                    await asyncio.sleep(wait_time)

        finally:
            # Cleanup
            if page is not None:
                try:
                    await asyncio.wait_for(page.close(), timeout=5.0)
                except Exception:
                    pass
            try:
                await asyncio.wait_for(context.close(), timeout=10.0)
            except Exception:
                pass
            try:
                await asyncio.wait_for(browser.close(), timeout=10.0)
            except Exception:
                pass
            await p.stop()

        self._print_summary()
        return self.results

    async def scrape(
        self, urls: List[tuple[str, str, str, str]]
    ) -> List[JobDetailModel]:
        """Main entry point - ROUND-ROBIN with rate limiting"""

        # Use simple sequential mode if enabled (more reliable)
        if self.sequential:
            return await self._scrape_sequential(urls)

        initial_count = len(urls)

        logger.info("=" * 60)
        logger.info("🚀 ROUND-ROBIN SCRAPER with Adaptive Rate Limiting")
        logger.info("=" * 60)
        logger.info(f"   Slots: {self.num_slots}")
        logger.info(f"   Base delay: {self.delay_between_jobs}s")
        logger.info(f"   Effective delay: {self.current_delay:.1f}s (scaled for {self.num_slots} slots)")
        logger.info(f"   Min interval: {self.min_interval_between_jobs:.1f}s between ANY job starts")
        logger.info(f"   Input URLs: {initial_count}")
        logger.info("")
        logger.info("   🌐 English-only: Content-based filtering (all subdomains allowed)")
        logger.info("   ⚡ Rate limiting enforced via min_interval + token bucket")
        logger.info("=" * 60)

        # Build task list - all subdomains allowed, content checked after load
        tasks: List[JobTask] = []

        for idx, url_tuple in enumerate(urls, 1):
            url, _, actual_role, _ = url_tuple
            job_id = url.split("/")[-1]
            tasks.append(
                JobTask(
                    url=url,
                    job_id=job_id,
                    platform="linkedin",
                    actual_role=actual_role,
                    index=idx,
                    total=initial_count,
                )
            )

        self.total_jobs = len(tasks)

        # Setup proxy
        proxy_url = os.getenv("PROXY_URL")
        proxy_config = None
        if proxy_url:
            parts = proxy_url.replace("http://", "").split("@")
            if len(parts) == 2:
                auth, server = parts
                username, password = auth.split(":")
                proxy_config = ProxySettings(
                    server=f"http://{server}",
                    username=username,
                    password=password,
                )

        # Launch browser WITH TIMEOUTS to prevent hanging
        p = await async_playwright().start()
        try:
            browser = await asyncio.wait_for(
                p.chromium.launch(headless=self.headless, proxy=proxy_config),
                timeout=30.0  # 30s timeout for browser launch
            )
        except asyncio.TimeoutError:
            logger.error("❌ Browser launch timed out after 30s")
            await p.stop()
            return []

        try:
            context = await asyncio.wait_for(
                browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    user_agent=get_random_user_agent(),
                ),
                timeout=10.0  # 10s timeout for context creation
            )
        except asyncio.TimeoutError:
            logger.error("❌ Context creation timed out after 10s")
            await browser.close()
            await p.stop()
            return []

        try:
            # Create all slots WITH TIMEOUTS and STAGGERED DELAYS
            # Each slot opens 3s after the previous one to avoid rate limits
            SLOT_STAGGER_DELAY = 3.0  # 3 seconds between each slot creation
            logger.info(f"🖥️ Creating {self.num_slots} slots (staggered by {SLOT_STAGGER_DELAY}s each)...")
            for i in range(self.num_slots):
                try:
                    # Stagger slot creation - slot 0 at 0s, slot 1 at 3s, slot 2 at 6s...
                    if i > 0:
                        logger.info(f"   ⏳ Waiting {SLOT_STAGGER_DELAY}s before creating Slot {i}...")
                        await asyncio.sleep(SLOT_STAGGER_DELAY)

                    page = await asyncio.wait_for(context.new_page(), timeout=10.0)
                    await asyncio.wait_for(
                        page.set_extra_http_headers({"User-Agent": get_random_user_agent()}),
                        timeout=5.0
                    )
                    self.slots[i] = page
                    self.slot_busy[i] = False
                    self.slot_locks[i] = asyncio.Lock()
                    logger.info(f"   ✅ Slot {i} created")
                except asyncio.TimeoutError:
                    logger.error(f"❌ Slot {i} creation timed out")
                    # Close already created slots and return
                    for j in range(i):
                        try:
                            await self.slots[j].close()
                        except Exception:
                            pass
                    await context.close()
                    await browser.close()
                    await p.stop()
                    return []
            logger.info(f"✅ {self.num_slots} slots created (staggered)")

            logger.info("🚀 Starting scraper (authwall jobs will be auto-skipped)")

            # Emit start event
            emit_progress("scraper_start", {
                "total_jobs": self.total_jobs,
                "num_slots": self.num_slots,
                "delay_between_jobs": self.delay_between_jobs
            })

            # ═══════════════════════════════════════════════════════════════
            # DSA: PRODUCER-CONSUMER PATTERN with asyncio.Queue
            # ═══════════════════════════════════════════════════════════════
            # - Producer: Fills queue with jobs (FIFO order guaranteed)
            # - Consumers: N independent workers (one per slot)
            # - Benefits: No deadlocks, sequential order, parallel execution
            # ═══════════════════════════════════════════════════════════════

            job_queue: asyncio.Queue[JobTask | None] = asyncio.Queue()  # Queue of JobTask or None
            HARD_TIMEOUT = 35.0  # Per-job timeout (5s rate + 10s nav + 10s extract + 10s buffer)
            QUEUE_GET_TIMEOUT = 10.0  # Max wait for next job (fail fast - 10s instead of 60s)

            # Worker function - runs independently for each slot
            WORKER_STAGGER_DELAY = 2.0  # 2 seconds between each worker start

            async def slot_worker(slot_id: int, page: Page):
                """Independent worker for each slot - pulls jobs from queue."""
                # STAGGERED START: Each worker waits slot_id * 2 seconds before starting
                # Slot 0: 0s, Slot 1: 2s, Slot 2: 4s, Slot 3: 6s...
                if slot_id > 0:
                    stagger_wait = slot_id * WORKER_STAGGER_DELAY
                    logger.info(f"⏳ Slot {slot_id}: Waiting {stagger_wait:.0f}s before starting (staggered)")
                    await asyncio.sleep(stagger_wait)
                    logger.info(f"🚀 Slot {slot_id}: Starting now")

                consecutive_timeouts = 0
                MAX_CONSECUTIVE_TIMEOUTS = 5  # Exit after 5 consecutive get() timeouts

                while True:
                    task = None
                    task_done_called = False  # Prevent double task_done()

                    try:
                        # Get next job from queue WITH TIMEOUT (prevents infinite blocking)
                        try:
                            task = await asyncio.wait_for(
                                job_queue.get(),
                                timeout=QUEUE_GET_TIMEOUT
                            )
                            consecutive_timeouts = 0  # Reset on successful get
                        except asyncio.TimeoutError:
                            consecutive_timeouts += 1
                            if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
                                logger.warning(f"⚠️ Slot {slot_id}: {MAX_CONSECUTIVE_TIMEOUTS} consecutive queue timeouts - worker exiting")
                                break
                            # Queue might be empty and waiting - continue loop
                            continue

                        # Poison pill - signal to stop
                        if task is None:
                            logger.info(f"🛑 Slot {slot_id}: Worker shutting down")
                            emit_progress("slot_idle", {
                                "slot_id": slot_id,
                                "message": "Worker shutdown"
                            })
                            job_queue.task_done()
                            task_done_called = True
                            break

                        # Mark slot as busy
                        self.slot_busy[slot_id] = True
                        self.free_slots.discard(slot_id)

                        # Emit dispatch event
                        emit_progress("job_dispatch", {
                            "slot_id": slot_id,
                            "job_id": task.job_id,
                            "job_index": task.index,
                            "total_jobs": task.total,
                            "is_retry": task.retry_count > 0
                        })

                        logger.info(f"📌 Slot {slot_id}: Processing Job {task.index}/{task.total}")

                        # Process job with hard timeout (includes rate limiting via token bucket)
                        # CRITICAL: All waits must be INSIDE the timeout to prevent hanging
                        current_task: JobTask = task

                        async def _process_with_rate_limit() -> tuple[bool, str | None]:
                            """Wrapper to include rate limiting and job start coordination in timeout"""
                            # GLOBAL JOB START COORDINATOR: Ensure minimum interval between job starts
                            # This prevents bursts when jobs are skipped quickly
                            await self._acquire_job_start_slot(slot_id)

                            # Check rate limit INSIDE timeout (max 5s wait)
                            wait_time = await self._wait_for_rate_limit()
                            if wait_time > 0:
                                capped_wait = min(wait_time, 5.0)  # Cap at 5s (inside 35s timeout)
                                logger.info(f"⏳ Slot {slot_id}: Rate limit wait {capped_wait:.1f}s")
                                await asyncio.sleep(capped_wait)

                            await self._enforce_dispatch_delay()
                            return await self._process_job_in_slot(slot_id, page, current_task)

                        try:
                            success, error_type = await asyncio.wait_for(
                                _process_with_rate_limit(),
                                timeout=HARD_TIMEOUT
                            )
                        except asyncio.TimeoutError:
                            success, error_type = False, "timeout"
                            logger.warning(f"⏱️ Slot {slot_id}: Job timeout after {HARD_TIMEOUT}s")
                            emit_progress("slot_timeout", {
                                "slot_id": slot_id,
                                "job_id": task.job_id[:20],
                                "timeout": HARD_TIMEOUT
                            })
                            # Reset page state
                            try:
                                await asyncio.wait_for(
                                    page.goto("about:blank", timeout=2000),
                                    timeout=3.0
                                )
                            except Exception:
                                pass
                        except asyncio.CancelledError:
                            success, error_type = False, "cancelled"
                            logger.warning(f"⚠️ Slot {slot_id}: Job cancelled")
                            raise  # Re-raise to exit worker

                        # Determine status for progress event
                        status = "success" if success else error_type or "error"
                        error_msg = "" if success else (error_type or "unknown")

                        # Handle result
                        if not success:
                            if error_type == "rate_limit":
                                # Protect all counter modifications with lock
                                async with self.stats_lock:
                                    self.consecutive_429_count += 1
                                    self.current_delay = self.delay_between_jobs
                                    self.consecutive_success_count = 0
                                await self.token_bucket.penalize(penalty_seconds=10.0)

                                # Check BOTH per-job retries AND total 429 retries
                                can_retry = (
                                    task.retry_count < self.max_retries and
                                    self.total_429_retries < self.max_total_429_retries
                                )

                                if can_retry:
                                    backoff = self.rate_limit_backoff_base * (2 ** task.retry_count)
                                    backoff = min(backoff, 300)
                                    async with self.stats_lock:
                                        self.rate_limit_until = time.time() + backoff
                                        self.total_429_retries += 1
                                    task.retry_count += 1
                                    await job_queue.put(task)  # Re-queue for retry
                                    async with self.stats_lock:
                                        self.total_retried += 1
                                    logger.warning(f"⚠️ Slot {slot_id}: 429 - requeued for retry #{task.retry_count} (total 429s: {self.total_429_retries})")
                                else:
                                    async with self.stats_lock:
                                        self.total_failed += 1
                                    if self.total_429_retries >= self.max_total_429_retries:
                                        logger.error(f"🛑 Slot {slot_id}: Max total 429 retries ({self.max_total_429_retries}) reached - marking job as failed")
                            elif error_type == "server_error":
                                if task.retry_count < self.max_retries:
                                    task.retry_count += 1
                                    await job_queue.put(task)
                                    self.total_retried += 1
                                else:
                                    self.total_failed += 1
                            elif error_type == "authwall":
                                # Authwall - just skip, don't retry
                                logger.info(f"🔐 Slot {slot_id}: Authwall - skipping")
                            elif error_type == "timeout":
                                self.total_failed += 1
                            elif error_type not in ("expired",):
                                self.total_failed += 1

                        self.total_processed += 1

                        # CRITICAL: Emit job_complete event for UI progress tracking
                        emit_progress("job_complete", {
                            "slot_id": slot_id,
                            "job_id": task.job_id,
                            "job_index": task.index,
                            "total_jobs": task.total,
                            "status": status,
                            "error": error_msg,
                            "stats": {
                                "success": self.total_success,
                                "expired": self.total_expired,
                                "failed": self.total_failed,
                                "processed": self.total_processed
                            }
                        })

                    except asyncio.CancelledError:
                        logger.warning(f"⚠️ Slot {slot_id}: Worker cancelled")
                        if task is not None and not task_done_called:
                            job_queue.task_done()
                            task_done_called = True
                        break

                    except Exception as e:
                        logger.error(f"❌ Slot {slot_id}: Worker error - {e}")
                        self.total_failed += 1
                        self.total_processed += 1
                        if task is not None:
                            emit_progress("job_complete", {
                                "slot_id": slot_id,
                                "job_id": task.job_id,
                                "job_index": task.index,
                                "total_jobs": task.total,
                                "status": "error",
                                "error": str(e)[:50],
                                "stats": {
                                    "success": self.total_success,
                                    "expired": self.total_expired,
                                    "failed": self.total_failed,
                                    "processed": self.total_processed
                                }
                            })

                    finally:
                        # Mark slot as free and signal task done (only once per task)
                        if task is not None and not task_done_called:
                            self.slot_busy[slot_id] = False
                            self.free_slots.add(slot_id)
                            job_queue.task_done()
                            task_done_called = True

                            emit_progress("slot_idle", {
                                "slot_id": slot_id,
                                "message": "Ready for next job"
                            })
                        elif task is not None:
                            # task_done already called, just update slot state
                            self.slot_busy[slot_id] = False
                            self.free_slots.add(slot_id)

            # Start all worker tasks (one per slot)
            workers = [
                asyncio.create_task(slot_worker(i, self.slots[i]))
                for i in range(self.num_slots)
            ]
            logger.info(f"🚀 Started {self.num_slots} independent worker tasks")

            # Producer: Fill queue with all jobs (sequential order)
            for task in tasks:
                await job_queue.put(task)
            logger.info(f"📥 Queued {len(tasks)} jobs")

            # Wait for all jobs to be processed WITH PROGRESS-BASED TIMEOUT
            # Instead of fixed timeout, check if progress is being made
            # If no progress for 120s, assume deadlock
            last_processed = 0
            no_progress_count = 0
            max_no_progress = 12  # 12 checks * 10s = 120s without progress = deadlock
            check_interval = 10.0  # Check every 10 seconds

            logger.info(f"⏳ Waiting for {len(tasks)} jobs to complete (progress-based timeout)...")

            while not job_queue.empty() or self.total_processed < len(tasks):
                try:
                    # Wait for queue to drain with short timeout
                    await asyncio.wait_for(job_queue.join(), timeout=check_interval)
                    logger.info("✅ All jobs processed")
                    break
                except asyncio.TimeoutError:
                    # Check if progress is being made
                    if self.total_processed > last_processed:
                        # Progress made - reset counter
                        no_progress_count = 0
                        last_processed = self.total_processed
                        remaining = len(tasks) - self.total_processed
                        logger.debug(f"📊 Progress: {self.total_processed}/{len(tasks)} ({remaining} remaining)")
                    else:
                        # No progress
                        no_progress_count += 1
                        if no_progress_count >= max_no_progress:
                            logger.error(f"⚠️ No progress for {no_progress_count * check_interval}s - forcing completion")
                            emit_progress("deadlock_warning", {
                                "message": f"No progress for {no_progress_count * check_interval}s",
                                "processed": self.total_processed,
                                "remaining": job_queue.qsize()
                            })
                            break
                        logger.warning(f"⚠️ No progress for {no_progress_count * check_interval}s ({max_no_progress - no_progress_count} checks remaining)")

            # Send poison pills to stop workers
            for _ in range(self.num_slots):
                await job_queue.put(None)

            # Wait for workers to finish with timeout
            try:
                await asyncio.wait_for(
                    asyncio.gather(*workers, return_exceptions=True),
                    timeout=30.0  # 30s to clean up workers
                )
            except asyncio.TimeoutError:
                logger.warning("⚠️ Workers did not terminate cleanly - cancelling")
                for w in workers:
                    w.cancel()
                await asyncio.gather(*workers, return_exceptions=True)

        finally:
            # ROBUST CLEANUP: Each step in try-except to ensure all resources are released
            for slot_id, page in self.slots.items():
                try:
                    await asyncio.wait_for(page.close(), timeout=5.0)
                except Exception as e:
                    logger.warning(f"⚠️ Failed to close page {slot_id}: {e}")

            try:
                await asyncio.wait_for(context.close(), timeout=10.0)
            except Exception as e:
                logger.warning(f"⚠️ Failed to close context: {e}")

            try:
                await asyncio.wait_for(browser.close(), timeout=10.0)
            except Exception as e:
                logger.warning(f"⚠️ Failed to close browser: {e}")
            await p.stop()

        self._print_summary()
        return self.results

    def _print_summary(self) -> None:
        logger.info("")
        logger.info("=" * 60)
        logger.info("📊 SESSION SUMMARY")
        logger.info("=" * 60)
        logger.info(f"   Total processed: {self.total_processed}")
        logger.info(f"   ✅ Successful:    {self.total_success}")
        logger.info(f"   🗑️ Expired:       {self.total_expired}")
        logger.info(f"   ❌ Failed:        {self.total_failed}")
        logger.info(f"   🔄 Retried:       {self.total_retried}")
        logger.info("=" * 60)

        # Emit finish event
        emit_progress("scraper_finish", {
            "total_processed": self.total_processed,
            "success": self.total_success,
            "expired": self.total_expired,
            "failed": self.total_failed,
            "retried": self.total_retried
        })


# Exceptions
class RateLimitError(Exception):
    pass


class ExpiredJobError(Exception):
    pass


class ServerError(Exception):
    pass


class LoginWallError(Exception):
    pass


async def scrape_job_details_staggered(
    urls: List[tuple[str, str, str, str]],
    headless: bool = False,
    num_workers: int = 2,  # REDUCED: 2 tabs (much safer for LinkedIn)
    stagger_delay: float = 5.0,  # INCREASED: 5s delay to avoid 429
    sequential: bool = True,  # DEFAULT TO SEQUENTIAL (most reliable)
) -> List[JobDetailModel]:
    """Round-Robin Scraper with Adaptive Rate Limiting

    MODES:
    ═══════════════════════════════════════════════════════
    - sequential=True (DEFAULT): Simple 1-job-at-a-time processing
      - Most reliable, no queue/worker complexity
      - Use this if parallel mode has issues

    - sequential=False: Parallel multi-tab processing
      - Faster but more complex
      - May have issues with rate limiting
    ═══════════════════════════════════════════════════════

    ADAPTIVE THROTTLING (parallel mode only):
    - Starts at base delay between jobs
    - After 15 successes: reduces delay by 0.2s
    - On 429 error: resets to base delay
    - Jitter: ±0.3s randomization on every delay
    """

    scraper = RoundRobinScraper(
        num_slots=num_workers,
        delay_between_jobs=stagger_delay,
        headless=headless,
        max_retries=3,
        rate_limit_backoff_base=30.0,
        sequential=sequential,  # Pass sequential flag
    )

    return await scraper.scrape(urls)
