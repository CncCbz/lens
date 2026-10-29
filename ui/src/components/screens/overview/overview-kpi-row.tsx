import type { ComponentType } from "react";
import {
  Activity,
  CircleCheck,
  Clock3,
  Coins,
  Database,
  Gauge,
} from "lucide-react";

import type { OverviewDailyPoint, OverviewSummary } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import { Sparkline } from "./overview-sparkline";
import {
  formatCompact,
  formatDuration,
  formatMoney,
  formatPercent,
} from "./overview-utils";
import { SectionMessage, errorLabel } from "./section-state";

type KpiItem = {
  title: string;
  value: string;
  detail: string;
  delta?: number;
  icon: ComponentType<{ className?: string }>;
  series: number[];
  color: string;
};

function KpiCard({ item }: { item: KpiItem }) {
  const Icon = item.icon;
  const delta = item.delta ?? 0;
  const deltaLabel =
    delta === 0 ? "0%" : `${delta > 0 ? "+" : ""}${delta.toFixed(1)}%`;

  return (
    <Card size="sm" className="min-w-0 gap-0 overflow-hidden py-0">
      <CardHeader className="flex flex-row items-center justify-between gap-3 px-4 pt-4 pb-2">
        <CardTitle className="truncate text-sm font-medium text-muted-foreground">
          {item.title}
        </CardTitle>
        <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
          <Icon className="size-4" />
        </span>
      </CardHeader>
      <CardContent className="px-4 pb-0">
        <div className="flex items-end justify-between gap-3">
          <div className="min-w-0">
            <div className="truncate font-mono text-2xl font-semibold tabular-nums">
              {item.value}
            </div>
            <div className="mt-1 truncate text-xs text-muted-foreground">
              {item.detail}
            </div>
          </div>
          <div className="shrink-0 rounded-md bg-muted px-2 py-1 font-mono text-xs tabular-nums text-muted-foreground">
            {deltaLabel}
          </div>
        </div>
      </CardContent>
      <div className="-mx-px -mb-px mt-3">
        <Sparkline values={item.series} color={item.color} />
      </div>
    </Card>
  );
}

function KpiSkeleton() {
  return (
    <Card size="sm" className="py-0">
      <CardHeader className="px-4 pt-4 pb-2">
        <Skeleton className="h-4 w-24" />
      </CardHeader>
      <CardContent className="space-y-2 px-4 pb-4">
        <Skeleton className="h-8 w-28" />
        <Skeleton className="h-3 w-36" />
      </CardContent>
    </Card>
  );
}

export function OverviewKpiRow({
  summary,
  points,
  isLoading,
  error,
  zh,
}: {
  summary?: OverviewSummary;
  points?: OverviewDailyPoint[];
  isLoading: boolean;
  error: unknown;
  zh: boolean;
}) {
  if (isLoading) {
    return (
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
        {Array.from({ length: 6 }).map((_, index) => (
          <KpiSkeleton key={index} />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <SectionMessage
        tone="error"
        label={errorLabel(
          error,
          zh ? "核心指标加载失败" : "Failed to load KPIs",
        )}
      />
    );
  }

  const daily = points ?? [];
  const series = (pick: (point: OverviewDailyPoint) => number) =>
    daily.map(pick);

  const requestCount = summary?.request_count.value ?? 0;
  const tokenTotal = summary?.total_tokens.value ?? 0;
  const inputTokens = summary?.input_tokens.value ?? 0;
  const outputTokens = summary?.output_tokens.value ?? 0;
  const cacheRead = summary?.cache_read_input_tokens.value ?? 0;
  const cacheWrite = summary?.cache_write_input_tokens.value ?? 0;

  const cacheDelta = (() => {
    const readDelta = summary?.cache_read_input_tokens.delta ?? 0;
    const writeDelta = summary?.cache_write_input_tokens.delta ?? 0;
    const prevRead = readDelta === -100 ? 0 : cacheRead / (1 + readDelta / 100);
    const prevWrite =
      writeDelta === -100 ? 0 : cacheWrite / (1 + writeDelta / 100);
    const prev = prevRead + prevWrite;
    return prev > 0 ? ((cacheRead + cacheWrite - prev) / prev) * 100 : 0;
  })();

  const items: KpiItem[] = [
    {
      title: zh ? "请求总量" : "Requests",
      value: formatCompact(requestCount),
      detail: zh
        ? `失败 ${formatCompact(summary?.failed_requests.value ?? 0)}`
        : `Failed ${formatCompact(summary?.failed_requests.value ?? 0)}`,
      delta: summary?.request_count.delta,
      icon: Activity,
      series: series((point) => point.request_count),
      color: "var(--chart-1)",
    },
    {
      title: zh ? "成功率" : "Success rate",
      value: formatPercent(summary?.success_rate.value ?? 0),
      detail: zh
        ? `成功 ${formatCompact(summary?.successful_requests.value ?? 0)}`
        : `Succeeded ${formatCompact(summary?.successful_requests.value ?? 0)}`,
      delta: summary?.success_rate.delta,
      icon: CircleCheck,
      series: series((point) =>
        point.request_count
          ? (point.successful_requests / point.request_count) * 100
          : 0,
      ),
      color: "var(--chart-2)",
    },
    {
      title: zh ? "总费用" : "Spend",
      value: formatMoney(summary?.total_cost_usd.value ?? 0),
      detail: zh
        ? `输入 ${formatMoney(summary?.input_cost_usd.value ?? 0)} / 输出 ${formatMoney(summary?.output_cost_usd.value ?? 0)}`
        : `Input ${formatMoney(summary?.input_cost_usd.value ?? 0)} / Output ${formatMoney(summary?.output_cost_usd.value ?? 0)}`,
      delta: summary?.total_cost_usd.delta,
      icon: Coins,
      series: series((point) => point.total_cost_usd),
      color: "var(--chart-3)",
    },
    {
      title: zh ? "Token 使用" : "Tokens",
      value: formatCompact(tokenTotal),
      detail: zh
        ? `入 ${formatCompact(inputTokens)} / 出 ${formatCompact(outputTokens)}`
        : `In ${formatCompact(inputTokens)} / Out ${formatCompact(outputTokens)}`,
      delta: summary?.total_tokens.delta,
      icon: Gauge,
      series: series((point) => point.total_tokens),
      color: "var(--chart-4)",
    },
    {
      title: zh ? "平均延迟" : "Avg latency",
      value: formatDuration(summary?.average_latency_ms.value ?? 0),
      detail: zh
        ? `累计 ${formatDuration(summary?.wait_time_ms.value ?? 0)}`
        : `Total ${formatDuration(summary?.wait_time_ms.value ?? 0)}`,
      delta: summary?.average_latency_ms.delta,
      icon: Clock3,
      series: series((point) =>
        point.request_count ? point.wait_time_ms / point.request_count : 0,
      ),
      color: "var(--chart-5)",
    },
    {
      title: zh ? "缓存 Token" : "Cached tokens",
      value: formatCompact(cacheRead + cacheWrite),
      detail: zh
        ? `读 ${formatCompact(cacheRead)} / 写 ${formatCompact(cacheWrite)}`
        : `Read ${formatCompact(cacheRead)} / Write ${formatCompact(cacheWrite)}`,
      delta: cacheDelta,
      icon: Database,
      series: series(
        (point) =>
          point.cache_read_input_tokens + point.cache_write_input_tokens,
      ),
      color: "var(--chart-2)",
    },
  ];

  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
      {items.map((item) => (
        <KpiCard key={item.title} item={item} />
      ))}
    </div>
  );
}
