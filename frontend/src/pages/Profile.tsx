import { useEffect, useState } from 'react';
import { Trash2 } from 'lucide-react';
import { api, ApiError } from '../api/client';
import type {
  ActiveMedication,
  ClinicalContext,
  Diagnosis,
  DisplayPreferences,
  EquipmentItem,
  Provider,
  QuickLogButton,
  TreatmentGoal,
  UIPersonalization,
  UserProfile,
} from '../api/types';

/**
 * Profile page — Phase 3 Item 4B.
 *
 * Three tabs matching the three top-level UserProfile sections:
 *   1. Display & Preferences (DisplayPreferences)
 *   2. Clinical Context (ClinicalContext)
 *   3. Personalization (UIPersonalization)
 *
 * Each tab has its own Save button that PATCHes just that section, so a
 * user editing Display doesn't accidentally clobber unrelated Clinical
 * edits they started but hadn't saved. Clinical sub-tables (diagnoses,
 * providers, etc.) use an add-form-below-list pattern with delete-only
 * row editing — minimal but functional. Edit-in-place can land in
 * Phase 4 if usage shows it's worth the complexity.
 */

type TabId = 'display' | 'clinical' | 'personalization';

const ALL_QUICK_LOG_BUTTONS: { value: QuickLogButton; label: string }[] = [
  { value: 'medication',        label: 'Medication' },
  { value: 'symptom',           label: 'Symptom' },
  { value: 'alertness',         label: 'Alertness' },
  { value: 'sleep_environment', label: 'Environment' },
  { value: 'freeform',          label: 'Free-form note' },
];

const TIMEZONES = (() => {
  try {
    // Modern browsers support this.
    return (Intl as unknown as { supportedValuesOf?: (k: string) => string[] })
      .supportedValuesOf?.('timeZone') ?? ['UTC', 'America/New_York', 'America/Los_Angeles', 'Europe/London'];
  } catch {
    return ['UTC', 'America/New_York', 'America/Los_Angeles', 'Europe/London'];
  }
})();

