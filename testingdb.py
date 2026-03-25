# testingdb.py
from app.data.database import EHRDatabase
from datetime import datetime

def test_database_operations():
    """Test the database with Fitbit data import"""
    
    # Use test database file
    db = EHRDatabase("test.db")
    
    try:
        print("=" * 50)
        print("TESTING DATABASE OPERATIONS")
        print("=" * 50)
        
        # STEP 1: Add a test patient
        print("\n1. Adding test patient...")
        patient_id = db.add_patient("Test User", "test_fitbit_123")
        print(f"   → Patient ID: {patient_id}")
        
        if not patient_id:
            # Patient might already exist, try to get existing
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
        
        # STEP 3: Import Fitbit data
        print("\n3. Importing Fitbit data...")
        print("   (This will make API calls - may take a moment)")
        
        imported = db.import_fitbit_data(
            patient_id=patient_id,
            start=start,
            end=end,
            include_intraday=True  # Get minute-level data too
        )
        
        print(f"   → Imported {imported} total records")
        
        # STEP 4: Verify daily data
        print("\n4. Verifying DAILY data:")
        daily_data = db.get_patient_daily_health_data(patient_id, start, end)
        
        if daily_data:
            for date, steps, source in daily_data:
                print(f"   • {date}: {steps} steps ({source})")
        else:
            print("   → No daily data found")
        
        # STEP 5: Verify intraday data (first day only, sample)
        print("\n5. Sampling INTRADAY data (first day, first 5 entries):")
        intraday_data = db.get_intraday_by_date(patient_id, "2025-08-11")
        
        if intraday_data:
            sample = intraday_data[:5]  # First 5 entries
            for timestamp, steps, source in sample:
                print(f"   • {timestamp}: {steps} steps")
            print(f"   → ... and {len(intraday_data) - 5} more entries for this day")
        else:
            print("   → No intraday data found")
        
        # STEP 6: Check latest entry dates
        print("\n6. Latest data entries:")
        latest_daily = db.get_latest_daily_health_entry_date(patient_id)
        latest_intraday = db.get_latest_intraday_health_entry_date(patient_id)
        
        print(f"   • Latest daily: {latest_daily}")
        print(f"   • Latest intraday: {latest_intraday}")
        
        # STEP 7: Export to CSV (optional)
        print("\n7. Exporting to CSV...")
        csv_file = "test_export.csv"
        exported = db.export_patient_to_csv(patient_id, csv_file)
        print(f"   → Exported {exported} records to {csv_file}")
        
        print("\n" + "=" * 50)
        print("✅ TEST COMPLETE")
        print("=" * 50)
        
        return patient_id
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        return None
    finally:
        db.close()

def clear_test_data(patient_id=None):
    """Optional: Clean up test data"""
    db = EHRDatabase("test.db")
    
    if patient_id:
        # Delete specific patient
        print(f"\nCleaning up: Deleting patient {patient_id}...")
        db.delete_patient(patient_id)
    else:
        # Ask user
        response = input("\nDelete ALL test data? (y/n): ")
        if response.lower() == 'y':
            # Get all patients
            patients = db.get_all_patients()
            for pid, name in patients:
                print(f"Deleting {name} (ID: {pid})...")
                db.delete_patient(pid)
    
    db.close()

# peek_db.py
import sqlite3

def peek_database(db_path="test.db"):
    """Quick look at what's in your database"""
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("=" * 60)
    print("DATABASE CONTENTS")
    print("=" * 60)
    
    # Check patients
    cursor.execute("SELECT patient_id, name, fitbit_user_id FROM patients")
    patients = cursor.fetchall()
    print(f"\n📋 PATIENTS ({len(patients)}):")
    for p in patients:
        print(f"   ID {p[0]}: {p[1]} (Fitbit: {p[2]})")
    
    if not patients:
        print("   No patients found!")
        conn.close()
        return
    
    patient_id = patients[0][0]
    
    # Daily data
    cursor.execute('''
        SELECT date, steps, heart, source FROM daily_data 
        WHERE patient_id = ? 
        ORDER BY date
    ''', (patient_id,))
    daily = cursor.fetchall()
    
    print(f"\n📊 DAILY DATA (patient {patient_id}):")
    if daily:
        for date, steps, heart, source in daily:
            print(f"   {date}: {steps} steps | {heart} bpm ({source})")
        print(f"   → Total days: {len(daily)}")
    else:
        print("   No daily data found")
    
    # Intraday data - sample

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
        
        cursor.execute('''
            SELECT COUNT(*) FROM intraday_data WHERE patient_id = ?
        ''', (patient_id,))
        total = cursor.fetchone()[0]
        print(f"   → Total intraday records: {total}")
        
        cursor.execute('''
            SELECT MIN(timestamp), MAX(timestamp) FROM intraday_data 
            WHERE patient_id = ?
        ''', (patient_id,))
        min_ts, max_ts = cursor.fetchone()
        print(f"   → Date range: {min_ts} to {max_ts}")
    else:
        print("   No intraday data found")
    
    conn.close()

if __name__ == "__main__":
    patient_id = test_database_operations()

    peek_database("test.db")
    