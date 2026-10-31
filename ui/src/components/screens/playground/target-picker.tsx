"use client";

import { useMemo, useState } from "react";
import { Check, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import {
  uniqueOptions,
  type PlaygroundModel,
  type PlaygroundOption,
} from "./catalog";

type TargetPickerProps = {
  models: PlaygroundModel[];
  selectedKey: string | null;
  onConfirm: (model: PlaygroundModel) => void;
  onCancel: () => void;
};

function matchesSearch(model: PlaygroundModel, query: string): boolean {
  if (!query) {
    return true;
  }
  return [model.channelName, model.groupName, model.modelName].some((value) =>
    value.toLowerCase().includes(query),
  );
}

export function TargetPicker({
  models,
  selectedKey,
  onConfirm,
  onCancel,
}: TargetPickerProps) {
  const selected = models.find((model) => model.key === selectedKey) ?? null;
  const [search, setSearch] = useState("");
  const [enabledOnly, setEnabledOnly] = useState(false);
  const [channelId, setChannelId] = useState(selected?.channelId ?? "");
  const [groupId, setGroupId] = useState(selected?.groupId ?? "");
  const [modelKey, setModelKey] = useState(selected?.key ?? "");

  const query = search.trim().toLowerCase();

  const visible = useMemo(() => {
    const filtered = enabledOnly
      ? models.filter((model) => model.enabled)
      : models;
    return filtered.filter((model) => matchesSearch(model, query));
  }, [enabledOnly, models, query]);

  const channels = useMemo(
    () =>
      uniqueOptions(
        visible,
        (model) => model.channelId,
        (model) => model.channelName,
      ),
    [visible],
  );
  const effectiveChannelId = channels.some((item) => item.id === channelId)
    ? channelId
    : (channels[0]?.id ?? "");

  const groups = useMemo(
    () =>
      uniqueOptions(
        visible.filter((model) => model.channelId === effectiveChannelId),
        (model) => model.groupId,
        (model) => model.groupName,
      ),
    [effectiveChannelId, visible],
  );
  const effectiveGroupId = groups.some((item) => item.id === groupId)
    ? groupId
    : (groups[0]?.id ?? "");

  const modelsInGroup = useMemo(
    () =>
      visible.filter(
        (model) =>
          model.channelId === effectiveChannelId &&
          model.groupId === effectiveGroupId,
      ),
    [effectiveChannelId, effectiveGroupId, visible],
  );
  const effectiveModelKey = modelsInGroup.some(
    (model) => model.key === modelKey,
  )
    ? modelKey
    : (modelsInGroup[0]?.key ?? "");

  const chosen = modelsInGroup.find((model) => model.key === effectiveModelKey);
  const channelLabel =
    channels.find((item) => item.id === effectiveChannelId)?.name ?? "";
  const groupLabel =
    groups.find((item) => item.id === effectiveGroupId)?.name ?? "";

  return (
    <div className="flex flex-col">
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <Search className="size-3.5 shrink-0 text-muted-foreground" />
        <Input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="搜索"
          className="h-7 border-0 px-0 text-sm shadow-none focus-visible:ring-0"
        />
        <label className="flex shrink-0 items-center gap-1.5 text-[11px] text-muted-foreground">
          <Switch
            size="sm"
            checked={enabledOnly}
            onCheckedChange={setEnabledOnly}
          />
          仅看可用
        </label>
      </div>

      <div className="grid max-h-72 grid-cols-[minmax(0,9rem)_minmax(0,9rem)_minmax(0,1fr)]">
        <PickerColumn
          title="渠道"
          options={channels}
          activeId={effectiveChannelId}
          onSelect={(id) => {
            setChannelId(id);
            setGroupId("");
            setModelKey("");
          }}
        />
        <PickerColumn
          title="组合"
          options={groups}
          activeId={effectiveGroupId}
          onSelect={(id) => {
            setGroupId(id);
            setModelKey("");
          }}
          bordered
        />
        <div className="flex min-w-0 flex-col border-l">
          <div className="px-2 py-2 text-[11px] text-muted-foreground">
            模型
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-2">
            {modelsInGroup.length === 0 ? (
              <div className="px-2 py-1.5 text-xs text-muted-foreground">
                无可用模型
              </div>
            ) : null}
            {modelsInGroup.map((model) => (
              <button
                key={model.key}
                type="button"
                disabled={!model.enabled}
                onClick={() => setModelKey(model.key)}
                className={cn(
                  "flex w-full min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors",
                  "hover:bg-muted disabled:pointer-events-none disabled:opacity-45",
                  model.key === effectiveModelKey && "bg-accent",
                )}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-mono text-xs">
                    {model.modelName}
                  </span>
                  <span className="block truncate text-[11px] text-muted-foreground">
                    {model.credentialName}
                  </span>
                </span>
                {model.key === effectiveModelKey ? (
                  <Check className="size-3.5 shrink-0 text-primary" />
                ) : null}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2 border-t bg-muted/40 px-3 py-2">
        <span className="min-w-0 truncate text-[11px] text-muted-foreground">
          {channelLabel} / {groupLabel} / {chosen?.modelName ?? ""}
        </span>
        <span className="flex-1" />
        <Button type="button" size="sm" variant="outline" onClick={onCancel}>
          取消
        </Button>
        <Button
          type="button"
          size="sm"
          disabled={!chosen}
          onClick={() => {
            if (chosen) {
              onConfirm(chosen);
            }
          }}
        >
          确定
        </Button>
      </div>
    </div>
  );
}

type PickerColumnProps = {
  title: string;
  options: PlaygroundOption[];
  activeId: string;
  onSelect: (id: string) => void;
  bordered?: boolean;
};

function PickerColumn({
  title,
  options,
  activeId,
  onSelect,
  bordered,
}: PickerColumnProps) {
  return (
    <div className={cn("flex min-w-0 flex-col", bordered && "border-l")}>
      <div className="px-2 py-2 text-[11px] text-muted-foreground">{title}</div>
      <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-2">
        {options.map((option) => (
          <button
            key={option.id}
            type="button"
            onClick={() => onSelect(option.id)}
            className={cn(
              "flex w-full min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-muted",
              option.id === activeId && "bg-accent font-medium",
            )}
          >
            <span className="min-w-0 flex-1 truncate">{option.name}</span>
            {option.id === activeId ? (
              <Check className="size-3.5 shrink-0 text-primary" />
            ) : null}
          </button>
        ))}
      </div>
    </div>
  );
}
