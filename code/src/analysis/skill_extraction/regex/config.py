"""Configuration for skill extraction"""

from pathlib import Path

# Path to skills reference JSON
SKILLS_JSON_PATH = (
    Path(__file__).parent.parent.parent.parent / "config" / "skills_reference_2026.json"
)

# Categories to exclude from extraction
EXCLUDED_CATEGORIES = {
    "industry_domains",
    "soft_skills",
    "business_domains",
    "sectors",
    "domains",
    "industries",
}

# Specific non-technical skills to exclude
EXCLUDED_SKILLS = {
    "Education",
    "Healthcare",
    "Financial Services",
    "E-commerce",
    "Manufacturing",
    "Telecommunications",
}
