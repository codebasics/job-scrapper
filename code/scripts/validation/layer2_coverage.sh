#!/bin/bash
# ============================================================================
# LAYER 1: Pattern Validation - Quick grep-based pattern matching
# Reads patterns from skills_reference_2026.json
# Uses Node.js for DB access + pattern matching
# ============================================================================

set -e

# Use relative paths (run from project root)
DB_PATH="${1:-data/jobs.db}"
SAMPLE_SIZE="${2:-500}"
SKILLS_REF="src/config/skills_reference_2026.json"

echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║           LAYER 1: PATTERN VALIDATION                                ║"
echo "║           Using patterns from skills_reference_2026.json             ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""
echo "Database: $DB_PATH"
echo "Skills Reference: $SKILLS_REF"
echo "Sample Size: $SAMPLE_SIZE jobs"
echo ""

# Run validation using Node.js
node -e "
const Database = require('better-sqlite3');
const fs = require('fs');

const db = new Database('$DB_PATH', { readonly: true });
const skillsData = JSON.parse(fs.readFileSync('$SKILLS_REF', 'utf8'));

// Get sample jobs
const jobs = db.prepare('SELECT job_id, job_description FROM jobs WHERE job_description IS NOT NULL LIMIT $SAMPLE_SIZE').all();

console.log('Loaded ' + jobs.length + ' job descriptions');
console.log('Checking ' + skillsData.skills.length + ' skill patterns...');
console.log('');
console.log('┌────────────────────────────────────────────────────────────────┐');
console.log('│ Skill                      │ Matches    │ Coverage (%)        │');
console.log('├────────────────────────────────────────────────────────────────┤');

// Check top 30 most important skills (by coverage)
const results = [];

for (const skill of skillsData.skills) {
    if (!skill.patterns || skill.patterns.length === 0) continue;

    let matchCount = 0;
    const regexes = skill.patterns.map(p => {
        try { return new RegExp(p, 'i'); } catch(e) { return null; }
    }).filter(r => r);

    for (const job of jobs) {
        const jd = job.job_description || '';
        for (const regex of regexes) {
            if (regex.test(jd)) {
                matchCount++;
                break;
            }
        }
    }

    if (matchCount > 0) {
        results.push({ skill: skill.name, matches: matchCount, pct: (matchCount / jobs.length * 100).toFixed(1) });
    }
}

// Sort by matches descending, show top 30
results.sort((a, b) => b.matches - a.matches);
for (const r of results.slice(0, 30)) {
    const skillPad = r.skill.substring(0, 26).padEnd(26);
    const matchPad = String(r.matches).padStart(10);
    const pctPad = (r.pct + '%').padStart(18);
    console.log('│ ' + skillPad + ' │ ' + matchPad + ' │' + pctPad + ' │');
}

console.log('└────────────────────────────────────────────────────────────────┘');
console.log('');
console.log('Total skills with matches: ' + results.length);
console.log('Total pattern checks: ' + results.reduce((sum, r) => sum + r.matches, 0));

db.close();
"

echo ""
echo "✓ Layer 1 validation complete"
