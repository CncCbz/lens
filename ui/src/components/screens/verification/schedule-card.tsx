"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Save } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { AppDialogContent, Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { apiRequest, type SettingItem } from "@/lib/api";
import { titleForLocale, useI18n } from "@/lib/i18n";

import { useVerificationCatalog, type VerificationTarget } from "./catalog";
import {
  ConfidenceBadge,
  useLatestVerdicts,
  useVerificationThresholds,
  verdictKey,
} from "./confidence";

const BATCH_SIZE = "verification_batch_size";
const CHALLENGE_COUNT = "verification_challenge_count";
const CONCURRENCY = "verification_concurrency";
const TIMEOUT_SECONDS = "verification_timeout_seconds";
const MAX_TOKENS = "verification_max_tokens";
const REASONING_EFFORT = "verification_reasoning_effort";
const SELECTED_MODELS = "verification_selected_models";
const TRUST_THRESHOLD = "verification_trust_threshold";
const WARN_THRESHOLD = "verification_warn_threshold";

const EFFORTS = ["none", "minimal", "low", "medium", "high", "xhigh", "max"];

function valueOf(items: SettingItem[], key: string, fallback: string): string {
  return items.find((item) => item.key === key)?.value ?? fallback;
}

function targetSelectionKey(target: VerificationTarget): string {
  return verdictKey(target.comboId, target.credentialId, target.modelName);
}

function parseSelectedModels(raw: string | undefined): string[] | null {
  if (raw === undefined) return null;
  const trimmed = raw.trim();
  if (!trimmed) return null;
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (!Array.isArray(parsed)) return null;
    return parsed
      .map((item) => String(item ?? "").trim())
      .filter((item) => item.length > 0);
  } catch {
    return null;
  }
}

