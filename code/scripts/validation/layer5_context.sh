#!/bin/bash
# ============================================================================
# LAYER 5: Context Validation
# Validates skills appear in correct context (not negative mentions)
# Detects: "no Python required", "without Java", wrong context usage
# ============================================================================

set -e

DB_PATH="${1:-data/jobs.db}"
SAMPLE_SIZE="${2:-500}"
SKILLS_REF="src/config/skills_reference_2026.json"

echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║           LAYER 5: CONTEXT VALIDATION                                ║"
echo "║           Detecting negative mentions & wrong context                ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""
echo "Database: $DB_PATH"
echo "Sample Size: $SAMPLE_SIZE jobs"
echo ""

node -e "
const Database = require('better-sqlite3');
const fs = require('fs');

const db = new Database('$DB_PATH', { readonly: true });
const skillsData = JSON.parse(fs.readFileSync('$SKILLS_REF', 'utf8'));

// Build skill pattern map
const skillPatterns = {};
for (const skill of skillsData.skills) {
    if (skill.patterns && skill.patterns.length > 0) {
        skillPatterns[skill.name.toLowerCase()] = skill.name;
    }
}

// Negative context patterns
const negativePatterns = [
    /\b(no|not|without|don'?t|doesn'?t|won'?t|isn'?t|aren'?t)\s+(?:need|require|using|use|want|looking for)\s+/i,
    /\b(not\s+required|not\s+necessary|not\s+needed|no\s+experience\s+needed)\b/i,
    /\b(instead\s+of|rather\s+than|as\s+opposed\s+to)\b/i,
    /\b(deprecated|legacy|outdated|old)\b/i,
];

// Requirement level patterns
const requirementLevels = {
    required: /\b(required|must\s+have|essential|mandatory|need|necessary)\b/i,
    preferred: /\b(preferred|ideally|preferably|desired|would\s+be\s+nice)\b/i,
    bonus: /\b(bonus|plus|nice\s+to\s+have|advantage|added\s+benefit)\b/i,
};

// Experience level patterns
const experienceLevels = {
    senior: /\b(senior|lead|principal|staff|architect|\d+\+?\s*years?)\b/i,
    mid: /\b(mid-?level|intermediate|\d-\d\s*years?)\b/i,
    junior: /\b(junior|entry|graduate|intern|fresher|beginner)\b/i,
};

// Get jobs
const jobs = db.prepare('SELECT job_id, skills, job_description FROM jobs WHERE skills IS NOT NULL AND job_description IS NOT NULL LIMIT $SAMPLE_SIZE').all();

console.log('Analyzing ' + jobs.length + ' jobs for context issues...');
console.log('');

let negativeCount = 0;
let contextIssues = {};
let requirementStats = { required: 0, preferred: 0, bonus: 0, unspecified: 0 };
let experienceStats = { senior: 0, mid: 0, junior: 0, unspecified: 0 };

for (const job of jobs) {
    const jd = job.job_description || '';
    const jdLower = jd.toLowerCase();
    const extractedSkills = job.skills.split(',').map(s => s.trim()).filter(s => s);

    for (const skill of extractedSkills) {
        const skillLower = skill.toLowerCase();

        // Check for negative mentions
        // Look for skill name near negative patterns
        const skillRegex = new RegExp('(' + negativePatterns.map(p => p.source).join('|') + ')\\\\s*' + skillLower.replace(/[.*+?^\${}()|[\\]\\\\]/g, '\\\\\\$&'), 'i');

        if (skillRegex.test(jd)) {
            negativeCount++;
            contextIssues[skill] = (contextIssues[skill] || 0) + 1;
        }

        // Analyze requirement levels
        const skillContext = jd.substring(
            Math.max(0, jdLower.indexOf(skillLower) - 100),
            Math.min(jd.length, jdLower.indexOf(skillLower) + skillLower.length + 100)
        );

        if (requirementLevels.required.test(skillContext)) {
            requirementStats.required++;
        } else if (requirementLevels.preferred.test(skillContext)) {
            requirementStats.preferred++;
        } else if (requirementLevels.bonus.test(skillContext)) {
            requirementStats.bonus++;
        } else {
            requirementStats.unspecified++;
        }
    }

    // Job-level experience analysis
    if (experienceLevels.senior.test(jd)) {
        experienceStats.senior++;
    } else if (experienceLevels.mid.test(jd)) {
        experienceStats.mid++;
    } else if (experienceLevels.junior.test(jd)) {
        experienceStats.junior++;
    } else {
        experienceStats.unspecified++;
    }
}

// Report negative context issues
const sortedIssues = Object.entries(contextIssues).sort((a, b) => b[1] - a[1]);

console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
console.log('NEGATIVE CONTEXT DETECTION');
console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
console.log('Total potential negative mentions: ' + negativeCount);
console.log('');

if (sortedIssues.length > 0) {
    console.log('┌────────────────────────────────────────────────────────────────┐');
    console.log('│ Skill                      │ Negative Mentions                │');
    console.log('├────────────────────────────────────────────────────────────────┤');
    for (const [skill, count] of sortedIssues.slice(0, 15)) {
        console.log('│ ' + skill.substring(0, 26).padEnd(26) + ' │ ' + String(count).padStart(10) + '                      │');
    }
    console.log('└────────────────────────────────────────────────────────────────┘');
} else {
    console.log('✅ No significant negative context issues detected');
}

console.log('');
console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
console.log('REQUIREMENT LEVEL DISTRIBUTION');
console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
const totalReq = requirementStats.required + requirementStats.preferred + requirementStats.bonus + requirementStats.unspecified;
console.log('Required:    ' + requirementStats.required + ' (' + ((requirementStats.required/totalReq)*100).toFixed(1) + '%)');
console.log('Preferred:   ' + requirementStats.preferred + ' (' + ((requirementStats.preferred/totalReq)*100).toFixed(1) + '%)');
console.log('Bonus:       ' + requirementStats.bonus + ' (' + ((requirementStats.bonus/totalReq)*100).toFixed(1) + '%)');
console.log('Unspecified: ' + requirementStats.unspecified + ' (' + ((requirementStats.unspecified/totalReq)*100).toFixed(1) + '%)');

console.log('');
console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
console.log('JOB EXPERIENCE LEVEL DISTRIBUTION');
console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
const totalExp = experienceStats.senior + experienceStats.mid + experienceStats.junior + experienceStats.unspecified;
console.log('Senior:      ' + experienceStats.senior + ' (' + ((experienceStats.senior/totalExp)*100).toFixed(1) + '%)');
console.log('Mid-level:   ' + experienceStats.mid + ' (' + ((experienceStats.mid/totalExp)*100).toFixed(1) + '%)');
console.log('Junior:      ' + experienceStats.junior + ' (' + ((experienceStats.junior/totalExp)*100).toFixed(1) + '%)');
console.log('Unspecified: ' + experienceStats.unspecified + ' (' + ((experienceStats.unspecified/totalExp)*100).toFixed(1) + '%)');

db.close();
"

echo ""
echo "✓ Layer 5 context validation complete"
