"""Re-extract skills for all jobs using updated patterns"""
import sqlite3
import time
from src.analysis.skill_extraction.extractor import AdvancedSkillExtractor

def main():
    print("Initializing skill extractor...")
    extractor = AdvancedSkillExtractor('src/config/skills_reference_2026.json')

    conn = sqlite3.connect('data/jobs.db')
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM jobs WHERE job_description IS NOT NULL")
    total = cursor.fetchone()[0]
    print(f"Re-extracting skills for {total} jobs...")

    start_time = time.time()

    cursor.execute("SELECT job_id, job_description FROM jobs WHERE job_description IS NOT NULL")
    jobs = cursor.fetchall()

    for i, (job_id, description) in enumerate(jobs, 1):
        if description:
            skills = extractor.extract(description)
            skills_str = ', '.join(skills) if skills else ''
            cursor.execute("UPDATE jobs SET skills = ? WHERE job_id = ?", (skills_str, job_id))

        if i % 200 == 0:
            conn.commit()
            elapsed = time.time() - start_time
            rate = i / elapsed
            remaining = (total - i) / rate
            print(f"Progress: {i}/{total} ({i*100//total}%) - {rate:.1f} jobs/sec - ETA: {remaining:.0f}s")

    conn.commit()
    conn.close()

    elapsed = time.time() - start_time
    print(f"\nCompleted! Processed {total} jobs in {elapsed:.1f} seconds ({total/elapsed:.1f} jobs/sec)")

if __name__ == "__main__":
    main()
