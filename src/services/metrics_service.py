"""Metric definitions aligned with database row layout.

Daily row (get_patient_daily_health_data): date, patient_id, source, then A–Z metrics.
Intraday row: timestamp, patient_id, source, then A–Z metrics (indices in METRIC_SPECS).
"""

METRIC_SPECS = {
    "active_zone_minutes": {
        "daily_idx": 3,
        "intraday_idx": 3,
        "daily_label": "active zone min (daily)",
        "intraday_label": "active zone min (intraday)",
        "unit": "min",
    },
    "blood_pressure_diastolic": {
        "daily_idx": 4,
        "intraday_idx": None,
        "daily_label": "BP diastolic (daily)",
        "intraday_label": "BP diastolic",
        "unit": "mmHg",
    },
    "blood_pressure_systolic": {
        "daily_idx": 5,
        "intraday_idx": None,
        "daily_label": "BP systolic (daily)",
        "intraday_label": "BP systolic",
        "unit": "mmHg",
    },
    "calories": {
        "daily_idx": 6,
        "intraday_idx": 4,
        "daily_label": "calories (daily)",
        "intraday_label": "calories (intraday)",
        "unit": "kcal",
    },
    "distance": {
        "daily_idx": 7,
        "intraday_idx": 5,
        "daily_label": "distance (daily)",
        "intraday_label": "distance (intraday)",
        "unit": "km",
    },
    "heart": {
        "daily_idx": 8,
        "intraday_idx": 6,
        "daily_label": "heart daily",
        "intraday_label": "heart intraday",
        "unit": "bpm",
    },
    "hrv": {
        "daily_idx": 9,
        "intraday_idx": 7,
        "daily_label": "HRV (daily)",
        "intraday_label": "HRV (intraday)",
        "unit": "ms",
    },
    "resting_heart_rate": {
        "daily_idx": 10,
        "intraday_idx": 6,
        "daily_label": "resting HR daily",
        "intraday_label": "HR intraday",
        "unit": "bpm",
    },
    "sleep_minutes": {
        "daily_idx": 11,
        "intraday_idx": None,
        "daily_label": "sleep (min)",
        "intraday_label": "sleep",
        "unit": "min",
    },
    "spo2": {
        "daily_idx": 12,
        "intraday_idx": 8,
        "daily_label": "SpO2 avg (daily)",
        "intraday_label": "SpO2 (intraday)",
        "unit": "%",
    },
    "steps": {
        "daily_idx": 13,
        "intraday_idx": 9,
        "daily_label": "steps daily",
        "intraday_label": "steps intraday",
        "unit": "steps",
    },
}

# (key, label, default_checked, daily_only) — keys must exist in METRIC_SPECS
METRIC_DIALOG_ROWS = [
    ("heart", "Heart rate", True, False),
    ("steps", "Step count", False, False),
    ("resting_heart_rate", "Resting heart rate", False, False),
    ("active_zone_minutes", "Active zone minutes", False, False),
    ("spo2", "Blood oxygen (SpO2 avg %)", False, False),
    ("blood_pressure_systolic", "Blood pressure (systolic)", False, True),
    ("blood_pressure_diastolic", "Blood pressure (diastolic)", False, True),
    ("calories", "Calories burned", False, False),
    ("hrv", "Heart rate variability", False, False),
    ("sleep_minutes", "Sleep duration (minutes asleep)", False, True),
    ("distance", "Travel distance", False, False),
]