export default function Profile() {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  // Phase 3 polish #1: if profile.display.timezone is the community-default
  // 'UTC' AND the browser detects a different IANA TZ, pre-fill the
  // detected TZ into the Display form. We keep `profile.display.timezone`
  // unchanged so the form's dirty check fires and the user is prompted
  // (via the Save button + a one-shot toast) to confirm the change.
  const [detectedTimezone, setDetectedTimezone] = useState<string | null>(null);
  const [tab, setTab] = useState<TabId>('display');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const p = await api.getProfile();
        setProfile(p);

        // Auto-detect browser timezone — fires only when the profile is
        // still on the 'UTC' community default and the browser knows a
        // more useful value. No auto-save; just pre-fill + toast.
        const browserTz = (() => {
          try {
            return Intl.DateTimeFormat().resolvedOptions().timeZone;
          } catch {
            return null;
          }
        })();
        if (p.display.timezone === 'UTC' && browserTz && browserTz !== 'UTC') {
          setDetectedTimezone(browserTz);
          showToast(`Detected timezone: ${browserTz} — save to confirm.`);
        }
      } catch (e) {
        setError(e instanceof ApiError ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  function showToast(msg: string) {
    setToast(msg);
    setTimeout(() => setToast(null), 5000);
  }

  async function saveTab(section: TabId, payload: object) {
    try {
      const updated = await api.patchProfile({ [section]: payload });
      setProfile(updated);
      if (section === 'clinical' && 'active_medications' in payload) {
        showToast('✓ Profile saved. Medications synced to autocomplete.');
      } else {
        showToast('✓ Profile saved.');
      }
    } catch (e) {
      showToast(`Save failed: ${e instanceof ApiError ? e.message : String(e)}`);
    }
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Profile</h1>
      </div>

      {loading && <div className="loading">Loading profile…</div>}
      {error && <div className="error-banner">{error}</div>}

      {profile && (
        <>
          <div style={{ display: 'flex', gap: '0.25rem', borderBottom: '1px solid var(--border-color)', marginBottom: '1rem' }}>
            <TabButton id="display" current={tab} onClick={setTab}>Display & Preferences</TabButton>
            <TabButton id="clinical" current={tab} onClick={setTab}>Clinical Context</TabButton>
            <TabButton id="personalization" current={tab} onClick={setTab}>Personalization</TabButton>
          </div>

          {tab === 'display' && (
            <DisplayTab
              initial={
                detectedTimezone
                  ? { ...profile.display, timezone: detectedTimezone }
                  : profile.display
              }
              dirtyBaseline={profile.display}
              onSave={(p) => saveTab('display', p)}
            />
          )}
          {tab === 'clinical' && (
            <ClinicalTab
              initial={profile.clinical}
              onSave={(p) => saveTab('clinical', p)}
            />
          )}
          {tab === 'personalization' && (
            <PersonalizationTab
              initial={profile.personalization}
              onSave={(p) => saveTab('personalization', p)}
            />
          )}
        </>
      )}

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


// --- Tab nav helper -------------------------------------------------------

function TabButton({ id, current, onClick, children }: {
  id: TabId; current: TabId; onClick: (id: TabId) => void; children: React.ReactNode;
}) {
  const active = id === current;
  return (
    <button
      type="button"
      onClick={() => onClick(id)}
      style={{
        background: 'transparent',
        border: 0,
        padding: '0.625rem 1rem',
        cursor: 'pointer',
        fontSize: '0.875rem',
        fontWeight: active ? 600 : 400,
        color: active ? 'var(--accent-primary)' : 'var(--text-secondary)',
        borderBottom: active ? '2px solid var(--accent-primary)' : '2px solid transparent',
        marginBottom: '-1px',
      }}
    >
      {children}
    </button>
  );
}


// --- Display tab ----------------------------------------------------------

function DisplayTab({ initial, dirtyBaseline, onSave }: {
  initial: DisplayPreferences;
  /** What to compare current state against for the dirty check. Defaults
   *  to `initial`. Profile component passes the un-prefilled saved
   *  profile so the auto-detected-timezone scenario can show as dirty
   *  even though the form's starting state has the detected value. */
  dirtyBaseline?: DisplayPreferences;
  onSave: (p: DisplayPreferences) => void | Promise<void>;
}) {
  const [s, setS] = useState<DisplayPreferences>(initial);
  const dirty = JSON.stringify(s) !== JSON.stringify(dirtyBaseline ?? initial);

  return (
    <div className="chart-card" style={{ maxWidth: '40rem' }}>
      <div className="field" style={{ marginBottom: '0.75rem' }}>
        <label>Display name</label>
        <input type="text" value={s.display_name ?? ''} onChange={(e) => setS({ ...s, display_name: e.target.value || null })} />
      </div>
      <div className="field" style={{ marginBottom: '0.75rem' }}>
        <label>Timezone</label>
        <select value={s.timezone} onChange={(e) => setS({ ...s, timezone: e.target.value })}>
          {TIMEZONES.map((tz) => <option key={tz} value={tz}>{tz}</option>)}
        </select>
      </div>
      <RadioRow
        label="Date format" value={s.date_format}
        options={[
          { value: 'YYYY-MM-DD', label: 'YYYY-MM-DD' },
          { value: 'MM/DD/YYYY', label: 'MM/DD/YYYY' },
          { value: 'DD/MM/YYYY', label: 'DD/MM/YYYY' },
        ]}
        onChange={(v) => setS({ ...s, date_format: v as DisplayPreferences['date_format'] })}
      />
      <RadioRow
        label="Pressure unit" value={s.pressure_unit}
        options={[{ value: 'cmH2O', label: 'cmH₂O' }, { value: 'hPa', label: 'hPa' }]}
        onChange={(v) => setS({ ...s, pressure_unit: v as DisplayPreferences['pressure_unit'] })}
      />
      <RadioRow
        label="Temperature unit" value={s.temperature_unit}
        options={[{ value: 'C', label: '°C' }, { value: 'F', label: '°F' }]}
        onChange={(v) => setS({ ...s, temperature_unit: v as DisplayPreferences['temperature_unit'] })}
      />
      <RadioRow
        label="Theme" value={s.theme}
        options={[
          { value: 'light', label: 'Light' },
          { value: 'dark', label: 'Dark (Phase 4)' },
          { value: 'auto', label: 'Auto' },
        ]}
        onChange={(v) => setS({ ...s, theme: v as DisplayPreferences['theme'] })}
      />

      <SaveButton dirty={dirty} label="Save display settings" onClick={() => onSave(s)} />
    </div>
  );
}


/**
 * Phase 3 polish #2 — save buttons now flip class between btn-primary
 * (dirty: vibrant accent color, full-strength) and btn-secondary (clean:
 * muted gray, clearly off). The CSS `:disabled` opacity rule alone made
 * dirty + clean states look too similar — both pale-blue at a glance.
 * This component makes the difference unambiguous AND surfaces a "no
 * changes" hint inline so users know why the button isn't doing anything.
 */
function SaveButton({ dirty, label, onClick }: {
  dirty: boolean; label: string; onClick: () => void;
}) {
  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: '0.625rem' }}>
      <button
        type="button"
        className={dirty ? 'btn-primary' : 'btn-secondary'}
        disabled={!dirty}
        onClick={onClick}
      >
        {label}
      </button>
      {!dirty && (
        <span style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>
          No changes to save
        </span>
      )}
    </div>
  );
}

