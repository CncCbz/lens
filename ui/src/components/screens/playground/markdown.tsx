"use client";

import DOMPurify from "dompurify";
import { Marked } from "marked";
import { useMemo } from "react";

import { cn } from "@/lib/utils";

const parser = new Marked({ async: false, breaks: false, gfm: true });

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
  "[&_h1]:mt-[1.6em] [&_h1]:mb-[0.65em] [&_h1]:border-b [&_h1]:pb-[0.35em] [&_h1]:text-[1.65em] [&_h1]:leading-[1.25] [&_h1]:font-bold",
  "[&_h2]:mt-[1.6em] [&_h2]:mb-[0.65em] [&_h2]:text-[1.35em] [&_h2]:leading-[1.3] [&_h2]:font-bold",
  "[&_h3]:mt-[1.6em] [&_h3]:mb-[0.65em] [&_h3]:text-[1.15em] [&_h3]:font-medium",
  "[&_h4]:mt-[1.6em] [&_h4]:mb-[0.65em] [&_h4]:text-[1em] [&_h4]:font-medium",
  "[&_ul]:my-[0.8em] [&_ul]:list-disc [&_ul]:pl-[1.45em] [&_ol]:my-[0.8em] [&_ol]:list-decimal [&_ol]:pl-[1.45em]",
  "[&_li]:my-[0.3em] [&_li>p]:my-[0.35em] [&_li>p:first-child]:mt-0 [&_li>p:last-child]:mb-0",
  "[&_strong]:font-bold [&_em]:italic [&_del]:line-through",
  "[&_a]:text-primary [&_a]:no-underline hover:[&_a]:underline hover:[&_a]:underline-offset-[0.18em]",
  "[&_code]:rounded-[5px] [&_code]:border [&_code]:border-border/60 [&_code]:bg-muted/60 [&_code]:px-[0.38em] [&_code]:py-[0.12em] [&_code]:font-mono [&_code]:text-[0.92em]",
  "[&_pre]:my-[1em] [&_pre]:overflow-x-auto [&_pre]:rounded-[10px] [&_pre]:border [&_pre]:border-border/60 [&_pre]:bg-muted/60 [&_pre]:px-[1em] [&_pre]:py-[0.9em] [&_pre]:font-mono [&_pre]:text-[0.92em] [&_pre]:leading-[1.6]",
  "[&_pre_code]:border-0 [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:text-[1em]",
  "[&_blockquote]:my-[1em] [&_blockquote]:rounded-r-md [&_blockquote]:border-l-2 [&_blockquote]:border-primary [&_blockquote]:bg-muted/60 [&_blockquote]:px-[1em] [&_blockquote]:py-[0.75em] [&_blockquote]:text-muted-foreground",
  "[&_table]:my-[1em] [&_table]:block [&_table]:w-full [&_table]:overflow-x-auto [&_table]:border-collapse [&_table]:text-[0.92em]",
  "[&_th]:border [&_th]:border-border/60 [&_th]:bg-muted/60 [&_th]:px-[0.75em] [&_th]:py-[0.65em] [&_th]:text-left [&_th]:font-medium",
  "[&_td]:border [&_td]:border-border/60 [&_td]:px-[0.75em] [&_td]:py-[0.65em] [&_td]:align-top",
  "[&_hr]:my-[1.5em] [&_hr]:border-border",
  "[&_img]:my-[1em] [&_img]:max-w-full [&_img]:rounded-lg",
);

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

type MarkdownProps = {
  text: string;
  className?: string;
};

export function Markdown({ text, className }: MarkdownProps) {
  const html = useMemo(() => renderMarkdown(text), [text]);
  if (!html) {
    return null;
  }
  return (
    <div
      className={cn(STYLES, className)}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
