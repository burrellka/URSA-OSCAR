// In-app Help — Phase 7.
//
// Two-column layout:
//   LEFT  — topic tree organized by section, with a substring search
//           box at the top.
//   RIGHT — the active topic, rendered as Markdown via react-markdown
//           with GFM (tables, autolinks, strikethrough), math (KaTeX),
//           and code-block syntax highlighting.
//
// Routing: /help renders the index (first topic of the first
// non-empty section); /help/<slug> renders a specific topic.

import { useEffect, useMemo, useState } from 'react';
import { NavLink, useNavigate, useParams } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import rehypeHighlight from 'rehype-highlight';
import { BookOpen, Search } from 'lucide-react';

// KaTeX + highlight.js CSS — bundled via Vite at build time.
import 'katex/dist/katex.min.css';
import 'highlight.js/styles/github.css';

import {
  SECTIONS,
  searchTopics,
  topicBySlug,
  topicsBySection,
  type Topic,
} from '../help/topics';


export default function Help() {
  const { slug } = useParams<{ slug?: string }>();
  const navigate = useNavigate();
  const [query, setQuery] = useState('');

  const grouped = useMemo(() => topicsBySection(), []);
  const searchResults = useMemo(() => searchTopics(query), [query]);

  // Resolve the active topic. /help with no slug → first topic.
  // /help/<unknown-slug> → first topic + a warning banner.
  const fallbackTopic: Topic | undefined =
    grouped['Getting started'][0] ?? Object.values(grouped).flat()[0];
  const activeTopic: Topic | undefined = slug
    ? topicBySlug(slug) ?? fallbackTopic
    : fallbackTopic;
  const slugWasInvalid = !!slug && !topicBySlug(slug);

  // When we land on /help bare, redirect to the canonical first topic
  // so the URL reflects what's rendered. Keeps deep-link sharing clean.
  useEffect(() => {
    if (!slug && activeTopic) {
      navigate(`/help/${activeTopic.slug}`, { replace: true });
    }
  }, [slug, activeTopic, navigate]);

  return (
    <div style={{ display: 'flex', gap: '1.5rem', alignItems: 'flex-start' }}>
      {/* ----- LEFT: search + topic tree ----- */}
      <aside style={navStyle}>
        <div style={searchWrapStyle}>
          <Search
            size={14}
            style={{
              position: 'absolute',
              left: '0.625rem',
              top: '50%',
              transform: 'translateY(-50%)',
              color: 'var(--text-muted)',
              pointerEvents: 'none',
            }}
          />
          <input
            type="search"
            placeholder="Search Help"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            style={{ width: '100%', paddingLeft: '2rem' }}
          />
        </div>

        {query.trim() ? (
          <SearchResults
            results={searchResults}
            activeSlug={activeTopic?.slug}
          />
        ) : (
          <TopicTree grouped={grouped} activeSlug={activeTopic?.slug} />
        )}
      </aside>

      {/* ----- RIGHT: rendered topic ----- */}
      <main style={contentWrapStyle}>
        <div className="page-header">
          <h1
            className="page-title"
            style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem' }}
          >
            <BookOpen size={20} />
            {activeTopic ? activeTopic.title : 'Help'}
          </h1>
        </div>

        {slugWasInvalid && (
          <div className="error-banner" style={{ marginBottom: '1rem' }}>
            No Help topic matches "{slug}". Showing "{activeTopic?.title}" instead.
          </div>
        )}

        {activeTopic ? (
          <article className="chart-card" style={articleStyle}>
            <div style={prosePadStyle}>
              <ReactMarkdown
                remarkPlugins={[remarkGfm, remarkMath]}
                rehypePlugins={[rehypeKatex, rehypeHighlight]}
              >
                {activeTopic.body}
              </ReactMarkdown>
            </div>
          </article>
        ) : (
          <div className="empty-state">No Help topics installed yet.</div>
        )}
      </main>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function TopicTree({
  grouped, activeSlug,
}: {
  grouped: ReturnType<typeof topicsBySection>;
  activeSlug: string | undefined;
}) {
  return (
    <nav style={treeStyle}>
      {SECTIONS.map((section) => {
        const topics = grouped[section];
        if (topics.length === 0) return null;
        return (
          <div key={section} style={sectionStyle}>
            <h3 style={sectionHeaderStyle}>{section}</h3>
            <ul style={listStyle}>
              {topics.map((t) => (
                <li key={t.slug}>
                  <NavLink
                    to={`/help/${t.slug}`}
                    className={({ isActive }) =>
                      `nav-link ${isActive || t.slug === activeSlug ? 'active' : ''}`
                    }
                    style={topicLinkStyle}
                  >
                    {t.title}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </nav>
  );
}


function SearchResults({
  results, activeSlug,
}: {
  results: Topic[];
  activeSlug: string | undefined;
}) {
  if (results.length === 0) {
    return (
      <div style={{ ...sectionStyle, color: 'var(--text-muted)', fontSize: '0.875rem' }}>
        No topics match.
      </div>
    );
  }
  return (
    <nav style={treeStyle}>
      <h3 style={sectionHeaderStyle}>
        Results ({results.length})
      </h3>
      <ul style={listStyle}>
        {results.map((t) => (
          <li key={t.slug}>
            <NavLink
              to={`/help/${t.slug}`}
              className={({ isActive }) =>
                `nav-link ${isActive || t.slug === activeSlug ? 'active' : ''}`
              }
              style={topicLinkStyle}
            >
              <span style={{ fontSize: '0.6875rem', color: 'var(--text-muted)', display: 'block' }}>
                {t.section}
              </span>
              {t.title}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}


// ---------------------------------------------------------------------------
// Styles — inline objects so the Help page doesn't depend on adding to index.css.
// ---------------------------------------------------------------------------

const navStyle: React.CSSProperties = {
  width: '280px',
  flexShrink: 0,
  position: 'sticky',
  top: '1rem',
  alignSelf: 'flex-start',
  maxHeight: 'calc(100vh - 2rem)',
  overflowY: 'auto',
};

const searchWrapStyle: React.CSSProperties = {
  position: 'relative',
  marginBottom: '0.75rem',
};

const treeStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '0.75rem',
};

const sectionStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '0.125rem',
};

const sectionHeaderStyle: React.CSSProperties = {
  fontSize: '0.6875rem',
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
  color: 'var(--text-muted)',
  margin: '0 0 0.25rem 0.5rem',
};

const listStyle: React.CSSProperties = {
  listStyle: 'none',
  padding: 0,
  margin: 0,
  display: 'flex',
  flexDirection: 'column',
  gap: '0.125rem',
};

const topicLinkStyle: React.CSSProperties = {
  fontSize: '0.875rem',
  padding: '0.4rem 0.625rem',
  lineHeight: 1.3,
  display: 'block',
};

const contentWrapStyle: React.CSSProperties = {
  flex: 1,
  minWidth: 0,
};

const articleStyle: React.CSSProperties = {
  marginBottom: '1rem',
};

// Restrict prose width for readability + apply markdown-typography rules
// directly via inline styles on the wrapper. CSS for headings, code,
// tables, etc. is inherited from index.css's defaults.
const prosePadStyle: React.CSSProperties = {
  maxWidth: '76ch',
  fontSize: '0.9375rem',
  lineHeight: 1.6,
};