function RadioRow({ label, value, options, onChange }: {
  label: string; value: string; options: { value: string; label: string }[]; onChange: (v: string) => void;
}) {
  return (
    <div className="field" style={{ marginBottom: '0.75rem' }}>
      <label>{label}</label>
      <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
        {options.map((o) => (
          <label key={o.value} style={{ display: 'inline-flex', alignItems: 'center', gap: '0.375rem', fontSize: '0.875rem' }}>
            <input type="radio" checked={value === o.value} onChange={() => onChange(o.value)} />
            {o.label}
          </label>
        ))}
      </div>
    </div>
  );
}


// --- Clinical tab ---------------------------------------------------------

function ClinicalTab({ initial, onSave }: {
  initial: ClinicalContext;
  onSave: (p: ClinicalContext) => void | Promise<void>;
}) {
  const [c, setC] = useState<ClinicalContext>(initial);
  const dirty = JSON.stringify(c) !== JSON.stringify(initial);

  return (
    <div>
      <ClinicalSubcard title="Diagnoses">
        <DiagnosesTable
          rows={c.diagnoses}
          onAdd={(d) => setC({ ...c, diagnoses: [...c.diagnoses, d] })}
          onDelete={(i) => setC({ ...c, diagnoses: c.diagnoses.filter((_, idx) => idx !== i) })}
        />
      </ClinicalSubcard>

      <ClinicalSubcard title="Providers">
        <ProvidersTable
          rows={c.providers}
          onAdd={(p) => setC({ ...c, providers: [...c.providers, p] })}
          onDelete={(i) => setC({ ...c, providers: c.providers.filter((_, idx) => idx !== i) })}
        />
      </ClinicalSubcard>

      <ClinicalSubcard title="Treatment Goals">
        <GoalsTable
          rows={c.treatment_goals}
          onAdd={(g) => setC({ ...c, treatment_goals: [...c.treatment_goals, g] })}
          onDelete={(i) => setC({ ...c, treatment_goals: c.treatment_goals.filter((_, idx) => idx !== i) })}
        />
      </ClinicalSubcard>

      <ClinicalSubcard title="Active Medications">
        <MedicationsTable
          rows={c.active_medications}
          onAdd={(m) => setC({ ...c, active_medications: [...c.active_medications, m] })}
          onDelete={(i) => setC({ ...c, active_medications: c.active_medications.filter((_, idx) => idx !== i) })}
        />
      </ClinicalSubcard>

      <ClinicalSubcard title="Equipment">
        <EquipmentTable
          rows={c.equipment}
          onAdd={(eq) => setC({ ...c, equipment: [...c.equipment, eq] })}
          onDelete={(i) => setC({ ...c, equipment: c.equipment.filter((_, idx) => idx !== i) })}
        />
      </ClinicalSubcard>

      <SaveButton dirty={dirty} label="Save clinical context" onClick={() => onSave(c)} />
    </div>
  );
}

