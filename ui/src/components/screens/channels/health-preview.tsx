"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type {
  ProtocolKind,
  RouteSnapshot,
  SiteRuntimeSummary,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  compactProtocolLabel,
  credentialDisplayName,
  formatCooldownDuration,
  protocolConfigDisplayName,
  type Locale,
  type SiteRow,
} from "./shared";

type ChannelHealthRow = RouteSnapshot["health"][number];
type ChannelRuntimeSummary = SiteRuntimeSummary["channel_summaries"][number];
type ChannelHealthBucket = ChannelRuntimeSummary["health_buckets"][number];
type CoolingBadgeSpec = {
  label: string;
  title: string;
  className: string;
  channelId: string;
};
type HealthPreviewChannel = {
  channelId: string;
  protocolConfig: SiteRow["protocols"][number];
  protocolConfigIndex: number;
  protocol: ProtocolKind;
};
type AggregatedBucket = {
  started_at: string;
  ended_at: string;
  success_count: number;
  total_count: number;
};

const CHANNEL_HEALTH_BUCKET_COUNT = 12;

function maxKeyCooldownSeconds(health: ChannelHealthRow | undefined) {
  if (!health?.key_health?.length) {
    return 0;
  }
  return Math.max(
    0,
    ...health.key_health.map((item) => item.cooldown_remaining_seconds),
  );
}

function keyCooldownDetails(
  site: SiteRow,
  health: ChannelHealthRow,
  locale: Locale,
) {
  const credentialById = new Map(
    site.credentials.map((item) => [item.id, item] as const),
  );
  const credentialIndexById = new Map(
    site.credentials.map((item, index) => [item.id, index] as const),
  );

  return health.key_health
    .filter((item) => !item.available && item.cooldown_remaining_seconds > 0)
    .sort(
      (left, right) =>
        right.cooldown_remaining_seconds - left.cooldown_remaining_seconds,
    )
    .map((item) => {
      const credentialIndex = credentialIndexById.get(item.credential_id) ?? 0;
      const credentialName = credentialDisplayName(
        credentialById.get(item.credential_id),
        credentialIndex,
        locale,
      );
      const duration = formatCooldownDuration(item.cooldown_remaining_seconds);
      return `${credentialName} ${locale === "zh-CN" ? "冷却剩余" : "cooldown remaining"} ${duration}`;
    });
}

function resolveCoolingBadge(
  site: SiteRow,
  channelId: string,
  health: ChannelHealthRow | undefined,
  locale: Locale,
): CoolingBadgeSpec | null {
  if (!health) {
    return null;
  }
  if (health.cooldown_remaining_seconds > 0) {
    const duration = formatCooldownDuration(health.cooldown_remaining_seconds);
    return locale === "zh-CN"
      ? {
          label: `冷却 ${duration}`,
          title: `渠道冷却剩余 ${duration}`,
          className: "border-transparent bg-destructive/12 text-destructive",
          channelId,
        }
      : {
          label: `Cooling ${duration}`,
          title: `Channel cooldown remaining ${duration}`,
          className: "border-transparent bg-destructive/12 text-destructive",
          channelId,
        };
  }
  const keyCooldownSeconds = maxKeyCooldownSeconds(health);
  if (keyCooldownSeconds > 0) {
    const duration = formatCooldownDuration(keyCooldownSeconds);
    const details = keyCooldownDetails(site, health, locale).join("\n");
    return locale === "zh-CN"
      ? {
          label: `Key 冷却 ${duration}`,
          title: details || `Key 冷却剩余 ${duration}`,
          className: "border-transparent bg-amber-500/12 text-amber-700",
          channelId,
        }
      : {
          label: `Key cooling ${duration}`,
          title: details || `Key cooldown remaining ${duration}`,
          className: "border-transparent bg-amber-500/12 text-amber-700",
          channelId,
        };
  }
  return null;
}

function runtimeChannelId(protocolConfigId: string, protocol: ProtocolKind) {
  return `${protocolConfigId}_${protocol}`;
}

