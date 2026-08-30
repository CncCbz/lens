"use client";

import { useEffect, useMemo, useState } from "react";
import { ChevronDown, Plus, RefreshCcw, Trash2, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { AppDialogContent, Dialog } from "@/components/ui/dialog";
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Combobox, ComboboxOption } from "@/components/ui/combobox";
import { ProtocolMultiSelect } from "@/components/ui/protocol-multi-select";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { ErrorPolicySettings } from "@/components/settings/error-policy-settings";
import {
  parseErrorPolicyConfig,
  serializeErrorPolicyConfig,
  validateErrorPolicyDraft,
} from "@/lib/error-policy-config";
import type { RouterErrorPolicyDraft } from "@/lib/settings-types";
import type { ProtocolKind } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  activeBaseUrlValue,
  baseUrlLabel,
  compactProtocolLabel,
  credentialLabel,
  defaultProtocolConfigName,
  formBaseUrlsForPayload,
  FormProtocolConfig,
  FormState,
  HeaderItem,
  Locale,
  MAX_CHANNEL_CONCURRENCY,
  modelSupportedProtocols,
  protocolBadgeClassName,
  protocolConfigCredentialKeys,
  protocolConfigEffectiveProtocols,
  protocolLabel,
  resolveBaseUrlId,
} from "./shared";

