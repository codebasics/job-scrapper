#!/usr/bin/env node
/**
 * LAYER 8: Autonomous Skill Optimizer
 * Automatically discovers, validates, and adds new skills to the reference file
 *
 * Features:
 * - Reads emerging skills from Layer 6 output
 * - Filters non-skills using intelligent classification
 * - Generates proper regex patterns following existing format
 * - Auto-merges into skills_reference_2026.json with backup
 * - Tracks optimization history
 */

const Database = require('better-sqlite3');
const fs = require('fs');
const path = require('path');

// Configuration
const CONFIG = {
    DB_PATH: 'data/jobs.db',
    SKILLS_REF: 'src/config/skills_reference_2026.json',
    CANDIDATES_FILE: 'data/emerging_skills_candidates.json',
    HISTORY_FILE: 'data/skill_optimization_history.json',
    BACKUP_DIR: 'data/backups',

    // Thresholds
    MIN_COVERAGE: 1.0,        // Minimum % coverage to consider (1%)
    MIN_JOB_COUNT: 10,        // Minimum job mentions
    MAX_AUTO_ADD: 20,         // Max skills to add per run
    CONFIDENCE_THRESHOLD: 0.7 // Min confidence to auto-add
};

// Non-skill patterns (will be excluded)
const NON_SKILL_PATTERNS = [
    // Currency/Finance
    /^(USD|CAD|EUR|GBP|INR|AUD|JPY|CNY|CHF)$/i,
    /^\$?\d+[KkMm]?$/,

    // Location/Region codes
    /^(LATAM|LatAm|EMEA|APAC|AMER|NA|EU|UK)$/i,
    /^[A-Z]{2}$/,  // Two-letter country codes

    // Company/HR terms
    /^(CEO|CFO|CTO|COO|VP|HR|PTO|EEO|EEOC|DOE|EOE|OTE)$/i,
    /^(Inc|LLC|Ltd|Corp|Co)$/i,
    /^(FTE|W2|C2C|B2B|B2C)$/i,

    // Job-related non-skills
    /^(ASAP|TBD|N\/A|TBA|WFH|RTO|OOO)$/i,
    /^J-\d+$/i,  // Job IDs like J-18808

    // Education/Certification
    /^(GPA|PhD|MBA|MSc|BSc|BA|BS|MS|MD)$/i,
    /^(MIT|Stanford|Harvard|Berkeley)$/i,

    // Generic terms
    /^(open|stable|next|new|best|top|pro)$/i,
    /^(team|role|job|work|lead|senior|junior|staff)$/i,

    // Benefit/Admin codes
    /^(HSA|FSA|401K|QPIP|ARR|MRR|KPI|OKR|SLA)$/i,
    /^(PAY|PAID|BONUS|SALARY)$/i,

    // Time-related
    /^(AM|PM|EST|PST|UTC|GMT)$/i,
    /^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)$/i,

    // Company-specific identifiers
    /^(DaCodes|DaCoders)$/i,
    /^\d{4,}$/,  // Pure numbers

    // Spanish/Other language non-skills
    /^(automatizado|experiencia|empresa|trabajo)$/i,
];

