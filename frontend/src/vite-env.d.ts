/// <reference types="vite/client" />

// Phase 7 — Help system uses Vite's `?raw` import suffix to bundle
// markdown files as strings inside the web image at build time.
// TypeScript needs an ambient declaration so `import x from '...md?raw'`
// type-checks.
declare module '*.md?raw' {
  const content: string;
  export default content;
}
