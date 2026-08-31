# Job Scraper & Analytics Dashboard

**Automated job data pipeline** for LinkedIn with intelligent skill extraction and real-time analytics.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Playwright](https://img.shields.io/badge/playwright-1.40+-2EAD33?logo=playwright&logoColor=white)](https://playwright.dev/)
[![Streamlit](https://img.shields.io/badge/streamlit-1.28+-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io/)
[![SQLite](https://img.shields.io/badge/sqlite-3-003B57?logo=sqlite&logoColor=white)](https://sqlite.org/)

---

## Overview

A production-ready job scraping system that collects job listings from LinkedIn, extracts technical skills using regex-based pattern matching, and provides interactive analytics through a Streamlit dashboard.

### Key Capabilities

| Feature | Description |
|---------|-------------|
| **Two-Phase Scraping** | Separate URL collection and detail extraction for resilience |
| **3-Layer Skill Extraction** | 977 skills with regex patterns, minimal false positives |
| **150 Role Categories** | Automatic role normalization with pattern matching |
| **Real-Time Analytics** | Interactive charts, skill trends, and export capabilities |
| **Adaptive Rate Limiting** | Circuit breaker with auto-tuning concurrency (2-10 workers) |
| **Resume Capability** | Checkpoint-based recovery from interruptions |

---

## Project Structure

```
Job_Scrapper/
├── README.md                     # This file
├── requirements.txt              # Production dependencies
├── requirements-dev.txt          # Development dependencies
├── .gitignore                    # Git ignore rules
│
├── code/                         # All source code
│   ├── streamlit_app.py          # Main dashboard entry point
│   ├── run_scraper.py            # CLI scraper runner
│   ├── save_linkedin_cookies.py  # LinkedIn authentication helper
│   ├── setup_playwright.sh       # Playwright browser installer (WSL/Linux)
│   │
│   ├── data/
│   │   ├── jobs.db               # SQLite database (auto-created)
│   │   └── Analysis_Report/      # Generated analysis reports
│   │       ├── Data_Analyst/
│   │       ├── Data_Engineer/
│   │       └── GenAI_DataScience/
│   │
│   ├── src/
│   │   ├── config/               # Configuration files
│   │   │   ├── skills_reference_2026.json   # 977 skills with regex patterns
│   │   │   ├── roles_reference_2026.json    # 150 role categories
│   │   │   ├── countries.py      # Country/location mappings
│   │   │   └── naukri_locations.py
│   │   │
│   │   ├── db/                   # Database layer
│   │   │   ├── connection.py     # SQLite connection manager
│   │   │   ├── schema.py         # Table schemas
│   │   │   └── operations.py     # CRUD operations
│   │   │
│   │   ├── models/
│   │   │   └── models.py         # Pydantic data models
│   │   │
│   │   ├── scraper/
│   │   │   ├── unified/
│   │   │   │   ├── linkedin/     # LinkedIn scraper components
│   │   │   │   │   ├── concurrent_detail_scraper.py  # Multi-tab scraper (up to 10 tabs)
│   │   │   │   │   ├── sequential_detail_scraper.py  # Single-tab scraper
│   │   │   │   │   ├── playwright_url_scraper.py     # URL collection
│   │   │   │   │   ├── selector_config.py            # CSS selectors
│   │   │   │   │   ├── retry_helper.py               # 404/503 handling
│   │   │   │   │   └── job_validator.py              # Field validation
│   │   │   │   │
│   │   │   │   ├── naukri/       # Naukri scraper components
│   │   │   │   │   ├── url_scraper.py
│   │   │   │   │   ├── detail_scraper.py
│   │   │   │   │   └── selectors.py
│   │   │   │   │
│   │   │   │   ├── scalable/     # Rate limiting & resilience
│   │   │   │   │   ├── adaptive_rate_limiter.py
│   │   │   │   │   ├── checkpoint_manager.py
│   │   │   │   │   └── progress_tracker.py
│   │   │   │   │
│   │   │   │   ├── linkedin_unified.py   # LinkedIn orchestrator
│   │   │   │   └── naukri_unified.py     # Naukri orchestrator
│   │   │   │
│   │   │   └── services/         # External service clients
│   │   │       ├── playwright_browser.py
│   │   │       └── session_manager.py
│   │   │
│   │   ├── analysis/
│   │   │   └── skill_extraction/ # 3-layer skill extraction
│   │   │       ├── extractor.py           # Main AdvancedSkillExtractor class
│   │   │       ├── layer3_direct.py       # Pattern matching from JSON
│   │   │       ├── batch_reextract.py     # Re-process existing jobs
│   │   │       └── deduplicator.py        # Skill normalization
│   │   │
│   │   ├── ui/
│   │   │   └── components/       # Streamlit UI components
│   │   │       ├── kpi_dashboard.py
│   │   │       ├── link_scraper_form.py
│   │   │       ├── detail_scraper_form.py
│   │   │       └── analytics/
│   │   │           ├── skills_charts.py
│   │   │           └── overview_metrics.py
│   │   │
│   │   ├── utils/
│   │   │   └── cleanup_expired_urls.py
│   │   │
│   │   └── validation/
│   │       ├── validation_pipeline.py
│   │       └── single_job_validator.py
│   │
│   ├── scripts/
│   │   ├── extraction/
│   │   │   └── reextract_skills.py
│   │   │
│   │   └── validation/           # Validation suite
│   │       ├── layer1_syntax_check.sh
│   │       ├── layer2_coverage.sh
│   │       ├── layer3_fp_detection.sh
│   │       ├── layer4_fn_detection.sh
│   │       ├── cross_verify_skills.py
│   │       └── run_all_validations.sh
│   │
│   ├── tests/
│   │   ├── test_skill_validation_comprehensive.py
│   │   └── test_linkedin_selectors.py
│   │
│   └── docs/                     # Documentation
│       └── archive/              # Historical docs
│
└── Analysis/                     # Downloaded CSVs and notebooks (gitignored)
    ├── Data Analysis/
    │   ├── data_visualizer.ipynb    # Analysis notebook (update CSV path for charts)
    │   └── csv/                     # Add exported CSVs here
    │
    ├── Data Engineering/
    │   ├── data_visualizer.ipynb
    │   └── csv/
    │
    └── GenAI & DataScience/
        ├── data_visualizer.ipynb
        └── csv/
```

---

## Installation

### Prerequisites

- Python 3.11 or higher
- Git

### Step 1: Clone & Create Virtual Environment

#### Windows (PowerShell)

```powershell
git clone https://github.com/Gaurav-Wankhede/Job-Scrapper.git
cd Job-Scrapper

# Create virtual environment
python -m venv venv-win

# Activate
.\venv-win\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

#### Linux / WSL

```bash
git clone https://github.com/Gaurav-Wankhede/Job-Scrapper.git
cd Job-Scrapper

# Create virtual environment
python3 -m venv venv-linux

# Activate
source venv-linux/bin/activate

# Install dependencies
python -m pip install -r requirements.txt
```

**Note for dual-boot users:** Keep separate venvs (`venv-win/` and `venv-linux/`) as Python virtual environments are not cross-platform compatible.

### Step 2: Install Playwright Browsers

```bash
# Windows
playwright install chromium

# Linux/WSL (use python -m prefix)
python -m playwright install chromium
```

### Step 3: Launch Dashboard

```bash
cd code

# Windows
streamlit run streamlit_app.py

# Linux/WSL (use python -m prefix)
python -m streamlit run streamlit_app.py
```

The dashboard opens at `http://localhost:8501`

---

## Architecture

### Why Two-Phase Scraping?

```
Phase 1: URL Collection          Phase 2: Detail Scraping
┌─────────────────────┐         ┌─────────────────────┐
│  Search Results     │         │  Individual Jobs    │
│  ├── Fast scroll    │   ──▶   │  ├── Full desc      │
│  ├── Extract URLs   │         │  ├── Skills parse   │
│  └── Store to DB    │         │  └── Store details  │
└─────────────────────┘         └─────────────────────┘
      job_urls table                  jobs table
```

**Benefits:**
- **Resilience**: If detail scraping fails, URLs are preserved
- **Efficiency**: Batch process up to 10 jobs concurrently in Phase 2
- **Resumable**: Pick up exactly where you left off
- **Deduplication**: Skip already-scraped URLs automatically

### Why Regex-Based Skill Extraction?

| Approach | Speed | Accuracy | Maintenance |
|----------|-------|----------|-------------|
| **Regex (chosen)** | 0.3s/job | 85-90% | Pattern file updates |
| spaCy NER | 3-5s/job | 75-80% | Model retraining |
| GPT-based | 2-10s/job | 90%+ | API costs |

**Our 3-layer approach achieves 85-90% accuracy at 10x speed of NLP:**

1. **Layer 1**: Multi-word phrase extraction (priority matching)
2. **Layer 2**: Context-aware extraction (technical context detection)
3. **Layer 3**: Direct pattern matching (977 skill patterns from JSON)

---

## Usage

### Dashboard Workflow

1. **KPI Dashboard** - View overall statistics
2. **Link Scraper** - Phase 1: Collect job URLs
3. **Detail Scraper** - Phase 2: Extract job details & skills
4. **Analytics** - Analyze skill trends and export data

### Command Line

```bash
cd code

# Run validation suite
bash scripts/validation/run_all_validations.sh

# Re-extract skills for existing jobs
python -m src.analysis.skill_extraction.batch_reextract --batch-size 100
```

### LinkedIn Authentication (Optional)

For authenticated scraping with higher limits:

```bash
cd code
python save_linkedin_cookies.py
```

This saves cookies to `linkedin_cookies.json` for subsequent sessions.

---

## Configuration

### Skills Reference (`code/src/config/skills_reference_2026.json`)

```json
{
  "total_skills": 977,
  "skills": [
    {
      "name": "Python",
      "patterns": ["\\bPython\\b", "\\bpython\\b", "\\bPython3\\b"]
    }
  ]
}
```

### Environment Variables (Optional)

Create `.env` file in `code/` directory:

```env
# Database path (default: data/jobs.db)
DB_PATH=data/jobs.db

# Playwright browser path (for WSL)
PLAYWRIGHT_BROWSERS_PATH=.playwright-browsers
```

---

## Database Schema

```sql
-- Phase 1: URL Collection
CREATE TABLE job_urls (
    job_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    input_role TEXT NOT NULL,
    actual_role TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    scraped INTEGER DEFAULT 0
);

-- Phase 2: Full Details
CREATE TABLE jobs (
    job_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    actual_role TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    job_description TEXT,
    skills TEXT,
    company_name TEXT,
    posted_date TEXT,
    scraped_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

## Performance

| Metric | Value |
|--------|-------|
| URL Collection | 200-300 URLs/min |
| Detail Scraping | 15-20 jobs/min (10 workers) |
| Skill Extraction | 0.3s/job |
| Storage per Job | ~2KB |

---

## Troubleshooting

### Playwright Browser Not Found (WSL/Linux)

```bash
cd code
chmod +x setup_playwright.sh
./setup_playwright.sh
```

### "python" command not found (Linux)

Use `python3` or the `python -m` prefix:
```bash
python3 -m streamlit run streamlit_app.py
python3 -m pip install package_name
```

### Rate Limited (429 Errors)

The adaptive rate limiter handles this automatically:
- Concurrency reduces from 10 → 2
- Circuit breaker triggers 60s pause
- Gradually recovers when stable

### Database Locked

```bash
pkill -f streamlit
python -m streamlit run streamlit_app.py
```

---

## Development

### Install Dev Dependencies

```bash
pip install -r requirements-dev.txt
```

### Run Tests

```bash
cd code
python -m pytest tests/ -v
```

### Type Checking

```bash
cd code
python -m basedpyright src/
```

---

## License

MIT License - See [LICENSE](LICENSE) file for details.