export function ProtocolConfigItem({
  form,
  protocolConfig,
  protocolConfigIndex,
  locale,
  fetchingProtocolConfigIndex,
  duplicatedProtocolConfigKeys,
  onUpdateProtocolConfig,
  onRemoveProtocolConfig,
  onAddManualModel,
  onFetchModels,
  onOpenAdvanced,
  onUpdateModelProtocols,
  onOpenModelTest,
  canTestModel,
  testingDisabled,
}: {
  form: FormState;
  protocolConfig: FormProtocolConfig;
  protocolConfigIndex: number;
  locale: Locale;
  fetchingProtocolConfigIndex: number | null;
  duplicatedProtocolConfigKeys: Set<string>;
  onUpdateProtocolConfig: (
    index: number,
    patch: Partial<FormProtocolConfig>,
  ) => void;
  onRemoveProtocolConfig: (index: number) => void;
  onAddManualModel: (index: number, credentialId: string) => void;
  onFetchModels: (index: number) => void;
  onOpenAdvanced: (index: number) => void;
  onUpdateModelProtocols: (
    protocolConfigIndex: number,
    modelIndex: number,
    nextProtocols: ProtocolKind[],
  ) => void;
  onOpenModelTest: (protocolConfigIndex: number, modelIndex: number) => void;
  canTestModel: (protocolConfigIndex: number, modelIndex: number) => boolean;
  testingDisabled?: boolean;
}) {
  const submittedBaseUrls = formBaseUrlsForPayload(form);
  const submittedBaseUrlIds = new Set(submittedBaseUrls.map((item) => item.id));
  const protocolConfigDuplicated = protocolConfigCredentialKeys(
    protocolConfig,
    submittedBaseUrlIds,
  ).some((key) => duplicatedProtocolConfigKeys.has(key));
  const activeCredentialIds = new Set(
    form.credentials
      .filter((item) => item.enabled && item.api_key.trim())
      .map((item) => item.id),
  );
  const credentialOptions = form.credentials.map((item, index) => ({
    ...item,
    display_name: credentialLabel(item, index, locale),
  }));
  const selectedCredentialId = protocolConfig.credential_id;
  const selectedCredentialActive =
    activeCredentialIds.has(selectedCredentialId);
  const selectedCredentialKnown = credentialOptions.some(
    (item) => item.id === selectedCredentialId,
  );
  const visibleModels = protocolConfig.models
    .map((model, modelIndex) => ({ model, modelIndex }))
    .filter(
      ({ model }) =>
        selectedCredentialId && model.credential_id === selectedCredentialId,
    );
  const [selectedModelIndexes, setSelectedModelIndexes] = useState<number[]>(
    [],
  );
  const visibleModelIndexSet = useMemo(
    () => new Set(visibleModels.map((item) => item.modelIndex)),
    [visibleModels],
  );
  const activeSelectedIndexes = useMemo(
    () =>
      selectedModelIndexes.filter((index) => visibleModelIndexSet.has(index)),
    [selectedModelIndexes, visibleModelIndexSet],
  );
  const allVisibleSelected =
    visibleModels.length > 0 &&
    activeSelectedIndexes.length === visibleModels.length;
  const someVisibleSelected =
    activeSelectedIndexes.length > 0 && !allVisibleSelected;
  const batchProtocols = useMemo(() => {
    if (!activeSelectedIndexes.length) return [] as ProtocolKind[];
    const selectedModels = visibleModels.filter((item) =>
      activeSelectedIndexes.includes(item.modelIndex),
    );
    const first = modelSupportedProtocols(selectedModels[0]?.model)
      .slice()
      .sort();
    const same = selectedModels.every((item) => {
      const current = modelSupportedProtocols(item.model).slice().sort();
      return (
        current.length === first.length &&
        current.every((protocol, index) => protocol === first[index])
      );
    });
    return same ? (first as ProtocolKind[]) : [];
  }, [activeSelectedIndexes, visibleModels]);

  function toggleModelSelected(modelIndex: number, checked: boolean) {
    setSelectedModelIndexes((current) => {
      if (checked) {
        return current.includes(modelIndex)
          ? current
          : [...current, modelIndex];
      }
      return current.filter((index) => index !== modelIndex);
    });
  }

  function toggleSelectAllVisible(checked: boolean) {
    if (!checked) {
      setSelectedModelIndexes((current) =>
        current.filter((index) => !visibleModelIndexSet.has(index)),
      );
      return;
    }
    setSelectedModelIndexes((current) => {
      const next = new Set(current);
      for (const item of visibleModels) next.add(item.modelIndex);
      return Array.from(next);
    });
  }

  function applyProtocolsToSelected(nextProtocols: ProtocolKind[]) {
    if (!nextProtocols.length || !activeSelectedIndexes.length) return;
    const selected = new Set(activeSelectedIndexes);
    const modelProtocols = Array.from(new Set(nextProtocols));
    onUpdateProtocolConfig(protocolConfigIndex, {
      models: protocolConfig.models.map((model, modelIndex) =>
        selected.has(modelIndex)
          ? { ...model, protocols: modelProtocols }
          : model,
      ),
    });
  }

  const combinationName =
    protocolConfig.name.trim() ||
    defaultProtocolConfigName(protocolConfigIndex, locale);
  const activeBaseUrl = activeBaseUrlValue(form, protocolConfig).trim();
  const baseUrlIndex = form.base_urls.findIndex(
    (item) => item.id === protocolConfig.base_url_id,
  );
  const baseUrlItem =
    baseUrlIndex >= 0 ? form.base_urls[baseUrlIndex] : undefined;
  const summaryUrl =
    activeBaseUrl ||
    (baseUrlItem
      ? baseUrlLabel(baseUrlItem, baseUrlIndex, locale)
      : locale === "zh-CN"
        ? "未选地址"
        : "No base URL");
  const selectedCredential = credentialOptions.find(
    (item) => item.id === selectedCredentialId,
  );
  const summaryCredential =
    selectedCredential?.display_name ||
    (selectedCredentialId
      ? locale === "zh-CN"
        ? "无效密钥"
        : "Invalid key"
      : locale === "zh-CN"
        ? "未选密钥"
        : "No key");
  const effectiveProtocols = protocolConfigEffectiveProtocols(protocolConfig);

  function toggleExpanded() {
    onUpdateProtocolConfig(protocolConfigIndex, {
      expanded: !protocolConfig.expanded,
    });
  }

  return (
    <div
      className={cn(
        "overflow-hidden rounded-xl border border-border bg-background shadow-sm",
        !protocolConfig.enabled && "opacity-75",
      )}
    >
      <div
        role="button"
        tabIndex={0}
        className="flex cursor-pointer items-center gap-2.5 px-3.5 py-3 transition-colors hover:bg-muted/50"
        onClick={toggleExpanded}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            toggleExpanded();
          }
        }}
      >
        <div className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-primary/10 text-xs font-bold text-primary">
          {protocolConfigIndex + 1}
        </div>
        <div className="min-w-0 flex-[0_1_10rem]">
          <div className="truncate text-sm font-semibold text-foreground">
            {combinationName}
          </div>
          <div className="truncate font-mono text-[11px] text-muted-foreground">
            {summaryUrl}
          </div>
        </div>
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
          <Badge
            variant="outline"
            className="max-w-[9rem] truncate font-normal"
          >
            {summaryCredential}
          </Badge>
          <Badge
            variant="outline"
            className={cn(
              "font-normal",
              visibleModels.length
                ? "border-primary/30 bg-primary/10 text-primary"
                : "",
            )}
          >
            {locale === "zh-CN"
              ? `${visibleModels.length} 模型`
              : `${visibleModels.length} models`}
          </Badge>
          {effectiveProtocols.map((item) => (
            <Badge
              key={item}
              variant="outline"
              className={cn(
                "max-w-[7rem] truncate font-normal",
                protocolBadgeClassName(item),
              )}
            >
              {compactProtocolLabel(item)}
            </Badge>
          ))}
          {!protocolConfig.enabled ? (
            <Badge
              variant="outline"
              className="font-normal text-muted-foreground"
            >
              {locale === "zh-CN" ? "已停用" : "Disabled"}
            </Badge>
          ) : null}
        </div>
        <div
          className="flex shrink-0 items-center gap-2"
          onClick={(event) => event.stopPropagation()}
          onKeyDown={(event) => event.stopPropagation()}
        >
          <Switch
            checked={protocolConfig.enabled}
            onCheckedChange={(checked) =>
              onUpdateProtocolConfig(protocolConfigIndex, {
                enabled: checked,
              })
            }
          />
          <Button
            type="button"
            variant="outline"
            size="icon"
            className="h-7 w-7 text-muted-foreground"
            onClick={toggleExpanded}
            aria-expanded={protocolConfig.expanded}
          >
            <ChevronDown
              size={14}
              className={cn(
                "transition-transform",
                protocolConfig.expanded ? "rotate-180" : "",
              )}
            />
          </Button>
        </div>
      </div>

      {protocolConfigDuplicated ? (
        <div className="border-t border-border px-3.5 py-2 text-sm text-destructive">
          {locale === "zh-CN"
            ? "地址来源和密钥重复"
            : "Duplicate Base URL and key"}
        </div>
      ) : null}

      {protocolConfig.expanded ? (
        <div className="grid border-t border-border lg:grid-cols-[360px_minmax(0,1fr)]">
          <aside className="flex flex-col gap-3 border-b border-border bg-muted/40 p-4 lg:border-r lg:border-b-0">
            <Field>
              <FieldLabel>
                {locale === "zh-CN" ? "组合名称" : "Combination name"}
              </FieldLabel>
              <Input
                className="w-full min-w-0"
                value={protocolConfig.name}
                onChange={(event) =>
                  onUpdateProtocolConfig(protocolConfigIndex, {
                    name: event.target.value,
                  })
                }
                placeholder={defaultProtocolConfigName(
                  protocolConfigIndex,
                  locale,
                )}
              />
            </Field>
            <Field>
              <FieldLabel>
                {locale === "zh-CN" ? "地址来源" : "Base URL"}
              </FieldLabel>
              <Combobox
                className="w-full"
                value={resolveBaseUrlId(
                  form.base_urls,
                  protocolConfig.base_url_id,
                )}
                onChange={(event) =>
                  onUpdateProtocolConfig(protocolConfigIndex, {
                    base_url_id: event.target.value,
                  })
                }
              >
                {form.base_urls.map((item, baseUrlIndex) => (
                  <ComboboxOption key={item.id} value={item.id}>
                    {baseUrlLabel(item, baseUrlIndex, locale)}
                  </ComboboxOption>
                ))}
              </Combobox>
            </Field>
            <Field>
              <FieldLabel>{locale === "zh-CN" ? "密钥" : "Key"}</FieldLabel>
              <Combobox
                className="w-full"
                value={selectedCredentialId}
                onChange={(event) => {
                  const credentialId = event.target.value;
                  onUpdateProtocolConfig(protocolConfigIndex, {
                    credential_id: credentialId,
                    models: protocolConfig.models.filter(
                      (model) => model.credential_id === credentialId,
                    ),
                  });
                }}
              >
                {selectedCredentialId && !selectedCredentialKnown ? (
                  <ComboboxOption value={selectedCredentialId} disabled>
                    {locale === "zh-CN"
                      ? `无效密钥：${selectedCredentialId}`
                      : `Invalid key: ${selectedCredentialId}`}
                  </ComboboxOption>
                ) : null}
                {credentialOptions.length ? (
                  credentialOptions.map((item) => (
                    <ComboboxOption key={item.id} value={item.id}>
                      {item.display_name}
                    </ComboboxOption>
                  ))
                ) : (
                  <ComboboxOption value="" disabled>
                    {locale === "zh-CN" ? "暂无可用密钥" : "No available key"}
                  </ComboboxOption>
                )}
              </Combobox>
            </Field>
            <div className="mt-auto flex gap-2 pt-1">
              <Button
                type="button"
                variant="outline"
                className="min-w-0 flex-1"
                onClick={() => onOpenAdvanced(protocolConfigIndex)}
              >
                {locale === "zh-CN" ? "上游设置" : "Upstream"}
              </Button>
              <Button
                type="button"
                variant="outline"
                className="min-w-0 flex-1 text-destructive hover:text-destructive"
                onClick={() => onRemoveProtocolConfig(protocolConfigIndex)}
              >
                {locale === "zh-CN" ? "删除组合" : "Delete"}
              </Button>
            </div>
          </aside>

          <div className="flex min-w-0 flex-col gap-2.5 p-3.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <Checkbox
                  checked={
                    allVisibleSelected
                      ? true
                      : someVisibleSelected
                        ? "indeterminate"
                        : false
                  }
                  onCheckedChange={(checked) =>
                    toggleSelectAllVisible(checked === true)
                  }
                  disabled={!visibleModels.length}
                  aria-label={
                    locale === "zh-CN" ? "全选模型" : "Select all models"
                  }
                />
                <div className="text-sm font-semibold text-foreground">
                  {locale === "zh-CN" ? "模型" : "Models"}
                </div>
                <Badge
                  variant="outline"
                  className={cn(
                    "font-normal",
                    visibleModels.length
                      ? "border-primary/30 bg-primary/10 text-primary"
                      : "",
                  )}
                >
                  {visibleModels.length}
                </Badge>
                {activeSelectedIndexes.length ? (
                  <>
                    <span className="text-xs text-muted-foreground">
                      {locale === "zh-CN"
                        ? `已选 ${activeSelectedIndexes.length}`
                        : `${activeSelectedIndexes.length} selected`}
                    </span>
                    <ProtocolMultiSelect
                      value={batchProtocols}
                      onChange={applyProtocolsToSelected}
                      locale={locale}
                      className="w-auto min-w-[11rem]"
                      requireAtLeastOne
                      placeholder={
                        locale === "zh-CN" ? "批量设置协议" : "Set protocols"
                      }
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2 text-muted-foreground"
                      onClick={() => setSelectedModelIndexes([])}
                    >
                      {locale === "zh-CN" ? "取消选择" : "Clear selection"}
                    </Button>
                  </>
                ) : null}
              </div>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 text-destructive hover:text-destructive"
                onClick={() => {
                  setSelectedModelIndexes([]);
                  onUpdateProtocolConfig(protocolConfigIndex, { models: [] });
                }}
                disabled={!visibleModels.length}
              >
                <Trash2 data-icon="inline-start" />
                {locale === "zh-CN" ? "清空" : "Clear"}
              </Button>
            </div>

            <div className="h-[220px] overflow-hidden rounded-lg border border-border bg-muted/30">
              {visibleModels.length ? (
                <div className="flex h-full flex-col gap-1.5 overflow-y-auto overscroll-contain p-1.5 [scrollbar-gutter:stable]">
                  {visibleModels.map(({ model, modelIndex }) => {
                    const protocols = modelSupportedProtocols(model);
                    const testable = canTestModel(
                      protocolConfigIndex,
                      modelIndex,
                    );
                    const selected = activeSelectedIndexes.includes(modelIndex);
                    return (
                      <div
                        key={
                          model.id ||
                          `${model.credential_id}-${model.model_name}-${modelIndex}`
                        }
                        className={cn(
                          "grid shrink-0 grid-cols-1 items-center gap-2 rounded-lg border border-border bg-background px-2.5 py-2 sm:grid-cols-[auto_minmax(0,1fr)_minmax(11rem,12rem)_auto_auto]",
                          !model.enabled && "opacity-65",
                          selected && "border-primary/40 bg-primary/5",
                        )}
                      >
                        <Checkbox
                          checked={selected}
                          onCheckedChange={(checked) =>
                            toggleModelSelected(modelIndex, checked === true)
                          }
                          aria-label={
                            locale === "zh-CN"
                              ? `选择 ${model.model_name}`
                              : `Select ${model.model_name}`
                          }
                        />
                        <span className="min-w-0 truncate text-sm font-medium text-foreground">
                          {model.model_name}
                        </span>
                        <ProtocolMultiSelect
                          value={protocols}
                          onChange={(next) =>
                            onUpdateModelProtocols(
                              protocolConfigIndex,
                              modelIndex,
                              next,
                            )
                          }
                          locale={locale}
                          className="w-full min-w-0"
                          invalid={protocols.length === 0}
                          requireAtLeastOne
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-8 px-2 text-muted-foreground hover:text-foreground"
                          onClick={() =>
                            onOpenModelTest(protocolConfigIndex, modelIndex)
                          }
                          disabled={!testable || testingDisabled}
                        >
                          {locale === "zh-CN" ? "测试" : "Test"}
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7 text-muted-foreground hover:text-destructive"
                          onClick={() => {
                            setSelectedModelIndexes((current) =>
                              current.filter((index) => index !== modelIndex),
                            );
                            onUpdateProtocolConfig(protocolConfigIndex, {
                              models: protocolConfig.models.filter(
                                (_, currentIndex) =>
                                  currentIndex !== modelIndex,
                              ),
                            });
                          }}
                        >
                          <X size={14} />
                        </Button>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="grid h-full place-items-center text-sm text-muted-foreground">
                  {locale === "zh-CN" ? "当前没有模型" : "No models selected"}
                </div>
              )}
            </div>

            <div className="grid gap-2 sm:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_auto_auto]">
              <Input
                className="w-full min-w-0"
                value={protocolConfig.manual_model_name}
                onChange={(event) =>
                  onUpdateProtocolConfig(protocolConfigIndex, {
                    manual_model_name: event.target.value,
                  })
                }
                onKeyDown={(event) => {
                  if (event.key !== "Enter") return;
                  event.preventDefault();
                  onAddManualModel(protocolConfigIndex, selectedCredentialId);
                }}
                placeholder={
                  locale === "zh-CN" ? "手动添加模型名" : "Add model name"
                }
              />
              <Input
                className="w-full min-w-0"
                value={protocolConfig.match_regex}
                onChange={(event) =>
                  onUpdateProtocolConfig(protocolConfigIndex, {
                    match_regex: event.target.value,
                  })
                }
                placeholder={
                  locale === "zh-CN"
                    ? "获取过滤正则（可选）"
                    : "Fetch filter regex (optional)"
                }
              />
              <Button
                type="button"
                variant="outline"
                onClick={() =>
                  onAddManualModel(protocolConfigIndex, selectedCredentialId)
                }
                disabled={
                  !selectedCredentialId ||
                  !protocolConfig.manual_model_name.trim()
                }
              >
                <Plus data-icon="inline-start" />
                {locale === "zh-CN" ? "添加" : "Add"}
              </Button>
              <Button
                type="button"
                onClick={() => onFetchModels(protocolConfigIndex)}
                disabled={
                  fetchingProtocolConfigIndex === protocolConfigIndex ||
                  !activeBaseUrl ||
                  !selectedCredentialActive
                }
              >
                <RefreshCcw
                  data-icon="inline-start"
                  className={
                    fetchingProtocolConfigIndex === protocolConfigIndex
                      ? "animate-spin"
                      : ""
                  }
                />
                {locale === "zh-CN" ? "获取模型" : "Fetch"}
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

type AdvancedConfigTab = "limits" | "proxy" | "headers" | "body" | "errors";

function parseIntLimit(raw: string): number {
  const value = Number.parseInt(raw, 10);
  return Number.isFinite(value)
    ? Math.min(Math.max(value, 0), MAX_CHANNEL_CONCURRENCY)
    : 0;
}

function parseCostLimit(raw: string): number {
  const value = Number.parseFloat(raw);
  return Number.isFinite(value) ? Math.max(value, 0) : 0;
}

function headersToJson(headers: HeaderItem[]): string {
  const object = Object.fromEntries(
    headers
      .map((header) => [header.key.trim(), header.value] as const)
      .filter(([key]) => key),
  );
  return Object.keys(object).length ? JSON.stringify(object, null, 2) : "{}";
}

function parseHeadersJson(raw: string): HeaderItem[] | null {
  const trimmed = raw.trim();
  if (!trimmed) return [{ key: "", value: "" }];
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (
      parsed === null ||
      typeof parsed !== "object" ||
      Array.isArray(parsed)
    ) {
      return null;
    }
    const items: HeaderItem[] = [];
    for (const [key, value] of Object.entries(
      parsed as Record<string, unknown>,
    )) {
      if (typeof value !== "string") return null;
      items.push({ key, value });
    }
    return items.length ? items : [{ key: "", value: "" }];
  } catch {
    return null;
  }
}

export function AdvancedProtocolConfigDialog({
  open,
  protocolConfig,
  protocolConfigIndex,
  locale,
  globals,
  onOpenChange,
  onUpdateProtocolConfig,
}: {
  open: boolean;
  protocolConfig: FormProtocolConfig | undefined;
  protocolConfigIndex: number | null;
  locale: Locale;
  globals: { threshold: number; cooldown: number; maxCooldown: number };
  onOpenChange: (open: boolean) => void;
  onUpdateProtocolConfig: (
    index: number,
    patch: Partial<FormProtocolConfig>,
  ) => void;
}) {
  const [tab, setTab] = useState<AdvancedConfigTab>("limits");
  const [headersDraft, setHeadersDraft] = useState("{}");
  const [headersInvalid, setHeadersInvalid] = useState(false);
  const [errorPolicyDraft, setErrorPolicyDraft] =
    useState<RouterErrorPolicyDraft>(() =>
      parseErrorPolicyConfig("", globals, false),
    );
  const [errorPolicyInvalid, setErrorPolicyInvalid] = useState<string | null>(
    null,
  );
  const combinationName =
    protocolConfig?.name?.trim() ||
    (protocolConfigIndex !== null
      ? defaultProtocolConfigName(protocolConfigIndex, locale)
      : "");
  const filledHeaderCount =
    protocolConfig?.headers.filter((header) => header.key.trim()).length ?? 0;

  useEffect(() => {
    if (!open || protocolConfigIndex === null || !protocolConfig) return;
    setTab("limits");
    setHeadersDraft(headersToJson(protocolConfig.headers));
    setHeadersInvalid(false);
    setErrorPolicyDraft(
      parseErrorPolicyConfig(
        protocolConfig.router_error_policy_config,
        globals,
        false,
      ),
    );
    setErrorPolicyInvalid(null);
    // Sync draft only when dialog opens for a combination.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, protocolConfigIndex]);

  function handleErrorPolicyChange(next: RouterErrorPolicyDraft) {
    setErrorPolicyDraft(next);
    const error = validateErrorPolicyDraft(
      next,
      locale === "zh-CN" ? "zh-CN" : "en-US",
    );
    setErrorPolicyInvalid(error);
    if (error) return;
    const serialized = serializeErrorPolicyConfig(next, globals);
    onUpdateProtocolConfig(protocolConfigIndex as number, {
      router_error_policy_config:
        serialized === '{"overrides":{}}' ? "" : serialized,
    });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {protocolConfigIndex !== null && protocolConfig ? (
        <AppDialogContent
          className="max-w-5xl"
          title={
            locale === "zh-CN"
              ? `${combinationName} · 上游转发`
              : `${combinationName} · Upstream`
          }
        >
          <div className="grid gap-4">
            <div className="inline-flex w-full items-center gap-1 rounded-xl border bg-muted p-0.5">
              {(
                [
                  {
                    value: "limits" as const,
                    label: locale === "zh-CN" ? "限制" : "Limits",
                  },
                  {
                    value: "proxy" as const,
                    label: locale === "zh-CN" ? "代理" : "Proxy",
                  },
                  {
                    value: "headers" as const,
                    label:
                      locale === "zh-CN"
                        ? filledHeaderCount
                          ? `请求头 ${filledHeaderCount}`
                          : "请求头"
                        : filledHeaderCount
                          ? `Headers ${filledHeaderCount}`
                          : "Headers",
                  },
                  {
                    value: "body" as const,
                    label: "Body",
                  },
                  {
                    value: "errors" as const,
                    label: locale === "zh-CN" ? "错误策略" : "Errors",
                  },
                ] as const
              ).map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={cn(
                    "h-8 min-w-0 flex-1 truncate rounded-lg px-3 text-sm font-medium leading-none transition-colors",
                    option.value === tab
                      ? "bg-background text-foreground shadow-sm"
                      : "bg-transparent text-muted-foreground hover:text-foreground",
                  )}
                  onClick={() => setTab(option.value)}
                >
                  {option.label}
                </button>
              ))}
            </div>

            {tab === "limits" ? (
              <div className="grid gap-4 sm:grid-cols-2">
                {(
                  [
                    {
                      key: "concurrency_limit",
                      id: "protocol-concurrency-limit",
                      label:
                        locale === "zh-CN" ? "并发上限" : "Concurrency limit",
                      integer: true,
                    },
                    {
                      key: "rpm_limit",
                      id: "protocol-rpm-limit",
                      label: locale === "zh-CN" ? "RPM 上限" : "RPM limit",
                      integer: true,
                    },
                    {
                      key: "token_limit",
                      id: "protocol-token-limit",
                      label: locale === "zh-CN" ? "Token 上限" : "Token limit",
                      integer: true,
                    },
                    {
                      key: "cost_limit_usd",
                      id: "protocol-cost-limit",
                      label:
                        locale === "zh-CN"
                          ? "费用上限 (USD)"
                          : "Cost limit (USD)",
                      integer: false,
                    },
                  ] as const
                ).map((field) => (
                  <Field key={field.key}>
                    <FieldLabel htmlFor={field.id}>{field.label}</FieldLabel>
                    <Input
                      id={field.id}
                      type="number"
                      min={0}
                      max={field.integer ? MAX_CHANNEL_CONCURRENCY : undefined}
                      step={field.integer ? 1 : 0.0001}
                      value={protocolConfig[field.key]}
                      onChange={(event) => {
                        onUpdateProtocolConfig(protocolConfigIndex, {
                          [field.key]: field.integer
                            ? parseIntLimit(event.target.value)
                            : parseCostLimit(event.target.value),
                        });
                      }}
                    />
                  </Field>
                ))}
              </div>
            ) : null}

            {tab === "proxy" ? (
              <FieldGroup>
                <Field>
                  <FieldLabel htmlFor="protocol-proxy-mode">
                    {locale === "zh-CN" ? "模式" : "Mode"}
                  </FieldLabel>
                  <Select
                    value={protocolConfig.proxy_mode}
                    onValueChange={(value) =>
                      onUpdateProtocolConfig(protocolConfigIndex, {
                        proxy_mode: value as FormProtocolConfig["proxy_mode"],
                      })
                    }
                  >
                    <SelectTrigger id="protocol-proxy-mode" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="inherit">
                        {locale === "zh-CN"
                          ? "跟随系统代理"
                          : "Use system proxy"}
                      </SelectItem>
                      <SelectItem value="direct">
                        {locale === "zh-CN" ? "不使用代理" : "Direct"}
                      </SelectItem>
                      <SelectItem value="custom">
                        {locale === "zh-CN" ? "自定义代理" : "Custom proxy"}
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </Field>
                {protocolConfig.proxy_mode === "custom" ? (
                  <Field>
                    <FieldLabel htmlFor="protocol-proxy">
                      {locale === "zh-CN" ? "地址" : "URL"}
                    </FieldLabel>
                    <Input
                      id="protocol-proxy"
                      value={protocolConfig.channel_proxy}
                      onChange={(event) =>
                        onUpdateProtocolConfig(protocolConfigIndex, {
                          channel_proxy: event.target.value,
                        })
                      }
                      placeholder="http://127.0.0.1:7890"
                    />
                  </Field>
                ) : null}
              </FieldGroup>
            ) : null}

            {tab === "headers" ? (
              <Field>
                <div className="mb-2 flex items-center justify-between gap-3">
                  <FieldLabel htmlFor="protocol-headers-json" className="mb-0">
                    {locale === "zh-CN" ? "请求头" : "Headers"}
                  </FieldLabel>
                  <Badge variant="outline" className="font-normal">
                    JSON
                  </Badge>
                </div>
                <Textarea
                  id="protocol-headers-json"
                  className="min-h-50 font-mono text-sm"
                  value={headersDraft}
                  onChange={(event) => {
                    const next = event.target.value;
                    setHeadersDraft(next);
                    const parsed = parseHeadersJson(next);
                    if (!parsed) {
                      setHeadersInvalid(true);
                      return;
                    }
                    setHeadersInvalid(false);
                    onUpdateProtocolConfig(protocolConfigIndex, {
                      headers: parsed,
                    });
                  }}
                  placeholder={
                    '{\n  "user-agent": "Codex Desktop/0.144.0-alpha.4",\n  "originator": "Codex Desktop"\n}'
                  }
                />
                <FieldDescription
                  className={headersInvalid ? "text-destructive" : undefined}
                >
                  {headersInvalid
                    ? locale === "zh-CN"
                      ? "需为 JSON 对象，键值均为字符串"
                      : "Must be a JSON object with string values"
                    : locale === "zh-CN"
                      ? "JSON 对象，写入上游请求头"
                      : "JSON object for upstream headers"}
                </FieldDescription>
              </Field>
            ) : null}

            {tab === "body" ? (
              <Field>
                <div className="mb-2 flex items-center justify-between gap-3">
                  <FieldLabel
                    htmlFor="protocol-param-override"
                    className="mb-0"
                  >
                    {locale === "zh-CN" ? "参数覆盖" : "Param override"}
                  </FieldLabel>
                  <Badge variant="outline" className="font-normal">
                    deep merge
                  </Badge>
                </div>
                <Textarea
                  id="protocol-param-override"
                  className="min-h-50 font-mono text-sm"
                  value={protocolConfig.param_override}
                  onChange={(event) =>
                    onUpdateProtocolConfig(protocolConfigIndex, {
                      param_override: event.target.value,
                    })
                  }
                  placeholder={
                    '{\n  "client_metadata": {\n    "x-codex-installation-id": "..."\n  }\n}'
                  }
                />
                <FieldDescription>
                  {locale === "zh-CN"
                    ? "JSON 对象，深合并进上游 body；不可覆盖 model"
                    : "JSON object, deep-merged into upstream body; cannot override model"}
                </FieldDescription>
              </Field>
            ) : null}

            {tab === "errors" ? (
              <div className="grid min-w-0 gap-2">
                <ErrorPolicySettings
                  locale={locale}
                  draft={errorPolicyDraft}
                  globals={globals}
                  hint={
                    locale === "zh-CN"
                      ? "空配置使用全局策略。添加 502 / 429 / timeout 等即可覆盖；未添加的状态码仍走全局"
                      : "Empty inherits globals. Add 502 / 429 / timeout to override; other statuses stay global"
                  }
                  onChange={handleErrorPolicyChange}
                />
                {errorPolicyInvalid ? (
                  <p className="text-xs text-destructive">
                    {errorPolicyInvalid}
                  </p>
                ) : null}
              </div>
            ) : null}
          </div>
        </AppDialogContent>
      ) : null}
    </Dialog>
  );
}
