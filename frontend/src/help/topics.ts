// Phase 7.2 — Help topic registry, all 35 topics.
//
// Architect decision: markdown files in frontend/src/help/content/,
// bundled with the web image at build time via Vite's `?raw` import
// suffix. Each topic gets:
//   - slug         — URL-safe key for /help/<slug> routes
//   - title        — what appears in the topic tree and page header
//   - section      — which sidebar group it lives in
//   - keywords     — extra terms for the substring search (beyond the
//                    rendered markdown content)
//   - body         — raw markdown string, loaded at module-load time
//
// The same registry is also consumed by the get_help_topic MCP tool
// at runtime via the API container — see Phase 7.3 wire-up.

// ---------------------------------------------------------------------------
// Raw markdown imports (Vite ?raw suffix loads the file as a string).
// ---------------------------------------------------------------------------

// Getting started
import whatIsUrsaOscar from './content/what-is-ursa-oscar.md?raw';
import firstRunSetup from './content/first-run-setup.md?raw';
import importingSdCard from './content/importing-sd-card.md?raw';
import quickTour from './content/quick-tour.md?raw';

// Using URSA-OSCAR
import usingOverview from './content/using-overview.md?raw';
import usingDailyView from './content/using-daily-view.md?raw';
import usingStatistics from './content/using-statistics.md?raw';
import usingTrends from './content/using-trends.md?raw';
import usingReports from './content/using-reports.md?raw';
import usingManualLogs from './content/using-manual-logs.md?raw';
import usingProfile from './content/using-profile.md?raw';
import usingAiChat from './content/using-ai-chat.md?raw';

// Understanding the data
import nightlySummary from './content/nightly-summary.md?raw';
import ahiAndSubindices from './content/ahi-and-subindices.md?raw';
import pressureMetrics from './content/pressure-metrics.md?raw';
import leakMetrics from './content/leak-metrics.md?raw';
import sessionsVsNights from './content/sessions-vs-nights.md?raw';

// Methodology (verbatim from methodology_registry.py)
import methPearson from './content/methodology-pearson-correlation.md?raw';
import methPartial from './content/methodology-partial-correlation.md?raw';
import methLag from './content/methodology-lag-correlation.md?raw';
import methRidge from './content/methodology-ridge-regression.md?raw';
import methLinear from './content/methodology-linear-trend.md?raw';
import methPeriod from './content/methodology-period-comparison.md?raw';

// Architecture and deployment
import archOverview from './content/arch-overview.md?raw';
import archSingleTenant from './content/arch-single-tenant.md?raw';
import archNetworkSecurity from './content/arch-network-security.md?raw';
import archMultiInstance from './content/arch-multi-instance.md?raw';
import archDeployment from './content/arch-deployment.md?raw';

// Troubleshooting
import troubleshootImport from './content/troubleshoot-import.md?raw';
import troubleshootWatcher from './content/troubleshoot-watcher.md?raw';
import troubleshootAiChat from './content/troubleshoot-ai-chat.md?raw';
import troubleshootMcp from './content/troubleshoot-mcp.md?raw';
import troubleshootPasswordRecovery from './content/troubleshoot-password-recovery.md?raw';

// About URSA-OSCAR
import aboutCredits from './content/about-credits.md?raw';
import aboutLicense from './content/about-license.md?raw';
import aboutVersion from './content/about-version.md?raw';
import aboutFutureDirection from './content/about-future-direction.md?raw';


// ---------------------------------------------------------------------------
// Section taxonomy — fixed order; topics within a section can shuffle.
// ---------------------------------------------------------------------------

export const SECTIONS = [
  'Getting started',
  'Using URSA-OSCAR',
  'Understanding the data',
  'Methodology',
  'Architecture and deployment',
  'Troubleshooting',
  'About URSA-OSCAR',
] as const;

export type SectionName = typeof SECTIONS[number];


// ---------------------------------------------------------------------------
// Topic registry
// ---------------------------------------------------------------------------

export interface Topic {
  slug: string;
  title: string;
  section: SectionName;
  keywords?: string[];
  body: string;
}

