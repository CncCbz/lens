import { Activity, CheckCircle2, XCircle } from "lucide-react";

import type { OverviewChannelHealthPoint } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatCompact, formatDuration, formatPercent } from "./overview-utils";
import { SectionMessage, SectionSkeleton, errorLabel } from "./section-state";

export function ChannelHealthList({
  items,
  isLoading,
  error,
  zh,
}: {
  items?: OverviewChannelHealthPoint[];
  isLoading: boolean;
  error: unknown;
  zh: boolean;
}) {
  return (
    <Card size="sm" className="min-w-0 py-0">
      <CardHeader className="border-b px-4 py-4">
        <CardTitle className="text-base">
          {zh ? "渠道健康" : "Channel health"}
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
              zh ? "渠道健康加载失败" : "Failed to load channel health",
            )}
          />
        ) : !items?.length ? (
          <SectionMessage
            label={zh ? "暂无渠道健康数据" : "No channel health data"}
          />
        ) : (
          <div className="max-h-[320px] space-y-3 overflow-y-auto pr-1 [scrollbar-gutter:stable]">
            {[...items]
              .sort(
                (a, b) =>
                  b.success_rate - a.success_rate ||
                  b.request_count - a.request_count ||
                  b.failed_requests - a.failed_requests ||
                  a.channel_name.localeCompare(b.channel_name),
              )
              .map((item) => (
                <div
                  key={item.channel_id}
                  className="grid gap-2 rounded-md border p-3"
                >
                  <div className="flex min-w-0 items-center gap-3">
                    <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
                      <Activity className="size-4" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">
                        {item.channel_name || item.channel_id}
                      </div>
                      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                        <span className="inline-flex items-center gap-1">
                          <CheckCircle2 className="size-3 text-emerald-500" />
                          {formatCompact(item.successful_requests)}
                        </span>
                        <span className="inline-flex items-center gap-1">
                          <XCircle className="size-3 text-destructive" />
                          {formatCompact(item.failed_requests)}
                        </span>
                        <span>{formatDuration(item.average_latency_ms)}</span>
                      </div>
                    </div>
                    <div className="font-mono text-sm font-semibold tabular-nums">
                      {formatPercent(item.success_rate)}
                    </div>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-emerald-500"
                      style={{
                        width: `${Math.min(Math.max(item.success_rate, 0), 100)}%`,
                      }}
                    />
                  </div>
                </div>
              ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
