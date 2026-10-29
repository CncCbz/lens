"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  OverviewDailyPoint,
  OverviewDimensionUsageAnalytics,
  OverviewPerformanceAnalytics,
  OverviewSummary,
  apiRequest,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { SegmentedControl } from "@/components/ui/segmented-control";

import {
  METRIC_OPTIONS,
  TIME_RANGE_OPTIONS,
  type OverviewMetric,
  type OverviewTimeRange,
} from "./overview/overview-utils";
import { OverviewCostSection } from "./overview/overview-cost-section";
import { OverviewDetailTable } from "./overview/overview-detail-table";
import { OverviewHeatmapSection } from "./overview/overview-heatmap-section";
import { OverviewKeyUsageCard } from "./overview/overview-key-usage-card";
import { OverviewKpiRow } from "./overview/overview-kpi-row";
import { OverviewTrendSection } from "./overview/overview-trend-section";
import { OverviewUsageSection } from "./overview/overview-usage-section";

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
  const [metric, setMetric] = useState<OverviewMetric>("tokens");
  const days = Number(timeRange);

  const queryUrls = useMemo(
    () => ({
      summary: withParams("/admin/overview-summary", { days }),
      daily: withParams("/admin/overview-daily", { days }),
      heatmap: withParams("/admin/overview-daily", { days: 365 }),
      modelUsage: withParams("/admin/overview-usage/models", { days, metric }),
      channelUsage: withParams("/admin/overview-usage/channels", {
        days,
        metric,
      }),
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
  const modelUsageQuery = useQuery({
    queryKey: ["overview-usage", "models", days, metric],
    queryFn: () =>
      apiRequest<OverviewDimensionUsageAnalytics>(queryUrls.modelUsage),
    refetchOnMount: "always",
  });
  const channelUsageQuery = useQuery({
    queryKey: ["overview-usage", "channels", days, metric],
    queryFn: () =>
      apiRequest<OverviewDimensionUsageAnalytics>(queryUrls.channelUsage),
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

  const usageLoading = modelUsageQuery.isLoading || channelUsageQuery.isLoading;

  return (
    <section className="flex flex-col gap-4">
      <header className="flex flex-wrap items-center justify-end gap-2">
        <SegmentedControl
          value={metric}
          onValueChange={(value) => setMetric(value as OverviewMetric)}
          options={METRIC_OPTIONS.map((option) => ({
            value: option.value,
            label: isZh ? option.zhLabel : option.enLabel,
          }))}
        />
        <SegmentedControl
          value={timeRange}
          onValueChange={(value) => setTimeRange(value as OverviewTimeRange)}
          options={TIME_RANGE_OPTIONS.map((option) => ({
            value: option.value,
            label: isZh ? option.zhLabel : option.enLabel,
          }))}
        />
      </header>

      <OverviewKpiRow
        summary={summaryQuery.data}
        points={dailyQuery.data}
        isLoading={summaryQuery.isLoading || dailyQuery.isLoading}
        error={summaryQuery.error}
        zh={isZh}
      />

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(20rem,0.85fr)]">
        <OverviewTrendSection
          modelAnalytics={modelUsageQuery.data}
          channelAnalytics={channelUsageQuery.data}
          metric={metric}
          isLoading={modelUsageQuery.isLoading || channelUsageQuery.isLoading}
          error={modelUsageQuery.error || channelUsageQuery.error}
          zh={isZh}
        />
        <OverviewCostSection
          summary={summaryQuery.data}
          metric={metric}
          isLoading={summaryQuery.isLoading}
          error={summaryQuery.error}
          zh={isZh}
        />
      </section>

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(20rem,0.85fr)]">
        <OverviewUsageSection
          modelAnalytics={modelUsageQuery.data}
          channelAnalytics={channelUsageQuery.data}
          metric={metric}
          isLoading={usageLoading}
          modelError={modelUsageQuery.error}
          channelError={channelUsageQuery.error}
          zh={isZh}
        />
        <OverviewKeyUsageCard
          analytics={gatewayKeyUsageQuery.data}
          metric={metric}
          isLoading={gatewayKeyUsageQuery.isLoading}
          error={gatewayKeyUsageQuery.error}
          zh={isZh}
        />
      </section>

      <OverviewDetailTable
        channelUsage={channelUsageQuery.data}
        modelUsage={modelUsageQuery.data}
        channelPerformance={channelPerformanceQuery.data}
        modelPerformance={modelPerformanceQuery.data}
        metric={metric}
        isLoading={
          channelUsageQuery.isLoading ||
          modelUsageQuery.isLoading ||
          channelPerformanceQuery.isLoading ||
          modelPerformanceQuery.isLoading
        }
        error={
          channelUsageQuery.error ||
          modelUsageQuery.error ||
          channelPerformanceQuery.error ||
          modelPerformanceQuery.error
        }
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
