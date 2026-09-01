#!/bin/bash
# ============================================================================
# 8-LAYER SKILL VALIDATION ORCHESTRATOR
# Runs all validation layers using patterns from skills_reference_2026.json
# Must be run from project root directory
#
# Layer 8 (Auto-Optimizer) is optional and runs in dry-run mode by default
# Use --auto-add flag to enable automatic skill addition
# ============================================================================

set -e

# Use relative paths (run from project root)
DB_PATH="${1:-data/jobs.db}"
SAMPLE_SIZE="${2:-500}"
SKILLS_REF="src/config/skills_reference_2026.json"
SCRIPT_DIR="scripts/validation"
REPORT_DIR="data/validation_reports"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
REPORT_FILE="$REPORT_DIR/validation_report_$TIMESTAMP.txt"

mkdir -p "$REPORT_DIR"

# Check for --auto-add flag
AUTO_ADD=""
for arg in "$@"; do
    if [ "$arg" = "--auto-add" ]; then
        AUTO_ADD="--auto-add"
    fi
done

echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║                                                                      ║"
echo "║          8-LAYER SKILL VALIDATION SUITE                              ║"
echo "║          Comprehensive Skill Extraction Validation                   ║"
echo "║                                                                      ║"
echo "╠══════════════════════════════════════════════════════════════════════╣"
echo "║  Layer 1: Pattern Syntax Validation                                  ║"
echo "║  Layer 2: Coverage Analysis                                          ║"
echo "║  Layer 3: False Positive Detection                                   ║"
echo "║  Layer 4: False Negative Detection                                   ║"
echo "║  Layer 5: Context Validation                                         ║"
echo "║  Layer 6: Emerging Skills Detection                                  ║"
echo "║  Layer 7: Trend & Drift Analysis                                     ║"
echo "║  Layer 8: Autonomous Skill Optimizer                                 ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""
echo "Database: $DB_PATH"
echo "Skills Reference: $SKILLS_REF"
echo "Sample Size: $SAMPLE_SIZE"
echo "Report File: $REPORT_FILE"
echo ""
echo "Started: $(date)"
echo ""

# Check prerequisites
if [ ! -f "$DB_PATH" ]; then
    echo "❌ ERROR: Database not found at $DB_PATH"
    echo "   Make sure you run this script from the project root directory"
    exit 1
fi

if [ ! -f "$SKILLS_REF" ]; then
    echo "❌ ERROR: Skills reference not found at $SKILLS_REF"
    exit 1
fi