function ClinicalSubcard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="chart-card" style={{ marginBottom: '1rem' }}>
      <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem' }}>{title}</h2>
      {children}
    </div>
  );
}


function DiagnosesTable({ rows, onAdd, onDelete }: {
  rows: Diagnosis[];
  onAdd: (d: Diagnosis) => void;
  onDelete: (i: number) => void;
}) {
  const [draft, setDraft] = useState<Diagnosis>({ name: '', icd10_code: null, severity: null, diagnosed_date: null, notes: null });
  return (
    <>
      <RowsTable
        empty="No diagnoses on file."
        headers={['Name', 'ICD-10', 'Severity', 'Diagnosed', 'Notes']}
        rows={rows.map((d) => [d.name, d.icd10_code ?? '—', d.severity ?? '—', d.diagnosed_date ?? '—', d.notes ?? ''])}
        onDelete={onDelete}
      />
      <AddRowGrid>
        <input placeholder="Name *" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
        <input placeholder="ICD-10" value={draft.icd10_code ?? ''} onChange={(e) => setDraft({ ...draft, icd10_code: e.target.value || null })} />
        <input placeholder="Severity" value={draft.severity ?? ''} onChange={(e) => setDraft({ ...draft, severity: e.target.value || null })} />
        <input type="date" value={draft.diagnosed_date ?? ''} onChange={(e) => setDraft({ ...draft, diagnosed_date: e.target.value || null })} />
        <input placeholder="Notes" value={draft.notes ?? ''} onChange={(e) => setDraft({ ...draft, notes: e.target.value || null })} />
        <button type="button" className="btn-secondary" disabled={!draft.name.trim()} onClick={() => {
          onAdd(draft);
          setDraft({ name: '', icd10_code: null, severity: null, diagnosed_date: null, notes: null });
        }}>Add</button>
      </AddRowGrid>
    </>
  );
}

function ProvidersTable({ rows, onAdd, onDelete }: {
  rows: Provider[];
  onAdd: (p: Provider) => void;
  onDelete: (i: number) => void;
}) {
  const [draft, setDraft] = useState<Provider>({ name: '', role: 'pcp', organization: null, notes: null });
  return (
    <>
      <RowsTable
        empty="No providers on file."
        headers={['Name', 'Role', 'Organization', 'Notes']}
        rows={rows.map((p) => [p.name, p.role, p.organization ?? '—', p.notes ?? ''])}
        onDelete={onDelete}
      />
      <AddRowGrid>
        <input placeholder="Name *" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
        <select value={draft.role} onChange={(e) => setDraft({ ...draft, role: e.target.value as Provider['role'] })}>
          <option value="pcp">PCP</option>
          <option value="sleep_md">Sleep MD</option>
          <option value="sleep_pa">Sleep PA</option>
          <option value="ent">ENT</option>
          <option value="dental_sleep">Dental Sleep</option>
          <option value="cbti">CBT-I</option>
          <option value="cardiology">Cardiology</option>
          <option value="sleep_lab">Sleep Lab</option>
          <option value="other">Other</option>
        </select>
        <input placeholder="Organization" value={draft.organization ?? ''} onChange={(e) => setDraft({ ...draft, organization: e.target.value || null })} />
        <input placeholder="Notes" value={draft.notes ?? ''} onChange={(e) => setDraft({ ...draft, notes: e.target.value || null })} />
        <button type="button" className="btn-secondary" disabled={!draft.name.trim()} onClick={() => {
          onAdd(draft);
          setDraft({ name: '', role: 'pcp', organization: null, notes: null });
        }}>Add</button>
      </AddRowGrid>
    </>
  );
}

