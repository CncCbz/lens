"use client";

import { Plus, RotateCcw, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  createErrorPolicyRow,
  ERROR_POLICY_SCOPE_OPTIONS,
  policyKeyLabel,
} from "@/lib/error-policy-config";
import { titleForLocale, type Locale } from "@/lib/i18n";
import type {
  RouterErrorCooldownScope,
  RouterErrorPolicyDraft,
  RouterErrorPolicyRow,
} from "@/lib/settings-types";

type Props = {
  locale: Locale;
  draft: RouterErrorPolicyDraft;
  globals: { threshold: number; cooldown: number; maxCooldown: number };
  onChange: (draft: RouterErrorPolicyDraft) => void;
};

function updateRow(
  draft: RouterErrorPolicyDraft,
  key: string,
  patch: Partial<RouterErrorPolicyRow>,
): RouterErrorPolicyDraft {
  return {
    rows: draft.rows.map((row) =>
      row.key === key ? { ...row, ...patch, overridden: true } : row,
    ),
  };
}

export function ErrorPolicySettings({
  locale,
  draft,
  globals,
  onChange,
}: Props) {
  function addRow() {
    let code = 418;
    while (draft.rows.some((row) => row.key === String(code)) && code <= 599) {
      code += 1;
    }
    const key = String(Math.min(code, 599));
    if (draft.rows.some((row) => row.key === key)) return;
    onChange({
      rows: [...draft.rows, { ...createErrorPolicyRow(key, globals), overridden: true }],
    });
  }

  function resetRow(key: string) {
    const fresh = createErrorPolicyRow(key, globals);
    onChange({
      rows: draft.rows.map((row) => (row.key === key ? fresh : row)),
    });
  }

  function removeRow(key: string) {
    onChange({ rows: draft.rows.filter((row) => row.key !== key) });
  }

  const loc = locale === "zh-CN" ? "zh-CN" : "en-US";

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium">
            {titleForLocale(locale, "错误策略", "Error policies")}
          </div>
          <p className="text-xs text-muted-foreground">
            {titleForLocale(
              locale,
              "仅列出类别与特殊状态码。未列出的状态沿用 4xx/5xx；5xx 数值默认同上方全局项。",
              "Categories and special codes only. Other statuses inherit 4xx/5xx; 5xx numbers follow globals above.",
            )}
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="shrink-0"
          onClick={addRow}
        >
          <Plus className="size-4" />
          {titleForLocale(locale, "添加状态码", "Add status")}
        </Button>
      </div>

      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full min-w-[720px] border-collapse text-sm">
          <thead>
            <tr className="border-b bg-muted/40 text-left text-[11px] text-muted-foreground">
              <th className="px-3 py-2 font-medium">
                {titleForLocale(locale, "状态", "Status")}
              </th>
              <th className="px-2 py-2 font-medium">
                {titleForLocale(locale, "故障转移", "Fallback")}
              </th>
              <th className="px-2 py-2 font-medium">
                {titleForLocale(locale, "遵循等待", "Retry-After")}
              </th>
              <th className="px-2 py-2 font-medium">
                {titleForLocale(locale, "冷却范围", "Scope")}
              </th>
              <th className="px-2 py-2 font-medium">
                {titleForLocale(locale, "同目标重试", "Retries")}
              </th>
              <th className="px-2 py-2 font-medium">
                {titleForLocale(locale, "失败阈值", "Threshold")}
              </th>
              <th className="px-2 py-2 font-medium">
                {titleForLocale(locale, "基础秒", "Base")}
              </th>
              <th className="px-2 py-2 font-medium">
                {titleForLocale(locale, "最大秒", "Max")}
              </th>
              <th className="px-2 py-2 font-medium" />
            </tr>
          </thead>
          <tbody>
            {draft.rows.map((row) => (
              <tr
                key={row.key}
                className="border-b last:border-b-0 hover:bg-muted/20"
              >
                <td className="px-3 py-2 align-middle">
                  <div className="flex items-center gap-2">
                    {row.isDefault ? (
                      <span className="font-mono text-xs">
                        {policyKeyLabel(row.key, loc)}
                      </span>
                    ) : (
                      <Input
                        value={row.key}
                        onChange={(event) =>
                          onChange(
                            updateRow(draft, row.key, {
                              key: event.target.value.trim(),
                            }),
                          )
                        }
                        className="h-8 w-20 font-mono text-xs"
                      />
                    )}
                    {row.overridden ? (
                      <span className="text-[10px] text-amber-600">
                        {titleForLocale(locale, "已改", "Edited")}
                      </span>
                    ) : null}
                  </div>
                </td>
                <td className="px-2 py-2 align-middle">
                  <Switch
                    checked={row.fallback}
                    onCheckedChange={(checked) =>
                      onChange(updateRow(draft, row.key, { fallback: checked }))
                    }
                  />
                </td>
                <td className="px-2 py-2 align-middle">
                  <Switch
                    checked={row.respect_retry_after}
                    onCheckedChange={(checked) =>
                      onChange(
                        updateRow(draft, row.key, {
                          respect_retry_after: checked,
                        }),
                      )
                    }
                  />
                </td>
                <td className="px-2 py-2 align-middle">
                  <Select
                    value={row.cooldown_scope}
                    onValueChange={(value) =>
                      onChange(
                        updateRow(draft, row.key, {
                          cooldown_scope: value as RouterErrorCooldownScope,
                        }),
                      )
                    }
                  >
                    <SelectTrigger className="h-8 w-[6.5rem]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {ERROR_POLICY_SCOPE_OPTIONS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {locale === "zh-CN" ? option.zh : option.en}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </td>
                <td className="px-2 py-2 align-middle">
                  <Input
                    type="number"
                    min={0}
                    max={5}
                    value={row.same_target_retries}
                    onChange={(event) =>
                      onChange(
                        updateRow(draft, row.key, {
                          same_target_retries: Number(event.target.value || 0),
                        }),
                      )
                    }
                    className="h-8 w-16"
                  />
                </td>
                <td className="px-2 py-2 align-middle">
                  <Input
                    type="number"
                    min={1}
                    max={100}
                    value={row.failure_threshold}
                    onChange={(event) =>
                      onChange(
                        updateRow(draft, row.key, {
                          failure_threshold: Number(event.target.value || 1),
                        }),
                      )
                    }
                    className="h-8 w-16"
                  />
                </td>
                <td className="px-2 py-2 align-middle">
                  <Input
                    type="number"
                    min={0}
                    value={row.cooldown_seconds}
                    onChange={(event) =>
                      onChange(
                        updateRow(draft, row.key, {
                          cooldown_seconds: Number(event.target.value || 0),
                        }),
                      )
                    }
                    className="h-8 w-20"
                  />
                </td>
                <td className="px-2 py-2 align-middle">
                  <Input
                    type="number"
                    min={0}
                    value={row.max_cooldown_seconds}
                    onChange={(event) =>
                      onChange(
                        updateRow(draft, row.key, {
                          max_cooldown_seconds: Number(event.target.value || 0),
                        }),
                      )
                    }
                    className="h-8 w-20"
                  />
                </td>
                <td className="px-2 py-2 align-middle">
                  <div className="flex items-center justify-end gap-0.5">
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          type="button"
                          size="icon-sm"
                          variant="ghost"
                          onClick={() => resetRow(row.key)}
                        >
                          <RotateCcw className="size-3.5" />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>
                        {titleForLocale(locale, "恢复默认", "Reset")}
                      </TooltipContent>
                    </Tooltip>
                    {!row.isDefault ? (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            type="button"
                            size="icon-sm"
                            variant="ghost"
                            onClick={() => removeRow(row.key)}
                          >
                            <Trash2 className="size-3.5" />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>
                          {titleForLocale(locale, "删除", "Remove")}
                        </TooltipContent>
                      </Tooltip>
                    ) : null}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
