"use client";

import { Plus, RotateCcw, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  createErrorPolicyRow,
  policyKeyLabel,
  SUGGESTED_ERROR_POLICY_KEYS,
} from "@/lib/error-policy-config";
import { titleForLocale, type Locale } from "@/lib/i18n";
import type {
  RouterErrorPolicyDraft,
  RouterErrorPolicyRow,
} from "@/lib/settings-types";

type Props = {
  locale: Locale;
  draft: RouterErrorPolicyDraft;
  globals: { threshold: number; cooldown: number; maxCooldown: number };
  hint?: string;
  onChange: (draft: RouterErrorPolicyDraft) => void;
};

function HeaderCell({
  locale,
  zh,
  en,
  tipZh,
  tipEn,
}: {
  locale: Locale;
  zh: string;
  en: string;
  tipZh: string;
  tipEn: string;
}) {
  return (
    <th className="whitespace-nowrap px-2 py-2 font-medium">
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="cursor-help border-b border-dotted border-muted-foreground/60">
            {titleForLocale(locale, zh, en)}
          </span>
        </TooltipTrigger>
        <TooltipContent>{titleForLocale(locale, tipZh, tipEn)}</TooltipContent>
      </Tooltip>
    </th>
  );
}

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
  hint,
  onChange,
}: Props) {
  function addRow() {
    const used = new Set(draft.rows.map((row) => row.key));
    let key = SUGGESTED_ERROR_POLICY_KEYS.find((item) => !used.has(item)) ?? "";
    if (!key) {
      let code = 418;
      while (used.has(String(code)) && code <= 599) code += 1;
      key = String(Math.min(code, 599));
    }
    if (used.has(key)) return;
    onChange({
      rows: [
        ...draft.rows,
        { ...createErrorPolicyRow(key, globals), overridden: true },
      ],
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
    <div className="min-w-0 space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium">
            {titleForLocale(locale, "错误策略", "Error policies")}
          </div>
          <p className="text-xs text-muted-foreground">
            {hint ??
              titleForLocale(
                locale,
                "仅列出类别与特殊状态码。未列出的状态沿用 4xx/5xx。",
                "Categories and special codes only. Other statuses inherit 4xx/5xx.",
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

      <div className="max-w-full overflow-x-auto rounded-lg border">
        <table className="w-max min-w-full border-collapse text-sm">
          <thead>
            <tr className="border-b bg-muted/40 text-left text-[11px] text-muted-foreground">
              <HeaderCell
                locale={locale}
                zh="状态码"
                en="Status code"
                tipZh="HTTP 状态码，或 timeout / transport_error"
                tipEn="HTTP status, or timeout / transport_error"
              />
              <HeaderCell
                locale={locale}
                zh="失败后换渠道"
                en="Switch channel"
                tipZh="打开后这个错误会继续试其他渠道"
                tipEn="On: try other channels after this error"
              />
              <HeaderCell
                locale={locale}
                zh="当前渠道重试"
                en="Retry here"
                tipZh="换渠道前，在当前目标再试几次"
                tipEn="Retries on the same target before switching"
              />
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
                        placeholder="502"
                        className="h-8 w-28 font-mono text-xs"
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
