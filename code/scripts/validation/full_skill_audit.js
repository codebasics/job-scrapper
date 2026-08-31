#!/usr/bin/env node
/**
 * Full Skill Audit - Comprehensive FP/FN Analysis
 * Checks ALL skills from skills_reference_2026.json against scraped jobs
 */

const Database = require('better-sqlite3');
const fs = require('fs');
const path = require('path');

const DB_PATH = 'data/jobs.db';
const SKILLS_REF = 'src/config/skills_reference_2026.json';

console.log('='.repeat(70));
console.log('COMPREHENSIVE SKILL AUDIT - FP/FN ANALYSIS');
console.log('='.repeat(70));

// Load skills reference
const skillsData = JSON.parse(fs.readFileSync(SKILLS_REF, 'utf8'));
const skills = skillsData.skills;
console.log(`\nLoaded ${skills.length} skills from reference file`);

// Connect to database
const db = new Database(DB_PATH, { readonly: true });
const jobs = db.prepare(`
    SELECT job_id, job_description, skills
    FROM jobs
    WHERE job_description IS NOT NULL AND skills IS NOT NULL
`).all();

console.log(`Analyzing ${jobs.length} jobs...\n`);

// Results containers
const results = [];
const fpCandidates = [];
const fnCandidates = [];

// Process each skill
let processed = 0;
for (const skill of skills) {
    const skillName = skill.name;
    const patterns = skill.patterns || [];

    if (patterns.length === 0) continue;

    // Compile regex patterns
    const regexes = [];
    for (const p of patterns) {
        try {
            regexes.push(new RegExp(p, 'i'));
        } catch (e) {
            // Skip invalid patterns
        }
    }

    if (regexes.length === 0) continue;

    let mentionedInJD = 0;
    let extractedInSkills = 0;
    let bothMatch = 0;
    let onlyInJD = 0;
    let onlyExtracted = 0;

    for (const job of jobs) {
        const jd = job.job_description || '';
        const extractedSkills = (job.skills || '').toLowerCase();

        // Check if mentioned in JD
        let foundInJD = false;
        for (const regex of regexes) {
            if (regex.test(jd)) {
                foundInJD = true;
                break;
            }
        }

        // Check if in extracted skills (exact match in comma-separated list)
        const extractedList = extractedSkills.split(',').map(s => s.trim().toLowerCase());
        const foundInExtracted = extractedList.includes(skillName.toLowerCase());

        if (foundInJD) mentionedInJD++;
        if (foundInExtracted) extractedInSkills++;

        if (foundInJD && foundInExtracted) bothMatch++;
        if (foundInJD && !foundInExtracted) onlyInJD++;
        if (!foundInJD && foundInExtracted) onlyExtracted++;
    }

    // Calculate metrics
    const fnGap = onlyInJD;  // In JD but not extracted
    const fpGap = onlyExtracted;  // Extracted but not in JD

    results.push({
        skill: skillName,
        mentionedInJD,
        extractedInSkills,
        truePositives: bothMatch,
        falseNegatives: fnGap,
        falsePositives: fpGap,
        precision: extractedInSkills > 0 ? (bothMatch / extractedInSkills * 100).toFixed(1) : 'N/A',
        recall: mentionedInJD > 0 ? (bothMatch / mentionedInJD * 100).toFixed(1) : 'N/A'
    });

    // Track any issues (gap >= 1)
    if (fnGap >= 1) {
        fnCandidates.push({ skill: skillName, gap: fnGap, mentioned: mentionedInJD, extracted: extractedInSkills });
    }
    if (fpGap >= 1) {
        fpCandidates.push({ skill: skillName, gap: fpGap, extracted: extractedInSkills, mentioned: mentionedInJD });
    }

    processed++;
    if (processed % 100 === 0) {
        process.stdout.write(`\rProcessed ${processed}/${skills.length} skills...`);
    }
}

console.log(`\rProcessed ${processed} skills.                    \n`);

db.close();

// Sort by issues
fnCandidates.sort((a, b) => b.gap - a.gap);
fpCandidates.sort((a, b) => b.gap - a.gap);

// Print False Negatives (top 40)
console.log('='.repeat(80));
console.log('TOP FALSE NEGATIVES (Skills mentioned in JD but NOT extracted)');
console.log('='.repeat(80));
console.log(`${'Skill'.padEnd(30)} ${'In JD'.padStart(8)} ${'Extracted'.padStart(10)} ${'Gap'.padStart(8)} ${'Recall'.padStart(10)}`);
console.log('-'.repeat(68));

for (const item of fnCandidates.slice(0, 40)) {
    const recall = item.mentioned > 0 ? ((item.mentioned - item.gap) / item.mentioned * 100).toFixed(0) + '%' : 'N/A';
    console.log(`${item.skill.padEnd(30)} ${String(item.mentioned).padStart(8)} ${String(item.extracted).padStart(10)} ${String(item.gap).padStart(8)} ${recall.padStart(10)}`);
}

// Print False Positives (top 40)
console.log('\n' + '='.repeat(80));
console.log('TOP FALSE POSITIVES (Skills extracted but NOT in JD by pattern)');
console.log('='.repeat(80));
console.log(`${'Skill'.padEnd(30)} ${'Extracted'.padStart(10)} ${'In JD'.padStart(8)} ${'Gap'.padStart(8)} ${'Precision'.padStart(10)}`);
console.log('-'.repeat(68));

for (const item of fpCandidates.slice(0, 40)) {
    const precision = item.extracted > 0 ? ((item.extracted - item.gap) / item.extracted * 100).toFixed(0) + '%' : 'N/A';
    console.log(`${item.skill.padEnd(30)} ${String(item.extracted).padStart(10)} ${String(item.mentioned).padStart(8)} ${String(item.gap).padStart(8)} ${precision.padStart(10)}`);
}

// Summary stats
const totalFN = fnCandidates.reduce((sum, x) => sum + x.gap, 0);
const totalFP = fpCandidates.reduce((sum, x) => sum + x.gap, 0);
const skillsWithFN = fnCandidates.length;
const skillsWithFP = fpCandidates.length;

console.log('\n' + '='.repeat(70));
console.log('SUMMARY');
console.log('='.repeat(70));
console.log(`Total skills analyzed: ${processed}`);
console.log(`Skills with FN issues (gap > 5): ${skillsWithFN}`);
console.log(`Skills with FP issues (gap > 5): ${skillsWithFP}`);
console.log(`Total FN instances: ${totalFN}`);
console.log(`Total FP instances: ${totalFP}`);

// Save detailed report to JSON
const report = {
    timestamp: new Date().toISOString(),
    jobsAnalyzed: jobs.length,
    skillsAnalyzed: processed,
    falseNegatives: fnCandidates,
    falsePositives: fpCandidates,
    allResults: results
};

fs.writeFileSync('skill_audit_report.json', JSON.stringify(report, null, 2));
console.log('\nDetailed report saved to: skill_audit_report.json');