function siteHealthPreviewChannels(site: SiteRow): HealthPreviewChannel[] {
  return site.protocols.flatMap((protocolConfig, protocolConfigIndex) => {
    if (!protocolConfig.enabled) {
      return [];
    }
    return protocolConfig.protocols.map((protocol) => ({
      channelId: runtimeChannelId(protocolConfig.id, protocol),
      protocolConfig,
      protocolConfigIndex,
      protocol,
    }));
  });
}

function healthPreviewChannelLabel(
  channel: HealthPreviewChannel,
  locale: Locale,
) {
  return `${protocolConfigDisplayName(channel.protocolConfig, channel.protocolConfigIndex, locale)} / ${compactProtocolLabel(channel.protocol)}`;
}

function normalizedBucketCounts(bucket: {
  success_count: number;
  total_count: number;
}) {
  const total = Math.max(0, bucket.total_count);
  return {
    total,
    success: Math.min(Math.max(0, bucket.success_count), total),
  };
}

function healthBucketTone(bucket: {
  success_count: number;
  total_count: number;
}) {
  const { success, total } = normalizedBucketCounts(bucket);
  if (total <= 0) {
    return "bg-muted/70";
  }
  if (success >= total) {
    return "bg-emerald-500";
  }
  if (success > 0) {
    return "bg-amber-500";
  }
  return "bg-destructive";
}

function createHealthBucketTimeFormatter(locale: Locale, timeZone?: string) {
  return new Intl.DateTimeFormat(locale === "zh-CN" ? "zh-CN" : "en-US", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    ...(timeZone ? { timeZone } : {}),
  });
}

function formatHealthBucketRange(
  bucket: Pick<AggregatedBucket, "started_at" | "ended_at">,
  formatDateTime: Intl.DateTimeFormat,
) {
  return `${formatDateTime.format(new Date(bucket.started_at))} - ${formatDateTime.format(new Date(bucket.ended_at))}`;
}

function aggregateHealthBuckets(
  channels: HealthPreviewChannel[],
  summaryByChannelId: Map<string, ChannelRuntimeSummary>,
): Array<AggregatedBucket | null> {
  const channelBuckets = channels.map((channel) =>
    (summaryByChannelId.get(channel.channelId)?.health_buckets ?? []).slice(
      -CHANNEL_HEALTH_BUCKET_COUNT,
    ),
  );
  const maxLength = Math.max(
    0,
    ...channelBuckets.map((buckets) => buckets.length),
  );
  if (!maxLength) {
    return Array.from({ length: CHANNEL_HEALTH_BUCKET_COUNT }, () => null);
  }

  const recent: Array<AggregatedBucket | null> = [];
  for (let offset = maxLength; offset > 0; offset -= 1) {
    let success_count = 0;
    let total_count = 0;
    let started_at = "";
    let ended_at = "";
    let hasBucket = false;
    for (const buckets of channelBuckets) {
      const bucket = buckets[buckets.length - offset];
      if (!bucket) continue;
      hasBucket = true;
      success_count += Math.max(0, bucket.success_count);
      total_count += Math.max(0, bucket.total_count);
      if (!started_at || bucket.started_at < started_at) {
        started_at = bucket.started_at;
      }
      if (!ended_at || bucket.ended_at > ended_at) {
        ended_at = bucket.ended_at;
      }
    }
    recent.push(
      hasBucket ? { started_at, ended_at, success_count, total_count } : null,
    );
  }

  const placeholders = Array.from(
    { length: Math.max(CHANNEL_HEALTH_BUCKET_COUNT - recent.length, 0) },
    () => null,
  );
  return [...placeholders, ...recent].slice(-CHANNEL_HEALTH_BUCKET_COUNT);
}

function pickCoolingBadge(
  site: SiteRow,
  channels: HealthPreviewChannel[],
  healthByChannelId: Map<string, ChannelHealthRow>,
  locale: Locale,
): CoolingBadgeSpec | null {
  let best: CoolingBadgeSpec | null = null;
  let bestSeconds = -1;
  for (const channel of channels) {
    const health = healthByChannelId.get(channel.channelId);
    const badge = resolveCoolingBadge(site, channel.channelId, health, locale);
    if (!badge || !health) continue;
    const seconds = Math.max(
      health.cooldown_remaining_seconds,
      maxKeyCooldownSeconds(health),
    );
    if (seconds > bestSeconds) {
      best = {
        ...badge,
        title: [healthPreviewChannelLabel(channel, locale), badge.title].join(
          "\n",
        ),
      };
      bestSeconds = seconds;
    }
  }
  return best;
}

