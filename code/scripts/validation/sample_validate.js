#!/usr/bin/env node
/**
 * Sample Validation - Verify pattern matching on random job samples
 */

const Database = require('better-sqlite3');
const fs = require('fs');

const DB_PATH = 'data/jobs.db';
const SKILLS_REF = 'src/config/skills_reference_2026.json';

// Load skills reference with patterns
const skillsData = JSON.parse(fs.readFileSync(SKILLS_REF, 'utf8'));
const skillPatterns = {};

for (const skill of skillsData.skills) {
    if (skill.patterns && skill.patterns.length > 0) {
        const regexes = [];
        for (const p of skill.patterns) {
            try {
                regexes.push(new RegExp(p, 'i'));
            } catch (e) {}
        }
        if (regexes.length > 0) {
            skillPatterns[skill.name.toLowerCase()] = { name: skill.name, regexes };
        }
    }
}

// Connect to database
const db = new Database(DB_PATH, { readonly: true });

// Get 10 random jobs for detailed analysis
const jobs = db.prepare(`
    SELECT job_id, actual_role, job_description, skills
    FROM jobs
    WHERE job_description IS NOT NULL AND skills IS NOT NULL
    ORDER BY RANDOM()
    LIMIT 10
`).all();

db.close();

console.log('='.repeat(80));
console.log('SAMPLE VALIDATION - Checking pattern accuracy on 10 random jobs');
console.log('='.repeat(80));

let totalTP = 0, totalFP = 0, totalFN = 0;

for (const job of jobs) {
    const extractedSkills = new Set(job.skills.split(',').map(s => s.trim().toLowerCase()));
    const detectedByPattern = new Set();

    // Check what patterns match in JD
    for (const [skillLower, { name, regexes }] of Object.entries(skillPatterns)) {
        for (const regex of regexes) {
            if (regex.test(job.job_description)) {
                detectedByPattern.add(skillLower);
                break;
            }
        }
    }

    // Calculate matches
    const truePositives = [...extractedSkills].filter(s => detectedByPattern.has(s));
    const falsePositives = [...extractedSkills].filter(s => !detectedByPattern.has(s));
    const falseNegatives = [...detectedByPattern].filter(s => !extractedSkills.has(s));

    totalTP += truePositives.length;
    totalFP += falsePositives.length;
    totalFN += falseNegatives.length;

    console.log(`\n${'─'.repeat(80)}`);
    console.log(`Job: ${job.job_id.substring(0, 30)}... | Role: ${job.actual_role || 'N/A'}`);
    console.log(`Extracted: ${extractedSkills.size} skills | Pattern matches: ${detectedByPattern.size}`);
    console.log(`TP: ${truePositives.length} | FP: ${falsePositives.length} | FN: ${falseNegatives.length}`);

    if (falsePositives.length > 0) {
        console.log(`\n  FP (extracted but no pattern match): ${falsePositives.slice(0, 5).join(', ')}${falsePositives.length > 5 ? '...' : ''}`);
    }
    if (falseNegatives.length > 0) {
        console.log(`  FN (pattern matches but not extracted): ${falseNegatives.slice(0, 5).join(', ')}${falseNegatives.length > 5 ? '...' : ''}`);
    }
}

console.log('\n' + '='.repeat(80));
console.log('AGGREGATE RESULTS');
console.log('='.repeat(80));
console.log(`Total True Positives:  ${totalTP}`);
console.log(`Total False Positives: ${totalFP}`);
console.log(`Total False Negatives: ${totalFN}`);

const precision = totalTP / (totalTP + totalFP) * 100;
const recall = totalTP / (totalTP + totalFN) * 100;
const f1 = 2 * precision * recall / (precision + recall);

console.log(`\nPrecision: ${precision.toFixed(1)}%`);
console.log(`Recall:    ${recall.toFixed(1)}%`);
console.log(`F1 Score:  ${f1.toFixed(1)}%`);
