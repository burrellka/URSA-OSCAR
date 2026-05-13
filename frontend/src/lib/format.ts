// Display-formatting helpers used across pages.

export function formatNumber(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return n.toFixed(digits);
}

export function formatAhi(n: number | null | undefined): string {
  return formatNumber(n, 2);
}

export function formatMinutesAsHM(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return '—';
  const h = Math.floor(minutes / 60);
  const m = Math.floor(minutes % 60);
  return `${h}h ${m.toString().padStart(2, '0')}m`;
}

export function ahiSeverity(ahi: number | null | undefined): 'empty' | 'good' | 'warn' | 'bad' {
  if (ahi === null || ahi === undefined || Number.isNaN(ahi)) return 'empty';
  if (ahi <= 5) return 'good';
  if (ahi <= 15) return 'warn';
  return 'bad';
}

export function isoDateOnly(d: Date): string {
  // Local YYYY-MM-DD without timezone shenanigans
  const y = d.getFullYear();
  const m = (d.getMonth() + 1).toString().padStart(2, '0');
  const day = d.getDate().toString().padStart(2, '0');
  return `${y}-${m}-${day}`;
}
