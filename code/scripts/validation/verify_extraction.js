#!/usr/bin/env node
/**
 * Verify skill extraction - check for skills not in reference file
 */

const Database = require('better-sqlite3');
const fs = require('fs');

const DB_PATH = 'data/jobs.db';
const SKILLS_REF = 'src/config/skills_reference_2026.json';

// Load reference skills
const skillsData = JSON.parse(fs.readFileSync(SKILLS_REF, 'utf8'));
const referenceSkills = new Set(skillsData.skills.map(s => s.name.toLowerCase()));

console.log(`Reference file has ${referenceSkills.size} skills\n`);

// Get all extracted skills from database
const db = new Database(DB_PATH, { readonly: true });
const jobs = db.prepare(`
    SELECT skills FROM jobs WHERE skills IS NOT NULL
`).all();

// Count all extracted skills
const extractedCounts = {};
let totalJobs = 0;

for (const job of jobs) {
    totalJobs++;
    const skills = job.skills.split(',').map(s => s.trim()).filter(s => s);
    for (const skill of skills) {
        extractedCounts[skill] = (extractedCounts[skill] || 0) + 1;
    }
}

db.close();

// Find skills extracted but NOT in reference
const notInReference = [];
const inReference = [];

for (const [skill, count] of Object.entries(extractedCounts)) {
    if (!referenceSkills.has(skill.toLowerCase())) {
        notInReference.push({ skill, count });
    } else {
        inReference.push({ skill, count });
    }
}

notInReference.sort((a, b) => b.count - a.count);
inReference.sort((a, b) => b.count - a.count);

console.log(`Analyzed ${totalJobs} jobs`);
console.log(`Unique skills extracted: ${Object.keys(extractedCounts).length}`);
console.log(`Skills in reference: ${inReference.length}`);
console.log(`Skills NOT in reference: ${notInReference.length}\n`);

if (notInReference.length > 0) {
    console.log('='.repeat(60));
    console.log('SKILLS EXTRACTED BUT NOT IN REFERENCE FILE (Top 50)');
    console.log('='.repeat(60));
    console.log(`${'Skill'.padEnd(40)} ${'Count'.padStart(10)}`);
    console.log('-'.repeat(52));
    for (const item of notInReference.slice(0, 50)) {
        console.log(`${item.skill.padEnd(40)} ${String(item.count).padStart(10)}`);
    }
}

console.log('\n' + '='.repeat(60));
console.log('TOP 30 EXTRACTED SKILLS (in reference)');
console.log('='.repeat(60));
for (const item of inReference.slice(0, 30)) {
    console.log(`${item.skill.padEnd(40)} ${String(item.count).padStart(10)}`);
}