function HealthBarSegments({
  segments,
  locale,
  timeZone,
  compact = false,
  interactive = true,
}: {
  segments: Array<{
    key: string;
    bucket: AggregatedBucket | ChannelHealthBucket | null;
  }>;
  locale: Locale;
  timeZone?: string;
  compact?: boolean;
  interactive?: boolean;
}) {
  const bucketTimeFormatter = createHealthBucketTimeFormatter(locale, timeZone);
  const barClass = compact ? "h-4 w-1" : "h-5 w-1.5";

  return (
    <div
      className="flex min-w-0 flex-1 items-end gap-0.5"
      aria-label={locale === "zh-CN" ? "健康状态" : "health history"}
    >
      {segments.map((segment) => {
        if (!segment.bucket) {
          return (
            <span
              key={segment.key}
              className={cn("block rounded-[3px] bg-muted/70", barClass)}
              aria-hidden
            />
          );
        }

        const toneClass = healthBucketTone(segment.bucket);
        if (!interactive) {
          return (
            <span
              key={segment.key}
              className={cn("block rounded-[3px]", barClass, toneClass)}
              aria-hidden
            />
          );
        }

        const { success, total } = normalizedBucketCounts(segment.bucket);
        const bucketRange = formatHealthBucketRange(
          segment.bucket,
          bucketTimeFormatter,
        );
        return (
          <Tooltip key={segment.key}>
            <TooltipTrigger asChild>
              <button
                type="button"
                className={cn(
                  "block appearance-none rounded-[3px] border-0 p-0 outline-none transition-transform hover:scale-y-110 focus-visible:ring-2 focus-visible:ring-ring/70 focus-visible:ring-offset-1",
                  barClass,
                  toneClass,
                )}
                onClick={(event) => event.stopPropagation()}
                aria-label={`${bucketRange} ${success}/${total}`}
              />
            </TooltipTrigger>
            <TooltipContent
              side="bottom"
              sideOffset={8}
              collisionPadding={12}
              className="flex flex-col items-start gap-1 px-3 py-2 text-left text-xs"
            >
              <div className="font-medium">{bucketRange}</div>
              <div className="text-muted-foreground">
                {locale === "zh-CN" ? "成功" : "Success"}: {success}/{total}
              </div>
            </TooltipContent>
          </Tooltip>
        );
      })}
    </div>
  );
}

