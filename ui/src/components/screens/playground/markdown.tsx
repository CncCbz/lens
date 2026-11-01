"use client";

import DOMPurify from "dompurify";
import katex from "katex";
import { Marked } from "marked";
import type { TokenizerAndRendererExtension, Tokens } from "marked";
import markedKatex from "marked-katex-extension";
import { memo, useEffect, useMemo, useRef } from "react";

import { cn } from "@/lib/utils";
import { CodeBlock } from "./code-block";
import { animateTail, createTimeline } from "./stream-animate";
import "katex/dist/katex.min.css";

type MathToken = Tokens.Generic & { text: string; displayMode: boolean };

// Models write display math as \[...\] at least as often as $$...$$, which the
// katex extension does not know about, so both pairs are tokenized here.
function mathExtension(
  name: string,
  level: "block" | "inline",
  rule: RegExp,
  marker: string,
  displayMode: boolean,
): TokenizerAndRendererExtension {
  return {
    name,
    level,
    start(src: string) {
      const index = src.indexOf(marker);
      return index < 0 ? undefined : index;
    },
    tokenizer(src: string) {
      const match = rule.exec(src);
      if (!match) {
        return undefined;
      }
      return {
        type: name,
        raw: match[0],
        text: match[1].trim(),
        displayMode,
      } as Tokens.Generic;
    },
    renderer(token: Tokens.Generic) {
      const math = token as MathToken;
      return katex.renderToString(math.text, {
        throwOnError: false,
        displayMode: math.displayMode,
      });
    },
  };
}

const parser = new Marked({ async: false, breaks: false, gfm: true });

parser.use(markedKatex({ throwOnError: false, nonStandard: true }));
parser.use({
  extensions: [
    mathExtension("mathBlock", "block", /^\\\[([\s\S]+?)\\\]/, "\\[", true),
    mathExtension("mathInline", "inline", /^\\\(([\s\S]+?)\\\)/, "\\(", false),
  ],
});

const SANITIZE_OPTIONS = { ADD_ATTR: ["target", "rel"] };

if (typeof window !== "undefined") {
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "A" && node.getAttribute("href")) {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer");
    }
  });
}

const STYLES = cn(
  "min-w-0 text-sm leading-[1.65] break-words [overflow-wrap:anywhere]",
  // Markdown keeps soft newlines out of the HTML stream, so a paragraph has to
  // honour them itself; models use \n as a line break.
  "[&_p]:my-[0.8em] [&_p]:whitespace-pre-line [&_p:first-child]:mt-0 [&_p:last-child]:mb-0",
  "[&_p:has(+ul)]:mb-[0.35em] [&_p:has(+ol)]:mb-[0.35em]",
  "[&_h1]:mt-[1.6em] [&_h1]:mb-[0.65em] [&_h1]:border-b-[0.5px] [&_h1]:border-border [&_h1]:pb-[0.35em] [&_h1]:text-[1.65em] [&_h1]:leading-[1.25] [&_h1]:font-bold [&_h1]:text-balance",
  "[&_h2]:mt-[1.6em] [&_h2]:mb-[0.65em] [&_h2]:text-[1.35em] [&_h2]:leading-[1.3] [&_h2]:font-bold [&_h2]:text-balance",
  "[&_h3]:mt-[1.6em] [&_h3]:mb-[0.65em] [&_h3]:text-[1.15em] [&_h3]:font-medium",
  "[&_h4]:mt-[1.6em] [&_h4]:mb-[0.65em] [&_h4]:text-[1em] [&_h4]:font-medium",
  "[&_ul]:my-[0.8em] [&_ul]:list-disc [&_ul]:pl-[1.45em] [&_ol]:my-[0.8em] [&_ol]:list-decimal [&_ol]:pl-[1.45em]",
  "[&_li]:my-[0.3em] [&_li]:pl-[0.15em] [&_li>p]:my-[0.35em] [&_li>p:first-child]:mt-0 [&_li>p:last-child]:mb-0",
  "[&_li::marker]:text-muted-foreground",
  "[&_strong]:font-bold [&_em]:italic [&_del]:line-through",
  "[&_a]:text-primary [&_a]:no-underline hover:[&_a]:underline hover:[&_a]:underline-offset-[0.18em]",
  // Inline code only: fenced blocks are React components with their own <code>.
  "[&_:not(pre)>code]:rounded-[5px] [&_:not(pre)>code]:border-[0.5px] [&_:not(pre)>code]:border-border [&_:not(pre)>code]:bg-muted/60 [&_:not(pre)>code]:px-[0.38em] [&_:not(pre)>code]:py-[0.12em] [&_:not(pre)>code]:font-mono [&_:not(pre)>code]:text-[0.92em] [&_:not(pre)>code]:[box-decoration-break:clone]",
  "[&_blockquote]:my-[1em] [&_blockquote]:rounded-r-md [&_blockquote]:border-l-2 [&_blockquote]:border-primary [&_blockquote]:bg-muted/60 [&_blockquote]:px-[1em] [&_blockquote]:py-[0.75em] [&_blockquote]:text-muted-foreground",
  // Cherry rounds and clips the whole table; a scrolling wrapper would lose that.
  "[&_table]:my-[1em] [&_table]:block [&_table]:w-full [&_table]:overflow-x-auto [&_table]:rounded-lg [&_table]:border-[0.5px] [&_table]:border-border [&_table]:text-[0.92em] [&_table]:leading-[1.55]",
  "[&_th]:border-r-[0.5px] [&_th]:border-b-[0.5px] [&_th]:border-border [&_th]:bg-muted/60 [&_th]:px-[0.75em] [&_th]:py-[0.65em] [&_th]:text-left [&_th]:font-medium",
  "[&_td]:border-r-[0.5px] [&_td]:border-b-[0.5px] [&_td]:border-border [&_td]:px-[0.75em] [&_td]:py-[0.65em] [&_td]:align-top",
  "[&_tr:last-child_td]:border-b-0 [&_th:last-child]:border-r-0 [&_td:last-child]:border-r-0",
  "[&_hr]:my-[1.5em] [&_hr]:border-t-[0.5px] [&_hr]:border-border",
  "[&_img]:my-[1em] [&_img]:max-w-full [&_img]:rounded-lg",
  // A wide formula scrolls sideways instead of stretching the message column.
  "[&_.katex-display]:my-[0.7em] [&_.katex-display]:overflow-x-auto [&_.katex-display]:overflow-y-hidden",
  "[&_.katex]:text-[1.05em]",
);

