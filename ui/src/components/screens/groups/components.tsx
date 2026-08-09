"use client";

import type { ReactNode } from "react";
import {
  AlertCircle,
  Check,
  GripVertical,
  Plus,
  RefreshCcw,
  X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type {
  ModelGroupCandidateItem,
  ProtocolKind,
  RoutingStrategy,
} from "@/lib/api";
import { isItemValidForProtocols } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  credentialDisplayLabel,
  foldedMemberSourceLabel,
  formatMoney,
  metricLabel,
  protocolBadgeClassName,
  protocolLabel,
  strategyOptions,
  type FoldedMember,
} from "./shared";

export { SeriesChip } from "@/lib/model-prefix";

export function CompactPriceSummary({
  locale,
  inputPrice,
  outputPrice,
  cacheReadPrice,
  cacheWritePrice,
}: {
  locale: "zh-CN" | "en-US";
  inputPrice: number;
  outputPrice: number;
  cacheReadPrice: number;
  cacheWritePrice: number;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div className="mt-2 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
          <span>
            {metricLabel("input", locale)} ${formatMoney(inputPrice)}
          </span>
          <span>
            {metricLabel("output", locale)} ${formatMoney(outputPrice)}
          </span>
          <span>
            {metricLabel("cache_read", locale)} ${formatMoney(cacheReadPrice)}
          </span>
          <span>
            {metricLabel("cache_write", locale)} ${formatMoney(cacheWritePrice)}
          </span>
        </div>
      </TooltipTrigger>
      <TooltipContent side="top" align="start">
        <div className="grid gap-1">
          <div>
            {metricLabel("input", locale)}: ${formatMoney(inputPrice)} / 1M
            tokens
          </div>
          <div>
            {metricLabel("output", locale)}: ${formatMoney(outputPrice)} / 1M
            tokens
          </div>
          <div>
            {metricLabel("cache_read", locale)}: ${formatMoney(cacheReadPrice)}{" "}
            / 1M tokens
          </div>
          <div>
            {metricLabel("cache_write", locale)}: $
            {formatMoney(cacheWritePrice)} / 1M tokens
          </div>
        </div>
      </TooltipContent>
    </Tooltip>
  );
}

export function EditablePriceRow({
  locale,
  primaryLabel,
  primaryValue,
  secondaryLabel,
  secondaryValue,
  onPrimaryChange,
  onSecondaryChange,
}: {
  locale: "zh-CN" | "en-US";
  primaryLabel: "input" | "output";
  primaryValue: string;
  secondaryLabel: "cache_read" | "cache_write";
  secondaryValue: string;
  onPrimaryChange: (value: string) => void;
  onSecondaryChange: (value: string) => void;
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Field className="min-w-0">
        <FieldLabel>${metricLabel(primaryLabel, locale)}</FieldLabel>
        <Input
          className="mt-2"
          value={primaryValue}
          onChange={(event) => onPrimaryChange(event.target.value)}
        />
      </Field>

      <Field className="min-w-0">
        <FieldLabel>${metricLabel(secondaryLabel, locale)}</FieldLabel>
        <Input
          className="mt-2"
          value={secondaryValue}
          onChange={(event) => onSecondaryChange(event.target.value)}
        />
      </Field>
    </div>
  );
}

