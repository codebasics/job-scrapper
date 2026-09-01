# Link Scraper Component - Phase 1: URL Collection
# Supports single location and worldwide concurrent scraping
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TypedDict

import streamlit as st
from src.config.countries import LINKEDIN_COUNTRIES
from src.db import JobStorageOperations

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

# Windows Playwright browsers path (use lowercase for conditional assignment)
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


class ScraperResult(TypedDict):
    urls_collected: int
    urls_stored: int
    error: str | None


def run_single_location_scraper(
    platform: str, job_role: str, location: str, num_jobs: int
) -> ScraperResult:
    """Run single location scraper in subprocess"""
    # Use platform-aware paths
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
from src.db.operations import JobStorageOperations

async def scrape():
    result = {{"urls_collected": 0, "urls_stored": 0, "error": None}}

    try:
        if "{platform}".lower() == "linkedin":
            from src.scraper.unified.linkedin.infinite_scroll_scraper import scrape_linkedin_urls_infinite_scroll
            urls = await scrape_linkedin_urls_infinite_scroll(
                keyword="{job_role}",
                location="{location}",
                limit={num_jobs},
                headless=False
            )
        else:
            from src.scraper.unified.naukri.url_scraper import scrape_naukri_urls
            from src.config.naukri_locations import NAUKRI_ALL_LOCATIONS
            city_gid = NAUKRI_ALL_LOCATIONS.get("{location}")
            urls = await scrape_naukri_urls(
                keyword="{job_role}",
                location="{location}",
                limit={num_jobs},
                headless=False,
                store_to_db=False,
                city_gid=city_gid
            )

        result["urls_collected"] = len(urls) if urls else 0

        if urls:
            db = JobStorageOperations("data/jobs.db")
            stored = db.store_urls(urls)
            result["urls_stored"] = stored

    except Exception as e:
        result["error"] = str(e)

    print(json.dumps(result))

asyncio.run(scrape())
'''

    return _run_subprocess(script)


def run_worldwide_scraper(
    job_role: str, threshold: int, concurrent_tabs: int
) -> ScraperResult:
    """Run worldwide concurrent scraper in subprocess"""
    # Use platform-aware paths
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
from src.scraper.unified.linkedin.concurrent_url_scraper import scrape_worldwide_concurrent

async def scrape():
    result = {{"urls_collected": 0, "urls_stored": 0, "error": None}}

    try:
        urls = await scrape_worldwide_concurrent(
            keyword="{job_role}",
            threshold={threshold},
            max_concurrent={concurrent_tabs},
            headless=False
        )

        result["urls_collected"] = len(urls) if urls else 0
        result["urls_stored"] = result["urls_collected"]  # Already stored by the scraper

    except Exception as e:
        result["error"] = str(e)

    print(json.dumps(result))

asyncio.run(scrape())
'''

    return _run_subprocess(script, timeout=1800)  # 30 min timeout for worldwide


def _run_subprocess(script: str, timeout: int = 600) -> ScraperResult:
    """Execute script in subprocess and return result"""
    try:
        env = os.environ.copy()
        env["TMPDIR"] = TEMP_DIR
        env["TMP"] = TEMP_DIR
        env["TEMP"] = TEMP_DIR
        env["PLAYWRIGHT_BROWSERS_PATH"] = playwright_browsers_path

        result = subprocess.run(
            [VENV_PYTHON, "-c", script],  # Use venv Python for all platforms
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            env=env,
            timeout=timeout,
        )

        if result.returncode != 0:
            return ScraperResult(urls_collected=0, urls_stored=0, error=result.stderr)

        # Parse JSON output from last line
        output_lines = result.stdout.strip().split("\n")
        for line in reversed(output_lines):
            if line.startswith("{"):
                data = json.loads(line)
                return ScraperResult(
                    urls_collected=data.get("urls_collected", 0),
                    urls_stored=data.get("urls_stored", 0),
                    error=data.get("error"),
                )

        return ScraperResult(
            urls_collected=0, urls_stored=0, error="No JSON output found"
        )

    except subprocess.TimeoutExpired:
        return ScraperResult(
            urls_collected=0, urls_stored=0, error="Timeout - scraping took too long"
        )
    except Exception as e:
        return ScraperResult(urls_collected=0, urls_stored=0, error=str(e))


