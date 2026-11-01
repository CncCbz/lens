"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ShieldCheck,
  ShieldQuestion,
  ShieldX,
} from "lucide-react";

import {
  apiRequest,
  listLatestModelVerifications,
  type SettingItem,
} from "@/lib/api";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { titleForLocale, type Locale } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export type VerdictTone = "trusted" | "questionable" | "risky" | "unknown";

/**
 * What the user actually asks: did the API return the model its name claims?
 * `null` means the backend could not decide (no prediction, or the run failed).
 */
export type MatchState = "match" | "mismatch" | "unknown";

export function matchState(matches: boolean | null | undefined): MatchState {
  if (matches === true) return "match";
  if (matches === false) return "mismatch";
  return "unknown";
}

const GATEWAY_SUFFIX_RE =
  /-(?:free|paid|online|latest|high|medium|low|minimal|xhigh|max)$/i;
const DATE_SUFFIX_RE = /-(\d{4}|\d{8})$/;

type ModelIdentity = {
  strict: string;
  base: string;
  dateSuffix: string;
};

function extractModelIdentities(value: string): ModelIdentity[] {
  const raw = value.trim().toLowerCase();
  if (!raw) return [];
  const seeds: string[] = [];
  if (raw.includes("/")) {
    const tail = raw.slice(raw.lastIndexOf("/") + 1).trim();
    if (tail) seeds.push(tail);
  }
  seeds.push(raw);
  const identities: ModelIdentity[] = [];
  const seen = new Set<string>();
  for (const seed of seeds) {
    let current = seed;
    while (current) {
      const stripped = current
        .replace(GATEWAY_SUFFIX_RE, "")
        .replace(/^[\s._-]+|[\s._-]+$/g, "");
      if (!stripped || stripped === current) break;
      current = stripped;
    }
    const strict = current.replace(/[\s._-]+/g, "-").replace(/^-+|-+$/g, "");
    if (!strict) continue;
    const dateMatch = DATE_SUFFIX_RE.exec(strict);
    const dateSuffix = dateMatch ? dateMatch[1] : "";
    const base = dateMatch
      ? strict.slice(0, dateMatch.index).replace(/-+$/g, "") || strict
      : strict;
    const key = `${strict}|${base}|${dateSuffix}`;
    if (!seen.has(key)) {
      seen.add(key);
      identities.push({ strict, base, dateSuffix });
    }
  }
  return identities;
}

function modelsMatch(claimed: string, ...candidates: string[]): boolean {
  const claimedIds = extractModelIdentities(claimed);
  if (claimedIds.length === 0) return false;
  for (const candidate of candidates) {
    for (const cand of extractModelIdentities(candidate)) {
      for (const claim of claimedIds) {
        if (claim.strict === cand.strict) return true;
        if (
          claim.base === cand.base &&
          (!claim.dateSuffix || !cand.dateSuffix)
        ) {
          return true;
        }
      }
    }
  }
  return false;
}

/**
 * Rows written before the comparison existed have no `matches_expected` but do
 * carry both names, so derive the answer rather than showing a blank verdict.
 */
function resolveMatch(verdict: LatestVerdict | undefined): MatchState {
  if (!verdict) return "unknown";
  if (verdict.matches_expected != null)
    return matchState(verdict.matches_expected);
  // Nothing to compare against: the claim is not in the reference bank, so
  // the verifier could not score it at all.
  if (verdict.expected_probability === null && verdict.prediction) {
    return "unknown";
  }
  const identified = (
    verdict.prediction_name ||
    verdict.prediction ||
    ""
  ).trim();
  const claimed = (verdict.expected_model || verdict.model_name || "").trim();
  if (!identified || !claimed) return "unknown";
  return modelsMatch(claimed, verdict.prediction, verdict.prediction_name)
    ? "match"
    : "mismatch";
}

const MATCH_CLASS: Record<MatchState, string> = {
  match: "text-emerald-600 dark:text-emerald-400",
  mismatch: "text-destructive",
  unknown: "text-muted-foreground",
};

export function matchTextClass(state: MatchState): string {
  return MATCH_CLASS[state];
}

export type Thresholds = {
  /** At or above this the verdict is trusted. */
  trust: number;
  /** Below this the verdict is flagged. */
  warn: number;
};