function ChannelHealthBars({
  site,
  channel,
  channelSummary,
  health,
  locale,
  timeZone,
  busyId,
  onClearChannelCooldown,
}: {
  site: SiteRow;
  channel: HealthPreviewChannel;
  channelSummary?: ChannelRuntimeSummary;
  health?: ChannelHealthRow;
  locale: Locale;
  timeZone?: string;
  busyId?: string | null;
  onClearChannelCooldown?: (channelId: string) => void;
}) {
  const buckets = (channelSummary?.health_buckets ?? []).slice(
    -CHANNEL_HEALTH_BUCKET_COUNT,
  );
  const coolingBadge = resolveCoolingBadge(
    site,
    channel.channelId,
    health,
    locale,
  );
  const segments = [
    ...Array.from(
      {
        length: Math.max(CHANNEL_HEALTH_BUCKET_COUNT - buckets.length, 0),
      },
      (_, index) => ({
        key: `${channel.channelId}-placeholder-${index}`,
        bucket: null as ChannelHealthBucket | null,
      }),
    ),
    ...buckets.map((bucket, index) => ({
      key: `${channel.channelId}-bucket-${bucket.started_at}-${index}`,
      bucket,
    })),
  ];

  return (
    <div className="flex min-w-0 flex-col gap-1.5 rounded-lg border border-border/70 px-2.5 py-2">
      <div className="flex min-w-0 items-center justify-between gap-2">
        <span className="min-w-0 truncate text-[11px] font-medium text-foreground">
          {healthPreviewChannelLabel(channel, locale)}
        </span>
        {coolingBadge ? (
          <div className="flex shrink-0 items-center gap-1">
            <Badge
              variant="outline"
              title={coolingBadge.title}
              className={cn(
                "max-w-28 truncate px-1.5 py-0 text-[10px]",
                coolingBadge.className,
              )}
            >
              {coolingBadge.label}
            </Badge>
            {onClearChannelCooldown ? (
              <Button
                type="button"
                variant="ghost"
                size="xs"
                className="h-5 px-1.5 text-[10px]"
                disabled={busyId === channel.channelId}
                onClick={(event) => {
                  event.stopPropagation();
                  onClearChannelCooldown(channel.channelId);
                }}
              >
                {locale === "zh-CN" ? "取消" : "Clear"}
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>
      <HealthBarSegments
        segments={segments}
        locale={locale}
        timeZone={timeZone}
      />
    </div>
  );
}

export function SiteHealthPreview({
  site,
  summary,
  healthByChannelId,
  locale,
  timeZone,
  busyId,
  onClearChannelCooldown,
}: {
  site: SiteRow;
  summary?: SiteRuntimeSummary;
  healthByChannelId: Map<string, ChannelHealthRow>;
  locale: Locale;
  timeZone?: string;
  busyId?: string | null;
  onClearChannelCooldown?: (channelId: string) => void;
}) {
  const channels = siteHealthPreviewChannels(site);
  const summaryByChannelId = new Map(
    (summary?.channel_summaries ?? []).map(
      (item) => [item.channel_id, item] as const,
    ),
  );
  const aggregated = aggregateHealthBuckets(channels, summaryByChannelId);
  const coolingBadge = pickCoolingBadge(
    site,
    channels,
    healthByChannelId,
    locale,
  );
  const summarySegments = aggregated.map((bucket, index) => ({
    key: `summary-${index}`,
    bucket,
  }));

  if (!channels.length) {
    return (
      <div className="flex h-6 items-center text-xs text-muted-foreground">
        {locale === "zh-CN" ? "暂无健康数据" : "No health data"}
      </div>
    );
  }

  return (
    <div
      className="flex h-6 min-w-0 items-center gap-2"
      onClick={(event) => event.stopPropagation()}
      onKeyDown={(event) => event.stopPropagation()}
    >
      <Popover>
        <PopoverTrigger asChild>
          <button
            type="button"
            className="flex min-w-0 flex-1 items-center gap-2 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring/70 focus-visible:ring-offset-1"
            aria-label={
              locale === "zh-CN" ? "查看健康详情" : "View health details"
            }
          >
            <HealthBarSegments
              segments={summarySegments}
              locale={locale}
              timeZone={timeZone}
              compact
              interactive={false}
            />
          </button>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          sideOffset={8}
          className="w-80 gap-2 p-3"
          onClick={(event) => event.stopPropagation()}
        >
          <div className="text-xs font-medium text-foreground">
            {locale === "zh-CN" ? "健康状态" : "Health"}
          </div>
          <div className="flex max-h-72 flex-col gap-2 overflow-y-auto pr-0.5">
            {channels.map((channel) => (
              <ChannelHealthBars
                key={channel.channelId}
                site={site}
                channel={channel}
                channelSummary={summaryByChannelId.get(channel.channelId)}
                health={healthByChannelId.get(channel.channelId)}
                locale={locale}
                timeZone={timeZone}
                busyId={busyId}
                onClearChannelCooldown={onClearChannelCooldown}
              />
            ))}
          </div>
        </PopoverContent>
      </Popover>

      {coolingBadge ? (
        <div className="flex shrink-0 items-center gap-1">
          <Badge
            variant="outline"
            title={coolingBadge.title}
            className={cn(
              "max-w-24 truncate px-1.5 py-0 text-[10px]",
              coolingBadge.className,
            )}
          >
            {coolingBadge.label}
          </Badge>
          {onClearChannelCooldown ? (
            <Button
              type="button"
              variant="ghost"
              size="xs"
              className="h-5 px-1.5 text-[10px]"
              disabled={busyId === coolingBadge.channelId}
              onClick={(event) => {
                event.stopPropagation();
                onClearChannelCooldown(coolingBadge.channelId);
              }}
            >
              {locale === "zh-CN" ? "取消" : "Clear"}
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
