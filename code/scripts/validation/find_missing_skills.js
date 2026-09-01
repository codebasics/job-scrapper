#!/usr/bin/env node
/**
 * Find potential missing skills in reference file
 * Scans job descriptions for common tech terms not in skills reference
 */

const Database = require('better-sqlite3');
const fs = require('fs');

const DB_PATH = 'data/jobs.db';
const SKILLS_REF = 'src/config/skills_reference_2026.json';

// Load reference skills (lowercase for matching)
const skillsData = JSON.parse(fs.readFileSync(SKILLS_REF, 'utf8'));
const referenceSkillsLower = new Set(skillsData.skills.map(s => s.name.toLowerCase()));

// Common tech terms patterns to check (potential missing skills)
const potentialSkills = [
    // Data tools
    { name: 'dbt', pattern: /\bdbt\b/gi },
    { name: 'Fivetran', pattern: /\bfivetran\b/gi },
    { name: 'Airbyte', pattern: /\bairbyte\b/gi },
    { name: 'Stitch', pattern: /\bstitch\s*(data)?\b/gi },
    { name: 'Matillion', pattern: /\bmatillion\b/gi },
    { name: 'Talend', pattern: /\btalend\b/gi },
    { name: 'Informatica', pattern: /\binformatica\b/gi },
    { name: 'Alteryx', pattern: /\balteryx\b/gi },
    { name: 'KNIME', pattern: /\bknime\b/gi },
    { name: 'Dataiku', pattern: /\bdataiku\b/gi },
    { name: 'Palantir', pattern: /\bpalantir\b/gi },

    // Cloud & Infra
    { name: 'Terraform', pattern: /\bterraform\b/gi },
    { name: 'Pulumi', pattern: /\bpulumi\b/gi },
    { name: 'Ansible', pattern: /\bansible\b/gi },
    { name: 'Crossplane', pattern: /\bcrossplane\b/gi },

    // Data Warehouses
    { name: 'Synapse', pattern: /\bsynapse\b/gi },
    { name: 'Fabric', pattern: /\bmicrosoft\s*fabric\b/gi },
    { name: 'Clickhouse', pattern: /\bclickhouse\b/gi },
    { name: 'DuckDB', pattern: /\bduckdb\b/gi },
    { name: 'Druid', pattern: /\bapache\s*druid\b/gi },

    // ML/AI
    { name: 'Weights & Biases', pattern: /\bweights\s*(and|&)\s*biases\b|\bwandb\b/gi },
    { name: 'DVC', pattern: /\bdvc\b/gi },
    { name: 'Label Studio', pattern: /\blabel\s*studio\b/gi },
    { name: 'Comet', pattern: /\bcomet\s*(ml)?\b/gi },
    { name: 'ClearML', pattern: /\bclearml\b/gi },
    { name: 'Feast', pattern: /\bfeast\b/gi },
    { name: 'Tecton', pattern: /\btecton\b/gi },

    // LLM & GenAI
    { name: 'Anthropic', pattern: /\banthropic\b/gi },
    { name: 'Mistral', pattern: /\bmistral\b/gi },
    { name: 'Cohere', pattern: /\bcohere\b/gi },
    { name: 'Gemini', pattern: /\bgemini\b/gi },
    { name: 'Claude', pattern: /\bclaude\b/gi },
    { name: 'LlamaIndex', pattern: /\bllamaindex\b|\bllama\s*index\b/gi },
    { name: 'Haystack', pattern: /\bhaystack\b/gi },
    { name: 'AutoGPT', pattern: /\bautogpt\b/gi },
    { name: 'CrewAI', pattern: /\bcrewai\b/gi },
    { name: 'Semantic Kernel', pattern: /\bsemantic\s*kernel\b/gi },

    // Databases
    { name: 'CockroachDB', pattern: /\bcockroachdb\b|\bcockroach\s*db\b/gi },
    { name: 'TimescaleDB', pattern: /\btimescaledb\b|\btimescale\b/gi },
    { name: 'InfluxDB', pattern: /\binfluxdb\b|\binflux\b/gi },
    { name: 'QuestDB', pattern: /\bquestdb\b/gi },
    { name: 'SingleStore', pattern: /\bsinglestore\b/gi },
    { name: 'Supabase', pattern: /\bsupabase\b/gi },
    { name: 'PlanetScale', pattern: /\bplanetscale\b/gi },

    // Viz & BI
    { name: 'Metabase', pattern: /\bmetabase\b/gi },
    { name: 'Apache Superset', pattern: /\bsuperset\b/gi },
    { name: 'Redash', pattern: /\bredash\b/gi },
    { name: 'Mode', pattern: /\bmode\s*analytics\b/gi },
    { name: 'Hex', pattern: /\bhex\b/gi },
    { name: 'Observable', pattern: /\bobservable\b/gi },
    { name: 'Sigma', pattern: /\bsigma\s*(computing)?\b/gi },

    // Streaming
    { name: 'Pulsar', pattern: /\bapache\s*pulsar\b|\bpulsar\b/gi },
    { name: 'Kinesis', pattern: /\bkinesis\b/gi },
    { name: 'Pub/Sub', pattern: /\bpub\/?sub\b/gi },
    { name: 'RabbitMQ', pattern: /\brabbitmq\b/gi },
    { name: 'NATS', pattern: /\bnats\b/gi },

    // Orchestration
    { name: 'Dagster', pattern: /\bdagster\b/gi },
    { name: 'Prefect', pattern: /\bprefect\b/gi },
    { name: 'Mage', pattern: /\bmage\.ai\b|\bmage\s*ai\b/gi },
    { name: 'Argo', pattern: /\bargo\s*(workflows|cd)?\b/gi },
    { name: 'Temporal', pattern: /\btemporal\b/gi },

    // Languages
    { name: 'Rust', pattern: /\brust\b/gi },
    { name: 'Julia', pattern: /\bjulia\b/gi },
    { name: 'Elixir', pattern: /\belixir\b/gi },
    { name: 'Clojure', pattern: /\bclojure\b/gi },
];

// Connect to database
const db = new Database(DB_PATH, { readonly: true });
const jobs = db.prepare(`
    SELECT job_description FROM jobs WHERE job_description IS NOT NULL
`).all();
db.close();

console.log(`Scanning ${jobs.length} job descriptions for potential missing skills...\n`);

const results = [];

for (const { name, pattern } of potentialSkills) {
    // Skip if already in reference
    if (referenceSkillsLower.has(name.toLowerCase())) {
        continue;
    }

    let count = 0;
    for (const job of jobs) {
        if (pattern.test(job.job_description)) {
            count++;
        }
        // Reset regex lastIndex
        pattern.lastIndex = 0;
    }

    if (count > 0) {
        results.push({ name, count });
    }
}

results.sort((a, b) => b.count - a.count);

console.log('='.repeat(60));
console.log('POTENTIAL MISSING SKILLS (not in reference file)');
console.log('='.repeat(60));
console.log(`${'Skill'.padEnd(30)} ${'Mentions'.padStart(10)}`);
console.log('-'.repeat(42));

for (const { name, count } of results) {
    console.log(`${name.padEnd(30)} ${String(count).padStart(10)}`);
}

console.log(`\nTotal potential missing skills found: ${results.length}`);
console.log('\nNote: Some may already be covered under different names.');
