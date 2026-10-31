"use client";

import { useState } from "react";
import { ChevronDown } from "lucide-react";

import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { Markdown } from "./markdown";

type ReasoningBlockProps = {
  text: string;
  streaming: boolean;
  durationMs?: number;
};

export function ReasoningBlock({
  text,
  streaming,
  durationMs,
}: ReasoningBlockProps) {
  const { locale } = useI18n();
  const [manualOpen, setManualOpen] = useState<boolean | null>(null);
  // Open while reasoning streams, collapse once the answer starts, unless the
  // reader toggled it by hand.
  const open = manualOpen ?? streaming;

  if (!text.trim()) {
    return null;
  }

  const seconds = durationMs === undefined ? null : durationMs / 1000;
  const label = streaming
    ? locale === "zh-CN"
      ? "思考中"
      : "Thinking"
    : seconds !== null
      ? locale === "zh-CN"
        ? `思考了 ${seconds.toFixed(1)} 秒`
        : `Thought for ${seconds.toFixed(1)}s`
      : locale === "zh-CN"
        ? "思考过程"
        : "Reasoning";

  return (
    <div className="max-w-full">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setManualOpen(!open)}
        className="flex min-h-7 w-full items-center gap-1 rounded-lg py-0.5 text-[13px] text-muted-foreground transition-colors hover:text-foreground"
      >
        <span className="truncate">{label}</span>
        {streaming ? (
          <span className="flex shrink-0 items-center gap-0.5">
            {[0, 1, 2].map((index) => (
              <span
                key={index}
                style={{ animationDelay: `${index * 150}ms` }}
                className="size-1 animate-pulse rounded-full bg-current"
              />
            ))}
          </span>
        ) : null}
        <ChevronDown
          className={cn(
            "size-3.5 shrink-0 transition-transform",
            open && "rotate-180",
          )}
        />
      </button>

      {open ? (
        <div className="mt-1.5 max-h-[min(32rem,60dvh)] overflow-auto rounded-xl bg-muted px-4 py-3 text-[13px] leading-5 text-muted-foreground">
          <Markdown
            text={text}
            className="[&_blockquote]:bg-background/40 [&_code]:bg-background/70 [&_pre]:bg-background/70 [&_th]:bg-background/50"
          />
        </div>
      ) : null}
    </div>
  );
}
