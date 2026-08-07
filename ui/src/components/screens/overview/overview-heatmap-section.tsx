import type { OverviewDailyPoint } from "@/lib/api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

import {
  addDays,
  formatCompact,
  parseDateKey,
  toLocalDateKey,
} from "./overview-utils";
import { SectionMessage, SectionSkeleton, errorLabel } from "./section-state";

function intensity(value: number, max: number) {
  if (value <= 0 || max <= 0) return "bg-muted";
  const ratio = value / max;
  if (ratio >= 0.8) return "bg-primary";
  if (ratio >= 0.55) return "bg-primary/75";
  if (ratio >= 0.3) return "bg-primary/45";
  return "bg-primary/20";
}

export function OverviewHeatmapSection({
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
  const dailyMap = new Map(
    (points ?? []).map((point) => [parseDateKey(point.date), point]),
  );
  const today = new Date();
  const days = Array.from({ length: 365 }, (_, index) => {
    const date = toLocalDateKey(addDays(today, index - 364));
    const point = dailyMap.get(date);
    return {
      date,
      count: point?.request_count ?? 0,
      tokens: point?.total_tokens ?? 0,
      cost: point?.total_cost_usd ?? 0,
    };
  });
  const max = Math.max(...days.map((item) => item.count), 0);
  const total = days.reduce((sum, item) => sum + item.count, 0);

  return (
    <Card size="sm" className="min-w-0 py-0">
      <CardHeader className="border-b px-4 py-4">
        <CardTitle className="text-base">
          {zh ? "长期活跃度" : "Long-term activity"}
        </CardTitle>
        <CardDescription>
          {zh
            ? `近 365 天请求 ${formatCompact(total)}`
            : `${formatCompact(total)} requests in the last 365 days`}
        </CardDescription>
      </CardHeader>
      <CardContent className="p-4">
        {isLoading ? (
          <SectionSkeleton />
        ) : error ? (
          <SectionMessage
            tone="error"
            label={errorLabel(
              error,
              zh ? "热力图加载失败" : "Failed to load heatmap",
            )}
          />
        ) : days.length === 0 ? (
          <SectionMessage label={zh ? "暂无长期数据" : "No long-term data"} />
        ) : (
          <div className="overflow-x-auto pb-1">
            <div className="grid w-max grid-flow-col grid-rows-7 gap-1">
              {days.map((item) => (
                <Tooltip key={item.date}>
                  <TooltipTrigger asChild>
                    <div
                      className={`size-3 rounded-[3px] ${intensity(item.count, max)}`}
                      aria-label={`${item.date}: ${item.count}`}
                    />
                  </TooltipTrigger>
                  <TooltipContent>
                    <div className="grid gap-1 text-xs">
                      <span className="font-medium">{item.date}</span>
                      <span>
                        {zh ? "请求" : "Requests"}: {formatCompact(item.count)}
                      </span>
                      <span>Tokens: {formatCompact(item.tokens)}</span>
                    </div>
                  </TooltipContent>
                </Tooltip>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
