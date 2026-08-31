#!/bin/bash
# ============================================================================
# LAYER 1: Pattern Syntax Validation
# Validates all regex patterns in skills_reference_2026.json
# Catches broken patterns before extraction runs
# ============================================================================

set -e

SKILLS_REF="${1:-src/config/skills_reference_2026.json}"

echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║           LAYER 1: PATTERN SYNTAX VALIDATION                         ║"
echo "║           Pre-flight check for regex patterns                        ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""
echo "Skills Reference: $SKILLS_REF"
echo ""

node -e "
const fs = require('fs');

const skillsData = JSON.parse(fs.readFileSync('$SKILLS_REF', 'utf8'));

let totalPatterns = 0;
let validPatterns = 0;
let invalidPatterns = [];
let emptyPatterns = [];
let duplicatePatterns = [];
let riskyPatterns = [];

const allPatterns = new Set();

for (const skill of skillsData.skills) {
    if (!skill.patterns || skill.patterns.length === 0) {
        emptyPatterns.push(skill.name);
        continue;
    }

    for (const pattern of skill.patterns) {
        totalPatterns++;

        // Check for empty/whitespace
        if (!pattern || pattern.trim() === '') {
            emptyPatterns.push(skill.name + ': empty pattern');
            continue;
        }

        // Check for duplicates
        if (allPatterns.has(pattern)) {
            duplicatePatterns.push({ skill: skill.name, pattern });
        }
        allPatterns.add(pattern);

        // Check syntax validity
        try {
            new RegExp(pattern, 'i');
            validPatterns++;

            // Check for risky patterns (too broad)
            if (pattern.length < 5 && !pattern.includes('\\\\b')) {
                riskyPatterns.push({ skill: skill.name, pattern, reason: 'Too short, no word boundary' });
            }

            // Check for catastrophic backtracking potential
            if (/(\.\*){2,}|(\.\+){2,}/.test(pattern)) {
                riskyPatterns.push({ skill: skill.name, pattern, reason: 'Potential catastrophic backtracking' });
            }

        } catch (e) {
            invalidPatterns.push({ skill: skill.name, pattern, error: e.message });
        }
    }
}

console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
console.log('PATTERN STATISTICS');
console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
console.log('Total Skills:      ' + skillsData.skills.length);
console.log('Total Patterns:    ' + totalPatterns);
console.log('Valid Patterns:    ' + validPatterns);
console.log('Invalid Patterns:  ' + invalidPatterns.length);
console.log('Empty Patterns:    ' + emptyPatterns.length);
console.log('Duplicate Patterns:' + duplicatePatterns.length);
console.log('Risky Patterns:    ' + riskyPatterns.length);
console.log('');

if (invalidPatterns.length > 0) {
    console.log('┌────────────────────────────────────────────────────────────────┐');
    console.log('│ ❌ INVALID PATTERNS (will cause extraction errors)            │');
    console.log('├────────────────────────────────────────────────────────────────┤');
    for (const item of invalidPatterns.slice(0, 20)) {
        console.log('│ ' + item.skill.substring(0, 20).padEnd(20) + ' │ ' + item.error.substring(0, 40));
    }
    console.log('└────────────────────────────────────────────────────────────────┘');
    console.log('');
}

if (riskyPatterns.length > 0) {
    console.log('┌────────────────────────────────────────────────────────────────┐');
    console.log('│ ⚠️  RISKY PATTERNS (may cause FP or performance issues)        │');
    console.log('├────────────────────────────────────────────────────────────────┤');
    for (const item of riskyPatterns.slice(0, 15)) {
        console.log('│ ' + item.skill.substring(0, 18).padEnd(18) + ' │ ' + item.reason.substring(0, 42));
    }
    console.log('└────────────────────────────────────────────────────────────────┘');
    console.log('');
}

if (duplicatePatterns.length > 0) {
    console.log('┌────────────────────────────────────────────────────────────────┐');
    console.log('│ 🔄 DUPLICATE PATTERNS                                          │');
    console.log('├────────────────────────────────────────────────────────────────┤');
    for (const item of duplicatePatterns.slice(0, 10)) {
        console.log('│ ' + item.skill.substring(0, 25).padEnd(25) + ' │ ' + item.pattern.substring(0, 35));
    }
    console.log('└────────────────────────────────────────────────────────────────┘');
    console.log('');
}

// Health score
const healthScore = ((validPatterns / totalPatterns) * 100).toFixed(1);
console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
console.log('PATTERN HEALTH SCORE: ' + healthScore + '%');
console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');

if (invalidPatterns.length > 0) {
    console.log('');
    console.log('❌ FAILED: ' + invalidPatterns.length + ' invalid patterns found');
    process.exit(1);
} else {
    console.log('');
    console.log('✅ PASSED: All patterns are syntactically valid');
}
"

echo ""
echo "✓ Layer 1 syntax validation complete"
