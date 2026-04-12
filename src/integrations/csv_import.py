"""Barebones daily CSV → database. One fixed header layout (see ``daily_health_import_template.csv`` in project root)."""

from __future__ import annotations

import csv
import logging
from pathlib import Path

LOG = logging.getLogger(__name__)

# Exact column names required on the first row (order in the file may vary; DictReader).
DAILY_CSV_COLUMNS = frozenset(
    {
        "patient_id",
        "date",
        "source",
        "steps",
        "heart",
        "resting_heart_rate",
        "active_zone_minutes",
        "spo2_avg",
        "blood_pressure_systolic",
        "blood_pressure_diastolic",
        "calories",
        "hrv",
        "sleep_minutes",
        "distance",
    }
)


def _strip(s):
    return (s or "").strip()


def _parse_int(raw):
    raw = _strip(raw)
    if not raw:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def _parse_float(raw):
    raw = _strip(raw)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _headers_error(reader):
    if reader.fieldnames is None:
        return "CSV has no header row."
    headers = {_strip(h) for h in reader.fieldnames}
    if headers != DAILY_CSV_COLUMNS:
        missing = sorted(DAILY_CSV_COLUMNS - headers)
        extra = sorted(headers - DAILY_CSV_COLUMNS)
        msg = "Header row must contain exactly these columns (names, not order): "
        msg += ", ".join(sorted(DAILY_CSV_COLUMNS))
        if missing:
            msg += f" | missing: {missing}"
        if extra:
            msg += f" | unexpected: {extra}"
        return msg
    return None


def list_dates_in_daily_csv(path) -> tuple[list[str], list[str]]:
    """
    Collect unique ``date`` values from a daily import CSV (same headers as import).
    Returns ``(dates, errors)``; if *errors* is non-empty, *dates* may be empty.
    """
    path = Path(path)
    dates: list[str] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        err = _headers_error(reader)
        if err:
            return [], [err]
        for raw in reader:
            row = {(_strip(k) if k else ""): v for k, v in raw.items()}
            if not any(_strip(v) for v in row.values()):
                continue
            ds = _strip(row.get("date"))
            if ds:
                dates.append(ds)
    return sorted(set(dates)), []


def import_daily_csv(
    db,
    path,
    patient_id=None,
    source=None,
    overwrite=False,
) -> dict:
    """
    Read *path* (UTF-8) as CSV with headers exactly matching DAILY_CSV_COLUMNS.

    If *patient_id* is set, every row is stored for that patient (CSV ``patient_id`` ignored).
    If *source* is set (after strip; empty string treated as ``"csv"``), it overrides the CSV
    ``source`` column for every row.

    *overwrite*: passed as ``replace=`` to ``add_daily_health_data`` (``INSERT OR REPLACE`` for
    same patient/date/source).

    Returns: ``{"inserted": int, "skipped": int, "errors": list[str]}``
    """
    path = Path(path)
    inserted = 0
    skipped = 0
    errors: list[str] = []

    LOG.info(
        "CSV import start path=%s patient_id=%s source=%s overwrite=%s",
        path,
        patient_id,
        source,
        overwrite,
    )

    if patient_id is not None and not db.check_patient_exists(patient_id):
        errors.append(f"patient_id {patient_id} not found in database.")
        LOG.error("CSV import aborted: %s", errors[0])
        return {"inserted": 0, "skipped": 0, "errors": errors}

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        err = _headers_error(reader)
        if err:
            errors.append(err)
            LOG.warning("CSV import failed header check: %s", err)
            return {"inserted": 0, "skipped": 0, "errors": errors}

        for i, raw in enumerate(reader, start=2):
            line = f"line {i}"
            row = {(_strip(k) if k else ""): v for k, v in raw.items()}
            if not any(_strip(v) for v in row.values()):
                continue

            if patient_id is not None:
                pid = patient_id
            else:
                pid = _parse_int(row.get("patient_id"))
                if pid is None:
                    errors.append(f"{line}: patient_id is required.")
                    skipped += 1
                    continue
                if not db.check_patient_exists(pid):
                    errors.append(f"{line}: patient_id {pid} not in database.")
                    skipped += 1
                    continue

            date_s = _strip(row.get("date"))
            if not date_s:
                errors.append(f"{line}: date is required.")
                skipped += 1
                continue

            if source is not None:
                src = _strip(source) or "csv"
            else:
                src = _strip(row.get("source")) or "csv"

            ok = db.add_daily_health_data(
                patient_id=pid,
                date=date_s,
                steps=_parse_int(row.get("steps")),
                heart=_parse_int(row.get("heart")),
                source=src,
                resting_heart_rate=_parse_int(row.get("resting_heart_rate")),
                active_zone_minutes=_parse_int(row.get("active_zone_minutes")),
                spo2_avg=_parse_float(row.get("spo2_avg")),
                blood_pressure_systolic=_parse_int(row.get("blood_pressure_systolic")),
                blood_pressure_diastolic=_parse_int(row.get("blood_pressure_diastolic")),
                calories=_parse_float(row.get("calories")),
                hrv=_parse_float(row.get("hrv")),
                sleep_minutes=_parse_int(row.get("sleep_minutes")),
                distance=_parse_float(row.get("distance")),
                commit=False,
                replace=overwrite,
            )
            if ok:
                inserted += 1
            else:
                skipped += 1
                errors.append(
                    f"{line}: duplicate or DB constraint for patient {pid} date {date_s} source {src!r}."
                )

    db.conn.commit()
    LOG.info(
        "CSV import finished path=%s inserted=%s skipped=%s error_messages=%s",
        path,
        inserted,
        skipped,
        len(errors),
    )
    if errors:
        preview = "; ".join(errors[:8])
        if len(errors) > 8:
            preview += f" … (+{len(errors) - 8} more)"
        LOG.warning("CSV import issues: %s", preview)

    return {"inserted": inserted, "skipped": skipped, "errors": errors}