// Valid skill categories and their detection patterns
const SKILL_CATEGORIES = {
    'Programming Languages': {
        patterns: [/^(Rust|Go|Julia|Elixir|Clojure|Scala|Kotlin|Swift|Ruby|Perl|PHP|R|MATLAB)$/i],
        confidence: 0.95
    },
    'Cloud Services': {
        patterns: [/^(AWS|GCP|Azure|ECS|EKS|GKE|AKS|Lambda|EC2|S3|RDS|DynamoDB|SageMaker|Bedrock)$/i, /Cloud|Stack/i],
        confidence: 0.9
    },
    'AI/ML Tools': {
        patterns: [/^(LLM|GenAI|GPT|Llama|Mistral|Claude|Gemini|Anthropic|OpenAI|Cohere)$/i,
                   /^(LangChain|LlamaIndex|CrewAI|AutoGen|RAG|RLHF|PEFT|LoRA|QLoRA)$/i,
                   /^(Hugging\s?Face|Transformers|PyTorch|TensorFlow|JAX|MXNet)$/i],
        confidence: 0.95
    },
    'Data Tools': {
        patterns: [/^(dbt|Airflow|Dagster|Prefect|Spark|Kafka|Flink|Beam|Hive|Presto|Trino)$/i,
                   /^(Snowflake|Databricks|BigQuery|Redshift|Synapse|Clickhouse|DuckDB)$/i,
                   /^(Fivetran|Airbyte|Stitch|Matillion|Talend|Informatica|Alteryx)$/i],
        confidence: 0.9
    },
    'Databases': {
        patterns: [/^(PostgreSQL|MySQL|MongoDB|Redis|Cassandra|DynamoDB|CosmosDB|Neo4j)$/i,
                   /^(CockroachDB|TimescaleDB|InfluxDB|QuestDB|SingleStore|Supabase|PlanetScale)$/i,
                   /DB$/i],
        confidence: 0.85
    },
    'DevOps/Infrastructure': {
        patterns: [/^(Docker|Kubernetes|Terraform|Pulumi|Ansible|Chef|Puppet|Helm)$/i,
                   /^(Jenkins|GitLab|GitHub|CircleCI|ArgoCD|Flux|Crossplane)$/i,
                   /^(Prometheus|Grafana|Datadog|NewRelic|Splunk|ELK|Jaeger)$/i],
        confidence: 0.9
    },
    'Frameworks': {
        patterns: [/^(React|Vue|Angular|Svelte|Next|Nuxt|Remix|Astro|Solid)$/i,
                   /^(FastAPI|Flask|Django|Express|NestJS|Spring|Rails)$/i,
                   /^(Node|Deno|Bun)$/i],
        confidence: 0.85
    },
    'BI/Visualization': {
        patterns: [/^(Tableau|PowerBI|Looker|Metabase|Superset|Redash|Mode|Hex|Sigma)$/i,
                   /^(QuickSight|Grafana|Kibana|Observable|Streamlit|Dash|Plotly)$/i],
        confidence: 0.9
    },
    'Computer Vision/Image': {
        patterns: [/^(OpenCV|YOLO|Detectron|MMDetection|Tesseract|PIL|Pillow)$/i,
                   /^(CUDA|cuDNN|TensorRT|ONNX|OpenVINO)$/i],
        confidence: 0.85
    },
    'NLP/Text': {
        patterns: [/^(spaCy|NLTK|Gensim|fastText|BERT|GPT|T5|RoBERTa)$/i,
                   /^(Tokenizer|Embedding|TTS|STT|ASR|NER|POS)$/i],
        confidence: 0.85
    }
};

/**
 * Check if a term is a non-skill
 */
function isNonSkill(term) {
    for (const pattern of NON_SKILL_PATTERNS) {
        if (pattern.test(term)) {
            return true;
        }
    }
    return false;
}

/**
 * Classify a term and return confidence score
 */
function classifySkill(term) {
    // First check if it's a non-skill
    if (isNonSkill(term)) {
        return { isSkill: false, category: 'non-skill', confidence: 0 };
    }

    // Check against skill categories
    for (const [category, config] of Object.entries(SKILL_CATEGORIES)) {
        for (const pattern of config.patterns) {
            if (pattern.test(term)) {
                return {
                    isSkill: true,
                    category,
                    confidence: config.confidence
                };
            }
        }
    }

    // Heuristics for unknown terms
    let confidence = 0.5;

    // CamelCase tech names get higher confidence
    if (/^[A-Z][a-z]+(?:[A-Z][a-z]+)+$/.test(term)) {
        confidence += 0.2;
    }

    // Ends with common tech suffixes
    if (/(?:DB|ML|AI|API|SDK|CLI|JS|TS|IO)$/i.test(term)) {
        confidence += 0.15;
    }

    // Contains version numbers (e.g., React18, Python3)
    if (/\d+/.test(term) && !/^\d+$/.test(term)) {
        confidence += 0.1;
    }

    // All uppercase (acronyms) - moderate confidence
    if (/^[A-Z]{3,6}$/.test(term)) {
        confidence += 0.1;
    }

    return {
        isSkill: confidence >= 0.6,
        category: 'Unknown',
        confidence: Math.min(confidence, 0.8)
    };
}

