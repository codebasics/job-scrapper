#!/bin/bash
# ============================================================================
# LAYER 7: Trend & Drift Analysis
# Tracks skill extraction quality and trends over time
# Analyzes skill velocity (rising/falling trends)
# ============================================================================

set -e

DB_PATH="${1:-data/jobs.db}"
SKILLS_REF="src/config/skills_reference_2026.json"

echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║           LAYER 7: TREND & DRIFT ANALYSIS                            ║"
echo "║           Tracking skill trends & extraction quality                 ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""
echo "Database: $DB_PATH"
echo ""

node -e "
const Database = require('better-sqlite3');
const fs = require('fs');

const db = new Database('$DB_PATH', { readonly: true });
const skillsData = JSON.parse(fs.readFileSync('$SKILLS_REF', 'utf8'));

// Get jobs with timestamps
const jobs = db.prepare(\`
    SELECT job_id, skills, scraped_at, posted_date
    FROM jobs
    WHERE skills IS NOT NULL
    ORDER BY scraped_at DESC
\`).all();

console.log('Analyzing ' + jobs.length + ' jobs for trends...');
console.log('');

// Group jobs by time period (weekly)
const periods = {};
const skillTrends = {};

for (const job of jobs) {
    const date = new Date(job.scraped_at || job.posted_date || Date.now());
    const weekKey = date.toISOString().substring(0, 10); // YYYY-MM-DD

    if (!periods[weekKey]) {
        periods[weekKey] = { jobs: 0, skills: {} };
    }
    periods[weekKey].jobs++;

    const skills = (job.skills || '').split(',').map(s => s.trim()).filter(s => s);
    for (const skill of skills) {
        periods[weekKey].skills[skill] = (periods[weekKey].skills[skill] || 0) + 1;

        if (!skillTrends[skill]) {
            skillTrends[skill] = { total: 0, periods: {} };
        }
        skillTrends[skill].total++;
        skillTrends[skill].periods[weekKey] = (skillTrends[skill].periods[weekKey] || 0) + 1;
    }
}

// Sort periods
const sortedPeriods = Object.keys(periods).sort().reverse();

console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
console.log('SCRAPING TIMELINE');
console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');

console.log('┌────────────────────────────────────────────────────────────────┐');
console.log('│ Date                │ Jobs      │ Unique Skills              │');
console.log('├────────────────────────────────────────────────────────────────┤');

for (const period of sortedPeriods.slice(0, 15)) {
    const data = periods[period];
    const uniqueSkills = Object.keys(data.skills).length;
    console.log('│ ' + period.padEnd(19) + ' │ ' + String(data.jobs).padStart(9) + ' │ ' + String(uniqueSkills).padStart(12) + '              │');
}
console.log('└────────────────────────────────────────────────────────────────┘');

// Calculate skill velocity (compare recent vs older)
console.log('');
console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
console.log('SKILL VELOCITY ANALYSIS');
console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');

const recentPeriods = sortedPeriods.slice(0, Math.ceil(sortedPeriods.length / 2));
const olderPeriods = sortedPeriods.slice(Math.ceil(sortedPeriods.length / 2));

const velocity = {};
for (const [skill, data] of Object.entries(skillTrends)) {
    let recentCount = 0;
    let olderCount = 0;

    for (const period of recentPeriods) {
        recentCount += data.periods[period] || 0;
    }
    for (const period of olderPeriods) {
        olderCount += data.periods[period] || 0;
    }

    // Calculate velocity (positive = rising, negative = falling)
    if (olderCount > 0 && recentCount > 0) {
        const recentRate = recentCount / recentPeriods.length;
        const olderRate = olderCount / Math.max(1, olderPeriods.length);
        velocity[skill] = {
            total: data.total,
            recent: recentCount,
            older: olderCount,
            change: ((recentRate - olderRate) / Math.max(1, olderRate) * 100).toFixed(1)
        };
    }
}

// Sort by velocity
const rising = Object.entries(velocity)
    .filter(([_, v]) => parseFloat(v.change) > 20 && v.total >= 20)
    .sort((a, b) => parseFloat(b[1].change) - parseFloat(a[1].change));

const falling = Object.entries(velocity)
    .filter(([_, v]) => parseFloat(v.change) < -20 && v.total >= 20)
    .sort((a, b) => parseFloat(a[1].change) - parseFloat(b[1].change));

if (rising.length > 0) {
    console.log('');
    console.log('📈 RISING SKILLS (growing demand)');
    console.log('┌────────────────────────────────────────────────────────────────┐');
    console.log('│ Skill                      │ Total     │ Change               │');
    console.log('├────────────────────────────────────────────────────────────────┤');
    for (const [skill, data] of rising.slice(0, 15)) {
        const skillPad = skill.substring(0, 26).padEnd(26);
        const totalPad = String(data.total).padStart(9);
        const changePad = ('+' + data.change + '%').padStart(8);
        console.log('│ ' + skillPad + ' │ ' + totalPad + ' │ ' + changePad + '             │');
    }
    console.log('└────────────────────────────────────────────────────────────────┘');
}

if (falling.length > 0) {
    console.log('');
    console.log('📉 FALLING SKILLS (declining demand)');
    console.log('┌────────────────────────────────────────────────────────────────┐');
    console.log('│ Skill                      │ Total     │ Change               │');
    console.log('├────────────────────────────────────────────────────────────────┤');
    for (const [skill, data] of falling.slice(0, 15)) {
        const skillPad = skill.substring(0, 26).padEnd(26);
        const totalPad = String(data.total).padStart(9);
        const changePad = (data.change + '%').padStart(8);
        console.log('│ ' + skillPad + ' │ ' + totalPad + ' │ ' + changePad + '             │');
    }
    console.log('└────────────────────────────────────────────────────────────────┘');
}

// Top skills overall
console.log('');
console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
console.log('TOP 20 SKILLS BY FREQUENCY');
console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');

const topSkills = Object.entries(skillTrends)
    .sort((a, b) => b[1].total - a[1].total)
    .slice(0, 20);

console.log('┌────────────────────────────────────────────────────────────────┐');
console.log('│ Rank │ Skill                      │ Count     │ % of Jobs     │');
console.log('├────────────────────────────────────────────────────────────────┤');

for (let i = 0; i < topSkills.length; i++) {
    const [skill, data] = topSkills[i];
    const rank = String(i + 1).padStart(4);
    const skillPad = skill.substring(0, 26).padEnd(26);
    const countPad = String(data.total).padStart(9);
    const pctPad = ((data.total / jobs.length) * 100).toFixed(1).padStart(6) + '%';
    console.log('│ ' + rank + ' │ ' + skillPad + ' │ ' + countPad + ' │ ' + pctPad + '       │');
}
console.log('└────────────────────────────────────────────────────────────────┘');

// Quality metrics summary
console.log('');
console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
console.log('EXTRACTION QUALITY METRICS');
console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');

const totalUniqueSkills = Object.keys(skillTrends).length;
const avgSkillsPerJob = Object.values(skillTrends).reduce((sum, d) => sum + d.total, 0) / jobs.length;
const skillsInReference = skillsData.skills.length;
const coverageRate = ((totalUniqueSkills / skillsInReference) * 100).toFixed(1);

console.log('Total Jobs Analyzed:     ' + jobs.length);
console.log('Unique Skills Extracted: ' + totalUniqueSkills);
console.log('Skills in Reference:     ' + skillsInReference);
console.log('Reference Coverage:      ' + coverageRate + '%');
console.log('Avg Skills per Job:      ' + avgSkillsPerJob.toFixed(1));
console.log('Rising Skills:           ' + rising.length);
console.log('Falling Skills:          ' + falling.length);

// Save trend report
const report = {
    generated: new Date().toISOString(),
    total_jobs: jobs.length,
    unique_skills: totalUniqueSkills,
    avg_skills_per_job: parseFloat(avgSkillsPerJob.toFixed(2)),
    rising_skills: rising.slice(0, 20).map(([s, d]) => ({ skill: s, ...d })),
    falling_skills: falling.slice(0, 20).map(([s, d]) => ({ skill: s, ...d })),
    top_skills: topSkills.map(([s, d]) => ({ skill: s, count: d.total }))
};

fs.writeFileSync('data/skill_trends_report.json', JSON.stringify(report, null, 2));
console.log('');
console.log('📄 Trend report saved to: data/skill_trends_report.json');

db.close();
"

echo ""
echo "✓ Layer 7 trend analysis complete"
