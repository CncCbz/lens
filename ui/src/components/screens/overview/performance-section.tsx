import { Gauge } from "lucide-react";

import type {
  OverviewPerformanceAnalytics,
  OverviewPerformancePoint,
} from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { formatCompact, formatDuration } from "./overview-utils";
import { SectionMessage, SectionSkeleton, errorLabel } from "./section-state";

function PerformanceList({
  title,
  items,
  zh,
}: {
  title: string;
  items: OverviewPerformancePoint[];
  zh: boolean;
}) {
  const ranked = [...items].slice(0, 8);
  return (
    <section className="min-w-0">
      <h3 className="mb-3 text-sm font-medium">{title}</h3>
      <div className="space-y-3">
        {ranked.map((item) => (
          <div key={item.id} className="grid gap-2 rounded-md border p-3">
            <div className="flex min-w-0 items-center gap-3">
              <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
                <Gauge className="size-4" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium" title={item.name}>
                  {item.name || item.id}
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {zh ? "请求" : "Requests"} {formatCompact(item.request_count)}
                </div>
              </div>
              <div className="text-right font-mono text-sm font-semibold tabular-nums">
                {formatDuration(item.average_latency_ms)}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground sm:grid-cols-3">
              <span>
                {zh ? "首 token" : "TTFT"}{" "}
                {formatDuration(item.average_first_token_latency_ms)}
              </span>
              <span>
                {zh ? "吞吐" : "Throughput"}{" "}
                {formatCompact(item.throughput_tokens_per_second)}/s
              </span>
              <span>
                {zh ? "输出" : "Output"} {formatCompact(item.output_tokens)}
              </span>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

export function PerformanceSection({
  channelAnalytics,
  modelAnalytics,
  isLoading,
  channelError,
  modelError,
  zh,
}: {
  channelAnalytics?: OverviewPerformanceAnalytics;
  modelAnalytics?: OverviewPerformanceAnalytics;
  isLoading: boolean;
  channelError: unknown;
  modelError: unknown;
  zh: boolean;
}) {
  const channelItems = channelAnalytics?.items ?? [];
  const modelItems = modelAnalytics?.items ?? [];
  const hasError = channelError || modelError;

  return (
    <Card size="sm" className="min-w-0 py-0">
      <CardHeader className="border-b px-4 py-4">
        <CardTitle className="text-base">
          {zh ? "性能分析" : "Performance"}
        </CardTitle>
      </CardHeader>
      <CardContent className="p-4">
        {isLoading ? (
          <SectionSkeleton />
        ) : hasError ? (
          <SectionMessage
            tone="error"
            label={errorLabel(
              hasError,
              zh ? "性能数据加载失败" : "Failed to load performance data",
            )}
          />
        ) : channelItems.length === 0 && modelItems.length === 0 ? (
          <SectionMessage label={zh ? "暂无性能数据" : "No performance data"} />
        ) : (
          <div className="grid gap-6 lg:grid-cols-2">
            <PerformanceList
              title={zh ? "渠道性能" : "Channel performance"}
              items={channelItems}
              zh={zh}
            />
            <div className="min-w-0 lg:border-l lg:pl-6">
              <PerformanceList
                title={zh ? "模型性能" : "Model performance"}
                items={modelItems}
                zh={zh}
              />
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