export const DEFAULT_THRESHOLDS: Thresholds = { trust: 80, warn: 50 };

/**
 * A verdict without a probability cannot be graded, so it stays unknown rather
 * than silently reading as trusted.
 */
export function verdictTone(
  probability: number | null | undefined,
  thresholds: Thresholds = DEFAULT_THRESHOLDS,
): VerdictTone {
  if (probability == null) return "unknown";
  const percent = probability * 100;
  if (percent >= thresholds.trust) return "trusted";
  if (percent >= thresholds.warn) return "questionable";
  return "risky";
}

const TONE_CLASS: Record<VerdictTone, string> = {
  trusted: "text-emerald-600 dark:text-emerald-400",
  questionable: "text-amber-600 dark:text-amber-400",
  risky: "text-destructive",
  unknown: "text-muted-foreground",
};

const TONE_BAR_CLASS: Record<VerdictTone, string> = {
  trusted: "bg-emerald-500/70",
  questionable: "bg-amber-500/70",
  risky: "bg-destructive/70",
  unknown: "bg-muted-foreground/40",
};

export function toneTextClass(tone: VerdictTone): string {
  return TONE_CLASS[tone];
}

export function toneBarClass(tone: VerdictTone): string {
  return TONE_BAR_CLASS[tone];
}

function formatPercent(probability: number | null | undefined): string {
  if (probability == null) return "—";
  return `${(probability * 100).toFixed(1)}%`;
}

function VerdictTooltipBody({
  verdict,
  locale,
  thresholds,
}: {
  verdict?: LatestVerdict;
  locale: Locale;
  thresholds: Thresholds;
}) {
  const state = resolveMatch(verdict);
  const identified = verdict?.prediction_name || verdict?.prediction || "";
  const claimed = verdict?.model_name || verdict?.expected_model || "";
  // The winner's number describes a different model, so each one carries its
  // own so the two are never read as a single confidence.
  const identifiedProbability = verdict?.probability;
  const claimedProbability = verdict?.expected_probability;
  const row = (
    label: string,
    name: string,
    probability: number | null | undefined,
    tone: string,
  ) => (
    <span className="flex w-full items-baseline justify-between gap-3">
      <span className="shrink-0 text-background/60">{label}</span>
      <span className={cn("font-mono", tone)}>{name || "—"}</span>
      <span className="shrink-0 tabular-nums text-background/80">
        {formatPercent(probability)}
      </span>
    </span>
  );
  return (
    <div className="flex w-max min-w-64 max-w-md flex-col items-stretch gap-1.5 text-left">
      <span className={cn("font-medium", MATCH_CLASS[state])}>
        {state === "match"
          ? titleForLocale(locale, "识别模型与名称一致", "Name matches model")
          : state === "mismatch"
            ? titleForLocale(
                locale,
                "识别模型与名称不符",
                "Name does not match",
              )
            : titleForLocale(locale, "无法判定是否一致", "Match undetermined")}
      </span>
      {row(
        titleForLocale(locale, "识别", "Identified"),
        identified,
        identifiedProbability,
        state === "mismatch" ? "text-destructive" : "text-background",
      )}
      {row(
        titleForLocale(locale, "声明", "Claimed"),
        claimed,
        claimedProbability,
        state === "match" ? "text-emerald-400" : "text-background",
      )}
      <span className="mt-0.5 border-t border-background/15 pt-1.5 text-background/50">
        {titleForLocale(locale, "可信 ≥ ", "Trusted ≥ ")}
        {thresholds.trust}% · {titleForLocale(locale, "警告 < ", "Warn < ")}
        {thresholds.warn}%
      </span>
    </div>
  );
}

/**
 * The match between the claimed model name and the model the verifier
 * identified is the point of the feature, so the chip leads with it. The
 * probability is supporting evidence and only decides how loud the verdict is.
 */