export function VerificationScheduleDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { locale } = useI18n();
  const queryClient = useQueryClient();
  const { targets } = useVerificationCatalog();
  const verdicts = useLatestVerdicts();
  const thresholds = useVerificationThresholds();

  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: () => apiRequest<SettingItem[]>("/admin/settings"),
    staleTime: 5 * 60_000,
  });

  const verifiableTargets = useMemo(
    () => targets.filter((target) => target.verifiable),
    [targets],
  );

  const sites = useMemo(() => {
    const seen = new Map<string, string>();
    for (const target of verifiableTargets) {
      if (!seen.has(target.siteId)) {
        seen.set(target.siteId, target.siteName);
      }
    }
    return Array.from(seen, ([id, name]) => ({ id, name }));
  }, [verifiableTargets]);

  const [batch, setBatch] = useState("5");
  const [concurrency, setConcurrency] = useState("2");
  const [timeoutSeconds, setTimeoutSeconds] = useState("90");
  const [maxTokens, setMaxTokens] = useState("0");
  const [count, setCount] = useState("3");
  const [effort, setEffort] = useState("none");
  const [trust, setTrust] = useState("80");
  const [warn, setWarn] = useState("50");
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [siteFilter, setSiteFilter] = useState("");
  const [search, setSearch] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const items = settingsQuery.data;
    if (!items) return;
    setBatch(valueOf(items, BATCH_SIZE, "5"));
    setConcurrency(valueOf(items, CONCURRENCY, "2"));
    setTimeoutSeconds(valueOf(items, TIMEOUT_SECONDS, "90"));
    setMaxTokens(valueOf(items, MAX_TOKENS, "0"));
    setCount(valueOf(items, CHALLENGE_COUNT, "3"));
    setEffort(valueOf(items, REASONING_EFFORT, "none"));
    setTrust(valueOf(items, TRUST_THRESHOLD, "80"));
    setWarn(valueOf(items, WARN_THRESHOLD, "50"));

    const rawSelected = items.find(
      (item) => item.key === SELECTED_MODELS,
    )?.value;
    const parsedSelected = parseSelectedModels(rawSelected);
    if (parsedSelected !== null) {
      const legacyOrStandard = new Set(parsedSelected);
      const matched = new Set<string>();
      for (const target of verifiableTargets) {
        const key = targetSelectionKey(target);
        if (legacyOrStandard.has(key) || legacyOrStandard.has(target.key)) {
          matched.add(key);
        }
      }
      setSelectedKeys(matched);
    } else {
      setSelectedKeys(
        new Set(verifiableTargets.map((target) => targetSelectionKey(target))),
      );
    }
  }, [settingsQuery.data, verifiableTargets]);

  const filteredTargets = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return verifiableTargets.filter((target) => {
      if (siteFilter && target.siteId !== siteFilter) {
        return false;
      }
      if (!keyword) {
        return true;
      }
      return (
        target.channelName.toLowerCase().includes(keyword) ||
        target.modelName.toLowerCase().includes(keyword) ||
        target.groupName.toLowerCase().includes(keyword)
      );
    });
  }, [search, siteFilter, verifiableTargets]);

  function toggleTarget(key: string, checked: boolean) {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (checked) {
        next.add(key);
      } else {
        next.delete(key);
      }
      return next;
    });
  }

  function selectAllFiltered() {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      for (const target of filteredTargets) {
        next.add(targetSelectionKey(target));
      }
      return next;
    });
  }

  function clearFilteredOrAll() {
    if (!siteFilter && !search.trim()) {
      setSelectedKeys(new Set());
      return;
    }
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      for (const target of filteredTargets) {
        next.delete(targetSelectionKey(target));
      }
      return next;
    });
  }

  async function save() {
    setSaving(true);
    try {
      await apiRequest<SettingItem[]>("/admin/settings", {
        method: "PUT",
        body: JSON.stringify({
          items: [
            { key: BATCH_SIZE, value: batch },
            { key: CONCURRENCY, value: concurrency },
            { key: TIMEOUT_SECONDS, value: timeoutSeconds },
            { key: MAX_TOKENS, value: maxTokens },
            { key: CHALLENGE_COUNT, value: count },
            { key: REASONING_EFFORT, value: effort },
            {
              key: SELECTED_MODELS,
              value: JSON.stringify(Array.from(selectedKeys)),
            },
            { key: TRUST_THRESHOLD, value: trust },
            { key: WARN_THRESHOLD, value: warn },
          ],
        }),
      });
      await queryClient.invalidateQueries({ queryKey: ["settings"] });
      toast.success(titleForLocale(locale, "已保存", "Saved"));
      onOpenChange(false);
    } catch (cause) {
      toast.error((cause as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open ? (
        <AppDialogContent
          className="max-w-2xl"
          title={titleForLocale(
            locale,
            "定时任务参数",
            "Scheduled run settings",
          )}
          footer={
            <>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={saving}
              >
                {titleForLocale(locale, "取消", "Cancel")}
              </Button>
              <Button type="button" onClick={save} disabled={saving}>
                <Save className="mr-1.5 size-3.5" />
                {titleForLocale(locale, "保存", "Save")}
              </Button>
            </>
          }
        >
          <div className="flex flex-col gap-3">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <div className="flex flex-col gap-1.5">
                <Label className="text-xs text-muted-foreground">
                  {titleForLocale(locale, "每轮模型数", "Models per run")}
                </Label>
                <Input
                  value={batch}
                  onChange={(event) => setBatch(event.target.value)}
                  inputMode="numeric"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label className="text-xs text-muted-foreground">
                  {titleForLocale(locale, "并发数", "Concurrency")}
                </Label>
                <Input
                  value={concurrency}
                  onChange={(event) => setConcurrency(event.target.value)}
                  inputMode="numeric"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label className="text-xs text-muted-foreground">
                  {titleForLocale(locale, "单模型超时(秒)", "Timeout (s)")}
                </Label>
                <Input
                  value={timeoutSeconds}
                  onChange={(event) => setTimeoutSeconds(event.target.value)}
                  inputMode="numeric"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label className="text-xs text-muted-foreground">
                  {titleForLocale(locale, "挑战数", "Challenges")}
                </Label>
                <Select value={count} onValueChange={setCount}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="1">1</SelectItem>
                    <SelectItem value="2">2</SelectItem>
                    <SelectItem value="3">3</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div className="flex flex-col gap-1.5">
                <Label className="text-xs text-muted-foreground">
                  {titleForLocale(locale, "思考档位", "Reasoning effort")}
                </Label>
                <Select
                  value={effort || "__default__"}
                  onValueChange={(value) =>
                    setEffort(value === "__default__" ? "" : value)
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__default__">
                      {titleForLocale(locale, "默认", "Default")}
                    </SelectItem>
                    {EFFORTS.map((value) => (
                      <SelectItem key={value} value={value}>
                        {value}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex flex-col gap-1.5">
                <Label className="text-xs text-muted-foreground">
                  {titleForLocale(locale, "输出上限", "Max tokens")}
                </Label>
                <Input
                  value={maxTokens}
                  onChange={(event) => setMaxTokens(event.target.value)}
                  inputMode="numeric"
                  placeholder={titleForLocale(locale, "0 = 自动", "0 = auto")}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label className="text-xs text-muted-foreground">
                  {titleForLocale(locale, "可信阈值", "Trusted at")}
                </Label>
                <Input
                  value={trust}
                  onChange={(event) => setTrust(event.target.value)}
                  inputMode="numeric"
                  placeholder="80"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label className="text-xs text-muted-foreground">
                  {titleForLocale(locale, "警告阈值", "Warn below")}
                </Label>
                <Input
                  value={warn}
                  onChange={(event) => setWarn(event.target.value)}
                  inputMode="numeric"
                  placeholder="50"
                />
              </div>
            </div>

            <div className="flex flex-col gap-2 pt-1">
              <div className="flex items-center justify-between gap-2">
                <Label className="text-xs text-muted-foreground">
                  {titleForLocale(locale, "验真模型范围", "Target models")} (
                  {selectedKeys.size}/{verifiableTargets.length})
                </Label>
                <div className="flex items-center gap-1.5">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-7 px-2 text-xs"
                    onClick={selectAllFiltered}
                  >
                    {titleForLocale(locale, "全选", "Select all")}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-7 px-2 text-xs"
                    onClick={clearFilteredOrAll}
                  >
                    {titleForLocale(locale, "清空", "Clear")}
                  </Button>
                </div>
              </div>

              <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                <Select
                  value={siteFilter || "__all__"}
                  onValueChange={(value) =>
                    setSiteFilter(value === "__all__" ? "" : value)
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__all__">
                      {titleForLocale(locale, "全部渠道", "All sites")}
                    </SelectItem>
                    {sites.map((site) => (
                      <SelectItem key={site.id} value={site.id}>
                        {site.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <div className="sm:col-span-2">
                  <Input
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    placeholder={titleForLocale(
                      locale,
                      "搜索渠道或模型名称",
                      "Filter by site, channel or model",
                    )}
                  />
                </div>
              </div>

              <div className="max-h-64 overflow-y-auto rounded-lg border p-1.5">
                {filteredTargets.length === 0 ? (
                  <div className="py-6 text-center text-xs text-muted-foreground">
                    {titleForLocale(locale, "无匹配模型", "No matching models")}
                  </div>
                ) : (
                  <div className="flex flex-col gap-0.5">
                    {filteredTargets.map((target) => {
                      const key = targetSelectionKey(target);
                      const checked = selectedKeys.has(key);
                      const verdict = verdicts.get(key);
                      return (
                        <label
                          key={target.key}
                          className="flex cursor-pointer items-center justify-between gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-muted/60"
                        >
                          <span className="flex min-w-0 items-center gap-2">
                            <Checkbox
                              checked={checked}
                              onCheckedChange={(value) =>
                                toggleTarget(key, value === true)
                              }
                            />
                            <span className="truncate font-mono">
                              {target.channelName} / {target.modelName}
                            </span>
                          </span>
                          <ConfidenceBadge
                            verdict={verdict}
                            thresholds={thresholds}
                            locale={locale}
                            showUnverified
                          />
                        </label>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          </div>
        </AppDialogContent>
      ) : null}
    </Dialog>
  );
}
