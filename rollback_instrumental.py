import sqlite3

conn = sqlite3.connect("X:/LylicData/library.db")
with conn:
    cursor = conn.execute("""
        UPDATE tracks 
        SET status = 'lyrics_not_found',
            diagnostic_result = 'not_in_lrclib',
            updated_at = CURRENT_TIMESTAMP
        WHERE status = 'instrumental'
    """)
    print(f"Rollback completed: {cursor.rowcount} tracks reverted to status='lyrics_not_found'.")

cursor = conn.execute("SELECT status, count(*) FROM tracks GROUP BY status")
print("\n=== Restored tracks status breakdown ===")
for row in cursor.fetchall():
    print(f"  {row[0]}: {row[1]}")