export function StrategyToggle({
  value,
  locale,
  disabled = false,
  size = "default",
  className,
  onChange,
}: {
  value: RoutingStrategy;
  locale: "zh-CN" | "en-US";
  disabled?: boolean;
  size?: "default" | "sm";
  className?: string;
  onChange: (value: RoutingStrategy) => void;
}) {
  return (
    <ToggleGroup
      type="single"
      value={value}
      onValueChange={(nextValue) => {
        if (nextValue) {
          onChange(nextValue as RoutingStrategy);
        }
      }}
      variant="outline"
      size={size}
      spacing={1}
      className={cn("max-w-full flex-wrap", className)}
    >
      {strategyOptions.map((option) => (
        <ToggleGroupItem
          key={option.value}
          value={option.value}
          disabled={disabled}
          className="max-w-full"
        >
          {locale === "zh-CN" ? option.zh : option.en}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}

export function CandidateRow({
  candidate,
  active,
  selectedProtocols,
  locale,
  onClick,
}: {
  candidate: ModelGroupCandidateItem;
  active: boolean;
  selectedProtocols: ProtocolKind[];
  locale: "zh-CN" | "en-US";
  onClick: () => void;
}) {
  const nativeProtocols = candidate.protocols;
  const credentialLabel = credentialDisplayLabel(candidate, locale);

  return (
    <Button
      type="button"
      variant="ghost"
      className={cn(
        "h-auto min-h-8 w-full justify-between rounded-md px-3 py-1.5 text-left",
        active ? "cursor-not-allowed opacity-60" : "hover:bg-muted",
      )}
      onClick={onClick}
      disabled={active}
    >
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium text-foreground">
          {candidate.model_name}
        </div>
        <div className="truncate text-xs text-muted-foreground">
          {credentialLabel}
        </div>
      </div>
      <div className="flex min-w-0 shrink-0 flex-wrap items-center justify-end gap-1.5">
        {nativeProtocols.map((p) => {
          const usable = isItemValidForProtocols(p, selectedProtocols);
          return (
            <Badge
              key={p}
              variant="outline"
              className={cn(
                "px-1.5 py-0 text-[10px] font-normal",
                usable
                  ? protocolBadgeClassName(p)
                  : "border-transparent bg-muted/50 text-muted-foreground/50",
              )}
            >
              {protocolLabel(p, locale)}
            </Badge>
          );
        })}
        <span className="text-muted-foreground">
          {active ? (
            <Check size={15} className="text-primary" />
          ) : (
            <Plus size={15} />
          )}
        </span>
      </div>
    </Button>
  );
}

export function FoldedMemberRow({
  member,
  index,
  mode,
  weight,
  onWeightChange,
  sharePct,
  dragging,
  busy,
  testingDisabled,
  onTest,
  onToggle,
  onRemove,
  draggable = true,
  onDragStart,
  onDragEnter,
  onDragEnd,
  locale,
}: {
  member: FoldedMember;
  index: number;
  mode: RoutingStrategy;
  weight: number;
  onWeightChange: (value: number) => void;
  sharePct?: number;
  dragging: boolean;
  busy: boolean;
  testingDisabled?: boolean;
  onTest?: () => void;
  onToggle: () => void;
  onRemove: () => void;
  draggable?: boolean;
  onDragStart: () => void;
  onDragEnter: () => void;
  onDragEnd: () => void;
  locale: "zh-CN" | "en-US";
}) {
  const sourceLabel = foldedMemberSourceLabel(member, locale);
  const showWeightInput =
    mode === "round_robin" || mode === "priority_weighted";

  return (
    <div
      draggable={draggable}
      onDragStart={onDragStart}
      onDragEnter={onDragEnter}
      onDragOver={(event) => event.preventDefault()}
      onDragEnd={onDragEnd}
      className={cn(
        "flex min-w-0 items-center gap-2 border-b px-2.5 py-2 transition last:border-b-0",
        dragging && "opacity-60 shadow-sm",
        !member.enabled && "opacity-55",
        member.invalid && "border border-destructive bg-destructive/10",
      )}
    >
      <span className="grid h-5 w-5 shrink-0 place-items-center rounded-md bg-primary/10 text-xs font-semibold text-primary">
        {index + 1}
      </span>
      <span className="cursor-grab text-muted-foreground active:cursor-grabbing">
        <GripVertical size={14} />
      </span>
      {mode === "failover" ? (
        index === 0 ? (
          <span className="shrink-0 rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-semibold text-primary">
            {locale === "zh-CN" ? "主" : "Primary"}
          </span>
        ) : (
          <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
            {locale === "zh-CN" ? `备 ${index}` : `Backup ${index}`}
          </span>
        )
      ) : null}
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium text-foreground">
          {member.model_name}
        </div>
        <div className="truncate text-xs text-muted-foreground">
          {sourceLabel}
          {!member.enabled
            ? ` · ${locale === "zh-CN" ? "已关闭" : "Disabled"}`
            : ""}
        </div>
      </div>
      {showWeightInput ? (
        <>
          <div className="flex w-[120px] shrink-0 items-center justify-center gap-1.5">
            <span className="text-xs text-muted-foreground">
              {locale === "zh-CN" ? "权重" : "Weight"}
            </span>
            <Input
              type="number"
              min={1}
              value={weight}
              onChange={(event) => onWeightChange(Number(event.target.value))}
              className="h-7 w-14 px-1 text-center text-sm"
            />
          </div>
          {mode === "round_robin" ? (
            <span className="w-12 shrink-0 text-right text-[11px] text-muted-foreground">
              {sharePct != null ? `${sharePct}%` : ""}
            </span>
          ) : null}
        </>
      ) : null}
      {onTest ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={busy || testingDisabled}
          onClick={onTest}
        >
          <RefreshCcw data-icon="inline-start" />
          {locale === "zh-CN" ? "测试" : "Test"}
        </Button>
      ) : null}
      <div className="flex h-8 w-8 items-center justify-center">
        <Switch
          checked={member.enabled}
          disabled={busy}
          onCheckedChange={onToggle}
        />
      </div>
      {member.invalid ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="grid h-8 w-8 shrink-0 place-items-center text-destructive">
              <AlertCircle size={15} />
            </span>
          </TooltipTrigger>
          <TooltipContent>
            {locale === "zh-CN"
              ? "不适用于当前所选的对外协议"
              : "Invalid for current protocols"}
          </TooltipContent>
        </Tooltip>
      ) : null}
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="text-muted-foreground hover:text-destructive"
        onClick={onRemove}
      >
        <X size={13} />
      </Button>
    </div>
  );
}

export function PriorityLevelCard({
  priority,
  isTop,
  memberCount,
  locale,
  onPriorityChange,
  onRemoveLevel,
  children,
}: {
  priority: number;
  isTop: boolean;
  memberCount: number;
  locale: "zh-CN" | "en-US";
  onPriorityChange: (value: number) => void;
  onRemoveLevel: () => void;
  children: ReactNode;
}) {
  return (
    <div className="overflow-hidden rounded-lg border bg-background">
      <div className="flex items-center gap-2 border-b bg-muted/30 px-3 py-1.5">
        <span className="rounded-md bg-primary/10 px-2 py-0.5 text-[11px] font-semibold text-primary">
          {locale === "zh-CN" ? "优先级层" : "Priority level"}
        </span>
        <Input
          type="number"
          min={0}
          value={priority}
          onChange={(event) => onPriorityChange(Number(event.target.value))}
          className="h-7 w-14 px-1 text-center text-sm"
        />
        <span className="text-[11px] text-muted-foreground">
          {isTop
            ? locale === "zh-CN"
              ? "最高层 · 优先使用"
              : "Top level · used first"
            : locale === "zh-CN"
              ? "备用层"
              : "Backup level"}
        </span>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="ml-auto h-6 px-1.5 text-xs text-muted-foreground hover:text-destructive"
          onClick={onRemoveLevel}
        >
          <X size={12} />
          {locale === "zh-CN" ? "删除层" : "Remove level"}
        </Button>
      </div>
      <div className="flex flex-col">{children}</div>
      {memberCount === 0 ? (
        <p className="px-3 py-4 text-center text-sm text-muted-foreground">
          {locale === "zh-CN" ? "该层暂无成员" : "No members in this level"}
        </p>
      ) : null}
    </div>
  );
}