def render_link_scraper_form(db_path: str) -> None:
    """Render Phase 1: Link/URL collection interface"""
    st.header("🔗 Phase 1: Link Scraper")
    st.markdown("**Collect job URLs** | Visible Browser | Real-time deduplication")

    # Scraping mode selection
    scrape_mode = st.radio(
        "Scraping Mode",
        ["🌍 Worldwide (5 Concurrent Tabs)", "📍 Single Location"],
        horizontal=True,
        help="Worldwide scrapes multiple countries in parallel until threshold reached",
    )

    col1, col2 = st.columns(2)

    with col1:
        platform = st.selectbox(
            "Platform",
            ["LinkedIn", "Naukri"],
            help="Select platform to scrape URLs from",
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
            help="Select the job role to search for",
        )

    # Initialize variables with defaults
    threshold = 100
    concurrent_tabs = 5
    num_jobs = 100
    single_location = "India"

    with col2:
        if "Worldwide" in scrape_mode:
            threshold = st.number_input(
                "URL Threshold (Stop when reached)",
                min_value=50,
                max_value=5000,
                value=100,
                step=50,
                help="Scraping stops immediately when this many URLs are collected",
            )

            concurrent_tabs = st.number_input(
                "Concurrent Tabs",
                min_value=2,
                max_value=10,
                value=5,
                step=1,
                help="Number of browser tabs running in parallel",
            )
        else:
            num_jobs = st.number_input(
                "Number of URLs to Collect",
                min_value=10,
                max_value=1000,
                value=100,
                step=50,
                help="Maximum URLs to scrape from this location",
            )

            single_location = st.text_input(
                "Location", value="India", help="Location to search jobs in"
            )

    # Database stats
    db_ops = JobStorageOperations(db_path)
    existing_count = len(
        db_ops.get_unscraped_urls(platform.lower(), job_role, limit=10000)
    )

    st.info(
        f"📊 **Current Stats**: {existing_count} unscraped URLs for {job_role} on {platform}"
    )

    # Show countries for worldwide mode
    if "Worldwide" in scrape_mode:
        with st.expander(f"🌍 Countries to scrape ({len(LINKEDIN_COUNTRIES)} total)"):
            cols = st.columns(4)
            for i, country in enumerate(LINKEDIN_COUNTRIES):
                cols[i % 4].write(f"• {country}")

    # Action buttons
    st.divider()

    if "Worldwide" in scrape_mode:
        if st.button(
            f"🌍 Scrape {job_role} Worldwide ({concurrent_tabs} tabs, stop at {threshold})",
            type="primary",
            use_container_width=True,
        ):
            with st.spinner(
                f"🌐 Opening {concurrent_tabs} browser tabs to scrape {job_role} worldwide..."
            ):
                st.info(
                    f"👀 {concurrent_tabs} browser tabs will open. Scraping stops when {threshold} URLs collected."
                )
                st.warning(
                    "⏳ This may take several minutes. Do not close the browser windows."
                )

                result = run_worldwide_scraper(
                    job_role=job_role,
                    threshold=threshold,
                    concurrent_tabs=concurrent_tabs,
                )

                if result.get("error"):
                    st.error(f"❌ Error: {result['error']}")
                else:
                    st.success(
                        f"✅ Collected {result['urls_collected']} URLs worldwide!"
                    )
                    st.balloons()
                    st.rerun()
    else:
        col_btn1, col_btn2 = st.columns(2)

        with col_btn1:
            if st.button(
                f"🔗 Scrape {job_role} in {single_location}",
                type="primary",
                use_container_width=True,
            ):
                with st.spinner(
                    f"🌐 Opening browser to scrape {job_role} in {single_location}..."
                ):
                    st.info("👀 A browser window should open. Please wait...")

                    result = run_single_location_scraper(
                        platform=platform,
                        job_role=job_role,
                        location=single_location,
                        num_jobs=num_jobs,
                    )

                    if result.get("error"):
                        st.error(f"❌ Error: {result['error']}")
                    else:
                        st.success(
                            f"✅ Collected {result['urls_collected']} URLs, stored {result['urls_stored']} NEW"
                        )
                        st.rerun()

        with col_btn2:
            if st.button("🔄 Refresh Stats", use_container_width=True):
                st.rerun()
