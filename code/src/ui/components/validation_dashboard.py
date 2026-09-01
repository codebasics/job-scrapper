"""
Validation Dashboard Component
Full batch validation and fix for all jobs in database
Uses Node.js for speed (~60+ jobs/sec)
"""

import json
import sqlite3
import subprocess
from pathlib import Path

import streamlit as st


def get_job_count(db_path: str) -> int:
    """Get total job count with descriptions"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM jobs WHERE job_description IS NOT NULL")
    count = cursor.fetchone()[0]
    conn.close()
    return count


def run_batch_validation(
    db_path: str,
    skills_ref_path: str,
    progress_callback,
    batch_size: int = 500,
) -> dict:
    """
    Run batch validation using Node.js for speed.
    Returns stats dict with FP/FN counts.
    """
    # Node.js script for batch validation with progress
    node_script = f"""
const fs = require('fs');
const Database = require('better-sqlite3');

const skillsData = JSON.parse(fs.readFileSync('{skills_ref_path}', 'utf8'));

// Build skill pattern map
const skillPatterns = [];
for (const skill of skillsData.skills) {{
    const regexes = [];
    for (const p of skill.patterns || []) {{
        try {{ regexes.push(new RegExp(p, 'i')); }} catch (e) {{}}
    }}
    if (regexes.length) {{
        skillPatterns.push({{ name: skill.name, regexes }});
    }}
}}

function findSkillsInText(text) {{
    const found = new Set();
    for (const {{ name, regexes }} of skillPatterns) {{
        for (const r of regexes) {{
            if (r.test(text)) {{
                found.add(name);
                break;
            }}
        }}
    }}
    return found;
}}

const db = new Database('{db_path}');
const totalCount = db.prepare('SELECT COUNT(*) as c FROM jobs WHERE job_description IS NOT NULL').get().c;

const selectStmt = db.prepare('SELECT job_id, job_description, skills FROM jobs WHERE job_description IS NOT NULL');
const updateStmt = db.prepare('UPDATE jobs SET skills = ? WHERE job_id = ?');

let processed = 0;
let updated = 0;
let totalFpRemoved = 0;
let totalFnAdded = 0;
const fpCounts = {{}};
const fnCounts = {{}};

db.exec('BEGIN');

for (const job of selectStmt.iterate()) {{
    const jobDesc = job.job_description || '';
    const oldSkills = new Set((job.skills || '').split(',').map(s => s.trim()).filter(s => s));
    const patternMatchedSkills = findSkillsInText(jobDesc);

    const falsePositives = [...oldSkills].filter(s => !patternMatchedSkills.has(s));
    const falseNegatives = [...patternMatchedSkills].filter(s => !oldSkills.has(s));

    for (const fp of falsePositives) {{
        fpCounts[fp] = (fpCounts[fp] || 0) + 1;
        totalFpRemoved++;
    }}
    for (const fn of falseNegatives) {{
        fnCounts[fn] = (fnCounts[fn] || 0) + 1;
        totalFnAdded++;
    }}

    const newSkills = [...patternMatchedSkills].sort().join(', ');
    const oldSkillsStr = [...oldSkills].sort().join(', ');

    if (newSkills !== oldSkillsStr) {{
        updateStmt.run(newSkills, job.job_id);
        updated++;
    }}

    processed++;

    if (processed % {batch_size} === 0) {{
        console.log(JSON.stringify({{
            type: 'progress',
            processed,
            total: totalCount,
            updated,
            fpRemoved: totalFpRemoved,
            fnAdded: totalFnAdded
        }}));
    }}
}}

db.exec('COMMIT');
db.close();

// Top 10 FP and FN
const topFps = Object.entries(fpCounts).sort((a, b) => b[1] - a[1]).slice(0, 10);
const topFns = Object.entries(fnCounts).sort((a, b) => b[1] - a[1]).slice(0, 10);

