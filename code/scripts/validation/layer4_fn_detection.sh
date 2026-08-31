#!/bin/bash
# ============================================================================
# LAYER 3: False Negative Detection
# Finds skills mentioned in JD (by pattern) but NOT extracted
# Reads patterns from skills_reference_2026.json
# ============================================================================

set -e

# Use relative paths (run from project root)
DB_PATH="${1:-data/jobs.db}"
SAMPLE_SIZE="${2:-500}"
SKILLS_REF="src/config/skills_reference_2026.json"

echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║           LAYER 3: FALSE NEGATIVE DETECTION                          ║"
echo "║           Using patterns from skills_reference_2026.json             ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""
echo "Database: $DB_PATH"
echo "Sample Size: $SAMPLE_SIZE jobs"
echo ""

# Run FN detection using Node.js
node -e "
const Database = require('better-sqlite3');
const fs = require('fs');

const db = new Database('$DB_PATH', { readonly: true });
const skillsData = JSON.parse(fs.readFileSync('$SKILLS_REF', 'utf8'));

// Build skill pattern map
const skillPatterns = {};
for (const skill of skillsData.skills) {
    if (skill.patterns && skill.patterns.length > 0) {
        const regexes = skill.patterns.map(p => {
            try { return new RegExp(p, 'i'); } catch(e) { return null; }
        }).filter(r => r);
        if (regexes.length > 0) {
            skillPatterns[skill.name.toLowerCase()] = { name: skill.name, regexes };
        }
    }
}

// Get jobs
const jobs = db.prepare('SELECT job_id, skills, job_description FROM jobs WHERE job_description IS NOT NULL LIMIT $SAMPLE_SIZE').all();

console.log('Analyzing ' + jobs.length + ' jobs for false negatives...');
console.log('');

// Track FN by skill
const fnCounts = {};
const inJdCounts = {};
const extractedCounts = {};
let totalFN = 0;

for (const job of jobs) {
    const extractedSkills = new Set((job.skills || '').split(',').map(s => s.trim().toLowerCase()).filter(s => s));
    const jd = job.job_description || '';

    // Check each skill pattern against JD
    for (const [skillLower, { name, regexes }] of Object.entries(skillPatterns)) {
        let foundInJD = false;
        for (const regex of regexes) {
            if (regex.test(jd)) {
                foundInJD = true;
                break;
            }
        }

        if (foundInJD) {
            inJdCounts[name] = (inJdCounts[name] || 0) + 1;

            if (extractedSkills.has(skillLower)) {
                extractedCounts[name] = (extractedCounts[name] || 0) + 1;
            } else {
                // FN: In JD but not extracted
                fnCounts[name] = (fnCounts[name] || 0) + 1;
                totalFN++;
            }
        }
    }
}

// Sort by FN count
const sorted = Object.entries(fnCounts).sort((a, b) => b[1] - a[1]);

console.log('┌────────────────────────────────────────────────────────────────────────┐');
console.log('│ Skill              │ In JD     │ Extracted │ Missed (FN) │ Recall    │');
console.log('├────────────────────────────────────────────────────────────────────────┤');

for (const [skill, fnCount] of sorted.slice(0, 25)) {
    const inJd = inJdCounts[skill] || 0;
    const extracted = extractedCounts[skill] || 0;
    const recall = inJd > 0 ? ((extracted / inJd) * 100).toFixed(0) : 'N/A';

    const skillPad = skill.substring(0, 18).padEnd(18);
    const inJdPad = String(inJd).padStart(9);
    const extractedPad = String(extracted).padStart(9);
    const fnPad = String(fnCount).padStart(11);
    const recallPad = (recall + '%').padStart(9);

    console.log('│ ' + skillPad + ' │ ' + inJdPad + ' │ ' + extractedPad + ' │ ' + fnPad + ' │ ' + recallPad + ' │');
}

console.log('└────────────────────────────────────────────────────────────────────────┘');
console.log('');
console.log('Total False Negatives: ' + totalFN);
console.log('Skills with FN issues: ' + sorted.length);

if (sorted.length > 0) {
    console.log('');
    console.log('⚠ Top FN skills may need additional patterns or extraction fixes');
}

db.close();
"

echo ""
echo "✓ Layer 3 FN detection complete"
