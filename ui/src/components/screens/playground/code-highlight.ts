import { createHighlighterCore } from "shiki/core";
import type { HighlighterCore } from "shiki/core";
import { createJavaScriptRegexEngine } from "shiki/engine/javascript";

const THEMES = { light: "github-light", dark: "github-dark" };

// Each language is its own chunk, so the list stays explicit: only what a chat
// answer realistically contains gets bundled.
const LANGUAGES: Record<string, () => Promise<{ default: never }>> = {
  bash: () => import("shiki/langs/bash.mjs") as never,
  c: () => import("shiki/langs/c.mjs") as never,
  cpp: () => import("shiki/langs/cpp.mjs") as never,
  csharp: () => import("shiki/langs/csharp.mjs") as never,
  css: () => import("shiki/langs/css.mjs") as never,
  diff: () => import("shiki/langs/diff.mjs") as never,
  docker: () => import("shiki/langs/docker.mjs") as never,
  go: () => import("shiki/langs/go.mjs") as never,
  html: () => import("shiki/langs/html.mjs") as never,
  java: () => import("shiki/langs/java.mjs") as never,
  javascript: () => import("shiki/langs/javascript.mjs") as never,
  json: () => import("shiki/langs/json.mjs") as never,
  kotlin: () => import("shiki/langs/kotlin.mjs") as never,
  lua: () => import("shiki/langs/lua.mjs") as never,
  markdown: () => import("shiki/langs/markdown.mjs") as never,
  php: () => import("shiki/langs/php.mjs") as never,
  python: () => import("shiki/langs/python.mjs") as never,
  ruby: () => import("shiki/langs/ruby.mjs") as never,
  rust: () => import("shiki/langs/rust.mjs") as never,
  shell: () => import("shiki/langs/shellscript.mjs") as never,
  sql: () => import("shiki/langs/sql.mjs") as never,
  swift: () => import("shiki/langs/swift.mjs") as never,
  toml: () => import("shiki/langs/toml.mjs") as never,
  typescript: () => import("shiki/langs/typescript.mjs") as never,
  xml: () => import("shiki/langs/xml.mjs") as never,
  yaml: () => import("shiki/langs/yaml.mjs") as never,
};

const ALIASES: Record<string, string> = {
  cjs: "javascript",
  js: "javascript",
  jsx: "javascript",
  mjs: "javascript",
  node: "javascript",
  py: "python",
  rb: "ruby",
  rs: "rust",
  sh: "bash",
  ts: "typescript",
  tsx: "typescript",
  yml: "yaml",
  zsh: "bash",
};

const CACHE_LIMIT = 300;

let highlighterPromise: Promise<HighlighterCore> | null = null;
const loaded = new Set<string>();
const cache = new Map<string, string>();

function highlighter(): Promise<HighlighterCore> {
  highlighterPromise ??= createHighlighterCore({
    themes: [
      import("shiki/themes/github-light.mjs"),
      import("shiki/themes/github-dark.mjs"),
    ],
    langs: [],
    engine: createJavaScriptRegexEngine(),
  });
  return highlighterPromise;
}

function normalize(language: string): string {
  const name = language.trim().toLowerCase().split(/\s+/)[0] ?? "";
  return ALIASES[name] ?? name;
}

/**
 * Highlight a fenced code block into shiki's dual-theme HTML. Returns null when
 * the language is unknown, which leaves the plain `<pre>` in place.
 */
export async function highlightCode(
  code: string,
  language: string,
): Promise<string | null> {
  const lang = normalize(language);
  const loader = LANGUAGES[lang];
  if (!loader) {
    return null;
  }
  const key = `${lang}\u0000${code}`;
  const hit = cache.get(key);
  if (hit !== undefined) {
    return hit;
  }
  const instance = await highlighter();
  if (!loaded.has(lang)) {
    await instance.loadLanguage(loader as never);
    loaded.add(lang);
  }
  const html = instance.codeToHtml(code, {
    lang,
    themes: THEMES,
    // Keep the wrapper markup ours; only the tokens are swapped in.
    structure: "inline",
  });
  if (cache.size >= CACHE_LIMIT) {
    const oldest = cache.keys().next().value;
    if (oldest !== undefined) {
      cache.delete(oldest);
    }
  }
  cache.set(key, html);
  return html;
}
