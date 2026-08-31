# Configuration Module

## Purpose
Stores reference data and settings used across the entire system.

## What's Inside
- **skills_reference_2026.json** - The master list of 749 validated technical skills
- **countries.py** - LinkedIn country codes for international job searches
- **naukri_locations.py** - Indian city locations for Naukri searches

## Why It Matters
Centralizing configuration ensures consistency. All modules reference the same skill list, preventing mismatches and ensuring data accuracy across the system.

## Impact
When the canonical skill list is updated here, the entire system automatically uses the new validation rules—no code changes needed.