function GoalsTable({ rows, onAdd, onDelete }: {
  rows: TreatmentGoal[];
  onAdd: (g: TreatmentGoal) => void;
  onDelete: (i: number) => void;
}) {
  const [draft, setDraft] = useState<TreatmentGoal>({ description: '', target_metric: null, target_value: null, active: true, notes: null });
  return (
    <>
      <RowsTable
        empty="No treatment goals set."
        headers={['Description', 'Target metric', 'Target value', 'Active', 'Notes']}
        rows={rows.map((g) => [g.description, g.target_metric ?? '—', g.target_value ?? '—', g.active ? '✓' : '—', g.notes ?? ''])}
        onDelete={onDelete}
      />
      <AddRowGrid>
        <input placeholder="Description *" value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
        <input placeholder="Metric (e.g., ahi)" value={draft.target_metric ?? ''} onChange={(e) => setDraft({ ...draft, target_metric: e.target.value || null })} />
        <input type="number" step="any" placeholder="Target" value={draft.target_value ?? ''} onChange={(e) => setDraft({ ...draft, target_value: e.target.value ? Number(e.target.value) : null })} />
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: '0.375rem', fontSize: '0.8125rem' }}>
          <input type="checkbox" checked={draft.active} onChange={(e) => setDraft({ ...draft, active: e.target.checked })} />
          Active
        </label>
        <input placeholder="Notes" value={draft.notes ?? ''} onChange={(e) => setDraft({ ...draft, notes: e.target.value || null })} />
        <button type="button" className="btn-secondary" disabled={!draft.description.trim()} onClick={() => {
          onAdd(draft);
          setDraft({ description: '', target_metric: null, target_value: null, active: true, notes: null });
        }}>Add</button>
      </AddRowGrid>
    </>
  );
}

function MedicationsTable({ rows, onAdd, onDelete }: {
  rows: ActiveMedication[];
  onAdd: (m: ActiveMedication) => void;
  onDelete: (i: number) => void;
}) {
  const [draft, setDraft] = useState<ActiveMedication>({
    name: '', dose: null, dose_unit: null, schedule: null, route: 'oral', started_date: null, notes: null,
  });
  return (
    <>
      <RowsTable
        empty="No active medications."
        headers={['Name', 'Dose', 'Schedule', 'Route', 'Started', 'Notes']}
        rows={rows.map((m) => [
          m.name,
          m.dose !== null ? `${m.dose}${m.dose_unit ? ' ' + m.dose_unit : ''}` : '—',
          m.schedule ?? '—',
          m.route,
          m.started_date ?? '—',
          m.notes ?? '',
        ])}
        onDelete={onDelete}
      />
      <AddRowGrid>
        <input placeholder="Name *" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
        <input type="number" step="any" placeholder="Dose" value={draft.dose ?? ''} onChange={(e) => setDraft({ ...draft, dose: e.target.value ? Number(e.target.value) : null })} />
        <input placeholder="Unit" value={draft.dose_unit ?? ''} onChange={(e) => setDraft({ ...draft, dose_unit: e.target.value || null })} />
        <input placeholder="Schedule" value={draft.schedule ?? ''} onChange={(e) => setDraft({ ...draft, schedule: e.target.value || null })} />
        <select value={draft.route} onChange={(e) => setDraft({ ...draft, route: e.target.value as ActiveMedication['route'] })}>
          <option value="oral">Oral</option>
          <option value="sublingual">Sublingual</option>
          <option value="topical">Topical</option>
          <option value="injection">Injection</option>
          <option value="other">Other</option>
        </select>
        <input type="date" value={draft.started_date ?? ''} onChange={(e) => setDraft({ ...draft, started_date: e.target.value || null })} />
        <input placeholder="Notes" value={draft.notes ?? ''} onChange={(e) => setDraft({ ...draft, notes: e.target.value || null })} />
        <button type="button" className="btn-secondary" disabled={!draft.name.trim()} onClick={() => {
          onAdd(draft);
          setDraft({ name: '', dose: null, dose_unit: null, schedule: null, route: 'oral', started_date: null, notes: null });
        }}>Add</button>
      </AddRowGrid>
    </>
  );
}

