"""Meaningful boundary and scheduling cases for the deterministic core."""

import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from schedule_service.domain import BusyBlock, Exam, Interval, ScheduleSnapshot, WeeklySlot
from schedule_service.engine import (
    compute_availability, intersect_availability, next_available,
    owner_conflicts, priority_windows,
)

UTC = timezone.utc
LA = ZoneInfo("America/Los_Angeles")


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def interval(start, end):
    return Interval(instant(start), instant(end))


def snap(**changes):
    return ScheduleSnapshot(owner_id="student-a", timezone="America/Los_Angeles", **changes)


MONDAY = interval("2026-10-26T00:00:00-07:00", "2026-10-27T00:00:00-07:00")


class DomainTests(unittest.TestCase):
    def test_naive_interval_and_refresh_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone aware"):
            Interval(datetime(2026, 10, 26, 9), datetime(2026, 10, 26, 10))
        with self.assertRaisesRegex(ValueError, "timezone aware"):
            snap(calendar_refreshed_at=datetime(2026, 10, 26))

    def test_zero_and_reversed_intervals_are_rejected(self):
        for start, end in ((10, 10), (11, 10)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                Interval(datetime(2026, 10, 26, start, tzinfo=UTC),
                         datetime(2026, 10, 26, end, tzinfo=UTC))

    def test_validation_uses_absolute_instants_across_offsets(self):
        valid = interval("2026-10-26T09:00:00-07:00", "2026-10-26T17:00:00+00:00")
        self.assertEqual(valid.end - valid.start, timedelta(hours=1))
        with self.assertRaises(ValueError):
            interval("2026-10-26T10:00:00-07:00", "2026-10-26T11:00:00+00:00")

    def test_explicit_fold_one_off_is_valid_and_compared_in_utc(self):
        start = datetime(2026, 11, 1, 1, 30, tzinfo=LA, fold=0)
        end = datetime(2026, 11, 1, 1, 30, tzinfo=LA, fold=1)
        explicit = Interval(start, end)
        window = interval("2026-11-01T00:00:00-07:00", "2026-11-01T03:00:00-08:00")
        self.assertEqual(compute_availability(snap(additions=(explicit,)), window),
                         [interval("2026-11-01T08:30:00Z", "2026-11-01T09:30:00Z")])

    def test_weekday_time_zone_and_duration_validation(self):
        for weekday in (-1, 7, True, 1.5):
            with self.subTest(weekday=weekday), self.assertRaises(ValueError):
                WeeklySlot(weekday, time(9), time(10))
        with self.assertRaises(ValueError):
            WeeklySlot(0, time(9), time(9))
        with self.assertRaises(ValueError):
            WeeklySlot(0, time(9, tzinfo=UTC), time(10))
        with self.assertRaisesRegex(ValueError, "IANA"):
            ScheduleSnapshot("a", "Not/A_Real_Zone")
        with self.assertRaises(ValueError):
            Exam("e", "STAT153", MONDAY, priority_days=-1)

    def test_snapshot_is_immutable_and_copies_input_collections(self):
        hours = [WeeklySlot(0, time(9), time(10))]
        snapshot = snap(weekly=hours)
        hours.clear()
        self.assertEqual(len(snapshot.weekly), 1)
        self.assertIsInstance(snapshot.weekly, tuple)
        with self.assertRaises(FrozenInstanceError):
            snapshot.version = 2


class AvailabilityTests(unittest.TestCase):
    def test_weekly_hours_expand_in_local_zone_and_clip(self):
        snapshot = snap(weekly=(WeeklySlot(0, time(9), time(12)),))
        window = interval("2026-10-26T10:00:00-07:00", "2026-10-26T11:00:00-07:00")
        self.assertEqual(compute_availability(snapshot, window),
                         [interval("2026-10-26T17:00:00Z", "2026-10-26T18:00:00Z")])

    def test_weekday_is_monday_zero_and_multiple_weeks_expand(self):
        snapshot = snap(weekly=(WeeklySlot(0, time(9), time(10)),))
        window = interval("2026-10-25T00:00:00-07:00", "2026-11-03T00:00:00-08:00")
        self.assertEqual(compute_availability(snapshot, window), [
            interval("2026-10-26T16:00:00Z", "2026-10-26T17:00:00Z"),
            interval("2026-11-02T17:00:00Z", "2026-11-02T18:00:00Z"),
        ])

    def test_previous_day_overnight_is_included(self):
        snapshot = snap(weekly=(WeeklySlot(0, time(23), time(2)),))
        window = interval("2026-10-27T00:30:00-07:00", "2026-10-27T03:00:00-07:00")
        self.assertEqual(compute_availability(snapshot, window),
                         [interval("2026-10-27T07:30:00Z", "2026-10-27T09:00:00Z")])

    def test_sunday_overnight_wraps_to_monday(self):
        snapshot = snap(weekly=(WeeklySlot(6, time(23), time(1)),))
        self.assertEqual(compute_availability(snapshot, MONDAY),
                         [interval("2026-10-26T07:00:00Z", "2026-10-26T08:00:00Z")])

    def test_additions_union_with_weekly_and_adjacent_hours(self):
        snapshot = snap(
            weekly=(WeeklySlot(0, time(9), time(10)), WeeklySlot(0, time(10), time(11))),
            additions=(interval("2026-10-26T10:30:00-07:00", "2026-10-26T12:00:00-07:00"),),
        )
        self.assertEqual(compute_availability(snapshot, MONDAY),
                         [interval("2026-10-26T16:00:00Z", "2026-10-26T19:00:00Z")])

    def test_one_off_removal_cannot_be_overridden_by_an_addition(self):
        hours = interval("2026-10-26T09:00:00-07:00", "2026-10-26T12:00:00-07:00")
        removal = interval("2026-10-26T10:00:00-07:00", "2026-10-26T11:00:00-07:00")
        snapshot = snap(additions=(hours, removal), removals=(removal,))
        self.assertEqual(compute_availability(snapshot, MONDAY), [
            interval("2026-10-26T16:00:00Z", "2026-10-26T17:00:00Z"),
            interval("2026-10-26T18:00:00Z", "2026-10-26T19:00:00Z"),
        ])

    def test_overlapping_busy_sources_are_subtracted_without_bridging_gaps(self):
        snapshot = snap(
            weekly=(WeeklySlot(0, time(9), time(17)),),
            removals=(interval("2026-10-26T10:00:00-07:00", "2026-10-26T11:00:00-07:00"),),
            exams=(Exam("exam-a", "STAT153", interval(
                "2026-10-26T10:30:00-07:00", "2026-10-26T12:00:00-07:00")),),
            calendar_busy=(
                BusyBlock("cal-a", interval("2026-10-26T14:00:00-07:00", "2026-10-26T15:00:00-07:00"),
                          "calendar", "Private appointment"),
                BusyBlock("cal-b", interval("2026-10-26T15:00:00-07:00", "2026-10-26T16:00:00-07:00"),
                          "calendar", "Private class"),
            ),
        )
        self.assertEqual(compute_availability(snapshot, MONDAY), [
            interval("2026-10-26T16:00:00Z", "2026-10-26T17:00:00Z"),
            interval("2026-10-26T19:00:00Z", "2026-10-26T21:00:00Z"),
            interval("2026-10-26T23:00:00Z", "2026-10-27T00:00:00Z"),
        ])

    def test_busy_covering_all_free_hours_returns_empty(self):
        snapshot = snap(weekly=(WeeklySlot(0, time(9), time(10)),), removals=(MONDAY,))
        self.assertEqual(compute_availability(snapshot, MONDAY), [])
        self.assertEqual(compute_availability(snap(), MONDAY), [])

    def test_unrelated_and_touching_busy_blocks_do_not_remove_free_time(self):
        snapshot = snap(
            weekly=(WeeklySlot(0, time(9), time(10)),),
            removals=(
                interval("2026-10-26T08:00:00-07:00", "2026-10-26T09:00:00-07:00"),
                interval("2026-10-26T10:00:00-07:00", "2026-10-26T11:00:00-07:00"),
            ),
        )
        self.assertEqual(compute_availability(snapshot, MONDAY),
                         [interval("2026-10-26T16:00:00Z", "2026-10-26T17:00:00Z")])

    def test_imported_busy_remains_hard_when_connector_disconnected(self):
        snapshot = snap(additions=(MONDAY,), calendar_connected=False,
                        calendar_busy=(BusyBlock("old", MONDAY, "calendar", "Private"),))
        self.assertEqual(compute_availability(snapshot, MONDAY), [])

    def test_aware_addition_in_other_timezone_is_the_same_instant(self):
        tokyo_slot = interval("2026-10-27T01:00:00+09:00", "2026-10-27T02:00:00+09:00")
        self.assertEqual(compute_availability(snap(additions=(tokyo_slot,)), MONDAY),
                         [interval("2026-10-26T16:00:00Z", "2026-10-26T17:00:00Z")])


class ConflictAndExamTests(unittest.TestCase):
    def test_owner_conflicts_keep_original_private_details_and_all_sources(self):
        exam_time = interval("2026-10-26T10:00:00-07:00", "2026-10-26T12:00:00-07:00")
        removal = interval("2026-10-26T09:00:00-07:00", "2026-10-26T11:00:00-07:00")
        calendar = BusyBlock("cal", exam_time, "calendar", "Private medical visit")
        snapshot = snap(removals=(removal,), exams=(Exam("exam", "STAT153", exam_time, title="Midterm"),),
                        calendar_busy=(calendar,))
        conflicts = owner_conflicts(snapshot, interval("2026-10-26T10:30:00-07:00", "2026-10-26T11:00:00-07:00"))
        self.assertEqual({value.source for value in conflicts}, {"manual", "exam", "calendar"})
        self.assertIn(calendar, conflicts)
        self.assertEqual(next(value for value in conflicts if value.id == "cal").title,
                         "Private medical visit")
        self.assertEqual(next(value for value in conflicts if value.id == "exam").interval, exam_time)

    def test_touching_exam_is_not_an_owner_conflict(self):
        snapshot = snap(exams=(Exam("e", "STAT153", interval(
            "2026-10-26T10:00:00-07:00", "2026-10-26T11:00:00-07:00")),))
        self.assertEqual(owner_conflicts(snapshot, interval(
            "2026-10-26T09:00:00-07:00", "2026-10-26T10:00:00-07:00")), [])

    def test_priority_uses_local_date_midnight_and_is_clipped(self):
        exam_time = interval("2026-10-28T15:00:00-07:00", "2026-10-28T17:00:00-07:00")
        snapshot = snap(exams=(Exam("e", "STAT153", exam_time, priority_days=2),))
        window = interval("2026-10-25T00:00:00-07:00", "2026-10-29T00:00:00-07:00")
        self.assertEqual(priority_windows(snapshot, window), [{
            "exam_id": "e", "course_id": "STAT153",
            "start": instant("2026-10-26T07:00:00Z"), "end": instant("2026-10-28T22:00:00Z"),
        }])
        self.assertEqual(priority_windows(snapshot, MONDAY)[0]["end"], MONDAY.end.astimezone(UTC))

    def test_priority_days_follow_calendar_dates_across_dst(self):
        exam = Exam("e", "CS61A", interval("2026-11-02T09:00:00-08:00", "2026-11-02T10:00:00-08:00"),
                    priority_days=2)
        window = interval("2026-10-30T00:00:00-07:00", "2026-11-03T00:00:00-08:00")
        self.assertEqual(priority_windows(snap(exams=(exam,)), window)[0]["start"],
                         instant("2026-10-31T07:00:00Z"))

    def test_priority_is_soft_and_exam_hours_alone_are_busy(self):
        exam = Exam("e", "CS61A", interval("2026-10-28T09:00:00-07:00", "2026-10-28T10:00:00-07:00"))
        snapshot = snap(weekly=(WeeklySlot(0, time(9), time(10)),), exams=(exam,))
        self.assertTrue(priority_windows(snapshot, MONDAY))
        self.assertEqual(compute_availability(snapshot, MONDAY),
                         [interval("2026-10-26T16:00:00Z", "2026-10-26T17:00:00Z")])


class SharedScheduleTests(unittest.TestCase):
    def setUp(self):
        self.first = snap(weekly=(WeeklySlot(0, time(9), time(12)),))
        self.second = ScheduleSnapshot("student-b", "America/New_York",
                                       weekly=(WeeklySlot(0, time(13), time(16)),))

    def test_shared_hours_across_timezones(self):
        self.assertEqual(intersect_availability([self.first, self.second], MONDAY, 60),
                         [interval("2026-10-26T17:00:00Z", "2026-10-26T19:00:00Z")])

    def test_three_person_intersection_respects_every_calendar(self):
        third = ScheduleSnapshot("student-c", "UTC", additions=(
            interval("2026-10-26T17:30:00Z", "2026-10-26T20:00:00Z"),),
            calendar_busy=(BusyBlock("c", interval("2026-10-26T18:00:00Z", "2026-10-26T18:30:00Z"), "calendar"),))
        self.assertEqual(intersect_availability([self.first, self.second, third], MONDAY, 30), [
            interval("2026-10-26T17:30:00Z", "2026-10-26T18:00:00Z"),
            interval("2026-10-26T18:30:00Z", "2026-10-26T19:00:00Z"),
        ])
        self.assertEqual(intersect_availability([self.first, self.second, third], MONDAY, 45), [])

    def test_adjacent_free_windows_have_no_zero_length_intersection(self):
        second = snap(additions=(interval("2026-10-26T12:00:00-07:00", "2026-10-26T13:00:00-07:00"),))
        self.assertEqual(intersect_availability([self.first, second], MONDAY, 1), [])

    def test_next_available_returns_earliest_exact_duration(self):
        self.assertEqual(next_available([self.first, self.second], MONDAY, 60),
                         interval("2026-10-26T17:00:00Z", "2026-10-26T18:00:00Z"))
        self.assertEqual(next_available([self.first, self.second], MONDAY, 60,
                                        after=instant("2026-10-26T17:30:00Z")),
                         interval("2026-10-26T17:30:00Z", "2026-10-26T18:30:00Z"))

    def test_next_slot_cannot_combine_free_fragments_across_busy(self):
        busy = BusyBlock("b", interval("2026-10-26T17:45:00Z", "2026-10-26T18:30:00Z"), "calendar")
        second = replace(self.second, calendar_busy=(busy,))
        self.assertIsNone(next_available([self.first, second], MONDAY, 60))

    def test_next_slot_uses_following_free_fragment_after_late_cutoff(self):
        first = replace(self.first, removals=(interval("2026-10-26T17:30:00Z", "2026-10-26T18:00:00Z"),))
        self.assertEqual(next_available([first, self.second], MONDAY, 45,
                                        after=instant("2026-10-26T17:15:00Z")),
                         interval("2026-10-26T18:00:00Z", "2026-10-26T18:45:00Z"))

    def test_empty_people_insufficient_duration_and_late_cutoff(self):
        self.assertEqual(intersect_availability([], MONDAY, 30), [])
        self.assertIsNone(next_available([], MONDAY, 30))
        self.assertIsNone(next_available([self.first, self.second], MONDAY, 121))
        self.assertIsNone(next_available([self.first], MONDAY, 30, after=MONDAY.end))

    def test_invalid_minimum_duration_and_naive_cutoff_fail(self):
        for value in (0, -1, float("nan"), float("inf"), True, "30"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                intersect_availability([self.first], MONDAY, value)
        with self.assertRaisesRegex(ValueError, "timezone aware"):
            next_available([self.first], MONDAY, 30, after=datetime(2026, 10, 26, 10))


class DaylightSavingTests(unittest.TestCase):
    def test_spring_nonexistent_endpoint_requires_explicit_one_off(self):
        snapshot = snap(weekly=(WeeklySlot(6, time(2, 15), time(3, 30)),))
        window = interval("2026-03-08T00:00:00-08:00", "2026-03-09T00:00:00-07:00")
        with self.assertRaisesRegex(ValueError, "Nonexistent.*aware one-off"):
            compute_availability(snapshot, window)

    def test_fall_repeated_endpoint_requires_explicit_one_off(self):
        snapshot = snap(weekly=(WeeklySlot(6, time(1, 15), time(2, 30)),))
        window = interval("2026-11-01T00:00:00-07:00", "2026-11-02T00:00:00-08:00")
        with self.assertRaisesRegex(ValueError, "Ambiguous.*aware one-off"):
            compute_availability(snapshot, window)

    def test_ambiguous_end_is_rejected_too(self):
        snapshot = snap(weekly=(WeeklySlot(6, time(0, 30), time(1, 30)),))
        window = interval("2026-11-01T00:00:00-07:00", "2026-11-02T00:00:00-08:00")
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            compute_availability(snapshot, window)

    def test_unique_endpoints_spanning_dst_have_real_elapsed_duration(self):
        snapshot = snap(weekly=(WeeklySlot(6, time(0), time(3)),))
        spring = interval("2026-03-08T00:00:00-08:00", "2026-03-09T00:00:00-07:00")
        fall = interval("2026-11-01T00:00:00-07:00", "2026-11-02T00:00:00-08:00")
        spring_free = compute_availability(snapshot, spring)[0]
        fall_free = compute_availability(snapshot, fall)[0]
        self.assertEqual(spring_free.end - spring_free.start, timedelta(hours=2))
        self.assertEqual(fall_free.end - fall_free.start, timedelta(hours=4))

    def test_dst_occurrence_outside_window_does_not_reject_query(self):
        snapshot = snap(weekly=(WeeklySlot(6, time(1, 15), time(2, 30)),))
        window = interval("2026-10-31T00:00:00-07:00", "2026-11-01T00:00:00-07:00")
        self.assertEqual(compute_availability(snapshot, window), [])

    def test_local_zone_asia_has_no_dst_guessing(self):
        snapshot = ScheduleSnapshot("tokyo", "Asia/Tokyo",
                                    weekly=(WeeklySlot(1, time(1), time(2)),))
        self.assertEqual(compute_availability(snapshot, MONDAY),
                         [interval("2026-10-26T16:00:00Z", "2026-10-26T17:00:00Z")])


if __name__ == "__main__":
    unittest.main()
