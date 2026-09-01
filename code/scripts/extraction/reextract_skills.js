#!/usr/bin/env node
/**
 * Re-extract skills for all jobs using current skills_reference_2026.json
 * This fixes FP/FN issues caused by outdated skill extractions
 */

const fs = require('fs');
const path = require('path');
const Database = require('better-sqlite3');

// Load skills reference
const skillsPath = path.join(__dirname, 'src/config/skills_reference_2026.json');
const skillsData = JSON.parse(fs.readFileSync(skillsPath, 'utf8'));

console.log(`Loaded ${skillsData.total_skills} skills with patterns`);

// Compile patterns and map to canonical names
const patternToSkill = new Map();
for (const skill of skillsData.skills) {
    const canonicalName = skill.name;
    for (const p of skill.patterns) {
        try {
            const regex = new RegExp(p, 'i');
            patternToSkill.set(p, { regex, canonicalName });
        } catch (e) {
            // Skip invalid regex
        }
    }
}

// Sort by pattern length (longest first) for priority matching
const sortedPatterns = [...patternToSkill.entries()]
    .sort((a, b) => b[0].length - a[0].length);

console.log(`Compiled ${sortedPatterns.length} patterns`);

function extractSkills(description) {
    const foundSkills = new Set();
    const consumed = [];

    for (const [patternStr, { regex, canonicalName }] of sortedPatterns) {
        let match;
        const globalRegex = new RegExp(regex.source, 'gi');

        while ((match = globalRegex.exec(description)) !== null) {
            const start = match.index;
            const end = start + match[0].length;

            // Skip if region already consumed
            const isConsumed = consumed.some(([s, e]) =>
                (s <= start && start < e) || (s < end && end <= e)
            );

            if (!isConsumed) {
                foundSkills.add(canonicalName);
                consumed.push([start, end]);
            }
        }
    }

    return [...foundSkills].sort();
}

// Connect to database
const db = new Database(path.join(__dirname, 'data/jobs.db'));

// Get all jobs
const jobs = db.prepare(`
    SELECT job_id, job_description, skills as old_skills
    FROM jobs
    WHERE job_description IS NOT NULL AND job_description != ''
`).all();

console.log(`\nRe-extracting skills for ${jobs.length} jobs...`);

// Prepare update statement
const updateStmt = db.prepare('UPDATE jobs SET skills = ? WHERE job_id = ?');

let updated = 0;
let unchanged = 0;

const updateMany = db.transaction((updates) => {
    for (const { jobId, newSkills } of updates) {
        updateStmt.run(newSkills, jobId);
    }
});

const updates = [];

for (const job of jobs) {
    const newSkillsList = extractSkills(job.job_description);
    const newSkills = newSkillsList.join(', ');

    if (newSkills !== job.old_skills) {
        updates.push({ jobId: job.job_id, newSkills });
        updated++;
    } else {
        unchanged++;
    }

    if ((updated + unchanged) % 500 === 0) {
        console.log(`  Processed ${updated + unchanged} jobs...`);
    }
}

// Execute all updates in a transaction
console.log(`\nApplying ${updates.length} updates...`);
updateMany(updates);

db.close();

console.log(`\n${'='.repeat(50)}`);
console.log('RE-EXTRACTION COMPLETE');
console.log('='.repeat(50));
console.log(`Total jobs processed: ${jobs.length}`);
console.log(`Updated: ${updated}`);
console.log(`Unchanged: ${unchanged}`);
