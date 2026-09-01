# Job Detail Scraper Component - Phase 2: Detail Extraction
# Real-time progress updates | Round-robin tabs | Rate limiting
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Generator, TypedDict

import psutil
import streamlit as st
from src.db import JobStorageOperations
from src.ui.components.slot_monitor import SlotMonitor

logger = logging.getLogger(__name__)

# Cross-platform temp directory setup
IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform == "linux"
IS_MAC = sys.platform == "darwin"
TEMP_DIR = tempfile.gettempdir() if IS_WINDOWS else "/tmp"
PROJECT_ROOT = str(Path(__file__).resolve().parents[3])  # Go up 3 levels from this file


def get_venv_python() -> str:
    """Get the correct Python executable from venv for all platforms"""
    venv_path = Path(PROJECT_ROOT) / "venv"

    if IS_WINDOWS:
        python_path = venv_path / "Scripts" / "python.exe"
    else:  # Linux and Mac
        python_path = venv_path / "bin" / "python"

    if python_path.exists():
        return str(python_path)

    # Fallback to sys.executable if venv not found
    logger.warning(f"venv Python not found at {python_path}, using sys.executable")
    return sys.executable


# Get the correct Python executable
VENV_PYTHON = get_venv_python()

# Playwright browsers path for all platforms
if IS_WINDOWS:
    playwright_browsers_path = str(Path.home() / "AppData" / "Local" / "ms-playwright")
elif IS_MAC:
    playwright_browsers_path = str(Path.home() / "Library" / "Caches" / "ms-playwright")
else:  # Linux
    playwright_browsers_path = str(Path.home() / ".cache" / "ms-playwright")

os.environ["TMPDIR"] = TEMP_DIR
os.environ["TMP"] = TEMP_DIR
os.environ["TEMP"] = TEMP_DIR
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = playwright_browsers_path


def _init_scraper_session_state() -> None:
    """Initialize session state for scraper control"""
    if "scraper_process" not in st.session_state:
        st.session_state.scraper_process = None
    if "scraper_running" not in st.session_state:
        st.session_state.scraper_running = False
    if "scraper_stopped" not in st.session_state:
        st.session_state.scraper_stopped = False
    # Two-phase start: first click sets params, rerun starts scraping
    if "start_scraping_pending" not in st.session_state:
        st.session_state.start_scraping_pending = False
    if "scrape_params" not in st.session_state:
        st.session_state.scrape_params = None


def stop_scraper() -> bool:
    """Stop the scraper process and all child processes (browser)

    Returns True if process was stopped, False if no process was running
    """
    process = st.session_state.get("scraper_process")
    if process is None:
        return False

    try:
        pid = process.pid
        logger.info(f"Stopping scraper process {pid} and all children...")

        try:
            parent = psutil.Process(pid)
            # Get all child processes first (browser processes, etc.)
            children = parent.children(recursive=True)

            # Kill children first (browser processes)
            for child in children:
                try:
                    logger.info(f"Killing child process {child.pid}: {child.name()}")
                    child.kill()
                except psutil.NoSuchProcess:
                    pass  # Already terminated
                except Exception as e:
                    logger.warning(f"Failed to kill child {child.pid}: {e}")

            # Now kill the main process
            try:
                parent.kill()
                logger.info(f"Killed main scraper process {pid}")
            except psutil.NoSuchProcess:
                pass  # Already terminated

        except psutil.NoSuchProcess:
            # Process already terminated
            logger.info(f"Process {pid} already terminated")

        # Also try subprocess terminate as backup
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    except Exception as e:
        logger.error(f"Error stopping scraper: {e}")
    finally:
        st.session_state.scraper_process = None
        st.session_state.scraper_running = False
        st.session_state.scraper_stopped = True

    return True


class ScraperResult(TypedDict):
    jobs_scraped: int
    expired_removed: int
    failed: int
    error: str | None


