/**
 * Trends — Phase 3 Item 6 (not yet implemented; this is a placeholder).
 *
 * Design note for the Item 6 build:
 *   Default date range must be "all data", not the original "last 30 days"
 *   spec. With users carrying 1-3+ years of imported data, the 30-day
 *   default shows only the most recent cluster and hides the long-arc
 *   trajectory. A trends page that doesn't surface the trajectory by
 *   default is underusing the data. The 30 / 90 / all toggles can
 *   still be present, but the initial render is all-data.
 *
 *   (Directive from Kevin + architect, captured at Phase 3 pre-Item-5
 *   polish review.)
 */
export default function Trends() {
  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Trends</h1>
      </div>
      <div className="empty-state">
        Correlations + multi-metric scatter / regression analysis ship in Phase 3 Item 6.
        <br />
        <span style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
          When live, this page will default to <strong>all data</strong> (not the
          original "last 30 days" spec) so the long-arc trajectory shows on first load.
        </span>
      </div>
    </div>
  );
}
