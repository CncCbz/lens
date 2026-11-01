"use client";

import { useMemo } from "react";

import { Markdown } from "./markdown";

type LatexBlockProps = {
  code: string;
  streaming: boolean;
};

// A `latex` fence may hold a whole document, bare formulas, or already-fenced
// math. Normalising to $$...$$ lets the markdown pipeline do the rest.
function normalize(source: string): string {
  const text = source.trim();
  if (!text) {
    return "";
  }
  if (/\\\[|\\\(|\$\$|(^|\s)\$[^$\n]+\$/.test(text)) {
    return text;
  }
  if (/^\s*\\begin\{/.test(text)) {
    return `$$\n${text}\n$$`;
  }
  // Bare lines: one formula per line.
  return text
    .split(/\n{2,}/)
    .map((part) => `$$\n${part.trim()}\n$$`)
    .join("\n\n");
}

export function LatexBlock({ code, streaming }: LatexBlockProps) {
  const text = useMemo(() => normalize(code), [code]);
  return (
    <div className="my-[1em] overflow-x-auto rounded-lg border-[0.5px] border-border/60 bg-muted/30 px-4 py-3">
      <Markdown
        text={text}
        streaming={streaming}
        className="[&_.katex-display]:my-0"
      />
    </div>
  );
}
