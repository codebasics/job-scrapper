"""
Real-time Validation Module
Calls Node.js validation directly for speed - NO PYTHON PATTERN MATCHING
"""

import json
import subprocess
from pathlib import Path


def validate_skills_via_node(
    job_description: str,
    skills_reference_path: str = "src/config/skills_reference_2026.json",
) -> list[str]:
    """
    Validate and extract skills using Node.js for speed.
    This bypasses Python regex entirely - uses the same patterns as .sh validation layers.

    Args:
        job_description: The job description text
        skills_reference_path: Path to skills reference JSON

    Returns:
        List of validated skills (pattern-matched only)
    """
    if not job_description or not job_description.strip():
        return []

    # Escape job description for shell and JS template literals
    escaped_jd = (
        job_description.replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("$", "\\$")
        .replace("'", "'\\''")
    )

    # Node.js inline script for fast pattern matching
    node_script = f"""
const fs = require('fs');
const skillsData = JSON.parse(fs.readFileSync('{skills_reference_path}', 'utf8'));

const text = `{escaped_jd}`;

const found = new Set();
for (const skill of skillsData.skills) {{
    for (const p of skill.patterns || []) {{
        try {{
            if (new RegExp(p, 'i').test(text)) {{
                found.add(skill.name);
                break;
            }}
        }} catch (e) {{}}
    }}
}}

console.log(JSON.stringify([...found].sort()));
"""

    try:
        result = subprocess.run(
            ["node", "-e", node_script],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(
                Path(skills_reference_path).parent.parent.parent
            ),  # code/ directory
        )

        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout.strip())
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        pass

    return []


def validate_skills(
    job_description: str,
    extracted_skills: list[str],
    skills_reference_path: str = "src/config/skills_reference_2026.json",
) -> list[str]:
    """
    Validate extracted skills - returns ONLY pattern-matched skills.
    Uses Node.js for speed, same as .sh validation layers.
    """
    return validate_skills_via_node(job_description, skills_reference_path)
