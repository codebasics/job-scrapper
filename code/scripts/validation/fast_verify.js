#!/usr/bin/env node
/**
 * Fast cross-verification of skills using Node.js
 * Compares skills column vs job_description using patterns from skills_reference_2026.json
 */

const fs = require('fs');
const path = require('path');
const Database = require('better-sqlite3');

// Load skills reference
const skillsPath = path.join(__dirname, 'src/config/skills_reference_2026.json');
const skillsData = JSON.parse(fs.readFileSync(skillsPath, 'utf8'));

console.log(`Loaded ${skillsData.total_skills} skills with patterns`);

// Compile patterns
const skillPatterns = new Map();
for (const skill of skillsData.skills) {
    const name = skill.name.toLowerCase().trim();
    const patterns = [];
    for (const p of skill.patterns) {
        try {
            patterns.push(new RegExp(p, 'i'));
        } catch (e) {
            // Skip invalid regex
        }
    }
    skillPatterns.set(name, patterns);
}

// Connect to database
const db = new Database(path.join(__dirname, 'data/jobs.db'), { readonly: true });

// Get all jobs
const jobs = db.prepare(`
    SELECT job_id, skills, job_description
    FROM jobs
    WHERE skills IS NOT NULL AND skills != ''
    AND job_description IS NOT NULL AND job_description != ''
`).all();

console.log(`\nAnalyzing ${jobs.length} jobs...`);

// Track statistics
let totalTP = 0;
let totalFP = 0;
let totalFN = 0;
const fpBySkill = new Map();
const fnBySkill = new Map();

let processed = 0;
for (const job of jobs) {
    processed++;
    if (processed % 500 === 0) {
        console.log(`  Processed ${processed} jobs...`);
    }

    // Parse skills from DB
    const dbSkills = new Set(
        job.skills.split(',')
            .map(s => s.trim().toLowerCase())
            .filter(s => s.length > 0)
    );

    // Extract skills from description using patterns
    const extractedSkills = new Set();
    const desc = job.job_description;

    for (const [skillName, patterns] of skillPatterns) {
        for (const pattern of patterns) {
            if (pattern.test(desc)) {
                extractedSkills.add(skillName);
                break;
            }
        }
    }

    // Calculate TP, FP, FN
    for (const skill of dbSkills) {
        if (extractedSkills.has(skill)) {
            totalTP++;
        } else {
            totalFP++;
            fpBySkill.set(skill, (fpBySkill.get(skill) || 0) + 1);
        }
    }

    for (const skill of extractedSkills) {
        if (!dbSkills.has(skill)) {
            totalFN++;
            fnBySkill.set(skill, (fnBySkill.get(skill) || 0) + 1);
        }
    }
}

db.close();

// Calculate metrics
const precision = totalTP + totalFP > 0 ? (totalTP / (totalTP + totalFP) * 100) : 0;
const recall = totalTP + totalFN > 0 ? (totalTP / (totalTP + totalFN) * 100) : 0;
const f1 = precision + recall > 0 ? (2 * precision * recall / (precision + recall)) : 0;

// Print results
console.log('\n' + '='.repeat(60));
console.log('CROSS-VERIFICATION RESULTS');
console.log('='.repeat(60));
console.log(`\nTotal Jobs Analyzed: ${jobs.length}`);
console.log(`\nMetrics:`);
console.log(`  True Positives (correct extractions): ${totalTP}`);
console.log(`  False Positives (in DB but NOT in description): ${totalFP}`);
console.log(`  False Negatives (in description but NOT in DB): ${totalFN}`);
console.log(`\n  Precision: ${precision.toFixed(2)}%`);
console.log(`  Recall: ${recall.toFixed(2)}%`);
console.log(`  F1 Score: ${f1.toFixed(2)}%`);

console.log('\n' + '-'.repeat(60));
console.log('TOP 25 FALSE POSITIVES (skills in DB but NOT in description):');
console.log('-'.repeat(60));
const sortedFP = [...fpBySkill.entries()].sort((a, b) => b[1] - a[1]).slice(0, 25);
for (const [skill, count] of sortedFP) {
    console.log(`  ${skill}: ${count} FPs`);
}

console.log('\n' + '-'.repeat(60));
console.log('TOP 25 FALSE NEGATIVES (skills in description but NOT in DB):');
console.log('-'.repeat(60));
const sortedFN = [...fnBySkill.entries()].sort((a, b) => b[1] - a[1]).slice(0, 25);
for (const [skill, count] of sortedFN) {
    console.log(`  ${skill}: ${count} FNs`);
}
