#!/bin/bash
# ============================================================================
# LAYER 2: False Positive Detection
# Finds skills extracted but patterns don't match in job description
# Reads patterns from skills_reference_2026.json
# ============================================================================

set -e

# Use relative paths (run from project root)
DB_PATH="${1:-data/jobs.db}"
SAMPLE_SIZE="${2:-500}"
SKILLS_REF="src/config/skills_reference_2026.json"

echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║           LAYER 2: FALSE POSITIVE DETECTION                          ║"
echo "║           Using patterns from skills_reference_2026.json             ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""
echo "Database: $DB_PATH"
echo "Sample Size: $SAMPLE_SIZE jobs"
echo ""

# Run FP detection using Node.js
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

// Get jobs with extracted skills
const jobs = db.prepare('SELECT job_id, skills, job_description FROM jobs WHERE skills IS NOT NULL AND job_description IS NOT NULL LIMIT $SAMPLE_SIZE').all();

console.log('Analyzing ' + jobs.length + ' jobs for false positives...');
console.log('');

// Track FP by skill
const fpCounts = {};
let totalFP = 0;

for (const job of jobs) {
    const extractedSkills = job.skills.split(',').map(s => s.trim().toLowerCase()).filter(s => s);
    const jd = job.job_description || '';

    for (const skillLower of extractedSkills) {
        const skillData = skillPatterns[skillLower];
        if (!skillData) continue;

        // Check if pattern actually matches in JD
        let foundInJD = false;
        for (const regex of skillData.regexes) {
            if (regex.test(jd)) {
                foundInJD = true;
                break;
            }
        }

        if (!foundInJD) {
            // FP: Extracted but pattern doesn't match
            const name = skillData.name;
            fpCounts[name] = (fpCounts[name] || 0) + 1;
            totalFP++;
        }
    }
}

// Sort by FP count
const sorted = Object.entries(fpCounts).sort((a, b) => b[1] - a[1]);

console.log('┌────────────────────────────────────────────────────────────────┐');
console.log('│ Skill                      │ False Positives                  │');
console.log('├────────────────────────────────────────────────────────────────┤');

for (const [skill, count] of sorted.slice(0, 20)) {
    const skillPad = skill.substring(0, 26).padEnd(26);
    const countPad = String(count).padStart(10);
    console.log('│ ' + skillPad + ' │ ' + countPad + '                      │');
}

console.log('└────────────────────────────────────────────────────────────────┘');
console.log('');
console.log('Total False Positives: ' + totalFP);
console.log('Skills with FP issues: ' + sorted.length);

if (sorted.length > 0) {
    console.log('');
    console.log('⚠ Top FP skills may have overly broad patterns - review needed');
}

db.close();
"

echo ""
echo "✓ Layer 2 FP detection complete"