# Get summary stats
COUNTS=$(node -e "
const Database = require('better-sqlite3');
const fs = require('fs');
const db = new Database('$DB_PATH', { readonly: true });
const jobs = db.prepare('SELECT COUNT(*) as c FROM jobs WHERE job_description IS NOT NULL').get();
const skills = db.prepare('SELECT COUNT(*) as c FROM jobs WHERE skills IS NOT NULL').get();
const data = JSON.parse(fs.readFileSync('$SKILLS_REF', 'utf8'));
console.log(jobs.c + '|' + skills.c + '|' + data.skills.length);
db.close();
")

JOB_COUNT=$(echo "$COUNTS" | cut -d'|' -f1)
SKILL_COUNT=$(echo "$COUNTS" | cut -d'|' -f2)
PATTERN_COUNT=$(echo "$COUNTS" | cut -d'|' -f3)

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "DATABASE & REFERENCE SUMMARY"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Total Jobs with Descriptions: $JOB_COUNT"
echo "Total Jobs with Skills:       $SKILL_COUNT"
echo "Skill Patterns in Reference:  $PATTERN_COUNT"
echo ""

# Start report
{
    echo "7-LAYER SKILL EXTRACTION VALIDATION REPORT"
    echo "=========================================="
    echo "Generated: $(date)"
    echo "Database: $DB_PATH"
    echo "Skills Reference: $SKILLS_REF"
    echo "Sample Size: $SAMPLE_SIZE"
    echo ""
    echo "Jobs: $JOB_COUNT | Skills Reference: $PATTERN_COUNT patterns"
    echo ""
} > "$REPORT_FILE"

# ============================================================================
# LAYER 1: Pattern Syntax Validation
# ============================================================================
echo ""
echo "▶ [1/8] Running Layer 1: Pattern Syntax Validation..."
echo ""
bash "$SCRIPT_DIR/layer1_syntax_check.sh" "$SKILLS_REF" 2>&1 | tee -a "$REPORT_FILE"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ============================================================================
# LAYER 2: Coverage Analysis
# ============================================================================
echo ""
echo "▶ [2/8] Running Layer 2: Coverage Analysis..."
echo ""
bash "$SCRIPT_DIR/layer2_coverage.sh" "$DB_PATH" "$SAMPLE_SIZE" 2>&1 | tee -a "$REPORT_FILE"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ============================================================================
# LAYER 3: False Positive Detection
# ============================================================================
echo ""
echo "▶ [3/8] Running Layer 3: False Positive Detection..."
echo ""
bash "$SCRIPT_DIR/layer3_fp_detection.sh" "$DB_PATH" "$SAMPLE_SIZE" 2>&1 | tee -a "$REPORT_FILE"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ============================================================================
# LAYER 4: False Negative Detection
# ============================================================================
echo ""
echo "▶ [4/8] Running Layer 4: False Negative Detection..."
echo ""
bash "$SCRIPT_DIR/layer4_fn_detection.sh" "$DB_PATH" "$SAMPLE_SIZE" 2>&1 | tee -a "$REPORT_FILE"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ============================================================================
# LAYER 5: Context Validation
# ============================================================================
echo ""
echo "▶ [5/8] Running Layer 5: Context Validation..."
echo ""
bash "$SCRIPT_DIR/layer5_context.sh" "$DB_PATH" "$SAMPLE_SIZE" 2>&1 | tee -a "$REPORT_FILE"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ============================================================================
# LAYER 6: Emerging Skills Detection
# ============================================================================
echo ""
echo "▶ [6/8] Running Layer 6: Emerging Skills Detection..."
echo ""
bash "$SCRIPT_DIR/layer6_emerging_skills.sh" "$DB_PATH" "$SAMPLE_SIZE" 5 2>&1 | tee -a "$REPORT_FILE"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ============================================================================
# LAYER 7: Trend & Drift Analysis
# ============================================================================
echo ""
echo "▶ [7/8] Running Layer 7: Trend & Drift Analysis..."
echo ""
bash "$SCRIPT_DIR/layer7_trend_analysis.sh" "$DB_PATH" 2>&1 | tee -a "$REPORT_FILE"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ============================================================================
# LAYER 8: Autonomous Skill Optimizer
# ============================================================================
echo ""
echo "▶ [8/8] Running Layer 8: Autonomous Skill Optimizer..."
echo ""

if [ -n "$AUTO_ADD" ]; then
    echo "   Mode: LIVE (--auto-add flag detected, will modify files)"
    node "$SCRIPT_DIR/auto_skill_optimizer.js" 2>&1 | tee -a "$REPORT_FILE"
else
    echo "   Mode: DRY RUN (preview only, use --auto-add to enable)"
    node "$SCRIPT_DIR/auto_skill_optimizer.js" --dry-run 2>&1 | tee -a "$REPORT_FILE"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Summary
{
    echo ""
    echo "=========================================="
    echo "VALIDATION COMPLETE"
    echo "Completed: $(date)"
} >> "$REPORT_FILE"

echo ""
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║                    ALL 8 LAYERS COMPLETE                             ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""
echo "Reports saved to:"
echo "  - $REPORT_FILE"
echo "  - data/emerging_skills_candidates.json"
echo "  - data/skill_trends_report.json"
echo "  - data/skill_optimization_history.json"
echo ""
echo "Completed: $(date)"
echo ""
echo "┌────────────────────────────────────────────────────────────────────────┐"
echo "│ NEXT STEPS                                                             │"
echo "├────────────────────────────────────────────────────────────────────────┤"
echo "│ 1. Review Layer 1: Fix any invalid patterns                            │"
echo "│ 2. Review Layer 3: Adjust overly broad patterns causing FP             │"
echo "│ 3. Review Layer 4: Add missing patterns for FN skills                  │"
echo "│ 4. Review Layer 6: Add valid emerging skills to reference file         │"
echo "│ 5. Review Layer 7: Monitor skill velocity trends                       │"
echo "│ 6. Layer 8 Auto-Add: Run with --auto-add flag to auto-add skills       │"
echo "│ 7. Re-run extraction after pattern updates                             │"
echo "└────────────────────────────────────────────────────────────────────────┘"
echo ""
echo "To automatically add new skills:"
echo "  bash scripts/validation/run_all_validations.sh --auto-add"
echo ""
