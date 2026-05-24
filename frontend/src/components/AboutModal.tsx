// About modal — Phase 7.3.
//
// Surfaces version info, license, OSCAR attribution, and the GitHub
// link in one operator-facing dialog. Opened from the sidebar
// footer's "About" link.
//
// Version values come from /api/v1/system/config (the same chips
// the Settings → Configuration page already shows). License + OSCAR
// attribution are static text matching the long-form Help topics
// in About URSA-OSCAR.

import { useEffect, useState } from 'react';
import { Info, ExternalLink, X } from 'lucide-react';
import { api, ApiError, type SystemConfig } from '../api/client';


interface AboutModalProps {
  open: boolean;
  onClose: () => void;
}

export default function AboutModal({ open, onClose }: AboutModalProps) {
  const [cfg, setCfg] = useState<SystemConfig | null>(null);
  const [cfgErr, setCfgErr] = useState<string | null>(null);

  // Load the config when the modal opens; freshens on each open so
  // post-upgrade version chips show up immediately.
  useEffect(() => {
    if (!open) return;
    setCfg(null);
    setCfgErr(null);
    api.getSystemConfig()
      .then(setCfg)
      .catch((e: ApiError) => setCfgErr(e.message));
  }, [open]);

  if (!open) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-card"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: '560px' }}
      >
        <div style={headerStyle}>
          <h2
            style={{
              fontSize: '1.25rem',
              fontWeight: 600,
              margin: 0,
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.5rem',
            }}
          >
            <Info size={18} />
            About URSA-OSCAR
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="icon-btn"
            aria-label="Close About"
            style={{ padding: '0.25rem' }}
          >
            <X size={16} />
          </button>
        </div>

        <p style={taglineStyle}>
          Unified Rest &amp; Somatic Analytics — OSCAR
        </p>

        <p style={blurbStyle}>
          A self-hosted analytics platform for your CPAP machine's recorded
          therapy data. Not a medical device. Not a replacement for your
          sleep medicine provider.
        </p>

        {cfgErr && <div className="error-banner">{cfgErr}</div>}
        {!cfg && !cfgErr && <div className="loading">Loading…</div>}
        {cfg && (
          <div className="chart-card" style={versionsCardStyle}>
            <table className="data-table">
              <tbody>
                <VersionRow label="API" version={cfg.images.api} />
                <VersionRow label="MCP" version={cfg.images.mcp} />
                {/* 1.1.3 — web version from the bundle's baked constant
                    if the API didn't supply an override. */}
                <VersionRow label="Web" version={cfg.images.web || __URSA_WEB_VERSION__} />
                <VersionRow label="Watcher" version={cfg.images.watcher} />
              </tbody>
            </table>
          </div>
        )}

        <div style={sectionStyle}>
          <h3 style={sectionHeaderStyle}>OSCAR attribution</h3>
          <p style={bodyStyle}>
            URSA-OSCAR builds on the file-format work of the{' '}
            <a href="https://www.sleepfiles.com/OSCAR/" target="_blank" rel="noreferrer">
              OSCAR project <ExternalLink size={11} style={inlineIconStyle} />
            </a>
            {' — the open-source CPAP data viewer that figured out how to read '}
            ResMed's proprietary SD card format. Without OSCAR, this wouldn't
            exist. The trailing "OSCAR" in URSA-OSCAR is that attribution.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={sectionHeaderStyle}>License</h3>
          <p style={bodyStyle}>
            GNU General Public License v3.0 or later. You can use, modify,
            and share URSA-OSCAR; any modified versions you distribute must
            also be GPL-3.0. Full text in the repo's <code>LICENSE</code> file.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={sectionHeaderStyle}>Source code</h3>
          <p style={bodyStyle}>
            <a
              href="https://github.com/burrellka/URSA-OSCAR"
              target="_blank"
              rel="noreferrer"
              style={{ display: 'inline-flex', alignItems: 'center', gap: '0.25rem' }}
            >
              github.com/burrellka/URSA-OSCAR
              <ExternalLink size={11} style={inlineIconStyle} />
            </a>
          </p>
        </div>

        <p style={footerStyle}>
          For full credits, version history, and known limitations, see the{' '}
          <a href="/help/about-credits" onClick={onClose}>Credits</a>,{' '}
          <a href="/help/about-version" onClick={onClose}>Version</a>, and{' '}
          <a href="/help/about-future-direction" onClick={onClose}>Future direction</a>
          {' '}topics in the Help system.
        </p>
      </div>
    </div>
  );
}


function VersionRow({ label, version }: { label: string; version: string | null }) {
  return (
    <tr>
      <td style={{ width: '6rem', color: 'var(--text-secondary)' }}>{label}</td>
      <td>
        <code>{version ?? 'unknown'}</code>
      </td>
    </tr>
  );
}


// ---------------------------------------------------------------------------
// Inline styles
// ---------------------------------------------------------------------------

const headerStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  marginBottom: '0.25rem',
};

const taglineStyle: React.CSSProperties = {
  fontSize: '0.8125rem',
  fontStyle: 'italic',
  color: 'var(--text-muted)',
  marginTop: 0,
  marginBottom: '0.75rem',
};

const blurbStyle: React.CSSProperties = {
  fontSize: '0.875rem',
  color: 'var(--text-secondary)',
  lineHeight: 1.5,
  marginTop: 0,
  marginBottom: '1rem',
};

const versionsCardStyle: React.CSSProperties = {
  marginBottom: '1rem',
  padding: '0.5rem 0.75rem',
};

const sectionStyle: React.CSSProperties = {
  marginBottom: '0.875rem',
};

const sectionHeaderStyle: React.CSSProperties = {
  fontSize: '0.6875rem',
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
  color: 'var(--text-muted)',
  margin: '0 0 0.25rem 0',
};

const bodyStyle: React.CSSProperties = {
  fontSize: '0.875rem',
  color: 'var(--text-primary)',
  lineHeight: 1.5,
  margin: 0,
};

const inlineIconStyle: React.CSSProperties = {
  marginLeft: '0.125rem',
  verticalAlign: '-1px',
};

const footerStyle: React.CSSProperties = {
  fontSize: '0.8125rem',
  color: 'var(--text-muted)',
  lineHeight: 1.5,
  marginTop: '1rem',
  marginBottom: 0,
};
