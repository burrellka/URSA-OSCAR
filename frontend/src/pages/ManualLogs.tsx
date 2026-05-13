import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Pill, Activity, Brain, Moon, FileText, Trash2, type LucideIcon,
} from 'lucide-react';
import { api, ApiError } from '../api/client';
import type {
  AlertnessLog,
  FreeformLog,
  ManualLogEntry,
  ManualLogType,
  MedicationLog,
  QuickLogButton,
  SleepEnvironmentLog,
  SymptomLog,
  UserProfile,
} from '../api/types';

/**
 * Manual Logs page — Phase 3 Item 4A.
 *
 * Three sections, top to bottom:
 *   1. Active-concerns banner (if profile.personalization.active_concerns is non-empty)
 *   2. Quick-log button row — dynamically built from
 *      profile.personalization.quick_log_buttons. Each button opens an
 *      inline entry form for that log type.
 *   3. Recent-entries table — last 30 days, sortable, with delete affordance.
 *
 * Autocomplete state. Each form fetches its vocab field on demand
 * (medication_name, symptom_name) once at form-open time. POSTing a new
 * value to that vocab field also updates the autocomplete suggestion list
 * the next time the form opens — and via the profile-vocab sync service,
 * also patches profile.clinical.active_medications when the field is
 * medication_name.
 */

const QUICK_LOG_BUTTONS: Record<QuickLogButton, { label: string; Icon: LucideIcon }> = {
  medication:        { label: 'Medication',  Icon: Pill },
  symptom:           { label: 'Symptom',     Icon: Activity },
  alertness:         { label: 'Alertness',   Icon: Brain },
  sleep_environment: { label: 'Environment', Icon: Moon },
  freeform:          { label: '+ Note',      Icon: FileText },
};

