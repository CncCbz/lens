import { useMemo, useState } from "react";
import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts";

import type {
  OverviewDimensionTrendPoint,
  OverviewDimensionUsageAnalytics,
} from "@/lib/api";
import {
  Card,
  CardAction,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import { SegmentedControl } from "@/components/ui/segmented-control";

import {
  CHART_COLORS,
  formatCompact,
  formatDateLabel,
  formatMoney,
  type OverviewMetric,
} from "./overview-utils";
import { SectionMessage, SectionSkeleton, errorLabel } from "./section-state";

const MAX_SERIES = 5;
const OTHER_KEY = "other";

type TrendGroup = "model" | "channel";

type Series = {
  key: string;
  name: string;
  color: string;
  total: number;
};

function valueOf(metric: OverviewMetric, point: OverviewDimensionTrendPoint) {
  if (metric === "requests") return point.request_count;
  if (metric === "tokens") return point.total_tokens;
  return point.total_cost_usd;
}

function itemValue(
  metric: OverviewMetric,
  item: { request_count: number; total_tokens: number; total_cost_usd: number },
) {
  if (metric === "requests") return item.request_count;
  if (metric === "tokens") return item.total_tokens;
  return item.total_cost_usd;
}

function formatValue(metric: OverviewMetric, value: number) {
  return metric === "cost" ? formatMoney(value) : formatCompact(value);
}

export function OverviewTrendSection({
  modelAnalytics,
  channelAnalytics,
  metric,
  isLoading,
  error,
  zh,
}: {
  modelAnalytics?: OverviewDimensionUsageAnalytics;
  channelAnalytics?: OverviewDimensionUsageAnalytics;
  metric: OverviewMetric;
  isLoading: boolean;
  error: unknown;
  zh: boolean;
}) {
  const [group, setGroup] = useState<TrendGroup>("model");
  const analytics = group === "model" ? modelAnalytics : channelAnalytics;

  const { rows, series } = useMemo(() => {
    const items = analytics?.items ?? [];
    const trend = analytics?.trend ?? [];
    if (items.length === 0 || trend.length === 0) {
      return {
        rows: [] as Array<Record<string, number | string>>,
        series: [] as Series[],
      };
    }

    const ranked = [...items].sort(
      (a, b) => itemValue(metric, b) - itemValue(metric, a),
    );
    const top = ranked.slice(0, MAX_SERIES);
    const rest = ranked.slice(MAX_SERIES);

    const keyById = new Map<string, string>();
    const nextSeries: Series[] = top.map((item, index) => {
      const key = `s${index}`;
      keyById.set(item.id, key);
      return {
        key,
        name: item.name || item.id,
        color: CHART_COLORS[index % CHART_COLORS.length],
        total: itemValue(metric, item),
      };
    });

    if (rest.length > 0) {
      nextSeries.push({
        key: OTHER_KEY,
        name: zh ? "其他" : "Other",
        color: "var(--muted-foreground)",
        total: rest.reduce((sum, item) => sum + itemValue(metric, item), 0),
      });
    }

    const restIds = new Set(rest.map((item) => item.id));
    const byDate = new Map<string, Record<string, number | string>>();

    for (const point of trend) {
      const bucket = byDate.get(point.date) ?? {
        date: point.date,
        label: formatDateLabel(point.date),
      };
      const key =
        keyById.get(point.id) ?? (restIds.has(point.id) ? OTHER_KEY : null);
      if (key) {
        const current =
          typeof bucket[key] === "number" ? (bucket[key] as number) : 0;
        bucket[key] = current + valueOf(metric, point);
      }
      byDate.set(point.date, bucket);
    }

    const sorted = [...byDate.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([, row]) => row);

    return { rows: sorted, series: nextSeries };
  }, [analytics, metric, zh]);

  const chartConfig = useMemo(() => {
    const config: ChartConfig = {};
    for (const item of series) {
      config[item.key] = { label: item.name, color: item.color };
    }
    return config;
  }, [series]);

  return (
    <Card size="sm" className="min-w-0 py-0">
      <CardHeader className="border-b px-4 py-4">
        <CardTitle className="text-base">
          {zh ? "调用趋势" : "Usage trend"}
        </CardTitle>
        <CardAction>
          <SegmentedControl
            value={group}
            onValueChange={setGroup}
            options={[
              { value: "model", label: zh ? "按模型" : "By model" },
              { value: "channel", label: zh ? "按渠道" : "By channel" },
            ]}
          />
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
              zh ? "趋势数据加载失败" : "Failed to load trend data",
            )}
          />
        ) : rows.length === 0 ? (
          <SectionMessage label={zh ? "暂无趋势数据" : "No trend data"} />
        ) : (
          <>
            <ChartContainer config={chartConfig} className="h-[300px] w-full">
              <AreaChart
                data={rows}
                margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
              >
                <CartesianGrid vertical={false} strokeDasharray="3 3" />
                <XAxis
                  dataKey="label"
                  tickLine={false}
                  axisLine={false}
                  fontSize={11}
                  minTickGap={16}
                />
                <YAxis
                  tickLine={false}
                  axisLine={false}
                  fontSize={11}
                  width={52}
                  tickFormatter={(value: number) => formatValue(metric, value)}
                />
                <ChartTooltip
                  content={
                    <ChartTooltipContent
                      indicator="dot"
                      className="min-w-[10rem]"
                    />
                  }
                />
                {series.map((item) => (
                  <Area
                    key={item.key}
                    dataKey={item.key}
                    type="monotone"
                    stackId="stack"
                    fill={`var(--color-${item.key})`}
                    stroke={`var(--color-${item.key})`}
                    strokeWidth={1}
                    fillOpacity={0.85}
                    isAnimationActive={false}
                  />
                ))}
              </AreaChart>
            </ChartContainer>
            <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5">
              {series.map((item) => (
                <span
                  key={item.key}
                  className="flex items-center gap-1.5 text-xs text-muted-foreground"
                >
                  <span
                    className="size-2 shrink-0 rounded-[3px]"
                    style={{ backgroundColor: item.color }}
                  />
                  <span className="max-w-[12rem] truncate">{item.name}</span>
                  <span className="font-mono tabular-nums">
                    {formatValue(metric, item.total)}
                  </span>
                </span>
              ))}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