export function ConfidenceBadge({
  verdict,
  probability,
  thresholds = DEFAULT_THRESHOLDS,
  locale,
  showUnverified = false,
}: {
  verdict?: LatestVerdict;
  probability?: number | null;
  thresholds?: Thresholds;
  locale: Locale;
  showUnverified?: boolean;
}) {
  // The chip speaks for the claimed model; falling back to the winner's score
  // would show a mismatch as confident.
  const value =
    probability !== undefined
      ? probability
      : (verdict?.expected_probability ?? null);
  const hasVerdict = verdict !== undefined || probability !== undefined;
  if (!hasVerdict && !showUnverified) {
    return null;
  }
  const state = resolveMatch(verdict);
  const tone = verdictTone(value, thresholds);
  // A high score on the wrong model is still the wrong model: the match wins.
  const Icon =
    state === "match"
      ? ShieldCheck
      : state === "mismatch"
        ? ShieldX
        : tone === "unknown"
          ? ShieldQuestion
          : AlertTriangle;
  const label =
    state === "match"
      ? titleForLocale(locale, "一致", "Match")
      : state === "mismatch"
        ? titleForLocale(locale, "不符", "Mismatch")
        : hasVerdict
          ? titleForLocale(locale, "未知", "Unknown")
          : titleForLocale(locale, "未验真", "Unverified");

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={cn(
            "inline-flex shrink-0 cursor-help items-center gap-1 text-[11px]",
            MATCH_CLASS[state],
          )}
        >
          <Icon className="size-3" />
          {label}
          {value != null ? (
            <span className="tabular-nums opacity-70">
              {formatPercent(value)}
            </span>
          ) : null}
        </span>
      </TooltipTrigger>
      <TooltipContent side="top" sideOffset={6} collisionPadding={12}>
        <VerdictTooltipBody
          verdict={verdict}
          locale={locale}
          thresholds={thresholds}
        />
      </TooltipContent>
    </Tooltip>
  );
}

const TRUST_KEY = "verification_trust_threshold";
const WARN_KEY = "verification_warn_threshold";

/**
 * Thresholds live in settings so the grading matches what the backend reports,
 * but the UI must still work before the first save, hence the defaults.
 */
export function useVerificationThresholds(): Thresholds {
  const query = useQuery({
    queryKey: ["settings"],
    queryFn: () => apiRequest<SettingItem[]>("/admin/settings"),
    staleTime: 5 * 60_000,
  });
  return useMemo(() => {
    const items = query.data;
    if (!items) return DEFAULT_THRESHOLDS;
    const read = (key: string, fallback: number) => {
      const raw = items.find((item) => item.key === key)?.value;
      const value = Number(raw);
      return Number.isFinite(value)
        ? Math.min(Math.max(value, 0), 100)
        : fallback;
    };
    return {
      trust: read(TRUST_KEY, DEFAULT_THRESHOLDS.trust),
      warn: read(WARN_KEY, DEFAULT_THRESHOLDS.warn),
    };
  }, [query.data]);
}

export type LatestVerdict = {
  probability: number | null;
  decision: string;
  prediction: string;
  prediction_name: string;
  /** What the name claims; falls back to `model_name` when absent. */
  expected_model?: string;
  model_name?: string;
  matches_expected: boolean | null;
  /** Probability of the claimed model, not of the winner. */
  expected_probability?: number | null;
};

/**
 * Verdicts are keyed by the same triple the backend verifies, so a channel row
 * can look its own verdict up without knowing about model groups.
 */
export function useLatestVerdicts(): Map<string, LatestVerdict> {
  const query = useQuery({
    queryKey: ["model-verifications", "latest"],
    queryFn: () => listLatestModelVerifications(),
    staleTime: 60_000,
  });
  return useMemo(() => {
    const map = new Map<string, LatestVerdict>();
    for (const item of query.data ?? []) {
      map.set(
        `${item.protocol_config_id}|${item.credential_id}|${item.model_name}`,
        {
          probability: item.probability ?? null,
          decision: item.decision,
          prediction: item.prediction,
          prediction_name: item.prediction_name,
          expected_model: item.expected_model,
          model_name: item.model_name,
          expected_probability: item.expected_probability ?? null,
          matches_expected: item.matches_expected ?? null,
        },
      );
    }
    return map;
  }, [query.data]);
}

export function verdictKey(
  protocolConfigId: string,
  credentialId: string,
  modelName: string,
): string {
  return `${protocolConfigId}|${credentialId}|${modelName}`;
}
