/**
 * Display-time offset for ResMed device-clock drift.
 *
 * Phase 4 Ticket 4 — the AirSense 11 records wall-clock local time
 * but doesn't auto-adjust for DST. URSA stores what the EDF recorded
 * (canonical, byte-faithful to the SD card) and shifts at display
 * time based on the operator's DeviceClock profile.
 *
 * All UI sites that format timestamps go through `applyDeviceClockOffset`
 * so the rendered wall-clock matches the operator's actual local time
 * regardless of how their device is configured.
 */
import type { DeviceClock, UserProfile } from '../api/types';


/**
 * Return the display Date for a recorded timestamp.
 *
 * - When ``profile`` is null/undefined or device_clock.mode is 'none',
 *   returns the input Date unchanged.
 * - When mode='static', shifts the Date by manual_offset_minutes.
 * - When mode='auto', computes the difference between the browser's
 *   local UTC offset for THAT date (DST-aware, via getTimezoneOffset)
 *   and the device's static offset, and applies it. Handles spring-
 *   forward and fall-back naturally since getTimezoneOffset varies by
 *   the date you call it on.
 */
export function applyDeviceClockOffset(
  input: string | Date,
  profile: UserProfile | null | undefined,
): Date {
  const date = typeof input === 'string' ? new Date(input) : new Date(input.getTime());
  const minutes = computeShiftMinutes(date, profile);
  if (minutes === 0) return date;
  return new Date(date.getTime() + minutes * 60_000);
}


/**
 * Compute the offset shift in minutes that would be applied to a date.
 * Useful when pre-shifting a numeric epoch (e.g., uPlot timestamps)
 * without going through a Date round-trip per sample.
 *
 * Returns 0 when no shift applies — callers can short-circuit.
 */
export function computeShiftMinutes(
  date: Date,
  profile: UserProfile | null | undefined,
): number {
  if (!profile) return 0;
  const dc: DeviceClock | undefined = profile.display?.device_clock;
  if (!dc) return 0;

  if (dc.mode === 'none') return 0;
  if (dc.mode === 'static') return dc.manual_offset_minutes;
  if (dc.mode === 'auto') {
    if (dc.device_utc_offset_minutes == null) return 0;
    // Browser's "minutes from UTC" for this date. getTimezoneOffset()
    // returns positive minutes BEHIND UTC (so EDT = +240, JST = -540).
    // We negate to get the more intuitive "minutes ahead of UTC".
    const browserOffset = -date.getTimezoneOffset();
    return browserOffset - dc.device_utc_offset_minutes;
  }
  return 0;
}


/**
 * Apply the offset to a uPlot-style epoch-seconds array, returning a
 * new array. Used in the Daily View timeseries loader so the chart
 * X-axis labels render the operator's local time.
 *
 * Computes the shift ONCE per session (using the first timestamp) to
 * avoid per-sample Date math; on a DST boundary that would be a few
 * minutes off for samples on the other side of the transition — an
 * acceptable corner case since the operator rarely sleeps across a
 * DST boundary, and we round to per-second display precision.
 */
export function shiftEpochSecondsForDisplay(
  seconds: number[],
  profile: UserProfile | null | undefined,
): number[] {
  if (seconds.length === 0) return seconds;
  const firstDate = new Date(seconds[0] * 1000);
  const shift = computeShiftMinutes(firstDate, profile);
  if (shift === 0) return seconds;
  const deltaSec = shift * 60;
  return seconds.map((s) => s + deltaSec);
}


/**
 * Shift a naive ISO string and return a NEW naive ISO string whose
 * local-time components reflect the shifted wall-clock. Used to
 * round-trip events / session start-end values through the rest of
 * the UI without changing the call sites that parse ISO strings.
 *
 * Critically: the returned ISO has NO Z suffix and no offset. JS
 * Date(...) interprets it as local time, matching the operator's
 * desired wall-clock.
 */
export function shiftIsoForDisplay(
  iso: string,
  profile: UserProfile | null | undefined,
): string {
  if (!profile) return iso;
  const shifted = applyDeviceClockOffset(iso, profile);
  return localIsoString(shifted);
}


function localIsoString(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  );
}
