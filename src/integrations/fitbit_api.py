import logging
import re
import time

import requests
from datetime import datetime, timedelta

from src.integrations.fitbit_auth import refresh_access_token

LOG = logging.getLogger(__name__)

# Fitbit error bodies sometimes embed a JWT; never log it verbatim.
_JWT_IN_TEXT = re.compile(
    r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
    re.ASCII,
)


def _safe_api_body_snippet(text: str, limit: int = 400) -> str:
    if not text:
        return ""
    redacted = _JWT_IN_TEXT.sub("[REDACTED_JWT]", text)
    return redacted[:limit].replace("\n", " ")


class FitbitAPI:
    #class constructor
    def __init__(self, access_token, refresh_token, db=None, patient_id=None):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.db = db
        self.patient_id = patient_id
        self.last_request_time = 0
        self.min_request_interval = 0.5

    #gets data from fitbit based on provided endpoint parameter 
    def make_request(self, endpoint, api_version="1"):
        #check rate limiting 
        time_since_last = time.time() - self.last_request_time
        if time_since_last < self.min_request_interval:
            time.sleep(self.min_request_interval - time_since_last)

        my_url = f"https://api.fitbit.com/{api_version}/{endpoint}"
        my_headers = {"Authorization": f"Bearer {self.access_token}"}

        response = requests.get(my_url, headers=my_headers)
        self.last_request_time = time.time()

        # error handling logic
        if response.status_code != 200:
            snippet = _safe_api_body_snippet(response.text or "")
            LOG.warning(
                "Fitbit API HTTP %s for %s (v%s): %s",
                response.status_code,
                endpoint,
                api_version,
                snippet,
            )
            # token expiration (#401) — reactive refresh (no stored expires_at)
            if response.status_code == 401:
                LOG.info("HTTP 401 on %s; refreshing OAuth token and retrying once", endpoint)
                new_tokens = refresh_access_token(self.db, self.patient_id, self.refresh_token)
                self.tokens = new_tokens
                self.access_token = new_tokens["access_token"]
                self.refresh_token = new_tokens["refresh_token"]
                my_headers = {"Authorization": f"Bearer {self.access_token}"}
                response = requests.get(my_url, headers=my_headers)
                if response.status_code != 200:
                    snippet2 = _safe_api_body_snippet(response.text or "")
                    LOG.error(
                        "Fitbit API still HTTP %s after token refresh for %s: %s",
                        response.status_code,
                        endpoint,
                        snippet2,
                    )
                    raise Exception(f"API failed after token refresh: {response.status_code}")
                LOG.info("Retry after token refresh succeeded for %s", endpoint)
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

    # DAILY-----------------------------------------------------------------------------------------

    # --- Step count & distance (travel distance) ---
    def get_daily_steps(self, start_date, end_date):
        self.validate_dates(start_date, end_date)
        return self.make_request(f"user/-/activities/steps/date/{start_date}/{end_date}.json")

    def get_daily_distance(self, start_date, end_date):
        self.validate_dates(start_date, end_date)
        return self.make_request(f"user/-/activities/distance/date/{start_date}/{end_date}.json")

    # INTRADAY------------------------------------------------------------------------------------

    def get_intra_steps(self, date, detail="1min"):
        self.validate_dates(date, date)
        return self.make_request(f"user/-/activities/steps/date/{date}/1d/{detail}.json")

    def get_intra_distance(self, date, detail="1min"):
        self.validate_dates(date, date)
        return self.make_request(f"user/-/activities/distance/date/{date}/1d/{detail}.json")

    # --- Heart: resting + intraday (HRV is separate endpoint below) ---
    def get_daily_heart(self, start_date, end_date):
        self.validate_dates(start_date, end_date)
        return self.make_request(f"user/-/activities/heart/date/{start_date}/{end_date}.json")

    def get_intra_heart(self, date, detail="1min"):
        self.validate_dates(date, date)
        return self.make_request(f"user/-/activities/heart/date/{date}/1d/{detail}.json")

    # --- Calories burned ---
    def get_daily_calories(self, start_date, end_date):
        self.validate_dates(start_date, end_date)
        return self.make_request(f"user/-/activities/calories/date/{start_date}/{end_date}.json")

    def get_intra_calories(self, date, detail="1min"):
        self.validate_dates(date, date)
        return self.make_request(f"user/-/activities/calories/date/{date}/1d/{detail}.json")

    # --- Active Zone Minutes ---
    def get_daily_active_zone_minutes(self, start_date, end_date):
        self.validate_dates(start_date, end_date)
        return self.make_request(
            f"user/-/activities/active-zone-minutes/date/{start_date}/{end_date}.json"
        )

    def get_intra_active_zone_minutes(self, date, detail="1min"):
        self.validate_dates(date, date)
        return self.make_request(
            f"user/-/activities/active-zone-minutes/date/{date}/1d/{detail}.json"
        )

    # --- Blood oxygen (SpO2) — scope: oxygen_saturation ---
    def get_daily_spo2(self, start_date, end_date):
        self.validate_dates(start_date, end_date)
        return self.make_request(f"user/-/spo2/date/{start_date}/{end_date}.json")

    def get_intra_spo2(self, date, detail="1min"):
        self.validate_dates(date, date)
        return self.make_request(f"user/-/spo2/date/{date}/1d/{detail}.json")

    # --- Heart rate variability (sleep-related summaries) ---
    def get_daily_hrv(self, start_date, end_date):
        self.validate_dates(start_date, end_date)
        return self.make_request(f"user/-/hrv/date/{start_date}/{end_date}.json")

    def get_intra_hrv(self, date):
        self.validate_dates(date, date)
        return self.make_request(f"user/-/hrv/date/{date}/all.json")

    # --- Sleep duration (API 1.2) ---
    def get_sleep_logs(self, start_date, end_date):
        self.validate_dates(start_date, end_date)
        return self.make_request(
            f"user/-/sleep/date/{start_date}/{end_date}.json", api_version="1.2"
        )

    # Blood pressure: not exposed on standard Fitbit consumer Web API — intentionally omitted.
