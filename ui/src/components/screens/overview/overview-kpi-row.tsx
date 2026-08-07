import type { ComponentType } from "react";
import { Activity, CircleCheck, Clock3, Coins, Gauge } from "lucide-react";

import type { OverviewSummary } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

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
};

function KpiCard({ item }: { item: KpiItem }) {
  const Icon = item.icon;
  const delta = item.delta ?? 0;
  const deltaLabel =
    delta === 0 ? "0%" : `${delta > 0 ? "+" : ""}${delta.toFixed(1)}%`;

  return (
    <Card size="sm" className="min-w-0 py-0">
      <CardHeader className="flex flex-row items-center justify-between gap-3 px-4 pt-4 pb-2">
        <CardTitle className="truncate text-sm font-medium text-muted-foreground">
          {item.title}
        </CardTitle>
        <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
          <Icon className="size-4" />
        </span>
      </CardHeader>
      <CardContent className="px-4 pb-4">
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
  isLoading,
  error,
  zh,
}: {
  summary?: OverviewSummary;
  isLoading: boolean;
  error: unknown;
  zh: boolean;
}) {
  if (isLoading) {
    return (
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        {Array.from({ length: 5 }).map((_, index) => (
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

  const requestCount = summary?.request_count.value ?? 0;
  const tokenTotal = summary?.total_tokens.value ?? 0;
  const inputTokens = summary?.input_tokens.value ?? 0;
  const outputTokens = summary?.output_tokens.value ?? 0;
  const cacheTokens =
    (summary?.cache_read_input_tokens.value ?? 0) +
    (summary?.cache_write_input_tokens.value ?? 0);

  const items: KpiItem[] = [
    {
      title: zh ? "请求总量" : "Requests",
      value: formatCompact(requestCount),
      detail: zh
        ? `失败 ${formatCompact(summary?.failed_requests.value ?? 0)}`
        : `Failed ${formatCompact(summary?.failed_requests.value ?? 0)}`,
      delta: summary?.request_count.delta,
      icon: Activity,
    },
    {
      title: zh ? "成功率" : "Success rate",
      value: formatPercent(summary?.success_rate.value ?? 0),
      detail: zh
        ? `成功 ${formatCompact(summary?.successful_requests.value ?? 0)}`
        : `Succeeded ${formatCompact(summary?.successful_requests.value ?? 0)}`,
      delta: summary?.success_rate.delta,
      icon: CircleCheck,
    },
    {
      title: zh ? "总费用" : "Spend",
      value: formatMoney(summary?.total_cost_usd.value ?? 0),
      detail: zh
        ? `输入 ${formatMoney(summary?.input_cost_usd.value ?? 0)} / 输出 ${formatMoney(summary?.output_cost_usd.value ?? 0)}`
        : `Input ${formatMoney(summary?.input_cost_usd.value ?? 0)} / Output ${formatMoney(summary?.output_cost_usd.value ?? 0)}`,
      delta: summary?.total_cost_usd.delta,
      icon: Coins,
    },
    {
      title: zh ? "Token 使用" : "Tokens",
      value: formatCompact(tokenTotal),
      detail: zh
        ? `入 ${formatCompact(inputTokens)} / 出 ${formatCompact(outputTokens)} / 缓 ${formatCompact(cacheTokens)}`
        : `In ${formatCompact(inputTokens)} / Out ${formatCompact(outputTokens)} / Cache ${formatCompact(cacheTokens)}`,
      delta: summary?.total_tokens.delta,
      icon: Gauge,
    },
    {
      title: zh ? "平均延迟" : "Avg latency",
      value: formatDuration(summary?.average_latency_ms.value ?? 0),
      detail: zh
        ? `累计 ${formatDuration(summary?.wait_time_ms.value ?? 0)}`
        : `Total ${formatDuration(summary?.wait_time_ms.value ?? 0)}`,
      delta: summary?.average_latency_ms.delta,
      icon: Clock3,
    },
  ];

  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
      {items.map((item) => (
        <KpiCard key={item.title} item={item} />
      ))}
    </div>
  );
}
