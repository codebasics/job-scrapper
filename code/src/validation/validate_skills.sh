#!/bin/bash
# Fast Skill Validation Layer - Uses grep for speed
# Validates extracted skills against job descriptions

DB_PATH="${1:-data/jobs.db}"
SKILLS_JSON="src/config/skills_reference_2026.json"

echo "=== FAST SKILL VALIDATION LAYER ==="
echo "Database: $DB_PATH"

# Export job descriptions to temp file for fast grep
sqlite3 "$DB_PATH" "SELECT job_id, job_description FROM jobs WHERE job_description IS NOT NULL LIMIT 100" > /tmp/jobs_sample.txt

# Quick validation checks using grep
echo ""
echo "--- Quick Pattern Validation ---"

# Check for common patterns
patterns=(
    "openai:OpenAI|openai"
    "huggingface:HuggingFace|Hugging Face|hugging face"
    "postgresql:PostgreSQL|Postgres|postgres"
    "vertexai:Vertex AI|vertex ai|VertexAI"
    "powerbi:Power BI|PowerBI|power bi"
    "databricks:Databricks|databricks"
    "snowflake:Snowflake|snowflake"
    "langchain:LangChain|langchain"
)

for item in "${patterns[@]}"; do
    name="${item%%:*}"
    pattern="${item#*:}"
    count=$(grep -iEc "$pattern" /tmp/jobs_sample.txt 2>/dev/null || echo "0")
    echo "$name: $count matches in sample"
done

# Cleanup
rm -f /tmp/jobs_sample.txt

echo ""
echo "Validation complete. Run Python pipeline for full extraction."