export default function ManualLogs() {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [entries, setEntries] = useState<ManualLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openForm, setOpenForm] = useState<ManualLogType | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const refreshEntries = useCallback(async () => {
    try {
      const list = await api.listManualLogs();
      // Backend returns oldest-first; we want newest at top.
      setEntries(
        [...list].sort((a, b) => (a.timestamp < b.timestamp ? 1 : -1)),
      );
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const [p, _] = await Promise.all([api.getProfile(), refreshEntries()]);
        setProfile(p);
      } catch (e) {
        setError(e instanceof ApiError ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    })();
  }, [refreshEntries]);

  function showToast(msg: string) {
    setToast(msg);
    setTimeout(() => setToast(null), 3500);
  }

  async function handleSave(saved: ManualLogEntry, extraMessage?: string) {
    setOpenForm(null);
    await refreshEntries();
    showToast(extraMessage ?? `Saved ${saved.log_type} entry.`);
  }

  async function handleDelete(id: number) {
    if (!window.confirm('Delete this entry?')) return;
    try {
      await api.deleteManualLog(id);
      await refreshEntries();
      showToast('Entry deleted.');
    } catch (e) {
      showToast(`Delete failed: ${e instanceof ApiError ? e.message : String(e)}`);
    }
  }

  const enabledButtons = profile?.personalization.quick_log_buttons ?? [
    'medication', 'symptom', 'alertness', 'sleep_environment', 'freeform',
  ];

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Manual Logs</h1>
      </div>

      {loading && <div className="loading">Loading…</div>}
      {error && <div className="error-banner">{error}</div>}

      {profile && profile.personalization.active_concerns.length > 0 && (
        <div
          className="chart-card"
          style={{
            marginBottom: '1rem',
            borderLeft: '3px solid var(--accent-primary)',
            background: 'var(--bg-secondary)',
          }}
        >
          <h2 style={{ fontSize: '0.875rem', fontWeight: 600, marginBottom: '0.375rem' }}>
            Active concerns
          </h2>
          <ul style={{ margin: 0, paddingLeft: '1.25rem', fontSize: '0.875rem' }}>
            {profile.personalization.active_concerns.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Quick-log buttons */}
      <div className="chart-card" style={{ marginBottom: '1rem' }}>
        <h2 style={{ fontSize: '0.875rem', fontWeight: 600, marginBottom: '0.5rem', color: 'var(--text-secondary)' }}>
          Quick log
        </h2>
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
          {enabledButtons.map((t) => {
            const { label, Icon } = QUICK_LOG_BUTTONS[t];
            return (
              <button
                key={t}
                type="button"
                className={openForm === t ? 'btn-primary' : 'btn-secondary'}
                onClick={() => setOpenForm(openForm === t ? null : t)}
                style={{ display: 'inline-flex', alignItems: 'center', gap: '0.375rem' }}
              >
                <Icon size={14} />
                {label}
              </button>
            );
          })}
        </div>

        {openForm && (
          <div style={{ marginTop: '0.75rem', paddingTop: '0.75rem', borderTop: '1px solid var(--border-color)' }}>
            <EntryForm
              type={openForm}
              profile={profile}
              onCancel={() => setOpenForm(null)}
              onSaved={handleSave}
            />
          </div>
        )}
      </div>

      {/* Recent entries */}
      <div className="chart-card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '0.5rem' }}>
          <h2 style={{ fontSize: '1rem', fontWeight: 600 }}>Recent entries</h2>
          <span style={{ color: 'var(--text-muted)', fontSize: '0.8125rem' }}>
            last 30 days · {entries.length} entr{entries.length === 1 ? 'y' : 'ies'}
          </span>
        </div>
        {entries.length === 0 ? (
          <div className="empty-state" style={{ padding: '1.5rem 0' }}>
            No entries yet. Use a quick-log button above to add one.
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: '8.5rem' }}>When</th>
                <th style={{ width: '7rem' }}>Type</th>
                <th>Value</th>
                <th>Notes</th>
                <th style={{ width: '2rem' }}></th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id ?? `${e.date}-${e.timestamp}`}>
                  <td style={{ fontVariantNumeric: 'tabular-nums' }}>
                    {formatWhen(e.timestamp)}
                  </td>
                  <td><TypeBadge t={e.log_type} /></td>
                  <td>{renderValue(e)}</td>
                  <td style={{ color: 'var(--text-secondary)' }}>{e.notes ?? ''}</td>
                  <td>
                    {e.id !== null && (
                      <button
                        type="button"
                        className="icon-btn"
                        onClick={() => handleDelete(e.id!)}
                        title="Delete this entry"
                        style={{ color: 'var(--text-muted)' }}
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {toast && (
        <div
          style={{
            position: 'fixed',
            bottom: '1.5rem',
            right: '1.5rem',
            padding: '0.625rem 1rem',
            background: 'var(--bg-elevated, white)',
            border: '1px solid var(--border-color)',
            borderRadius: '8px',
            boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
            fontSize: '0.875rem',
            zIndex: 1000,
          }}
        >
          {toast}
        </div>
      )}
    </div>
  );
}


// --- Entry form -----------------------------------------------------------

function EntryForm({
  type, profile, onCancel, onSaved,
}: {
  type: ManualLogType;
  profile: UserProfile | null;
  onCancel: () => void;
  onSaved: (saved: ManualLogEntry, extraMsg?: string) => void;
}) {
  const now = useMemo(() => new Date(), []);
  const [date, setDate] = useState(toYMD(now));
  const [time, setTime] = useState(toHM(now));
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Per-type state
  const [medName, setMedName] = useState('');
  const [medDose, setMedDose] = useState<string>('');
  const [medUnit, setMedUnit] = useState('mg');
  const [symptomName, setSymptomName] = useState('');
  const [symptomSeverity, setSymptomSeverity] = useState<number>(5);
  const [alertScore, setAlertScore] = useState<number>(7);
  const [tempC, setTempC] = useState<string>('');
  const [noiseLevel, setNoiseLevel] = useState<'quiet' | 'moderate' | 'loud' | ''>('');
  const [lightLevel, setLightLevel] = useState<'dark' | 'dim' | 'bright' | ''>('');
  const [bedPartnerPresent, setBedPartnerPresent] = useState<boolean>(false);
  const [freeformTitle, setFreeformTitle] = useState('');
  const [freeformBody, setFreeformBody] = useState('');

  // Vocab suggestions — fetched on form mount per type.
  const [medSuggestions, setMedSuggestions] = useState<string[]>([]);
  const [symptomSuggestions, setSymptomSuggestions] = useState<string[]>([]);
  useEffect(() => {
    if (type === 'medication') {
      api.getVocabField('medication_name').then(setMedSuggestions).catch(() => {});
    } else if (type === 'symptom') {
      api.getVocabField('symptom_name').then((vocab) => {
        // Surface profile's symptom_watchlist at the top of the list,
        // followed by the rest of vocab (de-duped case-insensitively).
        const watch = profile?.personalization.symptom_watchlist ?? [];
        const watchLower = new Set(watch.map((s) => s.toLowerCase()));
        const rest = vocab.filter((s) => !watchLower.has(s.toLowerCase()));
        setSymptomSuggestions([...watch, ...rest]);
      }).catch(() => {});
    }
  }, [type, profile]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setSubmitError(null);
    try {
      const timestamp = `${date}T${time}:00`;
      const common = {
        date,
        timestamp,
        notes: notes.trim() || null,
      };

      // Per-branch typed body construction. TS strict mode + discriminated
      // unions don't narrow a `let body: Union` reassignment cleanly, so
      // each branch builds its own typed const and the api call site
      // narrows via log_type inspection.
      let savedEntry: ManualLogEntry;
      let medicationName: string | null = null;

      switch (type) {
        case 'medication': {
          if (!medName.trim()) throw new Error('Medication name is required.');
          const body: Omit<MedicationLog, 'id' | 'last_updated'> = {
            log_type: 'medication',
            name: medName.trim(),
            dose: medDose ? Number(medDose) : null,
            dose_unit: medUnit.trim() || null,
            ...common,
          };
          medicationName = body.name;
          savedEntry = await api.createManualLog(body);
          break;
        }
        case 'symptom': {
          if (!symptomName.trim()) throw new Error('Symptom name is required.');
          const body: Omit<SymptomLog, 'id' | 'last_updated'> = {
            log_type: 'symptom',
            name: symptomName.trim(),
            severity: symptomSeverity,
            ...common,
          };
          savedEntry = await api.createManualLog(body);
          break;
        }
        case 'alertness': {
          const body: Omit<AlertnessLog, 'id' | 'last_updated'> = {
            log_type: 'alertness',
            score: alertScore,
            ...common,
          };
          savedEntry = await api.createManualLog(body);
          break;
        }
        case 'sleep_environment': {
          const body: Omit<SleepEnvironmentLog, 'id' | 'last_updated'> = {
            log_type: 'sleep_environment',
            temperature_c: tempC ? Number(tempC) : null,
            noise_level: noiseLevel || null,
            light_level: lightLevel || null,
            bed_partner_present: bedPartnerPresent,
            ...common,
          };
          savedEntry = await api.createManualLog(body);
          break;
        }
        case 'freeform': {
          if (!freeformBody.trim()) throw new Error('Note body is required.');
          const body: Omit<FreeformLog, 'id' | 'last_updated'> = {
            log_type: 'freeform',
            title: freeformTitle.trim() || null,
            body: freeformBody.trim(),
            ...common,
          };
          savedEntry = await api.createManualLog(body);
          break;
        }
        default: {
          // Exhaustiveness check — TS will error here if a new log_type
          // is added without updating this switch.
          const _exhaustive: never = type;
          throw new Error(`Unhandled log type: ${_exhaustive}`);
        }
      }

      // For medication entries that introduce a new name, also POST to
      // vocab so the autocomplete and Profile.active_medications stay
      // in sync. (Profile→Vocab direction fires automatically inside
      // the API for Profile-driven edits; this is the
      // Manual Logs → Vocab → Profile path.)
      let extraMsg: string | undefined;
      if (medicationName) {
        const existing = medSuggestions.map((s) => s.toLowerCase());
        if (!existing.includes(medicationName.toLowerCase())) {
          try {
            const result = await api.addVocabValue('medication', 'medication_name', medicationName);
            if (result.profile_active_medications_updated) {
              extraMsg = `Saved. Added ${medicationName} to your active medications.`;
            }
          } catch {
            // Don't block save on vocab-sync failure.
          }
        }
      }

      onSaved(savedEntry, extraMsg);
    } catch (e) {
      setSubmitError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', marginBottom: '0.75rem' }}>
        <div className="field" style={{ width: '9rem' }}>
          <label>Date</label>
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
        </div>
        <div className="field" style={{ width: '7rem' }}>
          <label>Time</label>
          <input type="time" value={time} onChange={(e) => setTime(e.target.value)} required />
        </div>
      </div>

      {type === 'medication' && (
        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', marginBottom: '0.5rem' }}>
          <div className="field" style={{ flex: '1 1 14rem' }}>
            <label>Medication</label>
            <input
              list="ursa-med-list"
              type="text"
              value={medName}
              onChange={(e) => setMedName(e.target.value)}
              placeholder="e.g., Melatonin"
              required
            />
            <datalist id="ursa-med-list">
              {medSuggestions.map((s) => <option key={s} value={s} />)}
            </datalist>
          </div>
          <div className="field" style={{ width: '8rem' }}>
            <label>Dose</label>
            <input type="number" step="any" value={medDose}
                   onChange={(e) => setMedDose(e.target.value)} placeholder="e.g., 3" />
          </div>
          <div className="field" style={{ width: '6rem' }}>
            <label>Unit</label>
            <input type="text" value={medUnit} onChange={(e) => setMedUnit(e.target.value)} />
          </div>
        </div>
      )}

      {type === 'symptom' && (
        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', marginBottom: '0.5rem' }}>
          <div className="field" style={{ flex: '1 1 14rem' }}>
            <label>Symptom</label>
            <input
              list="ursa-symptom-list"
              type="text"
              value={symptomName}
              onChange={(e) => setSymptomName(e.target.value)}
              placeholder="e.g., headache"
              required
            />
            <datalist id="ursa-symptom-list">
              {symptomSuggestions.map((s) => <option key={s} value={s} />)}
            </datalist>
          </div>
          <div className="field" style={{ width: '12rem' }}>
            <label>Severity ({symptomSeverity})</label>
            <input
              type="range" min={0} max={10} step={1}
              value={symptomSeverity}
              onChange={(e) => setSymptomSeverity(Number(e.target.value))}
            />
          </div>
        </div>
      )}

      {type === 'alertness' && (
        <div className="field" style={{ width: '20rem', marginBottom: '0.5rem' }}>
          <label>Alertness score ({alertScore})</label>
          <input
            type="range" min={1} max={10} step={1}
            value={alertScore}
            onChange={(e) => setAlertScore(Number(e.target.value))}
          />
          <span className="stat-sub" style={{ color: 'var(--text-muted)' }}>
            1 = extremely sleepy · 10 = extremely alert
          </span>
        </div>
      )}

      {type === 'sleep_environment' && (
        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', marginBottom: '0.5rem' }}>
          <div className="field" style={{ width: '8rem' }}>
            <label>Temp °C</label>
            <input type="number" step="0.1" value={tempC}
                   onChange={(e) => setTempC(e.target.value)} placeholder="e.g., 19" />
          </div>
          <div className="field" style={{ width: '10rem' }}>
            <label>Noise</label>
            <select value={noiseLevel} onChange={(e) => setNoiseLevel(e.target.value as never)}>
              <option value="">—</option>
              <option value="quiet">Quiet</option>
              <option value="moderate">Moderate</option>
              <option value="loud">Loud</option>
            </select>
          </div>
          <div className="field" style={{ width: '10rem' }}>
            <label>Light</label>
            <select value={lightLevel} onChange={(e) => setLightLevel(e.target.value as never)}>
              <option value="">—</option>
              <option value="dark">Dark</option>
              <option value="dim">Dim</option>
              <option value="bright">Bright</option>
            </select>
          </div>
          <div className="field" style={{ display: 'flex', alignItems: 'flex-end', paddingBottom: '0.375rem' }}>
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: '0.375rem' }}>
              <input
                type="checkbox"
                checked={bedPartnerPresent}
                onChange={(e) => setBedPartnerPresent(e.target.checked)}
              />
              Bed partner present
            </label>
          </div>
        </div>
      )}

      {type === 'freeform' && (
        <div style={{ marginBottom: '0.5rem' }}>
          <div className="field" style={{ marginBottom: '0.5rem' }}>
            <label>Title (optional)</label>
            <input type="text" value={freeformTitle}
                   onChange={(e) => setFreeformTitle(e.target.value)} />
          </div>
          <div className="field">
            <label>Note</label>
            <textarea
              value={freeformBody}
              onChange={(e) => setFreeformBody(e.target.value)}
              rows={4}
              required
              style={{ width: '100%', fontFamily: 'inherit' }}
            />
          </div>
        </div>
      )}

      <div className="field" style={{ marginBottom: '0.75rem' }}>
        <label>Notes (optional)</label>
        <input type="text" value={notes} onChange={(e) => setNotes(e.target.value)} />
      </div>

      {submitError && <div className="error-banner" style={{ marginBottom: '0.5rem' }}>{submitError}</div>}

      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <button type="submit" className="btn-primary" disabled={submitting}>
          {submitting ? 'Saving…' : 'Save'}
        </button>
        <button type="button" className="btn-secondary" onClick={onCancel} disabled={submitting}>
          Cancel
        </button>
      </div>
    </form>
  );
}