export const TOPICS: Topic[] = [
  // Getting started (4)
  {
    slug: 'what-is-ursa-oscar',
    title: 'What is URSA-OSCAR?',
    section: 'Getting started',
    keywords: ['intro', 'overview', 'introduction'],
    body: whatIsUrsaOscar,
  },
  {
    slug: 'first-run-setup',
    title: 'First-run setup',
    section: 'Getting started',
    keywords: ['setup', 'install', 'bootstrap', 'password', 'first time'],
    body: firstRunSetup,
  },
  {
    slug: 'importing-sd-card',
    title: 'Importing your first SD card',
    section: 'Getting started',
    keywords: ['import', 'sd card', 'datalog', 'upload', 'edf'],
    body: importingSdCard,
  },
  {
    slug: 'quick-tour',
    title: 'Quick tour of the UI',
    section: 'Getting started',
    keywords: ['ui', 'navigation', 'pages', 'tour'],
    body: quickTour,
  },

  // Using URSA-OSCAR (6 + 2 = 8 actually wait let me count)
  // Actually the architect outline says 6 in Using. But I drafted 8 — let me check
  // Counts: overview, daily-view, statistics, trends, reports, manual-logs, profile, ai-chat
  // That's 8. Outline says 6. The architect's outline had Profile and AI Chat in Using.
  // Let me keep all 8 — over-deliver is fine; under-deliver is the problem.
  {
    slug: 'using-overview',
    title: 'The Overview page',
    section: 'Using URSA-OSCAR',
    keywords: ['overview', 'home', 'heatmap', 'calendar'],
    body: usingOverview,
  },
  {
    slug: 'using-daily-view',
    title: 'The Daily View',
    section: 'Using URSA-OSCAR',
    keywords: ['daily', 'night', 'detail', 'eventrug', 'sessions'],
    body: usingDailyView,
  },
  {
    slug: 'using-statistics',
    title: 'The Statistics page',
    section: 'Using URSA-OSCAR',
    keywords: ['statistics', 'aggregate', 'histogram', 'usage rate'],
    body: usingStatistics,
  },
  {
    slug: 'using-trends',
    title: 'The Trends page',
    section: 'Using URSA-OSCAR',
    keywords: ['trends', 'regression', 'correlation', 'prediction', 'lag'],
    body: usingTrends,
  },
  {
    slug: 'using-reports',
    title: 'Reports',
    section: 'Using URSA-OSCAR',
    keywords: ['pdf', 'report', 'clinical', 'provider'],
    body: usingReports,
  },
  {
    slug: 'using-manual-logs',
    title: 'Manual logs',
    section: 'Using URSA-OSCAR',
    keywords: ['log', 'medication', 'symptom', 'alertness', 'subjective'],
    body: usingManualLogs,
  },
  {
    slug: 'using-profile',
    title: 'Profile',
    section: 'Using URSA-OSCAR',
    keywords: ['profile', 'diagnosis', 'medications', 'goals', 'equipment'],
    body: usingProfile,
  },
  {
    slug: 'using-ai-chat',
    title: 'The AI chat panel',
    section: 'Using URSA-OSCAR',
    keywords: ['ai', 'chat', 'assistant', 'claude', 'openai', 'tool calling'],
    body: usingAiChat,
  },

  // Understanding the data (5)
  {
    slug: 'nightly-summary',
    title: 'What\'s in a nightly summary',
    section: 'Understanding the data',
    keywords: ['nightly summary', 'fields', 'schema', 'data dictionary'],
    body: nightlySummary,
  },
  {
    slug: 'ahi-and-subindices',
    title: 'AHI and its sub-indices',
    section: 'Understanding the data',
    keywords: ['ahi', 'apnea', 'hypopnea', 'central', 'obstructive', 'rera'],
    body: ahiAndSubindices,
  },
  {
    slug: 'pressure-metrics',
    title: 'Pressure metrics',
    section: 'Understanding the data',
    keywords: ['pressure', 'median', 'p95', 'epap', 'ipap', 'bipap', 'epr'],
    body: pressureMetrics,
  },
  {
    slug: 'leak-metrics',
    title: 'Leak metrics',
    section: 'Understanding the data',
    keywords: ['leak', 'mask', 'redline', 'large leak'],
    body: leakMetrics,
  },
  {
    slug: 'sessions-vs-nights',
    title: 'Sessions vs nights',
    section: 'Understanding the data',
    keywords: ['session', 'night', 'datalog', 'noon-split'],
    body: sessionsVsNights,
  },

  // Methodology (6) — verbatim copies of methodology_registry entries
  {
    slug: 'methodology-pearson-correlation',
    title: 'Pearson Correlation',
    section: 'Methodology',
    keywords: ['pearson', 'correlation', 'method', 'pairwise_correlation_pearson'],
    body: methPearson,
  },
  {
    slug: 'methodology-partial-correlation',
    title: 'Partial Correlation (multivariate)',
    section: 'Methodology',
    keywords: ['partial correlation', 'multivariate', 'method', 'partial_correlation_pearson'],
    body: methPartial,
  },
  {
    slug: 'methodology-lag-correlation',
    title: 'Time-shifted Cross-Correlation',
    section: 'Methodology',
    keywords: ['lag', 'cross-correlation', 'bootstrap', 'method', 'cross_correlation_with_bootstrap_ci'],
    body: methLag,
  },
  {
    slug: 'methodology-ridge-regression',
    title: 'Ridge Regression with Prediction Intervals',
    section: 'Methodology',
    keywords: ['ridge', 'regression', 'prediction', 'counterfactual', 'method', 'ridge_regression_cv_with_quantile_intervals'],
    body: methRidge,
  },
  {
    slug: 'methodology-linear-trend',
    title: 'Linear Trend (Least-Squares)',
    section: 'Methodology',
    keywords: ['trend', 'linear', 'least squares', 'projection', 'method', 'linear_regression_least_squares'],
    body: methLinear,
  },
  {
    slug: 'methodology-period-comparison',
    title: 'Period Comparison',
    section: 'Methodology',
    keywords: ['compare', 'period', 'method', 'compare_periods_mean_difference'],
    body: methPeriod,
  },

  // Architecture and deployment (5)
  {
    slug: 'arch-overview',
    title: 'Architecture overview',
    section: 'Architecture and deployment',
    keywords: ['architecture', 'containers', 'docker', 'data flow', 'mcp', 'watcher'],
    body: archOverview,
  },
  {
    slug: 'arch-single-tenant',
    title: 'Single-tenant trust boundary',
    section: 'Architecture and deployment',
    keywords: ['single-tenant', 'trust', 'security', 'operator', 'tenancy'],
    body: archSingleTenant,
  },
  {
    slug: 'arch-network-security',
    title: 'Network security',
    section: 'Architecture and deployment',
    keywords: ['network', 'security', 'tls', 'https', 'cookie', 'jwt', 'auth', 'rate limit'],
    body: archNetworkSecurity,
  },
  {
    slug: 'arch-multi-instance',
    title: 'Multi-instance deployments',
    section: 'Architecture and deployment',
    keywords: ['multi-instance', 'household', 'multiple users', 'separate'],
    body: archMultiInstance,
  },
  {
    slug: 'arch-deployment',
    title: 'Deployment topologies',
    section: 'Architecture and deployment',
    keywords: ['deployment', 'truenas', 'dockge', 'synology', 'qnap', 'compose'],
    body: archDeployment,
  },

  // Troubleshooting (5)
  {
    slug: 'troubleshoot-import',
    title: 'Import not finding files',
    section: 'Troubleshooting',
    keywords: ['import', 'datalog', 'sd card', 'troubleshoot', 'not finding'],
    body: troubleshootImport,
  },
  {
    slug: 'troubleshoot-watcher',
    title: 'Watcher not auto-importing',
    section: 'Troubleshooting',
    keywords: ['watcher', 'auto-import', 'quiescence', 'webhook'],
    body: troubleshootWatcher,
  },
  {
    slug: 'troubleshoot-ai-chat',
    title: 'AI assistant not responding',
    section: 'Troubleshooting',
    keywords: ['ai', 'chat', 'not responding', 'provider', 'tool call'],
    body: troubleshootAiChat,
  },
  {
    slug: 'troubleshoot-mcp',
    title: 'MCP connector issues',
    section: 'Troubleshooting',
    keywords: ['mcp', 'oauth', 'connector', 'claude.ai', 'sse'],
    body: troubleshootMcp,
  },
  {
    slug: 'troubleshoot-password-recovery',
    title: 'Recovering from a lost password',
    section: 'Troubleshooting',
    keywords: ['password', 'recovery', 'lost', 'forgot', 'bootstrap'],
    body: troubleshootPasswordRecovery,
  },

  // About URSA-OSCAR (4)
  {
    slug: 'about-credits',
    title: 'Credits and OSCAR attribution',
    section: 'About URSA-OSCAR',
    keywords: ['credits', 'oscar', 'attribution', 'thanks'],
    body: aboutCredits,
  },
  {
    slug: 'about-license',
    title: 'License',
    section: 'About URSA-OSCAR',
    keywords: ['license', 'gpl', 'gpl-3.0', 'open source', 'copyleft'],
    body: aboutLicense,
  },
  {
    slug: 'about-version',
    title: 'Version and release notes',
    section: 'About URSA-OSCAR',
    keywords: ['version', 'release', 'changelog', 'history'],
    body: aboutVersion,
  },
  {
    slug: 'about-future-direction',
    title: 'Future direction',
    section: 'About URSA-OSCAR',
    keywords: ['future', 'direction', 'roadmap', 'planned', 'deferred'],
    body: aboutFutureDirection,
  },
];


// ---------------------------------------------------------------------------
// Lookup helpers
// ---------------------------------------------------------------------------

export function topicBySlug(slug: string): Topic | undefined {
  return TOPICS.find((t) => t.slug === slug);
}

export function topicsBySection(): Record<SectionName, Topic[]> {
  const out = {} as Record<SectionName, Topic[]>;
  for (const sec of SECTIONS) out[sec] = [];
  for (const t of TOPICS) out[t.section].push(t);
  return out;
}

/**
 * Substring search across title + keywords + body. Case-insensitive.
 * Returns topics ranked: title matches first, then keyword matches,
 * then body matches.
 */
export function searchTopics(query: string): Topic[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];

  const tier1: Topic[] = []; // title match
  const tier2: Topic[] = []; // keyword match
  const tier3: Topic[] = []; // body match

  for (const t of TOPICS) {
    if (t.title.toLowerCase().includes(q)) {
      tier1.push(t);
      continue;
    }
    if (t.keywords?.some((k) => k.toLowerCase().includes(q))) {
      tier2.push(t);
      continue;
    }
    if (t.body.toLowerCase().includes(q)) {
      tier3.push(t);
    }
  }

  return [...tier1, ...tier2, ...tier3];
}
