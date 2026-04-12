import logging
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

from src.integrations import csv_import, fitbit_import
from src.paths import database_path

logger = logging.getLogger(__name__)


class EHRDatabase:
    #constructor -> connects app to database file and creates the tables
    def __init__(self, db_path=None):
        path = database_path() if db_path is None else Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.create_tables()
        logger.info("EHRDatabase opened at %s", path.resolve())
        
    #creates tables
    def create_tables(self):
        #patients table
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS patients (
                patient_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                fitbit_access_token TEXT UNIQUE,
                fitbit_refresh_token TEXT UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP 
            )
        ''')
        
        #health data by daily averages
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS daily_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER,
                date TEXT,
                steps INTEGER, 
                heart INTEGER,
                source TEXT,
                imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resting_heart_rate INTEGER,
                active_zone_minutes INTEGER,
                spo2_avg REAL,
                blood_pressure_systolic INTEGER,
                blood_pressure_diastolic INTEGER,
                calories REAL,
                hrv REAL,
                sleep_minutes INTEGER,
                distance REAL,
                FOREIGN KEY (patient_id) REFERENCES patients(patient_id),
                UNIQUE (patient_id, date, source)
            )
        ''')
        
        #health data by the minute
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS intraday_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER,
                timestamp TEXT,
                steps INTEGER,
                heart INTEGER,
                source TEXT,
                active_zone_minutes INTEGER,
                spo2 REAL,
                calories REAL,
                hrv REAL,
                distance REAL,
                FOREIGN KEY (patient_id) REFERENCES patients(patient_id),
                UNIQUE(patient_id, timestamp, source)
            )
        ''')
        
        self._migrate_health_columns()
        
        #save changes
        self.conn.commit()

    def _migrate_health_columns(self):
        def add_cols(table, cols):
            existing = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}
            for name, typ in cols:
                if name not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")

        add_cols(
            "daily_data",
            [
                ("resting_heart_rate", "INTEGER"),
                ("active_zone_minutes", "INTEGER"),
                ("spo2_avg", "REAL"),
                ("blood_pressure_systolic", "INTEGER"),
                ("blood_pressure_diastolic", "INTEGER"),
                ("calories", "REAL"),
                ("hrv", "REAL"),
                ("sleep_minutes", "INTEGER"),
                ("distance", "REAL"),
            ],
        )
        add_cols(
            "intraday_data",
            [
                ("active_zone_minutes", "INTEGER"),
                ("spo2", "REAL"),
                ("calories", "REAL"),
                ("hrv", "REAL"),
                ("distance", "REAL"),
            ],
        )
            
    #PATIENTS TABLE CRUD METHODS---------------------------------------------------------------------------------
    
    #returns a list of patient information as tuples 
    def get_all_patients(self):
        cursor = self.conn.execute('SELECT patient_id, name FROM patients ORDER BY name')
        return cursor.fetchall()
    
    #returns individual patient as a tuple 
    def get_patient_info(self, patient_id):
        cursor = self.conn.execute(
            'SELECT patient_id, name, fitbit_access_token, fitbit_refresh_token FROM patients WHERE patient_id = ?',
            (patient_id,)
        )
        return cursor.fetchone()
    
        #returns true if the patient exists 
    def check_patient_exists(self, patient_id):
        cursor = self.conn.execute(
            'SELECT 1 FROM patients WHERE patient_id = ?',
            (patient_id,)
        )
        return cursor.fetchone() is not None
    
    #adds a patient to table
    def add_patient(self, name, fitbit_access_token = None, fitbit_refresh_token = None):
        try:
            cursor = self.conn.execute(
                'INSERT INTO patients (name, fitbit_access_token, fitbit_refresh_token) VALUES (?, ?, ?)',
                (name, fitbit_access_token, fitbit_refresh_token)
            )
            self.conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            return None
    
    def update_patient_info(self, patient_id, name=None, fitbit_access_token = None, fitbit_refresh_token = None):
        if not self.check_patient_exists(patient_id):
            logger.warning("update_patient_info: patient_id=%s not found", patient_id)
            return False
    
        #building query 
        updates = []
        params = [] 
        
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        
        if fitbit_access_token is not None:
            updates.append("fitbit_access_token = ?")
            params.append(fitbit_access_token)
            
        if fitbit_refresh_token is not None:
            updates.append("fitbit_refresh_token = ?")
            params.append(fitbit_refresh_token)
             
        #updates is empty, so no update
        if not updates:
            return True
        
        params.append(patient_id)
        
        try:
            query = f"UPDATE patients SET {', '.join(updates)} WHERE patient_id = ?"
            self.conn.execute(query, params)
            self.conn.commit()
            return True
        
        except Exception as e:
            logger.exception("update_patient_info failed for patient_id=%s: %s", patient_id, e)
            return False

    
    #delete a patient and all associated health data 
    def delete_patient(self, patient_id):
        try: 
            self.conn.execute('DELETE FROM intraday_data WHERE patient_id = ?',
                              (patient_id,))
            self.conn.execute('DELETE FROM daily_data WHERE patient_id = ?',
                              (patient_id,))
            self.conn.execute('DELETE FROM patients WHERE patient_id = ?',
                              (patient_id,))
            self.conn.commit()
            return True
        except Exception as e:
            logger.error("delete_patient failed for patient_id=%s: %s", patient_id, e)
            return False
    
    
    #DAILY HEALTH METRICS CRUD METHODS--------------------------------------------------------------------------
    
    def get_sources_for_patient(self, patient_id):
        """Distinct non-empty source values for this patient (daily + intraday)."""
        cursor = self.conn.execute(
            """
            SELECT DISTINCT source FROM daily_data
            WHERE patient_id = ? AND source IS NOT NULL AND TRIM(source) != ''
            UNION
            SELECT DISTINCT source FROM intraday_data
            WHERE patient_id = ? AND source IS NOT NULL AND TRIM(source) != ''
            ORDER BY source
            """,
            (patient_id, patient_id),
        )
        return [row[0] for row in cursor.fetchall()]

    def daily_dates_existing_for_source(self, patient_id, source, dates):
        """Dates (YYYY-MM-DD strings) in *dates* that already have a daily_data row for this patient and source."""
        if not dates:
            return []
        placeholders = ",".join("?" * len(dates))
        cursor = self.conn.execute(
            f"""
            SELECT date FROM daily_data
            WHERE patient_id = ? AND source = ? AND date IN ({placeholders})
            """,
            (patient_id, source, *dates),
        )
        return sorted({row[0] for row in cursor.fetchall()})

    #returns the health metrics of a patient, filtered by date range if included -> CONNECTION TO UI
    def get_patient_daily_health_data(
        self, patient_id, start_date=None, end_date=None, source=None
    ):
        # Row shape: date, patient_id, source, then metrics A–Z:
        # active_zone_minutes, blood_pressure_diastolic, blood_pressure_systolic, calories,
        # distance, heart, hrv, resting_heart_rate, sleep_minutes, spo2_avg, steps
        query = '''
            SELECT date, patient_id, source,
                   active_zone_minutes, blood_pressure_diastolic, blood_pressure_systolic,
                   calories, distance, heart, hrv, resting_heart_rate, sleep_minutes, spo2_avg, steps
            FROM daily_data 
            WHERE patient_id = ?
        '''
        params = [patient_id]
        
        if start_date:
            query += ' AND date >= ?'
            params.append(start_date)
        if end_date:
            query += ' AND date <= ?'
            params.append(end_date)
        if source is not None:
            query += ' AND source = ?'
            params.append(source)
        
        query += ' ORDER BY date'
        
        cursor = self.conn.execute(query, params)
        return cursor.fetchall() #return all data that matches these params specific to this patient
    
    #returns the latest date where patient health data was updated
    def get_latest_daily_health_entry_date(self, patient_id):
        try:
            if not self.check_patient_exists(patient_id):
                logger.warning("get_latest_daily_health_entry_date: patient_id=%s not found", patient_id)
                return None
            
            cursor = self.conn.execute('''
                SELECT date FROM daily_data
                WHERE patient_id = ?
                ORDER BY date DESC
                LIMIT 1
            ''', (patient_id,))
            
            result = cursor.fetchone()
            
            if result:
                logger.debug("Latest daily date for patient_id=%s is %s", patient_id, result[0])
                return result[0]
            else:
                return None
        except Exception as e:
            logger.error("get_latest_daily_health_entry_date error: %s", e)
            return None
        
    #adds the daily health metrics for a patient -> GOOD FOR MANUAL ENTERING (METRICS ARE PARAMETERS)
    def add_daily_health_data(
        self,
        patient_id,
        date,
        steps=None,
        heart=None,
        source="fitbit",
        resting_heart_rate=None,
        active_zone_minutes=None,
        spo2_avg=None,
        blood_pressure_systolic=None,
        blood_pressure_diastolic=None,
        calories=None,
        hrv=None,
        sleep_minutes=None,
        distance=None,
        commit=True,
        replace=False,
    ):
        verb = "INSERT OR REPLACE" if replace else "INSERT"
        try:
            self.conn.execute(
                f"""
                {verb} INTO daily_data (
                    patient_id, date, steps, heart, source,
                    resting_heart_rate, active_zone_minutes, spo2_avg,
                    blood_pressure_systolic, blood_pressure_diastolic,
                    calories, hrv, sleep_minutes, distance
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    patient_id,
                    date,
                    steps,
                    heart,
                    source,
                    resting_heart_rate,
                    active_zone_minutes,
                    spo2_avg,
                    blood_pressure_systolic,
                    blood_pressure_diastolic,
                    calories,
                    hrv,
                    sleep_minutes,
                    distance,
                ),
            )
            if commit:
                self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        
    def import_fitbit_data(self, patient_id, start, end, include_intraday=True):
        return fitbit_import.import_fitbit_data(
            self, patient_id, start, end, include_intraday=include_intraday
        )

    def import_daily_csv_file(self, path, patient_id=None, source=None, overwrite=False):
        """Import rows from a fixed-layout daily CSV (see ``daily_health_import_template.csv`` in project root)."""
        return csv_import.import_daily_csv(
            self, path, patient_id=patient_id, source=source, overwrite=overwrite
        )

    #INTRADAY HEALTH METRICS CRUD METHODS---------------------------------------------------------------------

    #returns the health metrics of a patient, filtered by date range if included -> CONNECTION TO UI
    def get_patient_intraday_health_data(
        self, patient_id, start_datetime=None, end_datetime=None, source=None
    ):
        # Row shape: timestamp, patient_id, source, then metrics A–Z:
        # active_zone_minutes, calories, distance, heart, hrv, spo2, steps
        query = '''
            SELECT timestamp, patient_id, source,
                   active_zone_minutes, calories, distance, heart, hrv, spo2, steps
            FROM intraday_data 
            WHERE patient_id = ?
        '''
        params = [patient_id]
        
        if start_datetime:
            query += ' AND timestamp >= ?'
            params.append(start_datetime)
        if end_datetime:
            query += ' AND timestamp <= ?'
            params.append(end_datetime)
        if source is not None:
            query += ' AND source = ?'
            params.append(source)
        
        query += ' ORDER BY timestamp'
        
        cursor = self.conn.execute(query, params)
        return cursor.fetchall() #return all data that matches these params specific to this patient
    
    def get_intraday_by_date(self, patient_id, date):
        start = f"{date} 00:00:00"
        end = f"{date} 23:59:59"
        return self.get_patient_intraday_health_data(patient_id, start, end)
    
    #returns the latest date where patient health data was updated
    def get_latest_intraday_health_entry_date(self, patient_id):
        try:
            if not self.check_patient_exists(patient_id):
                logger.warning("get_latest_intraday_health_entry_date: patient_id=%s not found", patient_id)
                return None
            
            cursor = self.conn.execute('''
                SELECT timestamp FROM intraday_data
                WHERE patient_id = ?
                ORDER BY timestamp DESC
                LIMIT 1
            ''', (patient_id,))
            
            result = cursor.fetchone()
            
            if result:
                logger.debug("Latest intraday ts for patient_id=%s is %s", patient_id, result[0])
                return result[0]
            else:
                return None
        except Exception as e:
            logger.error("get_latest_intraday_health_entry_date error: %s", e)
            return None

    #adds data to the database        
    def add_intraday_health_data(
        self,
        patient_id,
        timestamp,
        steps=None,
        heart=None,
        source="fitbit",
        active_zone_minutes=None,
        spo2=None,
        calories=None,
        hrv=None,
        distance=None,
        commit=True,
    ):
        try:
            self.conn.execute(
                """
                INSERT INTO intraday_data (
                    patient_id, timestamp, steps, heart, source,
                    active_zone_minutes, spo2, calories, hrv, distance
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    patient_id,
                    timestamp,
                    steps,
                    heart,
                    source,
                    active_zone_minutes,
                    spo2,
                    calories,
                    hrv,
                    distance,
                ),
            )
            if commit:
                self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
    
    def delete_old_intraday_data(self, days_to_keep=90):
        cutoff = (datetime.now() - timedelta(days=days_to_keep)).strftime("%Y-%m-%d")
        cursor = self.conn.execute('''
            DELETE FROM intraday_data
            WHERE timestamp < ?                           
        ''', (cutoff,))
        self.conn.commit()
        return cursor.rowcount

            
    #MISC ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
            
    #adds this patient to the csv file on storage
    def export_patient_to_csv(self, patient_id, filename, data_type='daily'):
        import csv
        
        if data_type == 'daily':
            data = self.get_patient_daily_health_data(patient_id)
            headers = [
                'date', 'patient_id', 'source',
                'active_zone_minutes', 'blood_pressure_diastolic', 'blood_pressure_systolic',
                'calories', 'distance', 'heart', 'hrv', 'resting_heart_rate', 'sleep_minutes', 'spo2_avg', 'steps',
            ]
        else:
            data = self.get_patient_intraday_health_data(patient_id)
            headers = [
                'timestamp', 'patient_id', 'source',
                'active_zone_minutes', 'calories', 'distance', 'heart', 'hrv', 'spo2', 'steps',
            ]
        
        with open(filename, 'w', newline = '') as my_file:
            writer = csv.writer(my_file)
            writer.writerow(headers)
            writer.writerows(data)
            
        return len(data)
    
    #closes database connection 
    def close(self):
        logger.info("EHRDatabase connection closed")
        self.conn.close()

    #deletes entire database
    def clear_database(self):
        try:
            self.conn.execute('DELETE FROM intraday_data')
            self.conn.execute('DELETE FROM daily_data')
            self.conn.execute('DELETE FROM patients')
            self.conn.commit()
            return True
        except Exception as e:
            logger.error("clear_database failed: %s", e)
            return False
if __name__ == "__main__":
    db = EHRDatabase()
    db.clear_database()
    db.close()