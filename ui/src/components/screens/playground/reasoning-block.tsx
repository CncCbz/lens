"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";

import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { Markdown } from "./markdown";

type ReasoningBlockProps = {
  text: string;
  streaming: boolean;
  durationMs?: number;
};

// Reading distance from the bottom that still counts as "follow the stream".
const FOLLOW_THRESHOLD = 24;

export function ReasoningBlock({
  text,
  streaming,
  durationMs,
}: ReasoningBlockProps) {
  const { locale } = useI18n();
  const [manualOpen, setManualOpen] = useState<boolean | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  // Following stops as soon as the reader scrolls up, and resumes at the bottom.
  const followRef = useRef(true);
  const lastTopRef = useRef(0);
  // Open while reasoning streams, collapse once the answer starts, unless the
  // reader toggled it by hand.
  const open = manualOpen ?? streaming;

  useEffect(() => {
    followRef.current = true;
    lastTopRef.current = 0;
  }, [open]);

  useEffect(() => {
    const node = scrollRef.current;
    if (!open || !node) {
      return;
    }
    // Our own writes keep the offset stable, so any change means the reader
    // scrolled; scroll events arrive too late to tell, because the content has
    // already grown by then.
    const atBottom =
      node.scrollHeight - node.scrollTop - node.clientHeight <=
      FOLLOW_THRESHOLD;
    if (Math.abs(node.scrollTop - lastTopRef.current) > 1) {
      followRef.current = atBottom;
    } else if (followRef.current) {
      node.scrollTop = node.scrollHeight;
    }
    lastTopRef.current = node.scrollTop;
  }, [open, text]);

  function handleScroll() {
    const node = scrollRef.current;
    if (!node) {
      return;
    }
    if (
      node.scrollHeight - node.scrollTop - node.clientHeight <=
      FOLLOW_THRESHOLD
    ) {
      followRef.current = true;
    }
  }

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
        onClick={() => {
          setManualOpen(!open);
        }}
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
        <div
          ref={scrollRef}
          onScroll={handleScroll}
          className="mt-1.5 max-h-[min(32rem,60dvh)] overflow-auto pr-1 text-[13px] leading-5 text-muted-foreground [overflow-anchor:none]"
        >
          <Markdown
            text={text}
            streaming={streaming}
            className="[&_.katex-display]:my-[0.6em]"
          />
        </div>
      ) : null}
    </div>
  );
}