// --- Helpers --------------------------------------------------------------

function TypeBadge({ t }: { t: ManualLogType }) {
  const colors: Record<ManualLogType, string> = {
    medication:        'var(--accent-primary)',
    symptom:           'var(--event-oa)',
    alertness:         'var(--tier-primary)',
    sleep_environment: 'var(--tier-tertiary)',
    freeform:          'var(--text-secondary)',
  };
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.375rem', fontSize: '0.8125rem' }}>
      <span style={{ width: 8, height: 8, borderRadius: 4, background: colors[t] }} />
      {t.replace('_', ' ')}
    </span>
  );
}

function renderValue(e: ManualLogEntry): React.ReactNode {
  switch (e.log_type) {
    case 'medication':
      return (
        <span>
          <strong>{e.name}</strong>
          {e.dose !== null && (
            <> {e.dose}{e.dose_unit ? ` ${e.dose_unit}` : ''}</>
          )}
        </span>
      );
    case 'symptom':
      return (
        <span>
          <strong>{e.name}</strong>
          {e.severity !== null && <> · severity {e.severity}/10</>}
        </span>
      );
    case 'alertness':
      return <span>Score <strong>{e.score}</strong>/10</span>;
    case 'sleep_environment': {
      const parts: string[] = [];
      if (e.temperature_c !== null) parts.push(`${e.temperature_c.toFixed(1)}°C`);
      if (e.noise_level) parts.push(`noise ${e.noise_level}`);
      if (e.light_level) parts.push(`light ${e.light_level}`);
      if (e.bed_partner_present) parts.push('partner');
      return <span>{parts.length ? parts.join(' · ') : '—'}</span>;
    }
    case 'freeform':
      return (
        <span>
          {e.title && <strong>{e.title}: </strong>}
          <span style={{ color: 'var(--text-secondary)' }}>{truncate(e.body, 80)}</span>
        </span>
      );
  }
}

function formatWhen(iso: string): string {
  // "2026-05-08T21:00:00" -> "5/8 21:00"
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function toYMD(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function toHM(d: Date): string {
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function pad(n: number): string {
  return n.toString().padStart(2, '0');
}

function truncate(s: string, max: number): string {
  return s.length <= max ? s : s.slice(0, max - 1) + '…';
}
