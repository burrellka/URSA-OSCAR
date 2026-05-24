/// <reference types="vite/client" />

// Phase 7 — Help system uses Vite's `?raw` import suffix to bundle
// markdown files as strings inside the web image at build time.
// TypeScript needs an ambient declaration so `import x from '...md?raw'`
// type-checks.
declare module '*.md?raw' {
  const content: string;
  export default content;
}

// 1.1.3 — web container's own version, baked into the bundle at build
// time via Vite's `define` (see vite.config.ts). The Settings page
// reads this to populate the web image-version chip without needing
// an API roundtrip.
declare const __URSA_WEB_VERSION__: string;
