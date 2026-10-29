import { useMemo, useState } from "react";

import type {
  OverviewDimensionUsageAnalytics,
  OverviewPerformanceAnalytics,
} from "@/lib/api";
import {
  Card,
  CardAction,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { SegmentedControl } from "@/components/ui/segmented-control";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import {
  formatCompact,
  formatDuration,
  formatPercent,
  metricValue,
  type OverviewMetric,
} from "./overview-utils";
import { SectionMessage, SectionSkeleton, errorLabel } from "./section-state";

type DetailDimension = "channel" | "model";

export function OverviewDetailTable({
  channelUsage,
  modelUsage,
  channelPerformance,
  modelPerformance,
  metric,
  isLoading,
  error,
  zh,
}: {
  channelUsage?: OverviewDimensionUsageAnalytics;
  modelUsage?: OverviewDimensionUsageAnalytics;
  channelPerformance?: OverviewPerformanceAnalytics;
  modelPerformance?: OverviewPerformanceAnalytics;
  metric: OverviewMetric;
  isLoading: boolean;
  error: unknown;
  zh: boolean;
}) {
  const [dimension, setDimension] = useState<DetailDimension>("channel");

  const rows = useMemo(() => {
    const usage = dimension === "channel" ? channelUsage : modelUsage;
    const performance =
      dimension === "channel" ? channelPerformance : modelPerformance;
    const perfById = new Map(
      (performance?.items ?? []).map((item) => [item.id, item]),
    );
    return [...(usage?.items ?? [])]
      .sort((a, b) => metricValue(metric, b) - metricValue(metric, a))
      .map((item) => {
        const perf = perfById.get(item.id);
        return {
          id: item.id,
          name: item.name || item.id,
          requestCount: item.request_count,
          totalTokens: item.total_tokens,
          inputTokens: item.input_tokens,
          outputTokens: item.output_tokens,
          successRate: item.request_count
            ? (item.successful_requests / item.request_count) * 100
            : 0,
          latencyMs: item.average_latency_ms,
          firstTokenMs: perf?.average_first_token_latency_ms,
          throughput: perf?.throughput_tokens_per_second,
        };
      });
  }, [
    dimension,
    channelUsage,
    modelUsage,
    channelPerformance,
    modelPerformance,
    metric,
  ]);

  return (
    <Card size="sm" className="min-w-0 py-0">
      <CardHeader className="border-b px-4 py-4">
        <CardTitle className="text-base">{zh ? "明细" : "Details"}</CardTitle>
        <CardAction>
          <SegmentedControl
            value={dimension}
            onValueChange={setDimension}
            options={[
              { value: "channel", label: zh ? "渠道" : "Channel" },
              { value: "model", label: zh ? "模型" : "Model" },
            ]}
          />
        </CardAction>
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
                zh ? "明细加载失败" : "Failed to load details",
              )}
            />
          </div>
        ) : rows.length === 0 ? (
          <div className="p-4">
            <SectionMessage label={zh ? "暂无明细数据" : "No details"} />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-4">
                    {dimension === "channel"
                      ? zh
                        ? "渠道"
                        : "Channel"
                      : zh
                        ? "模型"
                        : "Model"}
                  </TableHead>
                  <TableHead className="text-right">
                    {zh ? "请求" : "Req"}
                  </TableHead>
                  <TableHead className="text-right">Token</TableHead>
                  <TableHead className="hidden text-right lg:table-cell">
                    {zh ? "输入" : "Input"}
                  </TableHead>
                  <TableHead className="hidden text-right lg:table-cell">
                    {zh ? "输出" : "Output"}
                  </TableHead>
                  <TableHead className="text-right">
                    {zh ? "成功率" : "Success"}
                  </TableHead>
                  <TableHead className="text-right">
                    {zh ? "平均延迟" : "Latency"}
                  </TableHead>
                  <TableHead className="hidden text-right md:table-cell">
                    {zh ? "首字延迟" : "TTFT"}
                  </TableHead>
                  <TableHead className="hidden pr-4 text-right md:table-cell">
                    {zh ? "吞吐 tok/s" : "Throughput"}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell className="max-w-[16rem] truncate pl-4 font-medium">
                      {row.name}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs tabular-nums text-muted-foreground">
                      {formatCompact(row.requestCount)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs tabular-nums">
                      {formatCompact(row.totalTokens)}
                    </TableCell>
                    <TableCell className="hidden text-right font-mono text-xs tabular-nums text-muted-foreground lg:table-cell">
                      {formatCompact(row.inputTokens)}
                    </TableCell>
                    <TableCell className="hidden text-right font-mono text-xs tabular-nums text-muted-foreground lg:table-cell">
                      {formatCompact(row.outputTokens)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs tabular-nums">
                      {formatPercent(row.successRate)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs tabular-nums">
                      {formatDuration(row.latencyMs)}
                    </TableCell>
                    <TableCell className="hidden text-right font-mono text-xs tabular-nums text-muted-foreground md:table-cell">
                      {row.firstTokenMs === undefined
                        ? "-"
                        : formatDuration(row.firstTokenMs)}
                    </TableCell>
                    <TableCell className="hidden pr-4 text-right font-mono text-xs tabular-nums text-muted-foreground md:table-cell">
                      {row.throughput === undefined
                        ? "-"
                        : formatCompact(row.throughput)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