console.log(JSON.stringify({{
    type: 'complete',
    processed,
    total: totalCount,
    updated,
    fpRemoved: totalFpRemoved,
    fnAdded: totalFnAdded,
    topFps,
    topFns
}}));
"""

    # Run Node.js script
    code_dir = Path(db_path).parent.parent
    process = subprocess.Popen(
        ["node", "-e", node_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(code_dir),
    )

    result = {
        "processed": 0,
        "total": 0,
        "updated": 0,
        "fpRemoved": 0,
        "fnAdded": 0,
        "topFps": [],
        "topFns": [],
        "error": None,
    }

    # Read output line by line for progress updates
    for line in process.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            if data.get("type") == "progress":
                progress_callback(
                    data["processed"],
                    data["total"],
                    data["updated"],
                    data["fpRemoved"],
                    data["fnAdded"],
                )
            elif data.get("type") == "complete":
                result = data
                progress_callback(
                    data["processed"],
                    data["total"],
                    data["updated"],
                    data["fpRemoved"],
                    data["fnAdded"],
                )
        except json.JSONDecodeError:
            pass

    process.wait()

    if process.returncode != 0:
        stderr = process.stderr.read()
        result["error"] = stderr

    return result


def render_validation_dashboard(db_path: str) -> None:
    """Render the validation dashboard UI"""
    st.header("🔧 Skill Validation & Fix")
    st.markdown("**Re-validate all jobs and fix False Positives / False Negatives**")

    # Get job count
    job_count = get_job_count(db_path)

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Total Jobs with Descriptions", f"{job_count:,}")
    with col2:
        est_time = job_count / 60  # ~60 jobs/sec
        st.metric("Estimated Time", f"~{est_time:.0f} seconds")

    st.divider()

    # Validation controls
    st.subheader("Run Full Validation")
    st.markdown(
        """
    This will:
    1. Re-extract skills from ALL job descriptions using pattern matching
    2. Compare with existing skills to find FP (False Positives) and FN (False Negatives)
    3. Update the database with corrected skills
    """
    )

    # Initialize session state for results
    if "validation_results" not in st.session_state:
        st.session_state.validation_results = None
    if "validation_running" not in st.session_state:
        st.session_state.validation_running = False

    # Run button
    if st.button(
        "🚀 Run Full Validation & Fix",
        disabled=st.session_state.validation_running,
        type="primary",
        use_container_width=True,
    ):
        st.session_state.validation_running = True

        # Progress containers
        progress_bar = st.progress(0, text="Starting validation...")
        status_container = st.empty()
        metrics_container = st.empty()

        def update_progress(processed, total, updated, fp_removed, fn_added):
            pct = processed / total if total > 0 else 0
            progress_bar.progress(
                pct, text=f"Processing... {processed:,}/{total:,} jobs"
            )
            with metrics_container.container():
                c1, c2, c3 = st.columns(3)
                c1.metric("Jobs Updated", f"{updated:,}")
                c2.metric("FP Removed", f"{fp_removed:,}")
                c3.metric("FN Added", f"{fn_added:,}")

        # Run validation
        skills_ref = "src/config/skills_reference_2026.json"
        results = run_batch_validation(
            db_path, skills_ref, update_progress, batch_size=500
        )

        st.session_state.validation_results = results
        st.session_state.validation_running = False

        if results.get("error"):
            status_container.error(f"Error: {results['error']}")
        else:
            progress_bar.progress(1.0, text="Validation complete!")
            status_container.success(
                f"Validated {results['processed']:,} jobs in database!"
            )

    # Display results if available
    if st.session_state.validation_results:
        results = st.session_state.validation_results

        if not results.get("error"):
            st.divider()
            st.subheader("Validation Results")

            # Summary metrics
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Total Processed", f"{results.get('processed', 0):,}")
            col2.metric("Jobs Updated", f"{results.get('updated', 0):,}")
            col3.metric("FP Removed", f"{results.get('fpRemoved', 0):,}")
            col4.metric("FN Added", f"{results.get('fnAdded', 0):,}")

            # Top FP and FN tables
            col_fp, col_fn = st.columns(2)

            with col_fp:
                st.markdown("**Top 10 False Positives Removed**")
                top_fps = results.get("topFps", [])
                if top_fps:
                    for skill, count in top_fps:
                        st.write(f"- {skill}: {count}")
                else:
                    st.info("No false positives found")

            with col_fn:
                st.markdown("**Top 10 False Negatives Added**")
                top_fns = results.get("topFns", [])
                if top_fns:
                    for skill, count in top_fns:
                        st.write(f"- {skill}: {count}")
                else:
                    st.info("No false negatives found")

            # Clear results button
            if st.button("Clear Results"):
                st.session_state.validation_results = None
                st.rerun()