function EquipmentTable({ rows, onAdd, onDelete }: {
  rows: EquipmentItem[];
  onAdd: (eq: EquipmentItem) => void;
  onDelete: (i: number) => void;
}) {
  const [draft, setDraft] = useState<EquipmentItem>({ item_type: 'cpap', model: '', started_date: null, active: true, notes: null });
  return (
    <>
      <RowsTable
        empty="No equipment on file."
        headers={['Type', 'Model', 'Started', 'Active', 'Notes']}
        rows={rows.map((eq) => [eq.item_type, eq.model, eq.started_date ?? '—', eq.active ? '✓' : '—', eq.notes ?? ''])}
        onDelete={onDelete}
      />
      <AddRowGrid>
        <select value={draft.item_type} onChange={(e) => setDraft({ ...draft, item_type: e.target.value as EquipmentItem['item_type'] })}>
          <option value="cpap">CPAP</option>
          <option value="mask">Mask</option>
          <option value="mad">MAD</option>
          <option value="wearable">Wearable</option>
          <option value="other">Other</option>
        </select>
        <input placeholder="Model *" value={draft.model} onChange={(e) => setDraft({ ...draft, model: e.target.value })} />
        <input type="date" value={draft.started_date ?? ''} onChange={(e) => setDraft({ ...draft, started_date: e.target.value || null })} />
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: '0.375rem', fontSize: '0.8125rem' }}>
          <input type="checkbox" checked={draft.active} onChange={(e) => setDraft({ ...draft, active: e.target.checked })} />
          Active
        </label>
        <input placeholder="Notes" value={draft.notes ?? ''} onChange={(e) => setDraft({ ...draft, notes: e.target.value || null })} />
        <button type="button" className="btn-secondary" disabled={!draft.model.trim()} onClick={() => {
          onAdd(draft);
          setDraft({ item_type: 'cpap', model: '', started_date: null, active: true, notes: null });
        }}>Add</button>
      </AddRowGrid>
    </>
  );
}


// --- Personalization tab --------------------------------------------------

