#!/bin/bash
# ============================================================================
# LAYER 6: Emerging Skills Detection
# Discovers new/trending skills NOT in the reference file
# Uses pattern matching to find potential new technologies
# ============================================================================

set -e

DB_PATH="${1:-data/jobs.db}"
SAMPLE_SIZE="${2:-1000}"
MIN_FREQUENCY="${3:-5}"
SKILLS_REF="src/config/skills_reference_2026.json"

echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║           LAYER 6: EMERGING SKILLS DETECTION                         ║"
echo "║           Discovering new technologies & skills                      ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""
echo "Database: $DB_PATH"
echo "Sample Size: $SAMPLE_SIZE jobs"
echo "Min Frequency: $MIN_FREQUENCY"
echo ""

node -e "
const Database = require('better-sqlite3');
const fs = require('fs');

const db = new Database('$DB_PATH', { readonly: true });
const skillsData = JSON.parse(fs.readFileSync('$SKILLS_REF', 'utf8'));

// Build set of known skills (lowercase)
const knownSkills = new Set();
for (const skill of skillsData.skills) {
    knownSkills.add(skill.name.toLowerCase());
    // Also add common variations
    knownSkills.add(skill.name.toLowerCase().replace(/\s+/g, ''));
    knownSkills.add(skill.name.toLowerCase().replace(/\s+/g, '-'));
}

// Patterns to detect potential new skills
const techPatterns = [
    // CamelCase tech names (e.g., FastAPI, LangChain, CrewAI)
    /\\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\\b/g,

    // Tech with numbers (e.g., GPT-4, Claude-3, Python3)
    /\\b([A-Z][a-zA-Z]*-?\\d+(?:\\.\\d+)?)\\b/g,

    // Acronyms (3-5 uppercase letters)
    /\\b([A-Z]{3,5})\\b/g,

    // Tech endings (.js, .io, .ai, DB, ML, AI)
    /\\b([A-Za-z]+(?:\\.(?:js|ts|py|io|ai|dev)|DB|ML|AI|API|SDK|CLI))\\b/g,

    // Framework patterns (e.g., React, Vue, Angular)
    /\\b((?:React|Vue|Angular|Svelte|Next|Nuxt|Remix|Astro)[A-Za-z]*)\\b/g,

    // Cloud/Platform patterns
    /\\b([A-Z][a-z]+(?:Cloud|Stack|Hub|Lab|Flow|Ops|Scale|Base))\\b/g,

    // AI/ML specific patterns
    /\\b((?:Chat|Auto|Open|Stable|Llama|Mistral|Claude|Gemini|Anthropic)[A-Za-z]*)\\b/gi,

    // Data tools patterns
    /\\b([A-Z][a-z]+(?:spark|flow|beam|storm|flink|kafka|hive))\\b/gi,
];

// Common words to exclude
const excludeWords = new Set([
    'the', 'and', 'for', 'with', 'that', 'this', 'from', 'have', 'will',
    'are', 'been', 'being', 'was', 'were', 'has', 'had', 'can', 'could',
    'would', 'should', 'may', 'might', 'must', 'shall', 'not', 'but',
    'about', 'into', 'through', 'during', 'before', 'after', 'above',
    'below', 'between', 'under', 'again', 'further', 'then', 'once',
    'here', 'there', 'when', 'where', 'why', 'how', 'all', 'each',
    'few', 'more', 'most', 'other', 'some', 'such', 'only', 'own',
    'same', 'than', 'too', 'very', 'just', 'also', 'now', 'new',
    'work', 'team', 'role', 'job', 'position', 'company', 'experience',
    'skills', 'ability', 'knowledge', 'understanding', 'strong', 'good',
    'excellent', 'required', 'preferred', 'bonus', 'plus', 'years',
    'data', 'business', 'technical', 'development', 'engineering',
    'software', 'system', 'systems', 'application', 'applications',
    'solution', 'solutions', 'project', 'projects', 'product', 'products',
    'build', 'create', 'develop', 'design', 'implement', 'manage',
    'lead', 'support', 'drive', 'ensure', 'provide', 'deliver',
    'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday',
    'january', 'february', 'march', 'april', 'june', 'july', 'august',
    'september', 'october', 'november', 'december',
    'etc', 'via', 'per', 'inc', 'llc', 'ltd', 'usa', 'remote',
]);

// Get jobs
const jobs = db.prepare('SELECT job_description FROM jobs WHERE job_description IS NOT NULL LIMIT $SAMPLE_SIZE').all();

