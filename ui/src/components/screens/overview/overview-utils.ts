import type { Locale } from "@/lib/i18n";

export type OverviewTimeRange = "-1" | "7" | "30" | "0";
export type OverviewMetric = "cost" | "requests" | "tokens";

export const TIME_RANGE_OPTIONS: Array<{
  value: OverviewTimeRange;
  zhLabel: string;
  enLabel: string;
}> = [
  { value: "-1", zhLabel: "今天", enLabel: "Today" },
  { value: "7", zhLabel: "近 7 天", enLabel: "Last 7 days" },
  { value: "30", zhLabel: "近 30 天", enLabel: "Last 30 days" },
  { value: "0", zhLabel: "全部", enLabel: "All time" },
];

export const METRIC_OPTIONS: Array<{
  value: OverviewMetric;
  zhLabel: string;
  enLabel: string;
}> = [
  { value: "cost", zhLabel: "费用", enLabel: "Cost" },
  { value: "requests", zhLabel: "请求", enLabel: "Requests" },
  { value: "tokens", zhLabel: "Token", enLabel: "Tokens" },
];

export const CHART_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--primary)",
  "var(--muted-foreground)",
];

export function zh(locale: Locale) {
  return locale === "zh-CN";
}

export function formatCompact(value: number, digits = 1) {
  const abs = Math.abs(value);
  if (abs >= 1_000_000_000)
    return `${(value / 1_000_000_000).toFixed(digits)}B`;
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(digits)}M`;
  if (abs >= 1_000) return `${(value / 1_000).toFixed(digits)}K`;
  return String(Math.round(value));
}

export function formatMoney(value: number) {
  if (Math.abs(value) >= 1000) return `$${formatCompact(value, 2)}`;
  return `$${value.toFixed(value >= 100 ? 0 : 2)}`;
}

export function formatDuration(ms: number) {
  if (ms >= 3_600_000) return `${(ms / 3_600_000).toFixed(1)}h`;
  if (ms >= 60_000) return `${(ms / 60_000).toFixed(1)}m`;
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms)}ms`;
}

export function formatPercent(value: number) {
  return `${value.toFixed(value >= 99.95 || value <= 0 ? 0 : 1)}%`;
}

export function parseDateKey(value: string) {
  if (value.includes("-")) return value;
  if (value.length >= 8)
    return `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`;
  return value;
}

export function formatDateLabel(value: string) {
  if (value.length >= 10 && !value.includes("-")) {
    return `${value.slice(4, 6)}/${value.slice(6, 8)} ${value.slice(8, 10)}:00`;
  }
  const date = parseDateKey(value);
  if (date.length >= 10) return `${date.slice(5, 7)}/${date.slice(8, 10)}`;
  return value;
}

export function metricLabel(metric: OverviewMetric, locale: Locale) {
  const option = METRIC_OPTIONS.find((item) => item.value === metric);
  if (!option) return metric;
  return zh(locale) ? option.zhLabel : option.enLabel;
}

export function metricValue(
  metric: OverviewMetric,
  item: { request_count: number; total_tokens: number; total_cost_usd: number },
) {
  if (metric === "requests") return item.request_count;
  if (metric === "tokens") return item.total_tokens;
  return item.total_cost_usd;
}

export function formatMetricValue(metric: OverviewMetric, value: number) {
  return metric === "cost" ? formatMoney(value) : formatCompact(value);
}

export function toLocalDateKey(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function addDays(date: Date, days: number) {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}
