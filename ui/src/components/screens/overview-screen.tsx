"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  OverviewChannelHealthPoint,
  OverviewDailyPoint,
  OverviewDimensionUsageAnalytics,
  OverviewPerformanceAnalytics,
  OverviewSummary,
  apiRequest,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { ChannelHealthList } from "./overview/channel-health-list";
import { DailyOverviewChart } from "./overview/daily-overview-chart";
import {
  DimensionUsageSection,
  ModelUsageSection,
} from "./overview/dimension-usage-section";
import { OverviewHeatmapSection } from "./overview/overview-heatmap-section";
import { OverviewKpiRow } from "./overview/overview-kpi-row";
import { PerformanceSection } from "./overview/performance-section";
import {
  METRIC_OPTIONS,
  TIME_RANGE_OPTIONS,
  type OverviewMetric,
  type OverviewTimeRange,
} from "./overview/overview-utils";

function withParams(path: string, params: Record<string, string | number>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    search.set(key, String(value));
  }
  return `${path}?${search.toString()}`;
}

export function OverviewScreen() {
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const [timeRange, setTimeRange] = useState<OverviewTimeRange>("7");
  const [metric, setMetric] = useState<OverviewMetric>("cost");
  const days = Number(timeRange);

  const queryUrls = useMemo(
    () => ({
      summary: withParams("/admin/overview-summary", { days }),
      daily: withParams("/admin/overview-daily", { days }),
      heatmap: withParams("/admin/overview-daily", { days: 365 }),
      channelHealth: withParams("/admin/overview-health/channels", { days }),
      modelUsage: withParams("/admin/overview-usage/models", { days, metric }),
      gatewayKeyUsage: withParams("/admin/overview-usage/gateway-keys", {
        days,
        metric,
      }),
      channelPerformance: withParams("/admin/overview-performance/channels", {
        days,
      }),
      modelPerformance: withParams("/admin/overview-performance/models", {
        days,
      }),
    }),
    [days, metric],
  );

  const summaryQuery = useQuery({
    queryKey: ["overview-summary", days],
    queryFn: () => apiRequest<OverviewSummary>(queryUrls.summary),
    refetchOnMount: "always",
  });
  const dailyQuery = useQuery({
    queryKey: ["overview-daily", days],
    queryFn: () => apiRequest<OverviewDailyPoint[]>(queryUrls.daily),
    refetchOnMount: "always",
  });
  const heatmapQuery = useQuery({
    queryKey: ["overview-daily", 365],
    queryFn: () => apiRequest<OverviewDailyPoint[]>(queryUrls.heatmap),
    staleTime: 60_000,
  });
  const channelHealthQuery = useQuery({
    queryKey: ["overview-health", "channels", days],
    queryFn: () =>
      apiRequest<OverviewChannelHealthPoint[]>(queryUrls.channelHealth),
    refetchOnMount: "always",
  });
  const modelUsageQuery = useQuery({
    queryKey: ["overview-usage", "models", days, metric],
    queryFn: () =>
      apiRequest<OverviewDimensionUsageAnalytics>(queryUrls.modelUsage),
    refetchOnMount: "always",
  });
  const gatewayKeyUsageQuery = useQuery({
    queryKey: ["overview-usage", "gateway-keys", days, metric],
    queryFn: () =>
      apiRequest<OverviewDimensionUsageAnalytics>(queryUrls.gatewayKeyUsage),
    refetchOnMount: "always",
  });
  const channelPerformanceQuery = useQuery({
    queryKey: ["overview-performance", "channels", days],
    queryFn: () =>
      apiRequest<OverviewPerformanceAnalytics>(queryUrls.channelPerformance),
    refetchOnMount: "always",
  });
  const modelPerformanceQuery = useQuery({
    queryKey: ["overview-performance", "models", days],
    queryFn: () =>
      apiRequest<OverviewPerformanceAnalytics>(queryUrls.modelPerformance),
    refetchOnMount: "always",
  });

  return (
    <section className="flex flex-col gap-4">
      <header className="flex justify-end gap-2">
        <Select
          value={metric}
          onValueChange={(value) => setMetric(value as OverviewMetric)}
        >
          <SelectTrigger
            className="w-full sm:w-36"
            aria-label={isZh ? "选择排行指标" : "Select ranking metric"}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent align="end" className="rounded-xl">
            {METRIC_OPTIONS.map((option) => (
              <SelectItem
                key={option.value}
                value={option.value}
                className="rounded-lg"
              >
                {isZh ? option.zhLabel : option.enLabel}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={timeRange}
          onValueChange={(value) => setTimeRange(value as OverviewTimeRange)}
        >
          <SelectTrigger
            className="w-full sm:w-36"
            aria-label={isZh ? "选择统计范围" : "Select time range"}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent align="end" className="rounded-xl">
            {TIME_RANGE_OPTIONS.map((option) => (
              <SelectItem
                key={option.value}
                value={option.value}
                className="rounded-lg"
              >
                {isZh ? option.zhLabel : option.enLabel}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </header>

      <OverviewKpiRow
        summary={summaryQuery.data}
        isLoading={summaryQuery.isLoading}
        error={summaryQuery.error}
        zh={isZh}
      />

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1.45fr)_minmax(22rem,0.8fr)]">
        <DailyOverviewChart
          points={dailyQuery.data}
          isLoading={dailyQuery.isLoading}
          error={dailyQuery.error}
          zh={isZh}
        />
        <ChannelHealthList
          items={channelHealthQuery.data}
          isLoading={channelHealthQuery.isLoading}
          error={channelHealthQuery.error}
          zh={isZh}
        />
      </section>

      <ModelUsageSection
        title={isZh ? "模型分析" : "Model analytics"}
        analytics={modelUsageQuery.data}
        isLoading={modelUsageQuery.isLoading}
        error={modelUsageQuery.error}
        zh={isZh}
      />

      <DimensionUsageSection
        title={isZh ? "API Key 分析" : "API Key analytics"}
        analytics={gatewayKeyUsageQuery.data}
        isLoading={gatewayKeyUsageQuery.isLoading}
        error={gatewayKeyUsageQuery.error}
        metric={metric}
        zh={isZh}
      />

      <PerformanceSection
        channelAnalytics={channelPerformanceQuery.data}
        modelAnalytics={modelPerformanceQuery.data}
        isLoading={
          channelPerformanceQuery.isLoading || modelPerformanceQuery.isLoading
        }
        channelError={channelPerformanceQuery.error}
        modelError={modelPerformanceQuery.error}
        zh={isZh}
      />

      <OverviewHeatmapSection
        points={heatmapQuery.data}
        isLoading={heatmapQuery.isLoading}
        error={heatmapQuery.error}
        zh={isZh}
      />
    </section>
  );
}
