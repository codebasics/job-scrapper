#!/bin/bash
# ============================================================================
# OFFLINE BATCH VALIDATION & FIX
# Re-validates ALL jobs in database and fixes FP/FN issues
# Uses Node.js for speed (~60+ jobs/sec on 13,500 jobs)
# ============================================================================

set -e

DB_PATH="${1:-data/jobs.db}"
SKILLS_REF="src/config/skills_reference_2026.json"
BATCH_SIZE="${2:-1000}"

echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║          OFFLINE BATCH VALIDATION & FIX                              ║"
echo "║          Re-validates all jobs and fixes FP/FN issues                ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""
echo "Database: $DB_PATH"
echo "Skills Reference: $SKILLS_REF"
echo "Batch Size: $BATCH_SIZE"
echo ""

# Run batch validation using Node.js for speed
node -e "
const fs = require('fs');
const Database = require('better-sqlite3');

console.log('Loading skills reference...');
const skillsData = JSON.parse(fs.readFileSync('$SKILLS_REF', 'utf8'));

// Build skill pattern map
const skillPatterns = [];
for (const skill of skillsData.skills) {
    const regexes = [];
    for (const p of skill.patterns || []) {
        try { regexes.push(new RegExp(p, 'i')); } catch (e) {}
    }
    if (regexes.length) {
        skillPatterns.push({ name: skill.name, regexes });
    }
}
console.log('Compiled patterns for ' + skillPatterns.length + ' skills');

// Function to find all skills matching patterns in text
function findSkillsInText(text) {
    const found = new Set();
    for (const { name, regexes } of skillPatterns) {
        for (const r of regexes) {
            if (r.test(text)) {
                found.add(name);
                break;
            }
        }
    }
    return found;
}

// Connect to database
const db = new Database('$DB_PATH');

// Get total count
const totalCount = db.prepare('SELECT COUNT(*) as c FROM jobs WHERE job_description IS NOT NULL').get().c;
console.log('Total jobs to validate: ' + totalCount);
console.log('');

// Prepare statements
const selectStmt = db.prepare('SELECT job_id, job_description, skills FROM jobs WHERE job_description IS NOT NULL');
const updateStmt = db.prepare('UPDATE jobs SET skills = ? WHERE job_id = ?');

// Stats
let processed = 0;
let updated = 0;
let totalFpRemoved = 0;
let totalFnAdded = 0;
const fpCounts = {};
const fnCounts = {};

const startTime = Date.now();

// Process in transaction for speed
db.exec('BEGIN');

for (const job of selectStmt.iterate()) {
    const jobDesc = job.job_description || '';
    const oldSkills = new Set((job.skills || '').split(',').map(s => s.trim()).filter(s => s));

    // Find skills that SHOULD be present (ground truth)
    const patternMatchedSkills = findSkillsInText(jobDesc);

    // Calculate FP and FN
    const falsePositives = [...oldSkills].filter(s => !patternMatchedSkills.has(s));
    const falseNegatives = [...patternMatchedSkills].filter(s => !oldSkills.has(s));

    // Track stats
    for (const fp of falsePositives) {
        fpCounts[fp] = (fpCounts[fp] || 0) + 1;
        totalFpRemoved++;
    }
    for (const fn of falseNegatives) {
        fnCounts[fn] = (fnCounts[fn] || 0) + 1;
        totalFnAdded++;
    }

    // New validated skills = pattern-matched skills
    const newSkills = [...patternMatchedSkills].sort().join(', ');
    const oldSkillsStr = [...oldSkills].sort().join(', ');

    if (newSkills !== oldSkillsStr) {
        updateStmt.run(newSkills, job.job_id);
        updated++;
    }

    processed++;

    if (processed % $BATCH_SIZE === 0) {
        const elapsed = (Date.now() - startTime) / 1000;
        const rate = (processed / elapsed).toFixed(0);
        const eta = ((totalCount - processed) / rate).toFixed(0);
        console.log('  Processed ' + processed + '/' + totalCount + ' (' + rate + '/s, ETA: ' + eta + 's)');
    }
}

db.exec('COMMIT');
db.close();

const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);

console.log('');
console.log('═'.repeat(60));
console.log('BATCH VALIDATION COMPLETE');
console.log('═'.repeat(60));
console.log('Total jobs processed: ' + processed);
console.log('Jobs updated:         ' + updated);
console.log('Time:                 ' + elapsed + 's (' + (processed / elapsed).toFixed(0) + ' jobs/sec)');
console.log('');
console.log('False Positives removed: ' + totalFpRemoved);
console.log('False Negatives added:   ' + totalFnAdded);

// Top FPs
const topFps = Object.entries(fpCounts).sort((a, b) => b[1] - a[1]).slice(0, 10);
if (topFps.length > 0) {
    console.log('');
    console.log('Top 10 False Positives Removed:');
    for (const [skill, count] of topFps) {
        console.log('  ' + skill + ': ' + count);
    }
}

// Top FNs
const topFns = Object.entries(fnCounts).sort((a, b) => b[1] - a[1]).slice(0, 10);
if (topFns.length > 0) {
    console.log('');
    console.log('Top 10 False Negatives Added:');
    for (const [skill, count] of topFns) {
        console.log('  ' + skill + ': ' + count);
    }
}
"

echo ""
echo "✓ Batch validation and fix complete"