// Fences are cut out before parsing so each one becomes a real component with
// its own header and copy button; marked would only give an opaque <pre>.
type Chunk =
  | { kind: "markdown"; text: string }
  | { kind: "code"; language: string; code: string };

function splitFences(text: string): Chunk[] {
  const chunks: Chunk[] = [];
  const lines = text.split("\n");
  let buffer: string[] = [];
  let open: { marker: string; language: string; body: string[] } | null = null;

  const flush = () => {
    const chunk = buffer.join("\n");
    if (chunk.trim()) {
      chunks.push({ kind: "markdown", text: chunk });
    }
    buffer = [];
  };

  for (const line of lines) {
    if (open === null) {
      const match = /^\s{0,3}(`{3,}|~{3,})\s*([^\s`]*)\s*$/.exec(line);
      if (match) {
        flush();
        open = { marker: match[1][0], language: match[2] ?? "", body: [] };
        continue;
      }
      buffer.push(line);
      continue;
    }
    if (/^\s{0,3}(`{3,}|~{3,})[ \t]*$/.test(line)) {
      chunks.push({
        kind: "code",
        language: open.language,
        code: open.body.join("\n"),
      });
      open = null;
      continue;
    }
    open.body.push(line);
  }

  if (open !== null) {
    chunks.push({
      kind: "code",
      language: open.language,
      code: open.body.join("\n"),
    });
  }
  flush();
  return chunks;
}

function renderMarkdown(text: string): string {
  if (!text) {
    return "";
  }
  // DOMPurify needs a DOM. Server render never carries messages (they live in
  // localStorage), so an empty body is the right server-side output.
  if (typeof window === "undefined") {
    return "";
  }
  return DOMPurify.sanitize(parser.parse(text) as string, SANITIZE_OPTIONS);
}

const timeline = createTimeline();

/**
 * Memoised so a settled block never re-parses: the stream only appends, so all
 * but the last chunk keep the same props and skip rendering entirely.
 */
const MarkdownChunk = memo(function MarkdownChunk({
  html,
  animate,
}: {
  html: string;
  animate: boolean;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const revealed = useRef(0);

  useEffect(() => {
    const node = ref.current;
    if (!node) {
      return;
    }
    const total = node.textContent?.length ?? 0;
    // A shorter body means the message was replaced, not extended.
    if (animate && total > revealed.current) {
      animateTail(node, revealed.current, timeline);
    }
    revealed.current = total;
  }, [animate, html]);

  return <div ref={ref} dangerouslySetInnerHTML={{ __html: html }} />;
});

type MarkdownProps = {
  text: string;
  className?: string;
  /** A trailing fence without its closing marker is still being written. */
  streaming?: boolean;
};

export function Markdown({
  text,
  className,
  streaming = false,
}: MarkdownProps) {
  const chunks = useMemo(() => splitFences(text), [text]);
  if (!text) {
    return null;
  }
  return (
    <div className={cn(STYLES, className)}>
      {chunks.map((chunk, index) => {
        const last = index === chunks.length - 1;
        if (chunk.kind === "code") {
          return (
            <CodeBlock
              key={index}
              code={chunk.code}
              language={chunk.language}
              streaming={streaming && last}
            />
          );
        }
        const html = renderMarkdown(chunk.text);
        if (!html) {
          return null;
        }
        return (
          <MarkdownChunk key={index} html={html} animate={streaming && last} />
        );
      })}
    </div>
  );
}
