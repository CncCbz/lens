import { useMemo } from "react";
import { Cell, Pie, PieChart } from "recharts";

import type { OverviewSummary } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";

import {
  formatCompact,
  formatMoney,
  type OverviewMetric,
} from "./overview-utils";
import { SectionMessage, SectionSkeleton, errorLabel } from "./section-state";

const COST_COLORS = {
  input: "var(--chart-1)",
  output: "var(--chart-3)",
} as const;

const TOKEN_PARTS = [
  { key: "input", color: "var(--chart-1)" },
  { key: "output", color: "var(--chart-3)" },
  { key: "cache", color: "var(--chart-2)" },
] as const;

export function OverviewCostSection({
  summary,
  metric,
  isLoading,
  error,
  zh,
}: {
  summary?: OverviewSummary;
  metric: OverviewMetric;
  isLoading: boolean;
  error: unknown;
  zh: boolean;
}) {
  const costConfig = useMemo(
    () =>
      ({
        input: { label: zh ? "输入" : "Input", color: COST_COLORS.input },
        output: { label: zh ? "输出" : "Output", color: COST_COLORS.output },
      }) satisfies ChartConfig,
    [zh],
  );

  if (isLoading) {
    return (
      <Card size="sm" className="min-w-0 py-0">
        <CardHeader className="border-b px-4 py-4">
          <CardTitle className="text-base">
            {zh ? "成本构成" : "Cost breakdown"}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-4">
          <SectionSkeleton />
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Card size="sm" className="min-w-0 py-0">
        <CardHeader className="border-b px-4 py-4">
          <CardTitle className="text-base">
            {zh ? "成本构成" : "Cost breakdown"}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-4">
          <SectionMessage
            tone="error"
            label={errorLabel(
              error,
              zh ? "成本数据加载失败" : "Failed to load cost data",
            )}
          />
        </CardContent>
      </Card>
    );
  }

  const totalCost = summary?.total_cost_usd.value ?? 0;
  const inputCost = summary?.input_cost_usd.value ?? 0;
  const outputCost = summary?.output_cost_usd.value ?? 0;
  const requestCount = summary?.request_count.value ?? 0;
  const inputTokens = summary?.input_tokens.value ?? 0;
  const outputTokens = summary?.output_tokens.value ?? 0;
  const cacheTokens =
    (summary?.cache_read_input_tokens.value ?? 0) +
    (summary?.cache_write_input_tokens.value ?? 0);
  const totalTokens = inputTokens + outputTokens + cacheTokens;

  const donutData = [
    { key: "output", value: outputCost },
    { key: "input", value: inputCost },
  ];

  const costRows = [
    {
      label: zh ? "输出费用" : "Output cost",
      value: outputCost,
      color: "var(--chart-3)",
    },
    {
      label: zh ? "输入费用" : "Input cost",
      value: inputCost,
      color: "var(--chart-1)",
    },
  ];

  const tokenParts = [
    { key: "input", label: zh ? "输入" : "Input", value: inputTokens },
    { key: "output", label: zh ? "输出" : "Output", value: outputTokens },
    { key: "cache", label: zh ? "缓存" : "Cache", value: cacheTokens },
  ];

  const showCost = metric === "cost";

  return (
    <Card size="sm" className="min-w-0 py-0">
      <CardHeader className="border-b px-4 py-4">
        <CardTitle className="text-base">
          {zh ? "成本构成" : "Cost breakdown"}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 p-4">
        <div className="flex items-center gap-4">
          <div className="relative size-[124px] shrink-0">
            <ChartContainer config={costConfig} className="size-full">
              <PieChart>
                <ChartTooltip
                  content={
                    <ChartTooltipContent
                      nameKey="key"
                      className="min-w-[8rem]"
                    />
                  }
                />
                <Pie
                  data={donutData}
                  dataKey="value"
                  nameKey="key"
                  innerRadius={44}
                  outerRadius={60}
                  paddingAngle={2}
                  strokeWidth={0}
                  isAnimationActive={false}
                >
                  {donutData.map((entry) => (
                    <Cell
                      key={entry.key}
                      fill={COST_COLORS[entry.key as keyof typeof COST_COLORS]}
                    />
                  ))}
                </Pie>
              </PieChart>
            </ChartContainer>
            <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-0.5">
              <span className="font-mono text-sm font-semibold tabular-nums">
                {formatMoney(totalCost)}
              </span>
              <span className="text-[10px] text-muted-foreground">
                {zh ? "总费用" : "Total"}
              </span>
            </div>
          </div>
          <div className="flex min-w-0 flex-1 flex-col gap-2.5">
            {costRows.map((row) => (
              <div
                key={row.label}
                className="flex items-center justify-between gap-3 text-sm"
              >
                <span className="flex min-w-0 items-center gap-2 text-muted-foreground">
                  <span
                    className="size-2 shrink-0 rounded-[3px]"
                    style={{ backgroundColor: row.color }}
                  />
                  <span className="truncate">{row.label}</span>
                </span>
                <span className="shrink-0 font-mono tabular-nums">
                  {formatMoney(row.value)}
                  <span className="ml-1.5 text-xs text-muted-foreground">
                    {totalCost
                      ? `${((row.value / totalCost) * 100).toFixed(1)}%`
                      : "0%"}
                  </span>
                </span>
              </div>
            ))}
            <div className="flex items-center justify-between gap-3 border-t pt-2.5 text-sm">
              <span className="text-muted-foreground">
                {zh ? "单请求均价" : "Per request"}
              </span>
              <span className="font-mono tabular-nums">
                ${requestCount ? (totalCost / requestCount).toFixed(4) : "0.00"}
              </span>
            </div>
          </div>
        </div>

        <div className="space-y-2 border-t pt-3">
          <div className="text-xs text-muted-foreground">
            {zh ? "Token 构成" : "Token mix"}
          </div>
          <div className="flex h-2 overflow-hidden rounded-full bg-muted">
            {tokenParts.map((part) => (
              <div
                key={part.key}
                className="h-full first:rounded-l-full last:rounded-r-full"
                style={{
                  width: `${totalTokens ? (part.value / totalTokens) * 100 : 0}%`,
                  backgroundColor: TOKEN_PARTS.find((p) => p.key === part.key)
                    ?.color,
                }}
              />
            ))}
          </div>
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            {tokenParts.map((part) => (
              <span
                key={part.key}
                className="flex items-center gap-1.5 text-xs text-muted-foreground"
              >
                <span
                  className="size-2 shrink-0 rounded-[3px]"
                  style={{
                    backgroundColor: TOKEN_PARTS.find((p) => p.key === part.key)
                      ?.color,
                  }}
                />
                {part.label}
                <span className="font-mono tabular-nums text-foreground">
                  {formatCompact(part.value)}
                </span>
              </span>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-3 gap-2 border-t pt-3">
          <div className="rounded-lg border px-2.5 py-2">
            <div className="text-[11px] text-muted-foreground">
              {showCost ? (zh ? "费用" : "Spend") : zh ? "请求" : "Requests"}
            </div>
            <div className="font-mono text-sm font-semibold tabular-nums">
              {showCost ? formatMoney(totalCost) : formatCompact(requestCount)}
            </div>
          </div>
          <div className="rounded-lg border px-2.5 py-2">
            <div className="text-[11px] text-muted-foreground">
              {zh ? "缓存读" : "Cache read"}
            </div>
            <div className="font-mono text-sm font-semibold tabular-nums">
              {formatCompact(summary?.cache_read_input_tokens.value ?? 0)}
            </div>
          </div>
          <div className="rounded-lg border px-2.5 py-2">
            <div className="text-[11px] text-muted-foreground">
              {zh ? "缓存写" : "Cache write"}
            </div>
            <div className="font-mono text-sm font-semibold tabular-nums">
              {formatCompact(summary?.cache_write_input_tokens.value ?? 0)}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
