import type { OverviewDimensionUsageAnalytics } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import {
  CHART_COLORS,
  formatCompact,
  formatDuration,
  formatMoney,
  formatPercent,
  metricValue,
  type OverviewMetric,
} from "./overview-utils";
import { SectionMessage, SectionSkeleton, errorLabel } from "./section-state";

export function OverviewKeyUsageCard({
  analytics,
  metric,
  isLoading,
  error,
  zh,
}: {
  analytics?: OverviewDimensionUsageAnalytics;
  metric: OverviewMetric;
  isLoading: boolean;
  error: unknown;
  zh: boolean;
}) {
  const items = [...(analytics?.items ?? [])]
    .sort((a, b) => metricValue(metric, b) - metricValue(metric, a))
    .slice(0, 8);
  const max = Math.max(...items.map((item) => metricValue(metric, item)), 1);

  return (
    <Card size="sm" className="min-w-0 py-0">
      <CardHeader className="border-b px-4 py-4">
        <CardTitle className="text-base">
          {zh ? "API Key 用量" : "API key usage"}
        </CardTitle>
        {items.length > 0 ? (
          <span className="text-xs text-muted-foreground">
            {zh ? `${items.length} 个 Key` : `${items.length} keys`}
          </span>
        ) : null}
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
                zh ? "API Key 用量加载失败" : "Failed to load key usage",
              )}
            />
          </div>
        ) : items.length === 0 ? (
          <div className="p-4">
            <SectionMessage label={zh ? "暂无 API Key 用量" : "No key usage"} />
          </div>
        ) : (
          <div className="divide-y">
            {items.map((item, index) => {
              const value = metricValue(metric, item);
              return (
                <div key={item.id} className="grid gap-1.5 px-4 py-3">
                  <div className="flex min-w-0 items-baseline justify-between gap-3">
                    <span
                      className="min-w-0 truncate text-sm font-medium"
                      title={item.name}
                    >
                      {item.name || item.id}
                    </span>
                    <span className="shrink-0 font-mono text-sm font-semibold tabular-nums">
                      {metric === "cost"
                        ? formatMoney(value)
                        : formatCompact(value)}
                    </span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${Math.max((value / max) * 100, value > 0 ? 3 : 0)}%`,
                        backgroundColor:
                          CHART_COLORS[index % CHART_COLORS.length],
                      }}
                    />
                  </div>
                  <div className="flex flex-wrap gap-x-3 gap-y-1 font-mono text-xs text-muted-foreground">
                    <span>
                      {zh ? "请求" : "Req"} {formatCompact(item.request_count)}
                    </span>
                    <span>Token {formatCompact(item.total_tokens)}</span>
                    <span>
                      {formatPercent(
                        item.request_count
                          ? (item.successful_requests / item.request_count) *
                              100
                          : 0,
                      )}
                    </span>
                    <span>{formatDuration(item.average_latency_ms)}</span>
                    <span>{formatMoney(item.total_cost_usd)}</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
