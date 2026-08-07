import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts";

import type { OverviewDailyPoint } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";

import { formatCompact, formatDateLabel, formatMoney } from "./overview-utils";
import { SectionMessage, SectionSkeleton, errorLabel } from "./section-state";

const chartConfig = {
  requests: { label: "Requests", color: "var(--chart-1)" },
  tokens: { label: "Tokens", color: "var(--chart-2)" },
  cost: { label: "Cost", color: "var(--chart-3)" },
} satisfies ChartConfig;

export function DailyOverviewChart({
  points,
  isLoading,
  error,
  zh,
}: {
  points?: OverviewDailyPoint[];
  isLoading: boolean;
  error: unknown;
  zh: boolean;
}) {
  const data = (points ?? []).map((point) => ({
    date: formatDateLabel(point.date),
    rawDate: point.date,
    requests: point.request_count,
    tokens: point.total_tokens,
    cost: point.total_cost_usd,
  }));

  return (
    <Card size="sm" className="min-w-0 py-0">
      <CardHeader className="border-b px-4 py-4">
        <CardTitle className="text-base">
          {zh ? "每日概览" : "Daily overview"}
        </CardTitle>
      </CardHeader>
      <CardContent className="p-4">
        {isLoading ? (
          <SectionSkeleton />
        ) : error ? (
          <SectionMessage
            tone="error"
            label={errorLabel(
              error,
              zh ? "每日趋势加载失败" : "Failed to load daily trend",
            )}
          />
        ) : data.length === 0 ? (
          <SectionMessage label={zh ? "暂无趋势数据" : "No trend data"} />
        ) : (
          <ChartContainer config={chartConfig} className="h-[320px] w-full">
            <AreaChart
              data={data}
              margin={{ top: 12, right: 12, left: 0, bottom: 0 }}
            >
              <defs>
                <linearGradient
                  id="overviewRequests"
                  x1="0"
                  y1="0"
                  x2="0"
                  y2="1"
                >
                  <stop
                    offset="5%"
                    stopColor="var(--chart-1)"
                    stopOpacity={0.28}
                  />
                  <stop
                    offset="95%"
                    stopColor="var(--chart-1)"
                    stopOpacity={0}
                  />
                </linearGradient>
                <linearGradient id="overviewTokens" x1="0" y1="0" x2="0" y2="1">
                  <stop
                    offset="5%"
                    stopColor="var(--chart-2)"
                    stopOpacity={0.18}
                  />
                  <stop
                    offset="95%"
                    stopColor="var(--chart-2)"
                    stopOpacity={0}
                  />
                </linearGradient>
                <linearGradient id="overviewCost" x1="0" y1="0" x2="0" y2="1">
                  <stop
                    offset="5%"
                    stopColor="var(--chart-3)"
                    stopOpacity={0.3}
                  />
                  <stop
                    offset="95%"
                    stopColor="var(--chart-3)"
                    stopOpacity={0}
                  />
                </linearGradient>
              </defs>
              <CartesianGrid vertical={false} strokeDasharray="3 3" />
              <XAxis
                dataKey="date"
                tickLine={false}
                axisLine={false}
                fontSize={11}
              />
              <YAxis
                tickLine={false}
                axisLine={false}
                fontSize={11}
                tickFormatter={(value) => formatCompact(Number(value))}
                width={42}
              />
              <ChartTooltip
                content={
                  <ChartTooltipContent
                    labelKey="date"
                    formatter={(value, name) => {
                      const numeric = Number(value);
                      const label = String(name);
                      const formatted =
                        label === "cost"
                          ? formatMoney(numeric)
                          : formatCompact(numeric);
                      const displayName =
                        label === "requests"
                          ? zh
                            ? "请求"
                            : "Requests"
                          : label === "tokens"
                            ? "Tokens"
                            : zh
                              ? "费用"
                              : "Cost";
                      return (
                        <>
                          {displayName}: {formatted}
                        </>
                      );
                    }}
                  />
                }
              />
              <Area
                type="monotone"
                dataKey="requests"
                stroke="var(--chart-1)"
                fill="url(#overviewRequests)"
                strokeWidth={2}
                dot={false}
              />
              <Area
                type="monotone"
                dataKey="tokens"
                stroke="var(--chart-2)"
                fill="url(#overviewTokens)"
                strokeWidth={2}
                dot={false}
              />
              <Area
                type="monotone"
                dataKey="cost"
                stroke="var(--chart-3)"
                fill="url(#overviewCost)"
                strokeWidth={2}
                dot={false}
              />
            </AreaChart>
          </ChartContainer>
        )}
      </CardContent>
    </Card>
  );
}
