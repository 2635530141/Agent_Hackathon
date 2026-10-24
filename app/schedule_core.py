"""Calendar compatibility facade: shared names and return models are preserved."""
from app.scheduling.facade import (
    BLOCKING_SOURCES, DAYS, DAY_LABELS, calendar_status, connect_calendar,
    create_exam, create_exception, effective_availability, list_busy, list_busy_for_match,
    list_exams, matching_free_time, own_conflict, remove_exam, remove_exception,
    save_weekly, update_exam, update_exception, weekly_hours,
)
