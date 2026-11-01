"use client";

import { useEffect, useRef, useState } from "react";
import {
  Check,
  ChevronsDownUp,
  ChevronsUpDown,
  Copy,
  WrapText,
} from "lucide-react";

import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { GraphvizBlock } from "./graphviz-block";
import { highlightCode } from "./code-highlight";
import { HtmlArtifactCard } from "./html-artifact-card";
import { LatexBlock } from "./latex-block";
import { MermaidBlock } from "./mermaid-block";
import { SvgBlock } from "./svg-block";

type CodeBlockProps = {
  code: string;
  language: string;
  streaming: boolean;
};

// Matches Cherry's MAX_COLLAPSED_CODE_HEIGHT.
const MAX_COLLAPSED_HEIGHT = 350;

const LABELS: Record<string, string> = {
  bash: "BASH",
  c: "C",
  cpp: "C++",
  csharp: "C#",
  css: "CSS",
  diff: "DIFF",
  docker: "DOCKERFILE",
  go: "GO",
  graphviz: "GRAPHVIZ",
  html: "HTML",
  java: "JAVA",
  javascript: "JS",
  json: "JSON",
  kotlin: "KOTLIN",
  latex: "LATEX",
  lua: "LUA",
  markdown: "MD",
  mermaid: "MERMAID",
  php: "PHP",
  python: "PYTHON",
  ruby: "RUBY",
  rust: "RUST",
  shell: "SHELL",
  sql: "SQL",
  svg: "SVG",
  swift: "SWIFT",
  toml: "TOML",
  typescript: "TS",
  xml: "XML",
  yaml: "YAML",
};

const ALIASES: Record<string, string> = {
  dot: "graphviz",
  gv: "graphviz",
  math: "latex",
  tex: "latex",
};

function normalize(language: string): string {
  const name = language.trim().toLowerCase().split(/\s+/)[0] ?? "";
  return ALIASES[name] ?? name;
}

const TOOL_BUTTON =
  "flex size-6 items-center justify-center rounded-[4px] text-muted-foreground transition-opacity hover:bg-accent hover:text-foreground";

export function CodeBlock({ code, language, streaming }: CodeBlockProps) {
  const { locale } = useI18n();
  const [html, setHtml] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [wrapped, setWrapped] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [tall, setTall] = useState(false);
  const bodyRef = useRef<HTMLDivElement | null>(null);

  const lang = normalize(language);
  const special =
    lang === "mermaid"
      ? "mermaid"
      : lang === "graphviz"
        ? "graphviz"
        : lang === "svg"
          ? "svg"
          : lang === "latex"
            ? "latex"
            : lang === "html"
              ? "html"
              : null;

  useEffect(() => {
    // Highlighting is per-language async; until it lands the plain text shows,
    // which is also what an unknown language keeps forever.
    if (special || !lang) {
      return;
    }
    let cancelled = false;
    void highlightCode(code, lang)
      .then((result) => {
        if (!cancelled) {
          setHtml(result);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setHtml(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [code, lang, special]);

  useEffect(() => {
    const node = bodyRef.current;
    if (!node || special) {
      return;
    }
    setTall(node.scrollHeight > MAX_COLLAPSED_HEIGHT);
  }, [code, html, special, wrapped]);

  async function copy() {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }

  if (special === "html") {
    return <HtmlArtifactCard html={code} streaming={streaming} />;
  }

  const label = LABELS[lang] ?? lang.toUpperCase();

  return (
    <div className="group/code relative my-[1em] w-full min-w-0 overflow-clip rounded-[10px] border-[0.5px] border-border/60 bg-muted/40">
      <div className="flex h-8 items-center justify-between border-b-[0.5px] border-border/60 px-2.5">
        <span className="text-[11px] leading-none font-medium tracking-wide text-muted-foreground">
          {label || "TEXT"}
        </span>
        <div
          className={cn(
            "flex items-center gap-0.5 opacity-0 transition-opacity group-hover/code:opacity-100 focus-within:opacity-100",
            copied && "opacity-100",
          )}
        >
          {special === null ? (
            <button
              type="button"
              aria-label={locale === "zh-CN" ? "换行" : "Wrap lines"}
              aria-pressed={wrapped}
              onClick={() => setWrapped((value) => !value)}
              className={cn(TOOL_BUTTON, wrapped && "text-primary")}
            >
              <WrapText className="size-3.5" />
            </button>
          ) : null}
          {tall || expanded ? (
            <button
              type="button"
              aria-label={
                expanded
                  ? locale === "zh-CN"
                    ? "收起"
                    : "Collapse"
                  : locale === "zh-CN"
                    ? "展开"
                    : "Expand"
              }
              onClick={() => setExpanded((value) => !value)}
              className={TOOL_BUTTON}
            >
              {expanded ? (
                <ChevronsDownUp className="size-3.5" />
              ) : (
                <ChevronsUpDown className="size-3.5" />
              )}
            </button>
          ) : null}
          <button
            type="button"
            aria-label={locale === "zh-CN" ? "复制" : "Copy"}
            onClick={() => void copy()}
            className={TOOL_BUTTON}
          >
            {copied ? (
              <Check className="size-3.5" />
            ) : (
              <Copy className="size-3.5" />
            )}
          </button>
        </div>
      </div>

      {special === "mermaid" ? (
        <div className="px-[1em] py-[0.9em]">
          <MermaidBlock code={code} />
        </div>
      ) : special === "graphviz" ? (
        <div className="px-[1em] py-[0.9em]">
          <GraphvizBlock code={code} />
        </div>
      ) : special === "svg" ? (
        <div className="px-[1em] py-[0.9em]">
          <SvgBlock code={code} />
        </div>
      ) : special === "latex" ? (
        <div className="px-[1em] py-[0.9em]">
          <LatexBlock code={code} streaming={streaming} />
        </div>
      ) : (
        <div
          ref={bodyRef}
          style={
            expanded || !tall ? undefined : { maxHeight: MAX_COLLAPSED_HEIGHT }
          }
          className={cn(
            "overflow-auto px-[1em] py-[0.9em] font-mono text-[0.92em] leading-[1.6] [tab-size:2]",
            !expanded && tall && "overflow-y-hidden",
          )}
        >
          {html ? (
            <code
              className={cn("shiki block", wrapped && "whitespace-pre-wrap")}
              // Shiki output is generated from the code text itself, never from
              // model-authored HTML.
              dangerouslySetInnerHTML={{ __html: html }}
            />
          ) : (
            <code
              className={cn(
                "block whitespace-pre",
                wrapped && "whitespace-pre-wrap",
              )}
            >
              {code}
              {streaming ? <span className="animate-pulse">▍</span> : null}
            </code>
          )}
        </div>
      )}

      {!expanded && tall && special === null ? (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="flex h-7 w-full items-center justify-center border-t-[0.5px] border-border/60 text-[11px] text-muted-foreground hover:bg-accent"
        >
          {locale === "zh-CN" ? "展开" : "Expand"}
        </button>
      ) : null}
    </div>
  );
}