function PersonalizationTab({ initial, onSave }: {
  initial: UIPersonalization;
  onSave: (p: UIPersonalization) => void | Promise<void>;
}) {
  const [p, setP] = useState<UIPersonalization>(initial);
  const dirty = JSON.stringify(p) !== JSON.stringify(initial);
  const [newSymptom, setNewSymptom] = useState('');
  const [newConcern, setNewConcern] = useState('');

  return (
    <div className="chart-card" style={{ maxWidth: '46rem' }}>
      <div className="field" style={{ marginBottom: '1rem' }}>
        <label>Quick-log buttons (visible on Manual Logs page)</label>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '0.375rem', marginTop: '0.25rem' }}>
          {ALL_QUICK_LOG_BUTTONS.map(({ value, label }) => {
            const enabled = p.quick_log_buttons.includes(value);
            return (
              <label key={value} style={{ display: 'inline-flex', alignItems: 'center', gap: '0.375rem', fontSize: '0.875rem' }}>
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={(e) => {
                    if (e.target.checked) {
                      setP({ ...p, quick_log_buttons: [...p.quick_log_buttons, value] });
                    } else {
                      setP({ ...p, quick_log_buttons: p.quick_log_buttons.filter((b) => b !== value) });
                    }
                  }}
                />
                {label}
              </label>
            );
          })}
        </div>
      </div>

      <div className="field" style={{ marginBottom: '1rem' }}>
        <label>Symptom watchlist (surfaced at top of symptom autocomplete)</label>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.375rem', marginBottom: '0.5rem' }}>
          {p.symptom_watchlist.length === 0 && (
            <span style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>None.</span>
          )}
          {p.symptom_watchlist.map((s, i) => (
            <Tag key={i} label={s} onRemove={() => setP({ ...p, symptom_watchlist: p.symptom_watchlist.filter((_, idx) => idx !== i) })} />
          ))}
        </div>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <input
            type="text"
            value={newSymptom}
            onChange={(e) => setNewSymptom(e.target.value)}
            placeholder="Add a symptom name"
            style={{ flex: 1 }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && newSymptom.trim()) {
                e.preventDefault();
                setP({ ...p, symptom_watchlist: [...p.symptom_watchlist, newSymptom.trim()] });
                setNewSymptom('');
              }
            }}
          />
          <button type="button" className="btn-secondary" disabled={!newSymptom.trim()} onClick={() => {
            setP({ ...p, symptom_watchlist: [...p.symptom_watchlist, newSymptom.trim()] });
            setNewSymptom('');
          }}>Add</button>
        </div>
      </div>

      <div className="field" style={{ marginBottom: '1rem' }}>
        <label>Active concerns (surfaced to URSA agent + on Manual Logs page)</label>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.375rem', marginBottom: '0.5rem' }}>
          {p.active_concerns.length === 0 && (
            <span style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>None.</span>
          )}
          {p.active_concerns.map((c, i) => (
            <Tag key={i} label={c} onRemove={() => setP({ ...p, active_concerns: p.active_concerns.filter((_, idx) => idx !== i) })} />
          ))}
        </div>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <input
            type="text"
            value={newConcern}
            onChange={(e) => setNewConcern(e.target.value)}
            placeholder="Add a concern (e.g., investigating evening alcohol vs AHI)"
            style={{ flex: 1 }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && newConcern.trim()) {
                e.preventDefault();
                setP({ ...p, active_concerns: [...p.active_concerns, newConcern.trim()] });
                setNewConcern('');
              }
            }}
          />
          <button type="button" className="btn-secondary" disabled={!newConcern.trim()} onClick={() => {
            setP({ ...p, active_concerns: [...p.active_concerns, newConcern.trim()] });
            setNewConcern('');
          }}>Add</button>
        </div>
      </div>

      <div className="field" style={{ marginBottom: '1rem' }}>
        <label>Free-text notes (visible to URSA agent)</label>
        <textarea
          value={p.notes ?? ''}
          onChange={(e) => setP({ ...p, notes: e.target.value || null })}
          rows={3}
          style={{ width: '100%', fontFamily: 'inherit' }}
        />
      </div>

      <SaveButton dirty={dirty} label="Save personalization" onClick={() => onSave(p)} />
    </div>
  );
}


// --- Shared primitives ----------------------------------------------------

function RowsTable({ empty, headers, rows, onDelete }: {
  empty: string; headers: string[]; rows: (string | number)[][]; onDelete: (i: number) => void;
}) {
  if (rows.length === 0) {
    return <div style={{ padding: '0.5rem 0', color: 'var(--text-muted)', fontSize: '0.875rem' }}>{empty}</div>;
  }
  return (
    <table className="data-table" style={{ marginBottom: '0.5rem' }}>
      <thead>
        <tr>
          {headers.map((h) => <th key={h}>{h}</th>)}
          <th style={{ width: '2rem' }}></th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i}>
            {row.map((cell, j) => <td key={j}>{cell}</td>)}
            <td>
              <button type="button" className="icon-btn" onClick={() => onDelete(i)} title="Delete row" style={{ color: 'var(--text-muted)' }}>
                <Trash2 size={14} />
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function AddRowGrid({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center', paddingTop: '0.5rem', borderTop: '1px solid var(--border-color)' }}>
      {children}
    </div>
  );
}

function Tag({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: '0.25rem',
      padding: '0.125rem 0.5rem',
      borderRadius: '999px',
      background: 'var(--bg-secondary)',
      border: '1px solid var(--border-color)',
      fontSize: '0.8125rem',
    }}>
      {label}
      <button type="button" onClick={onRemove} style={{
        background: 'transparent', border: 0, padding: 0, cursor: 'pointer', color: 'var(--text-muted)',
        display: 'inline-flex', alignItems: 'center',
      }} title="Remove">
        ×
      </button>
    </span>
  );
}
