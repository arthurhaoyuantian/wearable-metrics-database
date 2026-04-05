from src.data.database import EHRDatabase
from datetime import datetime

def test_database_operations():
    db = EHRDatabase("test.db")
    
    try:
        print("=" * 50)
        print("TESTING DATABASE OPERATIONS")
        print("=" * 50)
        
        # STEP 1: Add a test patient
        print("\n1. Adding test patient...")
        patient_id = db.add_patient("Test User")
        print(f"   → Patient ID: {patient_id}")
        
        if not patient_id:
            patients = db.get_all_patients()
            for pid, name in patients:
                if name == "Test User":
                    patient_id = pid
                    print(f"   → Using existing patient ID: {patient_id}")
                    break
        
        # STEP 2: Define test date range
        start = "2025-08-11"
        end = "2025-08-13"
        print(f"\n2. Test date range: {start} to {end}")
        
        # STEP 3: Import Fitbit data (requires valid tokens in DB)
        print("\n3. Importing Fitbit data...")
        print("   (This will make API calls - may take a moment)")
        
        imported = db.import_fitbit_data(
            patient_id=patient_id,
            start=start,
            end=end,
            include_intraday=True
        )
        print(f"   → Imported {imported} total records")
        
        # STEP 4: Verify daily data
        print("\n4. Verifying DAILY data:")
        daily_data = db.get_patient_daily_health_data(patient_id, start, end)
        
        if daily_data:
            for date, steps, heart, source in daily_data:
                print(f"   • {date}: {steps} steps | {heart} bpm ({source})")
        else:
            print("   → No daily data found")
        
        # STEP 5: Sample intraday data
        print("\n5. Sampling INTRADAY data (first day, first 5 entries):")
        intraday_data = db.get_intraday_by_date(patient_id, "2025-08-11")
        
        if intraday_data:
            for timestamp, steps, heart, source in intraday_data[:5]:
                print(f"   • {timestamp}: {steps} steps | {heart} bpm")
            print(f"   → ... and {len(intraday_data) - 5} more entries for this day")
        else:
            print("   → No intraday data found")
        
        # STEP 6: Check latest entry dates
        print("\n6. Latest data entries:")
        print(f"   • Latest daily: {db.get_latest_daily_health_entry_date(patient_id)}")
        print(f"   • Latest intraday: {db.get_latest_intraday_health_entry_date(patient_id)}")
        
        # STEP 7: Export to CSV
        print("\n7. Exporting to CSV...")
        exported = db.export_patient_to_csv(patient_id, "test_export.csv")
        print(f"   → Exported {exported} records to test_export.csv")
        
        print("\n" + "=" * 50)
        print("✅ TEST COMPLETE")
        print("=" * 50)
        
        return patient_id
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        return None
    finally:
        db.close()


def peek_database(db_path="test.db"):
    import sqlite3
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("=" * 60)
    print("DATABASE CONTENTS")
    print("=" * 60)
    
    cursor.execute("SELECT patient_id, name, fitbit_access_token FROM patients")
    patients = cursor.fetchall()
    print(f"\n📋 PATIENTS ({len(patients)}):")
    for p in patients:
        token_preview = p[2][:10] + "..." if p[2] else "None"
        print(f"   ID {p[0]}: {p[1]} (token: {token_preview})")
    
    if not patients:
        print("   No patients found!")
        conn.close()
        return
    
    patient_id = patients[0][0]
    
    cursor.execute('''
        SELECT date, steps, heart, source FROM daily_data 
        WHERE patient_id = ? ORDER BY date
    ''', (patient_id,))
    daily = cursor.fetchall()
    
    print(f"\n📊 DAILY DATA (patient {patient_id}):")
    if daily:
        for date, steps, heart, source in daily:
            print(f"   {date}: {steps} steps | {heart} bpm ({source})")
        print(f"   → Total days: {len(daily)}")
    else:
        print("   No daily data found")
    
    cursor.execute('''
        SELECT timestamp, steps, heart FROM intraday_data 
        WHERE patient_id = ? 
        AND timestamp BETWEEN '2025-08-11 13:20:00' AND '2025-08-11 14:00:59'
        ORDER BY timestamp
    ''', (patient_id,))
    intraday_sample = cursor.fetchall()
    
    print(f"\n⏱️  INTRADAY DATA SAMPLE:")
    if intraday_sample:
        for ts, steps, heart in intraday_sample:
            print(f"   {ts}: {steps} steps | {heart} bpm")
        
        cursor.execute('SELECT COUNT(*) FROM intraday_data WHERE patient_id = ?', (patient_id,))
        print(f"   → Total intraday records: {cursor.fetchone()[0]}")
        
        cursor.execute('SELECT MIN(timestamp), MAX(timestamp) FROM intraday_data WHERE patient_id = ?', (patient_id,))
        min_ts, max_ts = cursor.fetchone()
        print(f"   → Date range: {min_ts} to {max_ts}")
    else:
        print("   No intraday data found")
    
    conn.close()


if __name__ == "__main__":
    patient_id = test_database_operations()
    peek_database("test.db")