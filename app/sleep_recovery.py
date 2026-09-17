"""Pure calc for the Reminders window's bedtime-recovery reminder -- no game memory access itself,
just the formula documented in data/tables/sleep_recovery.json (SLEEP_RECOVERY_CONSTANTS), applied
to live Stamina/Fatigue/Power Berries (app/reminders_live_data.py's RemindersSleepData) and the live
in-game clock (RemindersLiveData.header()).

Dynamic-rule design: rather than compute a fixed "go to bed at X" once per day,
recompute the LATEST bedtime that still yields a full recovery every tick, off whatever
Stamina/Fatigue currently are. The reminder shows once that deadline is within `DEFAULT_LEAD_HOURS`
of the current time. Eating food (or anything else that changes Stamina/Fatigue live) automatically
pushes the deadline later and can clear the reminder -- no special-casing needed, it falls out of
recomputing from current values every time rather than a value snapshotted once at day-start.

Flower Vase bonuses aren't included yet -- no live memory value for vase ownership/placed flower
exists (see data/pointer_map.md's open items), so this can only under-estimate how much recovery a
player will get, never over-promise it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import game_calendar
from .data_tables import load_table

_CONSTANTS = load_table("sleep_recovery")["SLEEP_RECOVERY_CONSTANTS"]

WAKE_UP_HOUR: int = _CONSTANTS["wake_up_hour"]
_STAMINA_BASE: int = _CONSTANTS["stamina_change_base"]
_STAMINA_PER_HOUR: int = _CONSTANTS["stamina_change_per_hour_of_sleep"]
_FATIGUE_BASE: int = _CONSTANTS["fatigue_change_base"] # negative
_FATIGUE_PER_HOUR: int = _CONSTANTS["fatigue_change_per_hour_of_sleep"] # negative
_MAX_STAMINA_BASE: int = _CONSTANTS["max_stamina_base"]
_MAX_STAMINA_PER_POWER_BERRY: int = _CONSTANTS["max_stamina_per_power_berry"]
_MAX_FATIGUE: int = _CONSTANTS["max_fatigue"]

# Default "Hours Before to Notify" -- now a Settings-exposed value instead of a fixed constant.
DEFAULT_LEAD_HOURS = 3.0


def max_stamina(power_berries: int) -> int:
    return _MAX_STAMINA_BASE + _MAX_STAMINA_PER_POWER_BERRY * power_berries


def hours_until_wake(hour: int, minute: int) -> float:
    """Hours from the given in-game time until the next WAKE_UP_HOUR (wraps past midnight)."""
    now_minutes = hour * 60 + minute
    wake_minutes = WAKE_UP_HOUR * 60
    if now_minutes < wake_minutes:
        return (wake_minutes - now_minutes) / 60
    return (24 * 60 - now_minutes + wake_minutes) / 60


def hours_needed_for_full_recovery(current_stamina: int, current_fatigue: int, power_berries: int) -> float:
    """Hours of sleep needed for Stamina to reach its cap AND Fatigue to reach 0, whichever takes
    longer. Never negative -- already-full stats need 0 hours, not a negative one."""
    stamina_deficit = max_stamina(power_berries) - current_stamina - _STAMINA_BASE
    hours_for_stamina = max(0.0, stamina_deficit / _STAMINA_PER_HOUR)

    fatigue_excess = current_fatigue + _FATIGUE_BASE # _FATIGUE_BASE is negative: this is fatigue - 5
    hours_for_fatigue = max(0.0, fatigue_excess / -_FATIGUE_PER_HOUR)

    return max(hours_for_stamina, hours_for_fatigue)


def project_if_bed_now(hour: int, minute: int, current_stamina: int, current_fatigue: int,
                        power_berries: int) -> tuple[int, int]:
    """(new_stamina, new_fatigue) for going to bed at the given in-game time right now -- the same
    formula as hours_needed_for_full_recovery(), run forward instead of solved for hours_needed."""
    hours = hours_until_wake(hour, minute)
    new_stamina = current_stamina + _STAMINA_BASE + _STAMINA_PER_HOUR * hours
    new_fatigue = current_fatigue + _FATIGUE_BASE + _FATIGUE_PER_HOUR * hours

    new_stamina = max(0, min(max_stamina(power_berries), round(new_stamina)))
    new_fatigue = max(0, min(_MAX_FATIGUE, round(new_fatigue)))
    return new_stamina, new_fatigue


@dataclass
class BedtimeStatus:
    hours_until_critical: float # <= 0 once the critical bedtime has already passed today
    critical_hour: int
    critical_minute: int


def bedtime_status(hour: int, minute: int, current_stamina: int, current_fatigue: int,
                    power_berries: int) -> BedtimeStatus:
    hours_needed = hours_needed_for_full_recovery(current_stamina, current_fatigue, power_berries)
    hours_until_critical = hours_until_wake(hour, minute) - hours_needed

    total_minutes = (hour * 60 + minute + round(hours_until_critical * 60)) % (24 * 60)
    critical_hour, critical_minute = divmod(total_minutes, 60)

    return BedtimeStatus(hours_until_critical, critical_hour, critical_minute)


def _past_fixed_time(hour: int, minute: int, fixed_hour: Optional[int], fixed_minute: Optional[int]) -> bool:
    """True once the current time is at or past `fixed_hour`:`fixed_minute` (e.g. 10:00 PM),
    counting forward to the next WAKE_UP_HOUR the same way hours_until_wake() does -- so 11 PM,
    midnight, and 3 AM all still count as "past" a 10 PM fixed time, right up to wake-up. False
    when no fixed time is set (fixed_hour is None -- the Settings field was left blank)."""
    if fixed_hour is None:
        return False
    return hours_until_wake(hour, minute) <= hours_until_wake(fixed_hour, fixed_minute or 0)


def reminder_text(hour: int, minute: int, current_stamina: int, current_fatigue: int,
                   power_berries: int, lead_hours: float = DEFAULT_LEAD_HOURS,
                   fixed_hour: Optional[int] = None, fixed_minute: Optional[int] = None) -> Optional[str]:
    """None if there's nothing to show yet. Otherwise a short Reminders-header readout: the
    calculated latest bedtime for a full recovery, plus what Stamina/Fatigue would be if the player
    went to bed right now.

    Shows once EITHER: the calculated bedtime is within `lead_hours` of now, OR the current time has
    reached the optional `fixed_hour`/`fixed_minute` floor (Reminders Settings' "Always Show at
    Time"; leave both None/unset to disable that floor and rely on lead_hours alone). "Always Show
    at Time" is meant for an early check-in (e.g. 10 AM) well before the
    lead-hours window would otherwise open on its own -- it doesn't change WHAT is shown, only
    widens WHEN it's shown."""
    status = bedtime_status(hour, minute, current_stamina, current_fatigue, power_berries)
    if status.hours_until_critical > lead_hours and not _past_fixed_time(hour, minute, fixed_hour, fixed_minute):
        return None

    new_stamina, new_fatigue = project_if_bed_now(hour, minute, current_stamina, current_fatigue, power_berries)
    values_text = f"Stamina {new_stamina}/{max_stamina(power_berries)}, Fatigue {new_fatigue}/{_MAX_FATIGUE}"

    # Once the calculated bedtime has already passed, showing it (some wrapped, already-past clock
    # time) is more confusing than useful -- just say "Bed Now".
    if status.hours_until_critical <= 0:
        return f"Bed Now - {values_text}"

    bedtime_text = game_calendar.format_time_12h(status.critical_hour, status.critical_minute)
    return f"Go to bed by {bedtime_text} - {values_text}"
