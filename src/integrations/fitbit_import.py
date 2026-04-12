"""Pull Fitbit time-series for a range and write rows via EHRDatabase."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta

from src.integrations.fitbit_api import FitbitAPI

LOG = logging.getLogger(__name__)


def _to_float(val):
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _to_int(val):
    f = _to_float(val)
    return int(f) if f is not None else None


def _active_zone_total(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, dict):
        total = 0
        for key in (
            "fatBurnActiveZoneMinutes",
            "cardioActiveZoneMinutes",
            "peakActiveZoneMinutes",
            "totalMinutes",
            "activeZoneMinutes",
        ):
            if key in value and value[key] is not None:
                try:
                    total += int(float(value[key]))
                except (TypeError, ValueError):
                    pass
        return total if total else None
    return None


def _hrv_scalar(val_obj):
    if val_obj is None:
        return None
    if not isinstance(val_obj, dict):
        return _to_float(val_obj)
    for key in ("dailyRmssd", "rmssd", "avg"):
        if key in val_obj and val_obj[key] is not None:
            return _to_float(val_obj[key])
    for v in val_obj.values():
        x = _to_float(v)
        if x is not None:
            return x
    return None


def _sleep_date_chunks(start_str, end_str, max_days=100):
    s = datetime.strptime(start_str, "%Y-%m-%d")
    e = datetime.strptime(end_str, "%Y-%m-%d")
    while s <= e:
        chunk_end = min(s + timedelta(days=max_days - 1), e)
        yield s.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")
        s = chunk_end + timedelta(days=1)


def import_fitbit_data(db, patient_id, start, end, include_intraday=True):
    """Fetch daily (and optionally intraday) Fitbit metrics into *db* for *patient_id*."""
    LOG.info(
        "Fitbit import start patient_id=%s range=%s..%s include_intraday=%s",
        patient_id,
        start,
        end,
        include_intraday,
    )
    patient = db.get_patient_info(patient_id)
    if not patient:
        LOG.error("Fitbit import aborted: patient_id=%s not found", patient_id)
        return 0

    api = FitbitAPI(
        access_token=patient[2],
        refresh_token=patient[3],
        db=db,
        patient_id=patient_id,
    )
    imported_count = 0
    _skip_log_budget = 0
    _MAX_SKIP_LOGS = 25

    def safe(label, fn):
        nonlocal _skip_log_budget
        try:
            return fn()
        except Exception as exc:
            if _skip_log_budget < _MAX_SKIP_LOGS:
                LOG.warning("Fitbit import skipped (%s): %s", label, exc)
                _skip_log_budget += 1
            elif _skip_log_budget == _MAX_SKIP_LOGS:
                LOG.warning(
                    "Fitbit import: further per-call failures not logged individually "
                    "(cap=%s).",
                    _MAX_SKIP_LOGS,
                )
                _skip_log_budget += 1
            return None

    metrics = defaultdict(dict)

    def merge_daily_series(resp, list_key, date_key, value_fn):
        if not resp or list_key not in resp:
            return
        for row in resp[list_key]:
            d = row.get(date_key) or row.get("dateTime")
            if not d:
                continue
            val = value_fn(row)
            if val is not None:
                metrics[d].update(val)

    # daily
    ds = safe("daily steps", lambda: api.get_daily_steps(start, end))
    merge_daily_series(
        ds,
        "activities-steps",
        "dateTime",
        lambda row: {"steps": _to_int(row.get("value"))},
    )

    dh = safe("daily heart", lambda: api.get_daily_heart(start, end))
    if dh and "activities-heart" in dh:
        for day in dh["activities-heart"]:
            d = day.get("dateTime")
            rhr = _to_int((day.get("value") or {}).get("restingHeartRate"))
            if d and rhr is not None:
                metrics[d]["heart"] = rhr
                metrics[d]["resting_heart_rate"] = rhr

    dd = safe("daily distance", lambda: api.get_daily_distance(start, end))
    merge_daily_series(
        dd,
        "activities-distance",
        "dateTime",
        lambda row: {"distance": _to_float(row.get("value"))},
    )

    dc = safe("daily calories", lambda: api.get_daily_calories(start, end))
    merge_daily_series(
        dc,
        "activities-calories",
        "dateTime",
        lambda row: {"calories": _to_float(row.get("value"))},
    )

    daz = safe("daily AZM", lambda: api.get_daily_active_zone_minutes(start, end))
    if daz and "activities-active-zone-minutes" in daz:
        for day in daz["activities-active-zone-minutes"]:
            d = day.get("dateTime")
            total = _active_zone_total(day.get("value"))
            if d and total is not None:
                metrics[d]["active_zone_minutes"] = total

    dsp = safe("daily SpO2", lambda: api.get_daily_spo2(start, end))
    if dsp and "spo2" in dsp:
        for row in dsp["spo2"]:
            d = row.get("dateTime")
            avg = _to_float((row.get("value") or {}).get("avg"))
            if d and avg is not None:
                metrics[d]["spo2_avg"] = avg

    dhv = safe("daily HRV", lambda: api.get_daily_hrv(start, end))
    if dhv and "hrv" in dhv:
        for row in dhv["hrv"]:
            d = row.get("dateTime")
            hv = _hrv_scalar(row.get("value"))
            if d and hv is not None:
                metrics[d]["hrv"] = hv

    for chunk_start, chunk_end in _sleep_date_chunks(start, end):
        sl = safe(
            f"sleep {chunk_start}..{chunk_end}",
            lambda: api.get_sleep_logs(chunk_start, chunk_end),
        )
        if not sl or "sleep" not in sl:
            continue
        for log in sl["sleep"]:
            d = log.get("dateOfSleep")
            mins = _to_int(log.get("minutesAsleep"))
            if d and mins is not None:
                prev = metrics[d].get("sleep_minutes") or 0
                metrics[d]["sleep_minutes"] = prev + mins

    for date_str, data in metrics.items():
        success = db.add_daily_health_data(
            patient_id=patient_id,
            date=date_str,
            steps=data.get("steps"),
            heart=data.get("heart"),
            source="fitbit",
            resting_heart_rate=data.get("resting_heart_rate"),
            active_zone_minutes=data.get("active_zone_minutes"),
            spo2_avg=data.get("spo2_avg"),
            blood_pressure_systolic=data.get("blood_pressure_systolic"),
            blood_pressure_diastolic=data.get("blood_pressure_diastolic"),
            calories=data.get("calories"),
            hrv=data.get("hrv"),
            sleep_minutes=data.get("sleep_minutes"),
            distance=data.get("distance"),
            commit=False,
        )
        if success:
            imported_count += 1
    db.conn.commit()
    daily_inserts = imported_count
    LOG.info(
        "Fitbit daily phase done patient_id=%s distinct_dates_merged=%s daily_inserts_ok=%s "
        "(0 if those dates already had fitbit daily rows)",
        patient_id,
        len(metrics),
        daily_inserts,
    )

    # intraday
    if include_intraday:
        current = datetime.strptime(start, "%Y-%m-%d")
        end_date = datetime.strptime(end, "%Y-%m-%d")
        total_days = (end_date - current).days + 1
        day_num = 0
        while current <= end_date:
            date_str = current.strftime("%Y-%m-%d")
            intraday_metrics = defaultdict(dict)

            def merge_intraday(resp, series_key, field, transform=lambda v: v):
                if not resp:
                    return
                block = resp.get(series_key)
                if not block or "dataset" not in block:
                    return
                for point in block["dataset"]:
                    t = point.get("time")
                    if not t:
                        continue
                    ts = f"{date_str} {t}"
                    val = transform(point.get("value"))
                    if val is not None:
                        intraday_metrics[ts][field] = val

            merge_intraday(
                safe("intraday steps", lambda: api.get_intra_steps(date_str)),
                "activities-steps-intraday",
                "steps",
                _to_int,
            )
            merge_intraday(
                safe("intraday heart", lambda: api.get_intra_heart(date_str)),
                "activities-heart-intraday",
                "heart",
                _to_int,
            )
            merge_intraday(
                safe("intraday distance", lambda: api.get_intra_distance(date_str)),
                "activities-distance-intraday",
                "distance",
                _to_float,
            )
            merge_intraday(
                safe("intraday calories", lambda: api.get_intra_calories(date_str)),
                "activities-calories-intraday",
                "calories",
                _to_float,
            )
            merge_intraday(
                safe("intraday AZM", lambda: api.get_intra_active_zone_minutes(date_str)),
                "activities-active-zone-minutes-intraday",
                "active_zone_minutes",
                _active_zone_total,
            )

            spo2i = safe("intraday SpO2", lambda: api.get_intra_spo2(date_str))
            if spo2i:
                for series_key in (
                    "spo2-minutes-intraday",
                    "spo2_intraday",
                    "minutes",
                ):
                    merge_intraday(spo2i, series_key, "spo2", _to_float)

            hrvi = safe("intraday HRV", lambda: api.get_intra_hrv(date_str))
            if hrvi and "hrv" in hrvi:
                for row in hrvi["hrv"]:
                    t = row.get("time") or row.get("minute")
                    if not t:
                        continue
                    if len(t) <= 8 and " " not in t:
                        ts = f"{date_str} {t}"
                    else:
                        ts = t if " " in t else f"{date_str} {t}"
                    hv = _hrv_scalar(row.get("value"))
                    if hv is not None:
                        intraday_metrics[ts]["hrv"] = hv

            for timestamp, data in intraday_metrics.items():
                success = db.add_intraday_health_data(
                    patient_id=patient_id,
                    timestamp=timestamp,
                    steps=data.get("steps"),
                    heart=data.get("heart"),
                    source="fitbit",
                    active_zone_minutes=data.get("active_zone_minutes"),
                    spo2=data.get("spo2"),
                    calories=data.get("calories"),
                    hrv=data.get("hrv"),
                    distance=data.get("distance"),
                    commit=False,
                )
                if success:
                    imported_count += 1
            db.conn.commit()

            day_num += 1
            if day_num % 7 == 0 or day_num == total_days:
                LOG.info(
                    "Fitbit intraday progress patient_id=%s days_done=%s/%s running_total_rows=%s",
                    patient_id,
                    day_num,
                    total_days,
                    imported_count,
                )

            current += timedelta(days=1)

    LOG.info(
        "Fitbit import finished patient_id=%s total_rows_inserted=%s (daily+intraday counter)",
        patient_id,
        imported_count,
    )
    return imported_count
