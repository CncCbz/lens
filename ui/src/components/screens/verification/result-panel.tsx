"use client";

import { titleForLocale, useI18n } from "@/lib/i18n";
import type { ModelVerificationResult } from "@/lib/api";
import { AppDialogContent, Dialog } from "@/components/ui/dialog";
import { DecisionBadge } from "./decision-badge";
import { matchState, matchTextClass } from "./confidence";
import { cn } from "@/lib/utils";

function percent(value: number | null | undefined): string {
  if (value == null) return "—";
  return `${(value * 100).toFixed(2)}%`;
}

function VerificationDetails({ result }: { result: ModelVerificationResult }) {
  const { locale } = useI18n();
  const top = result.results.slice(0, 5);
  const match = matchState(result.matches_expected);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <DecisionBadge decision={result.decision} locale={locale} />
        <span className="font-mono text-xs text-muted-foreground">
          {result.model_name}
        </span>
        {result.prediction ? (
          <span className="text-sm font-medium">
            → {result.prediction_name || result.prediction}
          </span>
        ) : null}
        <span className={cn("text-xs font-medium", matchTextClass(match))}>
          {match === "match"
            ? titleForLocale(locale, "✓ 名称与模型一致", "✓ Name matches")
            : match === "mismatch"
              ? titleForLocale(
                  locale,
                  "✕ 名称与模型不符",
                  "✕ Name does not match",
                )
              : titleForLocale(locale, "? 无法判定", "? Undetermined")}
        </span>
        <span className="text-xs tabular-nums text-muted-foreground">
          {titleForLocale(locale, "置信度", "Confidence")}{" "}
          {percent(result.probability)}
        </span>
        <span className="text-xs tabular-nums text-muted-foreground">
          {titleForLocale(locale, "有效样本", "Valid")} {result.used_outputs}/
          {result.challenge_count}
        </span>
        <span className="text-xs tabular-nums text-muted-foreground">
          {(result.duration_ms / 1000).toFixed(1)}s
        </span>
      </div>

      {result.error_message ? (
        <p className="text-xs text-muted-foreground">{result.error_message}</p>
      ) : null}

      {top.length > 0 ? (
        <div className="flex flex-col gap-1.5">
          {top.map((candidate) => {
            const share = Math.max(
              0,
              Math.min(1, candidate.score / (top[0]?.score || 1)),
            );
            return (
              <div key={candidate.model} className="flex items-center gap-2">
                <span className="w-48 shrink-0 truncate font-mono text-xs">
                  {candidate.display_name || candidate.model}
                </span>
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full bg-primary/60"
                    style={{ width: `${share * 100}%` }}
                  />
                </div>
                <span className="w-16 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                  {percent(candidate.probability)}
                </span>
              </div>
            );
          })}
        </div>
      ) : null}

      {result.samples.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {result.samples.map((sample, index) => (
            <span
              key={index}
              className="rounded-md border-[0.5px] border-border/60 px-2 py-1 text-xs text-muted-foreground"
            >
              #{index + 1} {sample.state} {sample.count}/{sample.expected_count}
              {sample.error ? ` · ${sample.error}` : ""}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function VerificationResultDialog({
  result,
  onOpenChange,
}: {
  result: ModelVerificationResult | null;
  onOpenChange: (open: boolean) => void;
}) {
  const { locale } = useI18n();

  return (
    <Dialog open={result !== null} onOpenChange={onOpenChange}>
      {result ? (
        <AppDialogContent
          className="max-w-3xl"
          title={titleForLocale(locale, "验真结果", "Verification result")}
          description={
            result.created_at
              ? result.created_at.slice(0, 19).replace("T", " ")
              : undefined
          }
        >
          <VerificationDetails result={result} />
        </AppDialogContent>
      ) : null}
    </Dialog>
  );
}
