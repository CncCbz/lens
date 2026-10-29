import { useState } from "react";

import type { OverviewDimensionUsageAnalytics } from "@/lib/api";
import {
  Card,
  CardAction,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { SegmentedControl } from "@/components/ui/segmented-control";

import { ModelUsageList, UsageRankList } from "./dimension-usage-section";
import type { OverviewMetric } from "./overview-utils";
import { SectionMessage, SectionSkeleton, errorLabel } from "./section-state";

type UsageDimension = "model" | "channel";

export function OverviewUsageSection({
  modelAnalytics,
  channelAnalytics,
  metric,
  isLoading,
  modelError,
  channelError,
  zh,
}: {
  modelAnalytics?: OverviewDimensionUsageAnalytics;
  channelAnalytics?: OverviewDimensionUsageAnalytics;
  metric: OverviewMetric;
  isLoading: boolean;
  modelError: unknown;
  channelError: unknown;
  zh: boolean;
}) {
  const [dimension, setDimension] = useState<UsageDimension>("model");
  const analytics = dimension === "model" ? modelAnalytics : channelAnalytics;
  const items = analytics?.items ?? [];
  const error = dimension === "model" ? modelError : channelError;

  return (
    <Card size="sm" className="min-w-0 py-0">
      <CardHeader className="border-b px-4 py-4">
        <CardTitle className="text-base">{zh ? "用量分析" : "Usage"}</CardTitle>
        <CardAction>
          <SegmentedControl
            value={dimension}
            onValueChange={setDimension}
            options={[
              { value: "model", label: zh ? "模型" : "Model" },
              { value: "channel", label: zh ? "渠道" : "Channel" },
            ]}
          />
        </CardAction>
      </CardHeader>
      <CardContent className={dimension === "model" ? "p-0" : "p-4"}>
        {dimension === "model" ? (
          <ModelUsageList
            analytics={modelAnalytics}
            isLoading={isLoading}
            error={error}
            zh={zh}
          />
        ) : isLoading ? (
          <SectionSkeleton />
        ) : error ? (
          <SectionMessage
            tone="error"
            label={errorLabel(
              error,
              zh ? "渠道用量加载失败" : "Failed to load channel usage",
            )}
          />
        ) : items.length === 0 ? (
          <SectionMessage label={zh ? "暂无渠道用量" : "No channel usage"} />
        ) : (
          <UsageRankList items={items} metric={metric} zh={zh} />
        )}
      </CardContent>
    </Card>
  );
}
