#!/usr/bin/env python3
"""
Comprehensive Skills Validation Test
Detects false positives and false negatives in skill extraction
"""

import sys
import sqlite3
import json
from pathlib import Path
from collections import defaultdict

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from src.analysis.skill_extraction.skill_validator import SkillValidator

def load_canonical_skills(reference_path: str) -> set[str]:
    """Load all canonical skill names from reference file"""
    with open(reference_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    skills = set()
    for skills_list in data['skills'].values():
        for skill in skills_list:
            skills.add(skill['name'])
    
    return skills

def detect_false_positives_negatives() -> None:
    """Comprehensive false positive/negative detection"""
    
    validator = SkillValidator('src/config/skills_reference_2026.json')
    canonical_skills = load_canonical_skills('src/config/skills_reference_2026.json')
    
    conn = sqlite3.connect('data/jobs.db')
    cursor = conn.cursor()
    
    # Get all LinkedIn jobs
    cursor.execute('''
        SELECT job_id, actual_role, job_description, skills 
        FROM jobs 
        WHERE platform='linkedin'
        AND job_description IS NOT NULL
    ''')
    
    jobs = cursor.fetchall()
    
    false_positives = defaultdict(int)
    false_negatives = defaultdict(list)
    total_jobs = len(jobs)
    
    print("="*80)
    print("🔍 FALSE POSITIVE / FALSE NEGATIVE DETECTION")
    print("="*80)
    print(f"Analyzing {total_jobs} LinkedIn jobs...")
    print(f"Canonical Skills Reference: {len(canonical_skills)} skills\n")
    
    for job_id, description, skills_str in [(j[0], j[2], j[3]) for j in jobs]:
        if not skills_str:
            continue
            
        stored_skills = set([s.strip() for s in skills_str.split(',') if s.strip()])
        
        # Detect FALSE POSITIVES (skills not in canonical reference)
        for skill in stored_skills:
            if skill not in canonical_skills:
                false_positives[skill] += 1
        
        # Detect FALSE NEGATIVES (skills in description but not extracted)
        canonical_in_desc = validator.validate_and_extract(description)
        missing = canonical_in_desc - stored_skills
        
        if missing:
            false_negatives[job_id] = list(missing)
    
    # Report FALSE POSITIVES
    print("\n❌ FALSE POSITIVES (Skills NOT in canonical reference):")
    print("="*80)
    
    if false_positives:
        sorted_fps = sorted(false_positives.items(), key=lambda x: int(x[1]), reverse=True)
        for skill, count in sorted_fps[:20]:  # Top 20
            print(f"  • {skill}: {count} occurrences")
        
        print(f"\nTotal False Positive Types: {len(false_positives)}")
        print(f"Total False Positive Instances: {sum(false_positives.values())}")
    else:
        print("  ✅ NONE - All extracted skills are in canonical reference!")
    
    # Report FALSE NEGATIVES
    print(f"\n⚠️  FALSE NEGATIVES (Skills in description but not extracted):")
    print("="*80)
    
    if false_negatives:
        # Count missing skills across all jobs
        missing_counts = defaultdict(int)
        for missing_list in false_negatives.values():
            for skill in missing_list:
                missing_counts[skill] += 1
        
        sorted_fns = sorted(missing_counts.items(), key=lambda x: int(x[1]), reverse=True)
        print(f"\nMost Frequently Missing Skills:")
        for skill, count in sorted_fns[:20]:  # Top 20
            print(f"  • {skill}: missing in {count} job(s)")
        
        print(f"\nTotal Jobs with False Negatives: {len(false_negatives)}")
        print(f"Total False Negative Types: {len(missing_counts)}")
    else:
        print("  ✅ NONE - All relevant skills extracted!")
    
    # Overall Statistics
    print(f"\n{'='*80}")
    print("📊 VALIDATION STATISTICS")
    print(f"{'='*80}")
    print(f"Total Jobs Analyzed: {total_jobs}")
    print(f"Canonical Skills: {len(canonical_skills)}")
    print(f"False Positive Rate: {(len(false_positives)/len(canonical_skills)*100):.2f}%")
    print(f"Jobs with False Negatives: {len(false_negatives)}")
    print(f"False Negative Rate: {(len(false_negatives)/total_jobs*100):.2f}%")
    print("="*80)
    
    conn.close()

if __name__ == '__main__':
    detect_false_positives_negatives()
