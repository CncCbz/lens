"use client";

import { Plus, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { compactProtocolLabel } from "@/lib/protocols";
import {
  emptyMatchLeaf,
  emptyMatchRule,
  type FormItem,
  type MatchActionForm,
  type MatchNodeForm,
  type MatchOp,
  type MatchRuleForm,
} from "./shared";

const PATH_OPTIONS = [
  "channel",
  "header.User-Agent",
  "body.reasoning_effort",
  "body.reasoning.effort",
];

type ChannelOption = { id: string; label: string };

function channelOptions(items: FormItem[]): ChannelOption[] {
  const seen = new Map<string, ChannelOption>();
  for (const item of items) {
    if (seen.has(item.channel_id)) continue;
    const protocol = item.protocol ? compactProtocolLabel(item.protocol) : "";
    const name = item.channel_name || item.channel_id;
    seen.set(item.channel_id, {
      id: item.channel_id,
      label: protocol ? `${name} / ${protocol}` : name,
    });
  }
  return [...seen.values()];
}

export function MatchOverridesEditor({
  locale,
  items,
  rules,
  onChange,
}: {
  locale: "zh-CN" | "en-US";
  items: FormItem[];
  rules: MatchRuleForm[];
  onChange: (rules: MatchRuleForm[]) => void;
}) {
  const channels = channelOptions(items);
  const zh = locale === "zh-CN";
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 text-base font-semibold text-foreground">
        {zh ? "匹配覆盖" : "Match overrides"}
        <Badge variant={rules.length ? "default" : "secondary"}>
          {zh
            ? rules.length
              ? `${rules.length} 条`
              : "未配置"
            : rules.length
              ? `${rules.length}`
              : "Not set"}
        </Badge>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="ml-auto"
          onClick={() => onChange([...rules, emptyMatchRule()])}
        >
          <Plus className="mr-1 size-3.5" />
          {zh ? "规则" : "Rule"}
        </Button>
      </div>
      <datalist id="group-match-paths">
        {PATH_OPTIONS.map((path) => (
          <option key={path} value={path} />
        ))}
      </datalist>
      {rules.map((rule, index) => (
        <div key={index} className="flex flex-col gap-2 rounded-md border p-3">
          <div className="flex items-center gap-2 text-sm font-medium">
            #{index + 1}
            <span className="text-muted-foreground">{zh ? "匹配" : "If"}</span>
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              className="ml-auto"
              onClick={() => onChange(rules.filter((_, i) => i !== index))}
            >
              <X />
            </Button>
          </div>
          <MatchNodeEditor
            locale={locale}
            node={rule.if}
            channels={channels}
            depth={0}
            onChange={(next) =>
              onChange(
                rules.map((item, i) =>
                  i === index ? { ...item, if: next } : item,
                ),
              )
            }
          />
          <div className="text-sm font-medium">{zh ? "则" : "Then"}</div>
          {rule.then.map((action, actionIndex) => (
            <ActionRow
              key={actionIndex}
              locale={locale}
              action={action}
              onChange={(next) =>
                onChange(
                  rules.map((item, i) =>
                    i === index
                      ? {
                          ...item,
                          then: item.then.map((row, j) =>
                            j === actionIndex ? next : row,
                          ),
                        }
                      : item,
                  ),
                )
              }
              onRemove={() =>
                onChange(
                  rules.map((item, i) =>
                    i === index
                      ? {
                          ...item,
                          then: item.then.filter((_, j) => j !== actionIndex),
                        }
                      : item,
                  ),
                )
              }
            />
          ))}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() =>
              onChange(
                rules.map((item, i) =>
                  i === index
                    ? {
                        ...item,
                        then: [
                          ...item.then,
                          { path: "body.reasoning_effort", value: "" },
                        ],
                      }
                    : item,
                ),
              )
            }
          >
            <Plus className="mr-1 size-3.5" />
            {zh ? "赋值" : "Set"}
          </Button>
        </div>
      ))}
    </div>
  );
}