console.log('Scanning ' + jobs.length + ' job descriptions for emerging skills...');
console.log('');

// Count potential new skills
const candidateCounts = {};

for (const job of jobs) {
    const jd = job.job_description || '';
    const foundInJob = new Set();

    for (const pattern of techPatterns) {
        let match;
        const regex = new RegExp(pattern.source, pattern.flags);
        while ((match = regex.exec(jd)) !== null) {
            const term = match[1] || match[0];

            // Skip if too short or too long
            if (term.length < 3 || term.length > 30) continue;

            // Skip excluded words
            if (excludeWords.has(term.toLowerCase())) continue;

            // Skip if already known
            if (knownSkills.has(term.toLowerCase())) continue;

            // Skip pure numbers
            if (/^\\d+\$/.test(term)) continue;

            // Normalize
            const normalized = term.trim();

            // Count unique per job (not multiple in same JD)
            if (!foundInJob.has(normalized.toLowerCase())) {
                foundInJob.add(normalized.toLowerCase());
                candidateCounts[normalized] = (candidateCounts[normalized] || 0) + 1;
            }
        }
    }
}

// Filter by minimum frequency and sort
const minFreq = $MIN_FREQUENCY;
const candidates = Object.entries(candidateCounts)
    .filter(([term, count]) => count >= minFreq)
    .sort((a, b) => b[1] - a[1]);

console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
console.log('EMERGING SKILL CANDIDATES (frequency >= ' + minFreq + ')');
console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
console.log('');

// Categorize candidates
const categories = {
    'AI/LLM Tools': [],
    'Frameworks': [],
    'Cloud/Platform': [],
    'Data Tools': [],
    'Other': []
};

for (const [term, count] of candidates) {
    const termLower = term.toLowerCase();
    const coverage = ((count / jobs.length) * 100).toFixed(1);

    if (/gpt|llm|ai|ml|chat|llama|mistral|claude|gemini|anthropic|openai|langchain|rag|vector/i.test(term)) {
        categories['AI/LLM Tools'].push({ term, count, coverage });
    } else if (/react|vue|angular|next|nuxt|svelte|node|express|fast|flask|django/i.test(term)) {
        categories['Frameworks'].push({ term, count, coverage });
    } else if (/cloud|aws|azure|gcp|kubernetes|docker|terraform/i.test(term)) {
        categories['Cloud/Platform'].push({ term, count, coverage });
    } else if (/spark|kafka|airflow|dbt|snow|data|sql|db/i.test(term)) {
        categories['Data Tools'].push({ term, count, coverage });
    } else {
        categories['Other'].push({ term, count, coverage });
    }
}

let totalCandidates = 0;
for (const [category, items] of Object.entries(categories)) {
    if (items.length === 0) continue;

    console.log('┌────────────────────────────────────────────────────────────────┐');
    console.log('│ ' + category.padEnd(62) + '│');
    console.log('├────────────────────────────────────────────────────────────────┤');
    console.log('│ Term                       │ Jobs      │ Coverage             │');
    console.log('├────────────────────────────────────────────────────────────────┤');

    for (const item of items.slice(0, 15)) {
        totalCandidates++;
        const termPad = item.term.substring(0, 26).padEnd(26);
        const countPad = String(item.count).padStart(9);
        const covPad = (item.coverage + '%').padStart(8);
        console.log('│ ' + termPad + ' │ ' + countPad + ' │ ' + covPad + '             │');
    }
    console.log('└────────────────────────────────────────────────────────────────┘');
    console.log('');
}

console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
console.log('SUMMARY');
console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
console.log('Total candidate terms found: ' + candidates.length);
console.log('Terms shown (top per category): ' + totalCandidates);
console.log('');
console.log('Next steps:');
console.log('  1. Review candidates for actual new skills');
console.log('  2. Add valid skills to skills_reference_2026.json');
console.log('  3. Re-run extraction to include new skills');

// Save candidates to file
const output = {
    generated: new Date().toISOString(),
    sample_size: jobs.length,
    min_frequency: minFreq,
    total_candidates: candidates.length,
    candidates: candidates.slice(0, 100).map(([term, count]) => ({
        term,
        count,
        coverage: ((count / jobs.length) * 100).toFixed(2) + '%'
    }))
};

fs.writeFileSync('data/emerging_skills_candidates.json', JSON.stringify(output, null, 2));
console.log('');
console.log('📄 Full candidate list saved to: data/emerging_skills_candidates.json');

db.close();
"

echo ""
echo "✓ Layer 6 emerging skills detection complete"
