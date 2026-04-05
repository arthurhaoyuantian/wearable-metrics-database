import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta

from src.integrations.fitbit_api import FitbitAPI

class EHRDatabase:
    #constructor -> connects app to database file and creates the tables
    def __init__(self, db_path = "test.db"):
        self.conn = sqlite3.connect(db_path)
        self.create_tables()
        
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
                FOREIGN KEY (patient_id) REFERENCES patients(patient_id)
                UNIQUE(patient_id, timestamp, source)
            )
        ''')
        
        #save changes
        self.conn.commit()
        
        
            
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
            print(f"Error, patient {patient_id} not found")
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
            print(f"Error updating patient: {e}")
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
            print(f"error {e}")
            return False
    
    
    #DAILY HEALTH METRICS CRUD METHODS--------------------------------------------------------------------------
    
    #returns the health metrics of a patient, filtered by date range if included -> CONNECTION TO UI
    def get_patient_daily_health_data(self, patient_id, start_date = None, end_date = None):
        query = '''
            SELECT date, steps, heart, source FROM daily_data 
            WHERE patient_id = ?
        '''
        params = [patient_id]
        
        if start_date:
            query += ' AND date >= ?'
            params.append(start_date)
        if end_date:
            query += ' AND date <= ?'
            params.append(end_date)
        
        query += ' ORDER BY date'
        
        cursor = self.conn.execute(query, params)
        return cursor.fetchall() #return all data that matches these params specific to this patient
    
    #returns the latest date where patient health data was updated
    def get_latest_daily_health_entry_date(self, patient_id):
        try:
            if not self.check_patient_exists(patient_id):
                print(f"Warning: Patient {patient_id} not found")
                return None
            
            cursor = self.conn.execute('''
                SELECT date FROM daily_data
                WHERE patient_id = ?
                ORDER BY date DESC
                LIMIT 1
            ''', (patient_id,))
            
            result = cursor.fetchone()
            
            if result:
                print(f"Latest data for patient {patient_id}:{result[0]}")
                return result[0]
            else:
                return None
        except Exception as e:
            print(f"Error getting latest health date: {e}")
            return None
        
    #adds the daily health metrics for a patient -> GOOD FOR MANUAL ENTERING (METRICS ARE PARAMETERS)
    def add_daily_health_data(self, patient_id, date, steps = None, heart = None, source = 'fitbit', commit = True):
        try:
            self.conn.execute('''
                INSERT INTO daily_data (
                    patient_id, date, steps, heart, source) VALUES (?, ?, ?, ?, ?)
            ''', (patient_id, date, steps, heart, source)
            )
            if commit:
                self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        
    #adds both daily and intraday FITBIT data from the given time period to the database
    def import_fitbit_data(self, patient_id, start, end, include_intraday=True):
        #highlight patient
        patient = self.get_patient_info(patient_id) 
        if not patient:
            print(f"error, patient {patient_id} not found")
            return 0
        
        #getting data
        api = FitbitAPI(
            access_token=patient[2],
            refresh_token=patient[3],
            db=self,
            patient_id=patient_id
        )
        imported_count = 0
        
        #daily
        daily_steps_response = api.get_daily_steps(start, end)
        daily_heart_response = api.get_daily_heart(start, end)
        
        metrics = defaultdict(dict)
        if 'activities-steps' in daily_steps_response:
            for day in daily_steps_response['activities-steps']:
                metrics[day['dateTime']]['steps'] = int(day['value'])
        if 'activities-heart' in daily_heart_response:
            for day in daily_heart_response['activities-heart']:
                metrics[day['dateTime']]['heart'] = day['value'].get('restingHeartRate')
        
        for date, data in metrics.items():
            success = self.add_daily_health_data(
                patient_id=patient_id,
                date=date,
                steps=data.get('steps'),
                heart=data.get('heart'),
                source='fitbit',
                commit=False
            )
            if success:
                imported_count += 1
        self.conn.commit()
        
          
        #intraday 
        if include_intraday:
            current = datetime.strptime(start, "%Y-%m-%d")
            end_date = datetime.strptime(end, "%Y-%m-%d")
            while current <= end_date:
                date_str = current.strftime("%Y-%m-%d")
                
                intraday_metrics = defaultdict(dict)
                
                intraday_steps_response = api.get_intra_steps(date_str)
                if 'activities-steps-intraday' in intraday_steps_response:
                    for point in intraday_steps_response['activities-steps-intraday']['dataset']:
                        timestamp = f"{date_str} {point['time']}"
                        intraday_metrics[timestamp]['steps'] = point['value']
                
                intraday_heart_response = api.get_intra_heart(date_str)
                if 'activities-heart-intraday' in intraday_heart_response:
                    for point in intraday_heart_response['activities-heart-intraday']['dataset']:
                        timestamp = f"{date_str} {point['time']}"
                        intraday_metrics[timestamp]['heart'] = point['value']
                
                for timestamp, data in intraday_metrics.items():
                    success = self.add_intraday_health_data(
                        patient_id=patient_id,
                        timestamp=timestamp,
                        steps=data.get('steps'),
                        heart=data.get('heart'),
                        source='fitbit',
                        commit=False
                    )
                    if success:
                        imported_count += 1
                self.conn.commit()
                
                current += timedelta(days=1)
            
        return imported_count
        
        

        
        
        
    #INTRADAY HEALTH METRICS CRUD METHODS---------------------------------------------------------------------

    #returns the health metrics of a patient, filtered by date range if included -> CONNECTION TO UI
    def get_patient_intraday_health_data(self, patient_id, start_datetime = None, end_datetime = None):
        query = '''
            SELECT timestamp, steps, heart, source FROM intraday_data 
            WHERE patient_id = ?
        '''
        params = [patient_id]
        
        if start_datetime:
            query += ' AND timestamp >= ?'
            params.append(start_datetime)
        if end_datetime:
            query += ' AND timestamp <= ?'
            params.append(end_datetime)
        
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
                print(f"Warning: Patient {patient_id} not found")
                return None
            
            cursor = self.conn.execute('''
                SELECT timestamp FROM intraday_data
                WHERE patient_id = ?
                ORDER BY timestamp DESC
                LIMIT 1
            ''', (patient_id,))
            
            result = cursor.fetchone()
            
            if result:
                print(f"Latest intraday data for patient {patient_id}:{result[0]}")
                return result[0]
            else:
                return None
        except Exception as e:
            print(f"Error getting latest intraday entry: {e}")
            return None

    #adds data to the database        
    def add_intraday_health_data(self, patient_id, timestamp, steps = None, heart = None, source = 'fitbit', commit = True):
        try:
            self.conn.execute('''
                INSERT INTO intraday_data (
                    patient_id, timestamp, steps, heart, source) VALUES (?, ?, ?, ?, ?)
            ''', (patient_id, timestamp, steps, heart, source)
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
            headers = ['date', 'steps', 'heart', 'source']
        else:
            data = self.get_patient_intraday_health_data(patient_id)
            headers = ['timestamp', 'steps', 'heart', 'source']
        
        with open(filename, 'w', newline = '') as my_file:
            writer = csv.writer(my_file)
            writer.writerow(headers)
            writer.writerows(data)
            
        return len(data)
    
    #closes database connection 
    def close(self):
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
            print(f"Error clearing database: {e}")
            return False
if __name__ == "__main__":
    db = EHRDatabase("test.db")
    db.clear_database()
    db.close()