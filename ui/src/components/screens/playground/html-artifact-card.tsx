"use client";

import { useMemo } from "react";
import { ExternalLink, FileCode2 } from "lucide-react";

import { useI18n } from "@/lib/i18n";

type HtmlArtifactCardProps = {
  html: string;
  streaming: boolean;
};

function extractTitle(html: string): string {
  const match = /<title[^>]*>([\s\S]*?)<\/title>/i.exec(html);
  return match?.[1]?.trim() ?? "";
}

export function HtmlArtifactCard({ html, streaming }: HtmlArtifactCardProps) {
  const { locale } = useI18n();
  const title =
    extractTitle(html) || (locale === "zh-CN" ? "HTML 预览" : "HTML preview");

  // The URL is derived, not state: React reuses it across renders and the blob
  // lives as long as this component, which is enough for a chat transcript.
  const url = useMemo(() => {
    if (streaming || !html.trim()) {
      return null;
    }
    return URL.createObjectURL(new Blob([html], { type: "text/html" }));
  }, [html, streaming]);

  if (streaming) {
    return (
      <div className="my-[1em] flex min-h-12 items-center gap-2.5 rounded-lg border-[0.5px] border-border bg-muted/30 px-2.5 py-2">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-background">
          <FileCode2 className="size-4 text-muted-foreground" />
        </span>
        <span className="min-w-0 truncate text-[13px] leading-5 font-medium">
          {title}
        </span>
        <span className="flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
          <span className="size-1.5 animate-pulse rounded-full bg-primary" />
          {locale === "zh-CN" ? "生成中…" : "Generating…"}
        </span>
      </div>
    );
  }

  return (
    <div className="my-[1em] w-full min-w-0 overflow-clip rounded-[10px] border-[0.5px] border-border/60">
      <div className="flex h-8 items-center justify-between border-b-[0.5px] border-border/60 bg-muted/40 px-2.5">
        <span className="min-w-0 truncate text-[11px] leading-none font-medium tracking-wide text-muted-foreground">
          {title}
        </span>
        {url ? (
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={locale === "zh-CN" ? "新窗口打开" : "Open in new tab"}
            className="flex size-6 shrink-0 items-center justify-center rounded-[4px] text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <ExternalLink className="size-3.5" />
          </a>
        ) : null}
      </div>
      {url ? (
        // No same-origin token, so model-authored HTML cannot reach the dashboard.
        <iframe
          title={title}
          src={url}
          sandbox="allow-scripts"
          className="h-72 w-full bg-background"
        />
      ) : null}
    </div>
  );
}