/**
 * Generate regex patterns for a skill name
 */
function generatePatterns(skillName) {
    const patterns = [];
    const name = skillName.trim();

    // Exact match - UPPERCASE
    patterns.push(`\\\\b${escapeRegex(name.toUpperCase())}\\\\b`);

    // Exact match - Original case
    if (name !== name.toUpperCase()) {
        patterns.push(`\\\\b${escapeRegex(name)}\\\\b`);
    }

    // Exact match - lowercase
    if (name !== name.toLowerCase()) {
        patterns.push(`\\\\b${escapeRegex(name.toLowerCase())}\\\\b`);
    }

    // Handle multi-word skills
    if (name.includes(' ')) {
        const words = name.split(/\s+/);
        const joined = words.join('');
        const underscored = words.join('_');
        const hyphenated = words.join('-');

        // CamelCase version
        patterns.push(`\\\\b${escapeRegex(joined)}\\\\b`);

        // Underscore version
        patterns.push(`\\\\b${escapeRegex(underscored.toLowerCase())}\\\\b`);

        // Hyphenated version
        patterns.push(`\\\\b${escapeRegex(hyphenated.toLowerCase())}\\\\b`);

        // Flexible whitespace
        patterns.push(`\\\\b${words.map(w => escapeRegex(w.toLowerCase())).join('\\\\s+')}\\\\b`);
    }

    // Handle CamelCase (split and allow spaces)
    const camelWords = name.match(/[A-Z][a-z]+|[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)/g);
    if (camelWords && camelWords.length > 1) {
        const spacedLower = camelWords.join(' ').toLowerCase();
        if (!patterns.includes(`\\\\b${escapeRegex(spacedLower)}\\\\b`)) {
            patterns.push(`\\\\b${escapeRegex(spacedLower)}\\\\b`);
        }
    }

    // Remove duplicates
    return [...new Set(patterns)];
}

/**
 * Escape special regex characters
 */
function escapeRegex(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Load existing skills reference
 */
function loadSkillsReference() {
    const data = JSON.parse(fs.readFileSync(CONFIG.SKILLS_REF, 'utf8'));
    return data;
}

/**
 * Check if skill already exists (by name OR by pattern match)
 */
function skillExists(skillsData, skillName) {
    const nameLower = skillName.toLowerCase();

    // Check by name (exact match or contains)
    for (const skill of skillsData.skills) {
        const skillNameLower = skill.name.toLowerCase();

        // Exact match
        if (skillNameLower === nameLower) {
            return true;
        }

        // Name contains the term (e.g., "Amazon Web Services (AWS)" contains "AWS")
        if (skillNameLower.includes(nameLower) || nameLower.includes(skillNameLower)) {
            return true;
        }

        // Check if skill name has the term in parentheses (e.g., "Large Language Models (LLMs)")
        const parenMatch = skill.name.match(/\(([^)]+)\)/);
        if (parenMatch && parenMatch[1].toLowerCase() === nameLower) {
            return true;
        }
    }

    // Check if any existing pattern would match this term
    for (const skill of skillsData.skills) {
        for (const patternStr of skill.patterns) {
            try {
                const pattern = new RegExp(patternStr, 'i');
                if (pattern.test(skillName) || pattern.test(` ${skillName} `)) {
                    return true;
                }
            } catch (e) {
                // Invalid regex, skip
            }
        }
    }

    return false;
}

/**
 * Add new skill to reference
 */
function addSkill(skillsData, skillName, patterns, category = 'Unknown') {
    const newSkill = {
        name: skillName,
        patterns: patterns
    };

    skillsData.skills.push(newSkill);
    skillsData.total_skills = skillsData.skills.length;

    // Sort alphabetically
    skillsData.skills.sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));

    return newSkill;
}

/**
 * Create backup of skills reference
 */
function createBackup(skillsData) {
    if (!fs.existsSync(CONFIG.BACKUP_DIR)) {
        fs.mkdirSync(CONFIG.BACKUP_DIR, { recursive: true });
    }

    const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
    const backupPath = path.join(CONFIG.BACKUP_DIR, `skills_reference_backup_${timestamp}.json`);

    fs.writeFileSync(backupPath, JSON.stringify(skillsData, null, 2));
    return backupPath;
}