class ProgressEvent(TypedDict, total=False):
    """Progress event from scraper subprocess"""

    event: str

    event: str
    timestamp: float
    # scraper_start event
    total_jobs: int
    num_slots: int
    # job_dispatch event
    slot_id: int
    job_index: int
    # job_complete event
    status: str
    stats: dict[str, int]
    current_delay: float
    delay_changed: bool
    company: str
    title: str
    skills_count: int
    job_id: str
    error: str
    # rate_limit event
    wait_seconds: float
    # cookie_expired / deadlock_warning event
    message: str
    # scraper_finish event
    success: int
    expired: int
    failed: int
    total_processed: int


def stream_scraper_progress(
    platform: str,
    job_role: str,
    batch_size: int,
    concurrent_tabs: int,
    delay_seconds: float = 3.0,
    sequential: bool = True,  # Default to sequential mode (most reliable)
) -> Generator[ProgressEvent, None, ScraperResult]:
    """Stream real-time progress from scraper subprocess

    Yields progress events as they happen, returns final result
    """
    escaped_project_root = PROJECT_ROOT.replace("\\", "\\\\")
    escaped_playwright_path = playwright_browsers_path.replace("\\", "\\\\")

    script = f'''
import os
import sys
import json
import tempfile

# Cross-platform temp directory
TEMP_DIR = tempfile.gettempdir()
os.environ["TMPDIR"] = TEMP_DIR
os.environ["TMP"] = TEMP_DIR
os.environ["TEMP"] = TEMP_DIR

# Set Playwright browsers path for Windows
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = r"{escaped_playwright_path}"

# Add project root to path
sys.path.insert(0, r"{escaped_project_root}")

import asyncio
import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

from src.db.operations import JobStorageOperations
from src.scraper.unified.linkedin.staggered_queue_scraper import scrape_job_details_staggered

async def scrape():
    result = {{"jobs_scraped": 0, "expired_removed": 0, "failed": 0, "error": None}}

    try:
        db_ops = JobStorageOperations("data/jobs.db")
        urls = db_ops.get_unscraped_urls("{platform.lower()}", "{job_role}", limit={batch_size})

        if not urls:
            print(json.dumps(result), flush=True)
            return

        # Round-robin scraper with real-time progress
        jobs = await scrape_job_details_staggered(
            urls=urls,
            headless=False,
            num_workers={concurrent_tabs},
            stagger_delay={delay_seconds},
            sequential={sequential}
        )

        result["jobs_scraped"] = len(jobs)

    except Exception as e:
        result["error"] = str(e)

    print(json.dumps(result), flush=True)

asyncio.run(scrape())
'''

    env = os.environ.copy()
    env["TMPDIR"] = TEMP_DIR
    env["TMP"] = TEMP_DIR
    env["TEMP"] = TEMP_DIR
    env["PLAYWRIGHT_BROWSERS_PATH"] = playwright_browsers_path
    env["PYTHONUNBUFFERED"] = "1"  # Force unbuffered output

    # Use Popen for streaming
    # CRITICAL FIX: Redirect stderr to stdout to prevent pipe deadlock!
    # Without this, if stderr buffer fills up (64KB), subprocess blocks
    # waiting to write, while main process blocks waiting to read stdout.
    process = subprocess.Popen(
        [VENV_PYTHON, "-u", "-c", script],  # Use venv Python for all platforms
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # MERGE stderr into stdout to prevent deadlock
        text=True,
        cwd=PROJECT_ROOT,
        env=env,
        bufsize=1,  # Line buffered
    )

    # Store process in session state for stop button functionality
    st.session_state.scraper_process = process
    st.session_state.scraper_running = True
    st.session_state.scraper_stopped = False

    final_result = ScraperResult(
        jobs_scraped=0, expired_removed=0, failed=0, error=None
    )

    try:
        # Stream stdout line by line
        if process.stdout is None:
            raise RuntimeError("Process stdout is not available")
        for line in iter(process.stdout.readline, ""):
            line = line.strip()
            if not line:
                continue

            # Check for progress events
            if line.startswith("PROGRESS:"):
                try:
                    event_json: str = line[9:]  # Remove "PROGRESS:" prefix
                    raw_event: dict[str, object] = json.loads(event_json)
                    # Cast to ProgressEvent TypedDict
                    event: ProgressEvent = {
                        "event": str(raw_event.get("event", "")),
                    }
                    # Copy all known fields with proper defaults
                    if "timestamp" in raw_event:
                        val = raw_event["timestamp"]
                        event["timestamp"] = (
                            float(val) if isinstance(val, (int, float)) else 0.0
                        )
                    if "total_jobs" in raw_event:
                        val = raw_event["total_jobs"]
                        event["total_jobs"] = (
                            int(val) if isinstance(val, (int, float)) else 0
                        )
                    if "num_slots" in raw_event:
                        val = raw_event["num_slots"]
                        event["num_slots"] = (
                            int(val) if isinstance(val, (int, float)) else 0
                        )
                    if "slot_id" in raw_event:
                        val = raw_event["slot_id"]
                        event["slot_id"] = (
                            int(val) if isinstance(val, (int, float)) else 0
                        )
                    if "job_index" in raw_event:
                        val = raw_event["job_index"]
                        event["job_index"] = (
                            int(val) if isinstance(val, (int, float)) else 0
                        )
                    if "status" in raw_event:
                        val = raw_event["status"]
                        event["status"] = str(val) if val is not None else ""
                    if "stats" in raw_event:
                        val = raw_event["stats"]
                        if isinstance(val, dict):
                            event["stats"] = {
                                str(k): int(v) if isinstance(v, (int, float)) else 0
                                for k, v in val.items()
                            }
                    if "current_delay" in raw_event:
                        val = raw_event["current_delay"]
                        event["current_delay"] = (
                            float(val) if isinstance(val, (int, float)) else 0.0
                        )
                    if "delay_changed" in raw_event:
                        val = raw_event["delay_changed"]
                        event["delay_changed"] = bool(val)
                    if "company" in raw_event:
                        val = raw_event["company"]
                        event["company"] = str(val) if val is not None else ""
                    if "title" in raw_event:
                        val = raw_event["title"]
                        event["title"] = str(val) if val is not None else ""
                    if "skills_count" in raw_event:
                        val = raw_event["skills_count"]
                        event["skills_count"] = (
                            int(val) if isinstance(val, (int, float)) else 0
                        )
                    if "job_id" in raw_event:
                        val = raw_event["job_id"]
                        event["job_id"] = str(val) if val is not None else ""
                    if "error" in raw_event:
                        val = raw_event["error"]
                        event["error"] = str(val) if val is not None else ""
                    if "wait_seconds" in raw_event:
                        val = raw_event["wait_seconds"]
                        event["wait_seconds"] = (
                            float(val) if isinstance(val, (int, float)) else 0.0
                        )
                    if "message" in raw_event:
                        val = raw_event["message"]
                        event["message"] = str(val) if val is not None else ""
                    if "success" in raw_event:
                        val = raw_event["success"]
                        event["success"] = (
                            int(val) if isinstance(val, (int, float)) else 0
                        )
                    if "expired" in raw_event:
                        val = raw_event["expired"]
                        event["expired"] = (
                            int(val) if isinstance(val, (int, float)) else 0
                        )
                    if "failed" in raw_event:
                        val = raw_event["failed"]
                        event["failed"] = (
                            int(val) if isinstance(val, (int, float)) else 0
                        )
                    if "total_processed" in raw_event:
                        val = raw_event["total_processed"]
                        event["total_processed"] = (
                            int(val) if isinstance(val, (int, float)) else 0
                        )
                    yield event
                except json.JSONDecodeError:
                    pass
            # Check for final result
            elif line.startswith("{"):
                try:
                    data: dict[str, object] = json.loads(line)
                    if "jobs_scraped" in data:
                        jobs_scraped_val = data.get("jobs_scraped", 0)
                        expired_val = data.get("expired_removed", 0)
                        failed_val = data.get("failed", 0)
                        error_val = data.get("error")
                        final_result = ScraperResult(
                            jobs_scraped=int(jobs_scraped_val)
                            if isinstance(jobs_scraped_val, (int, float))
                            else 0,
                            expired_removed=int(expired_val)
                            if isinstance(expired_val, (int, float))
                            else 0,
                            failed=int(failed_val)
                            if isinstance(failed_val, (int, float))
                            else 0,
                            error=str(error_val) if error_val is not None else None,
                        )
                except json.JSONDecodeError:
                    pass

        process.wait()

        if process.returncode != 0:
            stderr = process.stderr.read() if process.stderr else ""
            if stderr and not final_result.get("error"):
                final_result["error"] = stderr[:500]

    except Exception as e:
        final_result["error"] = str(e)
    finally:
        if process.poll() is None:
            process.terminate()
        # Clear session state
        st.session_state.scraper_process = None
        st.session_state.scraper_running = False

    return final_result


