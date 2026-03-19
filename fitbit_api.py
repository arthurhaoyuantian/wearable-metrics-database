import json
import requests
import time
from datetime import datetime, timedelta
from fitbit_auth import load_tokens, refresh_access_token, check_token_expiry

class FitbitAPI:
    #class constructor
    def __init__(self):
        #create token object
        tokens = check_token_expiry()
        #check presence 
        if not tokens:
            raise Exception("no token was found -> Please authenticate first")
        
        #token is present: 
        self.tokens = tokens
        self.access_token = tokens["access_token"]
        self.refresh_token = tokens["refresh_token"]
        self.last_request_time = 0
        self.min_request_interval = 0.5
        
    #gets data from fitbit based on provided endpoint parameter 
    def make_request(self, endpoint):
        
        #check rate limiting 
        time_since_last = time.time() - self.last_request_time
        if time_since_last < self.min_request_interval:
            time.sleep(self.min_request_interval - time_since_last)
            
        my_url = f"https://api.fitbit.com/1/{endpoint}"
        my_headers = {"Authorization": f"Bearer {self.access_token}"}
        
        response = requests.get(my_url, headers = my_headers)
        self.last_request_time = time.time()
        
        #error handling logic 
        if response.status_code != 200:
            print(f"API Error {response.status_code}: {response.text}")
            
            #token expiration error (#401)
            if response.status_code == 401:
                print("Token expired, refreshing...")
                # Use the imported refresh function
                new_tokens = refresh_access_token(self.refresh_token)
                self.tokens = new_tokens
                self.access_token = new_tokens["access_token"]
                self.refresh_token = new_tokens["refresh_token"]
                
                # Retry with new token
                my_headers = {"Authorization": f"Bearer {self.access_token}"}
                response = requests.get(my_url, headers=my_headers)
                
                #throw error
                if response.status_code != 200:
                    raise Exception(f"API failed after token refresh: {response.status_code}")
        #return api key
        return response.json()
    
    #function throws error if the requested date is invalid
    def validate_dates(self, start_date, end_date):
        """Validate date format is YYYY-MM-DD"""
        try:
            datetime.strptime(start_date, "%Y-%m-%d")
            datetime.strptime(end_date, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Dates must be in YYYY-MM-DD format")
    
    #DAILY-----------------------------------------------------------------------------------------
    
    def get_daily_steps(self, start_date, end_date):
        self.validate_dates(start_date, end_date)
        return self.make_request(f"user/-/activities/steps/date/{start_date}/{end_date}.json")
    
    def get_daily_heart(self, start_date, end_date):
        self.validate_dates(start_date, end_date)
        return self.make_request(f"user/-/activities/heart/date/{start_date}/{end_date}.json")
    
    #INTRADAY------------------------------------------------------------------------------------
    
    def get_intra_steps(self, date, detail = '1min'):
        self.validate_dates(date, date)
        return self.make_request(f"user/-/activities/steps/date/{date}/1d/{detail}.json")
    
    def get_intra_heart(self, date, detail = '1min'):
        self.validate_dates(date, date)
        return self.make_request(f"user/-/activities/heart/date/{date}/1d/{detail}.json")