/**
 * Load optimization history
 */
function loadHistory() {
    if (fs.existsSync(CONFIG.HISTORY_FILE)) {
        return JSON.parse(fs.readFileSync(CONFIG.HISTORY_FILE, 'utf8'));
    }
    return { runs: [], total_skills_added: 0 };
}

/**
 * Save optimization history
 */
function saveHistory(history) {
    fs.writeFileSync(CONFIG.HISTORY_FILE, JSON.stringify(history, null, 2));
}

/**
 * Main optimization function
 */
function runOptimization(options = {}) {
    const {
        dryRun = false,
        minCoverage = CONFIG.MIN_COVERAGE,
        minJobCount = CONFIG.MIN_JOB_COUNT,
        maxAdd = CONFIG.MAX_AUTO_ADD,
        confidenceThreshold = CONFIG.CONFIDENCE_THRESHOLD
    } = options;

    console.log('╔══════════════════════════════════════════════════════════════════════╗');
    console.log('║           LAYER 8: AUTONOMOUS SKILL OPTIMIZER                        ║');
    console.log('║           Auto-discover and add skills to reference                  ║');
    console.log('╚══════════════════════════════════════════════════════════════════════╝');
    console.log('');
    console.log(`Mode: ${dryRun ? 'DRY RUN (no changes)' : 'LIVE (will modify files)'}`);
    console.log(`Min Coverage: ${minCoverage}%`);
    console.log(`Min Job Count: ${minJobCount}`);
    console.log(`Max Skills to Add: ${maxAdd}`);
    console.log(`Confidence Threshold: ${confidenceThreshold}`);
    console.log('');

    // Load candidates from Layer 6
    if (!fs.existsSync(CONFIG.CANDIDATES_FILE)) {
        console.log('ERROR: Candidates file not found. Run Layer 6 first.');
        console.log(`Expected: ${CONFIG.CANDIDATES_FILE}`);
        process.exit(1);
    }

    const candidates = JSON.parse(fs.readFileSync(CONFIG.CANDIDATES_FILE, 'utf8'));
    console.log(`Loaded ${candidates.candidates.length} candidates from Layer 6`);
    console.log('');

    // Load current skills reference
    const skillsData = loadSkillsReference();
    const originalCount = skillsData.total_skills;
    console.log(`Current skills in reference: ${originalCount}`);
    console.log('');

    // Process candidates
    console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
    console.log('ANALYZING CANDIDATES');
    console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
    console.log('');

    const toAdd = [];
    const rejected = [];
    const existing = [];

    for (const candidate of candidates.candidates) {
        const term = candidate.term;
        const coverage = parseFloat(candidate.coverage);
        const count = candidate.count;

        // Skip low coverage/count
        if (coverage < minCoverage || count < minJobCount) {
            continue;
        }

        // Check if already exists
        if (skillExists(skillsData, term)) {
            existing.push({ term, reason: 'Already in reference' });
            continue;
        }

        // Classify the skill
        const classification = classifySkill(term);

        if (!classification.isSkill) {
            rejected.push({ term, reason: 'Non-skill detected', category: classification.category });
            continue;
        }

        if (classification.confidence < confidenceThreshold) {
            rejected.push({ term, reason: `Low confidence (${(classification.confidence * 100).toFixed(0)}%)`, category: classification.category });
            continue;
        }

        // Generate patterns
        const patterns = generatePatterns(term);

        toAdd.push({
            term,
            coverage,
            count,
            category: classification.category,
            confidence: classification.confidence,
            patterns
        });

        if (toAdd.length >= maxAdd) {
            break;
        }
    }

    // Report existing
    if (existing.length > 0) {
        console.log(`⏭️  Skipped ${existing.length} skills (already in reference)`);
    }

    // Report rejected
    if (rejected.length > 0) {
        console.log(`❌ Rejected ${rejected.length} non-skills:`);
        for (const r of rejected.slice(0, 10)) {
            console.log(`   - ${r.term}: ${r.reason}`);
        }
        if (rejected.length > 10) {
            console.log(`   ... and ${rejected.length - 10} more`);
        }
        console.log('');
    }

    // Report skills to add
    console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
    console.log(`SKILLS TO ADD: ${toAdd.length}`);
    console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
    console.log('');

    if (toAdd.length === 0) {
        console.log('No new skills to add.');
        console.log('');
        return { added: 0, rejected: rejected.length, existing: existing.length };
    }

    console.log('┌────────────────────────────────────────────────────────────────────┐');
    console.log('│ Skill Name              │ Coverage │ Conf  │ Category             │');
    console.log('├────────────────────────────────────────────────────────────────────┤');

    for (const skill of toAdd) {
        const namePad = skill.term.substring(0, 23).padEnd(23);
        const covPad = (skill.coverage.toFixed(1) + '%').padStart(8);
        const confPad = ((skill.confidence * 100).toFixed(0) + '%').padStart(5);
        const catPad = skill.category.substring(0, 18).padEnd(18);
        console.log(`│ ${namePad} │ ${covPad} │ ${confPad} │ ${catPad} │`);
    }
    console.log('└────────────────────────────────────────────────────────────────────┘');
    console.log('');

    // Add skills if not dry run
    if (!dryRun) {
        // Create backup first
        const backupPath = createBackup(skillsData);
        console.log(`📦 Backup created: ${backupPath}`);
        console.log('');

        // Add each skill
        const addedSkills = [];
        for (const skill of toAdd) {
            const newSkill = addSkill(skillsData, skill.term, skill.patterns, skill.category);
            addedSkills.push({
                name: skill.term,
                category: skill.category,
                confidence: skill.confidence,
                coverage: skill.coverage,
                patterns: skill.patterns
            });
            console.log(`✅ Added: ${skill.term} (${skill.patterns.length} patterns)`);
        }

        // Save updated reference
        fs.writeFileSync(CONFIG.SKILLS_REF, JSON.stringify(skillsData, null, 2));
        console.log('');
        console.log(`💾 Updated ${CONFIG.SKILLS_REF}`);
        console.log(`   Skills: ${originalCount} → ${skillsData.total_skills} (+${addedSkills.length})`);

        // Update history
        const history = loadHistory();
        history.runs.push({
            timestamp: new Date().toISOString(),
            skills_added: addedSkills.length,
            skills: addedSkills.map(s => s.name),
            backup: backupPath
        });
        history.total_skills_added += addedSkills.length;
        saveHistory(history);
        console.log('');
        console.log(`📊 History updated: ${history.total_skills_added} total skills added across ${history.runs.length} runs`);
    } else {
        console.log('🔍 DRY RUN - No changes made');
        console.log('   Run without --dry-run to apply changes');
    }

    console.log('');
    console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
    console.log('SUMMARY');
    console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
    console.log(`Skills added: ${dryRun ? '0 (dry run)' : toAdd.length}`);
    console.log(`Skills rejected: ${rejected.length}`);
    console.log(`Skills already existing: ${existing.length}`);
    console.log('');
    console.log('✓ Layer 8 autonomous skill optimization complete');

    return {
        added: dryRun ? 0 : toAdd.length,
        rejected: rejected.length,
        existing: existing.length,
        toAdd: toAdd
    };
}

// CLI interface
if (require.main === module) {
    const args = process.argv.slice(2);
    const dryRun = args.includes('--dry-run') || args.includes('-n');

    // Parse optional arguments
    let minCoverage = CONFIG.MIN_COVERAGE;
    let maxAdd = CONFIG.MAX_AUTO_ADD;
    let confidence = CONFIG.CONFIDENCE_THRESHOLD;

    for (let i = 0; i < args.length; i++) {
        if (args[i] === '--min-coverage' && args[i + 1]) {
            minCoverage = parseFloat(args[i + 1]);
        }
        if (args[i] === '--max-add' && args[i + 1]) {
            maxAdd = parseInt(args[i + 1]);
        }
        if (args[i] === '--confidence' && args[i + 1]) {
            confidence = parseFloat(args[i + 1]);
        }
    }

    runOptimization({
        dryRun,
        minCoverage,
        maxAdd,
        confidenceThreshold: confidence
    });
}

module.exports = { runOptimization, classifySkill, generatePatterns };