def render_detail_scraper_form(db_path: str) -> None:
    """Render Phase 2: Job detail extraction interface with real-time updates"""
    # Initialize session state for scraper control
    _init_scraper_session_state()

    st.header("📝 Phase 2: Job Detail Scraper")
    st.markdown(
        "**Adaptive Throttling** | 2.0s → 1.5s after successes | Auto-reset on 429"
    )

    # Check if scraper was stopped - show message
    if st.session_state.get("scraper_stopped"):
        st.warning(
            "⏹️ Scraper was stopped by user. Browser and all processes terminated."
        )
        st.session_state.scraper_stopped = False  # Reset flag

    col1, col2 = st.columns(2)

    with col1:
        platform = st.selectbox(
            "Platform",
            ["LinkedIn", "Naukri"],
            help="Select platform to process URLs from",
        )

        job_role = st.selectbox(
            "Job Role",
            [
                # Data Analysis Bootcamp Roles
                "Data Analyst",
                "Financial Analyst",
                "Retail Analyst",
                "Automobile Analyst",
                "Reporting Analyst",
                "MIS Analyst",
                "Marketing Analyst",
                "Business Analyst",
                # Data Engineering Bootcamp Roles
                "Data Engineer",
                "Power BI Developer",
                "Tableau Developer",
                # GenAI & Data Science Bootcamp Roles
                "AI Engineer",
                "ML Engineer",
                "AI Architect",
                "Data Scientist",
                # FDE Bootcamp Roles
                "Forward Deployed Engineer",
            ],
            index=0,
            help="Select the job role to filter URLs",
        )

    with col2:
        batch_size = st.number_input(
            "Batch Size",
            min_value=10,
            max_value=10000,
            value=100,
            step=50,
            help="Number of URLs to process in one batch",
        )

        num_slots = st.number_input(
            "Number of Parallel Tabs",
            min_value=1,
            max_value=10,
            value=2,  # REDUCED: 2 tabs - much safer for LinkedIn (avoids 429)
            step=1,
            help="Number of browser tabs (1-10). More tabs = faster but higher risk of rate limits. Recommend 1-2 for LinkedIn.",
        )

    col3, col4 = st.columns(2)

    with col3:
        delay_seconds = st.number_input(
            "Base Delay (seconds)",
            min_value=3.0,
            max_value=15.0,
            value=5.0,  # INCREASED: 5s LinkedIn-safe default (avoids 429)
            step=0.5,
            help="Base delay between jobs. Higher = safer. Recommend 5-8s for LinkedIn.",
        )

        # Sequential mode toggle - most reliable option
        sequential_mode = st.checkbox(
            "🔒 Sequential Mode (Recommended)",
            value=True,  # Default ON for reliability
            help="Process jobs one at a time. More reliable but slower. Turn OFF for parallel multi-tab processing.",
        )

    # Calculate effective delay (scales with tabs)
    slot_delay_factor = 1.0 + 0.1 * (num_slots - 1)
    effective_delay = delay_seconds * slot_delay_factor
    min_delay = max(4.0, 4.0 + 0.2 * (num_slots - 1))  # INCREASED min delays

    with col4:
        if sequential_mode:
            st.markdown("### 🔒 Sequential Mode (Reliable)")
            st.markdown(
                f"**Mode:** 1 job at a time\n\n"
                f"**Delay:** {delay_seconds:.1f}s between jobs\n\n"
                f"**Speed:** ~{60 / (delay_seconds + 2):.0f} jobs/min"
            )
        else:
            st.markdown("### ⚡ Parallel Mode (Faster)")
            st.markdown(
                f"**Tabs:** {num_slots} → **Effective delay:** {effective_delay:.1f}s\n\n"
                f"**Min delay:** {min_delay:.1f}s (after 15 successes)"
            )

    # Database stats
    db_ops = JobStorageOperations(db_path)
    unscraped_count = len(
        db_ops.get_unscraped_urls(platform.lower(), job_role, limit=10000)
    )
    total_jobs = len(db_ops.get_all_jobs())

    # Calculate speed using effective delay and parallel tabs
    # With parallel tabs, throughput = tabs / effective_delay
    jobs_per_minute = (num_slots * 60) / (2 + effective_delay)

    col_stat1, col_stat2, col_stat3 = st.columns(3)
    col_stat1.metric("🔗 Unscraped URLs", unscraped_count)
    col_stat2.metric("✅ Jobs Stored", total_jobs)
    col_stat3.metric("⚡ Speed", f"~{jobs_per_minute:.0f}/min")

    st.divider()

    # Processing controls
    if unscraped_count == 0:
        st.warning(
            f"⚠️ No unscraped URLs for {job_role} on {platform}. Go to **Link Scraper** tab first!"
        )
    else:
        actual_batch = min(batch_size, unscraped_count)

        if sequential_mode:
            # Sequential: time = jobs * (delay + processing_time)
            estimated_time = actual_batch * (delay_seconds + 2) / 60
            mode_info = "🔒 **Sequential mode** - Most reliable, one job at a time"
        else:
            # Parallel processing: time = jobs / (tabs / effective_delay_per_job)
            estimated_time = actual_batch * (2 + effective_delay) / (60 * num_slots)
            mode_info = f"⚡ **Parallel mode** - {num_slots} tabs working together"

        st.info(
            f"📊 Will process **{actual_batch}** URLs\n\n"
            f"⏱️ Estimated time: **{estimated_time:.1f} minutes**\n\n"
            f"{mode_info}"
        )

        # Create buttons in columns - Start and Stop
        btn_col1, btn_col2 = st.columns([3, 1])

        # Check if scraping is pending (two-phase start)
        is_pending = st.session_state.get("start_scraping_pending", False)
        is_running = st.session_state.get("scraper_running", False)

        with btn_col1:
            process_button = st.button(
                f"📝 Scrape {actual_batch} Jobs",
                type="primary",
                use_container_width=True,
                disabled=unscraped_count == 0 or is_running or is_pending,
            )

        with btn_col2:
            stop_button = st.button(
                "⏹️ STOP",
                type="primary" if (is_running or is_pending) else "secondary",
                use_container_width=True,
                disabled=not (is_running or is_pending),
                help="Immediately stop scraper and close browser",
            )

        # Handle stop button click
        if stop_button:
            # Cancel pending start if not yet running
            if is_pending and not is_running:
                st.session_state.start_scraping_pending = False
                st.session_state.scrape_params = None
                st.warning("⏹️ Scraping cancelled before start.")
                st.rerun()
            elif stop_scraper():
                st.session_state.start_scraping_pending = False
                st.warning("⏹️ Stopping scraper... Please wait.")
                st.rerun()

        # PHASE 1: Button clicked - save params and rerun to show enabled STOP button
        if process_button:
            st.session_state.start_scraping_pending = True
            st.session_state.scraper_running = True
            st.session_state.scrape_params = {
                "platform": platform,
                "job_role": job_role,
                "batch_size": batch_size,
                "num_slots": num_slots,
                "delay_seconds": delay_seconds,
                "sequential_mode": sequential_mode,
            }
            st.rerun()  # Rerun to show enabled STOP button

        # PHASE 2: Pending start - now actually run the scraper (STOP button is already enabled)
        should_start_scraping = (
            is_pending and st.session_state.get("scrape_params") is not None
        )

        if should_start_scraping:
            # Clear pending flag
            st.session_state.start_scraping_pending = False
            params = st.session_state.scrape_params

            # Create placeholders for real-time updates
            progress_bar = st.progress(0, text="Initializing...")

            # Status container
            status_container = st.container()

            # Create columns for live stats
            stat_cols = st.columns(5)
            success_metric = stat_cols[0].empty()
            expired_metric = stat_cols[1].empty()
            failed_metric = stat_cols[2].empty()
            processed_metric = stat_cols[3].empty()
            delay_metric = stat_cols[4].empty()

            # Latest job info
            latest_job_container = st.empty()

            # Slot Monitor - detailed per-slot logging
            st.markdown("---")
            slot_monitor = SlotMonitor(params["num_slots"])
            slot_monitor.setup_ui()
            st.markdown("---")

            # Initialize stats (using dict for mutable state)
            stats = {"success": 0, "expired": 0, "failed": 0, "processed": 0}
            state = {
                "current_delay": params["delay_seconds"]
            }  # Mutable container for adaptive delay
            total_jobs_count = params["batch_size"]

            def update_metrics():
                success_metric.metric("✅ Success", stats["success"])
                expired_metric.metric("🗑️ Expired", stats["expired"])
                failed_metric.metric("❌ Failed", stats["failed"])
                processed_metric.metric(
                    "📊 Processed", f"{stats['processed']}/{total_jobs_count}"
                )
                delay_metric.metric("⚡ Delay", f"{state['current_delay']:.2f}s")

            update_metrics()

            try:
                # Stream progress events (using saved params)
                generator = stream_scraper_progress(
                    platform=params["platform"],
                    job_role=params["job_role"],
                    batch_size=params["batch_size"],
                    concurrent_tabs=params["num_slots"],
                    delay_seconds=params["delay_seconds"],
                    sequential=params["sequential_mode"],
                )

                final_result = None

                for event in generator:
                    event_type: str = event.get("event", "")

                    # Pass ALL slot-related events to the SlotMonitor
                    if event_type.startswith("slot_") or event_type in [
                        "job_dispatch",
                        "job_complete",
                        "deadlock_warning",
                    ]:
                        # TypedDict structural mismatch with Mapping - types are verified at definition
                        slot_monitor.update_from_event(event)  # type: ignore[arg-type]

                    if event_type == "scraper_start":
                        total_jobs_count = event.get("total_jobs") or actual_batch
                        num_slots_val: int = event.get("num_slots") or num_slots
                        with status_container:
                            st.info(
                                f"Started scraping {total_jobs_count} jobs with {num_slots_val} slots"
                            )
                            st.caption(
                                "Content-based filtering: Non-English content will be skipped automatically"
                            )

                    elif event_type == "job_dispatch":
                        slot_id_val: int = event.get("slot_id") or 0
                        job_index_val: int = event.get("job_index") or 0
                        progress: float = (
                            job_index_val / total_jobs_count
                            if total_jobs_count > 0
                            else 0.0
                        )
                        progress_bar.progress(
                            progress,
                            text=f"Dispatching Job {job_index_val}/{total_jobs_count} -> Slot {slot_id_val}",
                        )

                    elif event_type == "job_complete":
                        slot_id_val = event.get("slot_id") or 0
                        status_val: str = event.get("status") or "unknown"
                        job_index_val = event.get("job_index") or 0

                        # Update stats from event
                        event_stats = event.get("stats")
                        if event_stats is not None:
                            stats.update(event_stats)

                        # Update adaptive delay from event
                        current_delay_val = event.get("current_delay")
                        if current_delay_val is not None:
                            state["current_delay"] = current_delay_val

                        # Update progress
                        progress = (
                            stats["processed"] / total_jobs_count
                            if total_jobs_count > 0
                            else 0
                        )
                        progress_bar.progress(
                            min(progress, 1.0),
                            text=f"Processed {stats['processed']}/{total_jobs_count}",
                        )

                        # Update metrics
                        update_metrics()

                        # Show adaptive throttle notification
                        if event.get("delay_changed"):
                            with status_container:
                                st.success(
                                    f"Adaptive throttle: delay reduced to {state['current_delay']:.2f}s"
                                )

                        # Show latest job info for successful jobs
                        if status_val == "success":
                            company_val: str = event.get("company") or ""
                            title_val: str = event.get("title") or ""
                            skills_count_val: int = event.get("skills_count") or 0
                            latest_job_container.success(
                                f"**Latest:** {title_val[:40]} @ {company_val[:30]} ({skills_count_val} skills)"
                            )
                        elif status_val == "expired":
                            # Could be expired OR non-English content
                            error_msg: str = event.get("error") or ""
                            if "Non-English" in error_msg or "CJK" in error_msg:
                                latest_job_container.info(f"Skipped: {error_msg[:40]}")
                            else:
                                job_id_val: str = event.get("job_id") or ""
                                latest_job_container.warning(
                                    f"Job {job_id_val[:20]}... expired/removed"
                                )
                        elif status_val == "error":
                            error_msg_val: str = event.get("error") or "Unknown"
                            latest_job_container.error(f"Error: {error_msg_val[:50]}")

                    elif event_type == "deadlock_warning":
                        # Show deadlock warning prominently
                        message_val: str = event.get("message") or "All slots busy"
                        with status_container:
                            st.error(f"**POTENTIAL DEADLOCK**: {message_val}")

                    elif event_type == "rate_limit":
                        wait_time: float = event.get("wait_seconds") or 30.0
                        with status_container:
                            st.warning(
                                f"Rate limit detected - backing off for {wait_time:.0f}s"
                            )

                    elif event_type == "cookie_expired":
                        message_val = event.get("message") or "Cookies may be expired!"
                        with status_container:
                            st.error(f"**COOKIES EXPIRED**: {message_val}")
                            st.warning(
                                "Please refresh your LinkedIn cookies in `linkedin_cookies.json` and restart the scraper."
                            )

                    elif event_type == "session_valid":
                        with status_container:
                            st.success("LinkedIn session validated successfully")

                    elif event_type == "scraper_finish":
                        stats.update(
                            {
                                "success": event.get("success") or 0,
                                "expired": event.get("expired") or 0,
                                "failed": event.get("failed") or 0,
                                "processed": event.get("total_processed") or 0,
                            }
                        )
                        update_metrics()
                        progress_bar.progress(1.0, text="Complete!")

                # Get final result (returned from generator)
                try:
                    final_result = generator.send(None)
                except StopIteration as e:
                    final_result = e.value

                if final_result:
                    error_result: str | None = final_result.get("error")
                    if error_result:
                        st.error(f"Error: {error_result}")
                    else:
                        jobs_scraped_result: int = (
                            final_result.get("jobs_scraped") or stats["success"]
                        )
                        st.success(
                            f"**Scraping Complete!**\n\n"
                            f"- Jobs scraped: **{jobs_scraped_result}**\n"
                            f"- Expired removed: **{stats['expired']}**\n"
                            f"- Failed: **{stats['failed']}**"
                        )
                        st.balloons()

            except Exception as e:
                st.error(f"Error during scraping: {e!s}")

            # Refresh button
            if st.button("Refresh Stats", use_container_width=True):
                st.rerun()
