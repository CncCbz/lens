import { useState } from "react";
import { ChevronDown } from "lucide-react";

import type {
  OverviewDimensionUsageAnalytics,
  OverviewDimensionUsagePoint,
  OverviewModelChannelUsagePoint,
} from "@/lib/api";
import {
  Card,
  CardAction,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

import {
  CHART_COLORS,
  formatCompact,
  formatDuration,
  formatMetricValue,
  formatMoney,
  formatPercent,
  metricValue,
  type OverviewMetric,
} from "./overview-utils";
import { SectionMessage, SectionSkeleton, errorLabel } from "./section-state";

type UsageItem = OverviewDimensionUsagePoint | OverviewModelChannelUsagePoint;
type DimensionView = "rank" | "tokens";
type ModelBreakdownView = "cost" | "tokens";

function tokenTotal(item: UsageItem) {
  return (
    item.input_tokens +
    item.output_tokens +
    item.cache_read_input_tokens +
    item.cache_write_input_tokens
  );
}

function cacheTokens(item: UsageItem) {
  return item.cache_read_input_tokens + item.cache_write_input_tokens;
}

function itemCostMultiplier(item: UsageItem) {
  if (!("cost_multiplier" in item)) return null;
  if (Math.abs(item.cost_multiplier - 1) < 0.000001) return null;
  return item.cost_multiplier;
}

function formatMultiplier(value: number) {
  const text = value >= 100 ? value.toFixed(0) : value.toFixed(3);
  return text.replace(/\.?0+$/, "");
}

function ItemName({ item, zh }: { item: UsageItem; zh: boolean }) {
  const multiplier = itemCostMultiplier(item);
  return (
    <span className="flex min-w-0 items-center gap-2">
      <span className="min-w-0 truncate font-medium" title={item.name}>
        {item.name || item.id}
      </span>
      {multiplier === null ? null : (
        <span className="shrink-0 rounded-md border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 font-mono text-[11px] leading-none text-amber-700 dark:text-amber-300">
          {zh ? "倍率 " : ""}x{formatMultiplier(multiplier)}
        </span>
      )}
    </span>
  );
}

function UsageRankList({
  items,
  metric,
  zh,
}: {
  items: UsageItem[];
  metric: OverviewMetric;
  zh: boolean;
}) {
  const max = Math.max(...items.map((item) => metricValue(metric, item)), 1);
  return (
    <div className="space-y-3">
      {items.slice(0, 10).map((item, index) => {
        const value = metricValue(metric, item);
        return (
          <div key={item.id} className="grid gap-1.5">
            <div className="grid grid-cols-[auto_1fr_auto] items-center gap-3 text-sm">
              <span className="w-6 text-right font-mono text-xs text-muted-foreground tabular-nums">
                {String(index + 1).padStart(2, "0")}
              </span>
              <span className="min-w-0 truncate font-medium" title={item.name}>
                {item.name || item.id}
              </span>
              <span className="font-mono text-xs tabular-nums text-muted-foreground">
                {formatMetricValue(metric, value)}
              </span>
            </div>
            <div className="ml-9 h-2 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${Math.max((value / max) * 100, value > 0 ? 3 : 0)}%`,
                  backgroundColor: CHART_COLORS[index % CHART_COLORS.length],
                }}
              />
            </div>
            <div className="ml-9 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
              <span>
                {zh ? "请求" : "Req"} {formatCompact(item.request_count)}
              </span>
              <span>
                {zh ? "费用" : "Cost"} {formatMoney(item.total_cost_usd)}
              </span>
              <span>
                {zh ? "延迟" : "Latency"}{" "}
                {formatDuration(item.average_latency_ms)}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function TokenComposition({
  items,
  zh,
  panelRows = false,
}: {
  items: UsageItem[];
  zh: boolean;
  panelRows?: boolean;
}) {
  const ranked = [...items]
    .sort((a, b) => tokenTotal(b) - tokenTotal(a))
    .slice(0, 20);

  return (
    <div className="space-y-3">
      {ranked.map((item) => {
        const total = tokenTotal(item);
        const inputWidth = total ? (item.input_tokens / total) * 100 : 0;
        const outputWidth = total ? (item.output_tokens / total) * 100 : 0;
        const cacheWidth = total ? (cacheTokens(item) / total) * 100 : 0;
        return (
          <div
            key={item.id}
            className={cn(
              "grid gap-1.5",
              panelRows && "rounded-lg bg-background p-3 ring-1 ring-border/70",
            )}
          >
            <div className="flex min-w-0 items-center justify-between gap-3 text-sm">
              <ItemName item={item} zh={zh} />
              <span className="font-mono text-xs tabular-nums text-muted-foreground">
                {formatCompact(total)}
              </span>
            </div>
            <div className="flex h-2 overflow-hidden rounded-full bg-muted">
              <div
                className={cn(
                  "bg-[var(--chart-1)]",
                  inputWidth === 0 && "hidden",
                )}
                style={{ width: `${inputWidth}%` }}
              />
              <div
                className={cn(
                  "bg-[var(--chart-2)]",
                  outputWidth === 0 && "hidden",
                )}
                style={{ width: `${outputWidth}%` }}
              />
              <div
                className={cn(
                  "bg-[var(--chart-3)]",
                  cacheWidth === 0 && "hidden",
                )}
                style={{ width: `${cacheWidth}%` }}
              />
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
              <span>
                {zh ? "输入" : "Input"} {formatCompact(item.input_tokens)}
              </span>
              <span>
                {zh ? "输出" : "Output"} {formatCompact(item.output_tokens)}
              </span>
              <span>
                {zh ? "缓存" : "Cache"} {formatCompact(cacheTokens(item))}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function CostComposition({
  items,
  zh,
  panelRows = false,
}: {
  items: UsageItem[];
  zh: boolean;
  panelRows?: boolean;
}) {
  const ranked = [...items]
    .sort((a, b) => b.total_cost_usd - a.total_cost_usd)
    .slice(0, 20);
  const max = Math.max(...ranked.map((item) => item.total_cost_usd), 1);

  return (
    <div className="space-y-3">
      {ranked.map((item, index) => {
        const value = item.total_cost_usd;
        return (
          <div
            key={item.id}
            className={cn(
              "grid gap-1.5",
              panelRows && "rounded-lg bg-background p-3 ring-1 ring-border/70",
            )}
          >
            <div className="flex min-w-0 items-center justify-between gap-3 text-sm">
              <ItemName item={item} zh={zh} />
              <span className="font-mono text-xs tabular-nums text-muted-foreground">
                {formatMoney(value)}
              </span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${Math.max((value / max) * 100, value > 0 ? 3 : 0)}%`,
                  backgroundColor: CHART_COLORS[index % CHART_COLORS.length],
                }}
              />
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
              <span>
                {zh ? "输入" : "Input"} {formatMoney(item.input_cost_usd)}
              </span>
              <span>
                {zh ? "输出" : "Output"} {formatMoney(item.output_cost_usd)}
              </span>
              <span>
                {zh ? "请求" : "Req"} {formatCompact(item.request_count)}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function MetricCell({ label, value }: { label: string; value: string }) {
  return (
    <span className="grid gap-0.5 text-right">
      <span className="text-[11px] text-muted-foreground">{label}</span>
      <span className="font-mono text-xs tabular-nums">{value}</span>
    </span>
  );
}

export function DimensionUsageSection({
  title,
  analytics,
  isLoading,
  error,
  metric,
  zh,
}: {
  title: string;
  analytics?: OverviewDimensionUsageAnalytics;
  isLoading: boolean;
  error: unknown;
  metric: OverviewMetric;
  zh: boolean;
}) {
  const [view, setView] = useState<DimensionView>("rank");
  const items = analytics?.items ?? [];

  return (
    <Card size="sm" className="min-w-0 py-0">
      <CardHeader className="border-b px-4 py-4">
        <CardTitle className="text-base">{title}</CardTitle>
        <CardAction>
          <Select
            value={view}
            onValueChange={(value) => setView(value as DimensionView)}
          >
            <SelectTrigger
              className="w-28"
              aria-label={zh ? "选择用量视图" : "Select usage view"}
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent align="end" className="rounded-xl">
              <SelectItem value="rank" className="rounded-lg">
                {zh ? "排行" : "Rank"}
              </SelectItem>
              <SelectItem value="tokens" className="rounded-lg">
                {zh ? "Token 结构" : "Token mix"}
              </SelectItem>
            </SelectContent>
          </Select>
        </CardAction>
      </CardHeader>
      <CardContent className="p-4">
        {isLoading ? (
          <SectionSkeleton />
        ) : error ? (
          <SectionMessage
            tone="error"
            label={errorLabel(
              error,
              zh ? "用量数据加载失败" : "Failed to load usage data",
            )}
          />
        ) : items.length === 0 ? (
          <SectionMessage label={zh ? "暂无用量数据" : "No usage data"} />
        ) : view === "tokens" ? (
          <TokenComposition items={items} zh={zh} />
        ) : (
          <UsageRankList items={items} metric={metric} zh={zh} />
        )}
      </CardContent>
    </Card>
  );
}

export function ModelUsageSection({
  title,
  analytics,
  isLoading,
  error,
  zh,
}: {
  title: string;
  analytics?: OverviewDimensionUsageAnalytics;
  isLoading: boolean;
  error: unknown;
  zh: boolean;
}) {
  const [openModelId, setOpenModelId] = useState<string | null>(null);
  const [breakdownView, setBreakdownView] =
    useState<ModelBreakdownView>("cost");
  const items = analytics?.items ?? [];

  return (
    <Card size="sm" className="min-w-0 py-0">
      <CardHeader className="border-b px-4 py-4">
        <CardTitle className="text-base">{title}</CardTitle>
        <CardAction>
          <Select
            value={breakdownView}
            onValueChange={(value) =>
              setBreakdownView(value as ModelBreakdownView)
            }
          >
            <SelectTrigger
              className="w-32"
              aria-label={zh ? "选择模型明细视图" : "Select model detail view"}
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent align="end" className="rounded-xl">
              <SelectItem value="cost" className="rounded-lg">
                {zh ? "费用" : "Cost"}
              </SelectItem>
              <SelectItem value="tokens" className="rounded-lg">
                {zh ? "Token 结构" : "Token mix"}
              </SelectItem>
            </SelectContent>
          </Select>
        </CardAction>
      </CardHeader>
      <CardContent className="p-0">
        {isLoading ? (
          <div className="p-4">
            <SectionSkeleton />
          </div>
        ) : error ? (
          <div className="p-4">
            <SectionMessage
              tone="error"
              label={errorLabel(
                error,
                zh ? "模型数据加载失败" : "Failed to load model data",
              )}
            />
          </div>
        ) : items.length === 0 ? (
          <div className="p-4">
            <SectionMessage label={zh ? "暂无模型数据" : "No model data"} />
          </div>
        ) : (
          <div className="divide-y">
            {items.map((item) => {
              const open = openModelId === item.id;
              const channelItems = item.channel_items ?? [];
              return (
                <section key={item.id} className="min-w-0">
                  <button
                    type="button"
                    className={cn(
                      "grid w-full min-w-0 gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/50 md:grid-cols-[minmax(0,1fr)_auto_auto_auto_20px] md:items-center",
                      open && "bg-muted/35",
                    )}
                    aria-expanded={open}
                    onClick={() =>
                      setOpenModelId((current) =>
                        current === item.id ? null : item.id,
                      )
                    }
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium">
                        {item.name || item.id}
                      </span>
                      <span className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                        <span>
                          {zh ? "成功率" : "Success"}{" "}
                          {formatPercent(
                            item.request_count
                              ? (item.successful_requests /
                                  item.request_count) *
                                  100
                              : 0,
                          )}
                        </span>
                        <span>
                          {zh ? "延迟" : "Latency"}{" "}
                          {formatDuration(item.average_latency_ms)}
                        </span>
                        <span>
                          {zh ? "渠道" : "Channels"} {channelItems.length}
                        </span>
                      </span>
                    </span>
                    <MetricCell
                      label={zh ? "请求" : "Req"}
                      value={formatCompact(item.request_count)}
                    />
                    <MetricCell
                      label="Token"
                      value={formatCompact(item.total_tokens)}
                    />
                    <MetricCell
                      label={zh ? "费用" : "Cost"}
                      value={formatMoney(item.total_cost_usd)}
                    />
                    <ChevronDown
                      className={cn(
                        "size-4 justify-self-end text-muted-foreground transition-transform",
                        open && "rotate-180",
                      )}
                    />
                  </button>
                  {open ? (
                    <div className="border-t bg-muted/55 px-4 py-4">
                      <div className="rounded-lg border border-border/80 border-l-4 border-l-primary/60 bg-muted/20 p-3 shadow-sm">
                        {channelItems.length === 0 ? (
                          <SectionMessage
                            label={zh ? "暂无渠道明细" : "No channel details"}
                          />
                        ) : breakdownView === "tokens" ? (
                          <TokenComposition
                            items={channelItems}
                            zh={zh}
                            panelRows
                          />
                        ) : (
                          <CostComposition
                            items={channelItems}
                            zh={zh}
                            panelRows
                          />
                        )}
                      </div>
                    </div>
                  ) : null}
                </section>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