function MatchNodeEditor({
  locale,
  node,
  channels,
  depth,
  onChange,
}: {
  locale: "zh-CN" | "en-US";
  node: MatchNodeForm;
  channels: ChannelOption[];
  depth: number;
  onChange: (node: MatchNodeForm) => void;
}) {
  const zh = locale === "zh-CN";
  if (node.type === "leaf") {
    return (
      <LeafRow
        locale={locale}
        node={node}
        channels={channels}
        onChange={onChange}
      />
    );
  }
  return (
    <div className="flex flex-col gap-2 rounded-md border border-dashed p-2">
      <Select
        value={node.type}
        onValueChange={(value) =>
          onChange({ ...node, type: value as "all" | "any" })
        }
      >
        <SelectTrigger className="w-40">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">{zh ? "全部且" : "All (and)"}</SelectItem>
          <SelectItem value="any">{zh ? "任一或" : "Any (or)"}</SelectItem>
        </SelectContent>
      </Select>
      {node.children.map((child, index) => (
        <div key={index} className="flex items-start gap-1">
          <div className="min-w-0 flex-1">
            <MatchNodeEditor
              locale={locale}
              node={child}
              channels={channels}
              depth={depth + 1}
              onChange={(next) =>
                onChange({
                  ...node,
                  children: node.children.map((item, i) =>
                    i === index ? next : item,
                  ),
                })
              }
            />
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            onClick={() =>
              onChange({
                ...node,
                children: node.children.filter((_, i) => i !== index),
              })
            }
          >
            <X />
          </Button>
        </div>
      ))}
      <div className="flex gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() =>
            onChange({
              ...node,
              children: [...node.children, emptyMatchLeaf()],
            })
          }
        >
          <Plus className="mr-1 size-3.5" />
          {zh ? "条件" : "Condition"}
        </Button>
        {depth < 2 ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() =>
              onChange({
                ...node,
                children: [
                  ...node.children,
                  { type: "any", children: [emptyMatchLeaf()] },
                ],
              })
            }
          >
            <Plus className="mr-1 size-3.5" />
            {zh ? "条件组" : "Group"}
          </Button>
        ) : null}
      </div>
    </div>
  );
}

function LeafRow({
  locale,
  node,
  channels,
  onChange,
}: {
  locale: "zh-CN" | "en-US";
  node: Extract<MatchNodeForm, { type: "leaf" }>;
  channels: ChannelOption[];
  onChange: (node: MatchNodeForm) => void;
}) {
  const zh = locale === "zh-CN";
  return (
    <div className="grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,1.4fr)_5.5rem_minmax(0,1fr)]">
      <PathInput
        value={node.path}
        onChange={(path) => onChange({ ...node, path })}
      />
      <Select
        value={node.op}
        onValueChange={(value) => onChange({ ...node, op: value as MatchOp })}
      >
        <SelectTrigger>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="is">{zh ? "是" : "is"}</SelectItem>
          <SelectItem value="is_not">{zh ? "不是" : "is not"}</SelectItem>
        </SelectContent>
      </Select>
      {node.path === "channel" && channels.length ? (
        <Select
          value={node.value}
          onValueChange={(value) => onChange({ ...node, value })}
        >
          <SelectTrigger>
            <SelectValue placeholder={zh ? "渠道" : "Channel"} />
          </SelectTrigger>
          <SelectContent>
            {channels.map((channel) => (
              <SelectItem key={channel.id} value={channel.id}>
                {channel.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : (
        <Input
          value={node.value}
          onChange={(event) => onChange({ ...node, value: event.target.value })}
        />
      )}
    </div>
  );
}

function ActionRow({
  locale,
  action,
  onChange,
  onRemove,
}: {
  locale: "zh-CN" | "en-US";
  action: MatchActionForm;
  onChange: (action: MatchActionForm) => void;
  onRemove: () => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <div className="grid min-w-0 flex-1 grid-cols-1 gap-2 sm:grid-cols-2">
        <PathInput
          value={action.path}
          onChange={(path) => onChange({ ...action, path })}
        />
        <Input
          value={action.value}
          onChange={(event) =>
            onChange({ ...action, value: event.target.value })
          }
          placeholder={locale === "zh-CN" ? "值" : "Value"}
        />
      </div>
      <Button type="button" variant="ghost" size="icon-xs" onClick={onRemove}>
        <X />
      </Button>
    </div>
  );
}

function PathInput({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <Input
      list="group-match-paths"
      value={value}
      onChange={(event) => onChange(event.target.value)}
      placeholder="body.reasoning.effort"
      className="font-mono text-xs"
    />
  );
}
