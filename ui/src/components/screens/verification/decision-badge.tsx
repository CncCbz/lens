"use client";

import { ShieldCheck, ShieldX } from "lucide-react";

import { titleForLocale, type Locale } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export function DecisionBadge({
  decision,
  locale,
}: {
  decision: string;
  locale: Locale;
}) {
  const label =
    {
      scored: titleForLocale(locale, "已评分", "Scored"),
      partial: titleForLocale(locale, "部分", "Partial"),
      unscorable: titleForLocale(locale, "无法评分", "Unscorable"),
      not_model: titleForLocale(locale, "不像模型", "Not a model"),
    }[decision] ?? decision;

  const tone =
    decision === "scored"
      ? "text-emerald-600 dark:text-emerald-400"
      : decision === "partial"
        ? "text-amber-600 dark:text-amber-400"
        : "text-muted-foreground";

  return (
    <span className={cn("inline-flex items-center gap-1 text-xs", tone)}>
      {decision === "scored" ? (
        <ShieldCheck className="size-3.5" />
      ) : (
        <ShieldX className="size-3.5" />
      )}
      {label}
    </span>
  );
}
