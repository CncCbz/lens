"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Copy, Download } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { AppDialogContent, Dialog } from "@/components/ui/dialog";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  ApiError,
  type GatewayApiKey,
  type PiConfigExportResponse,
  apiRequest,
} from "@/lib/api";
import type { Locale } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { titleForLocale } from "./shared";

type ConfigView = "split" | "standard" | "custom";

function prettyJson(value: unknown) {
  return JSON.stringify(value, null, 2);
}

function parsePiConfig(text: string): PiConfigExportResponse | null {
  try {
    const parsed: unknown = JSON.parse(text);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return null;
    }
    const record = parsed as Record<string, unknown>;
    if (Object.keys(record).some((key) => key !== "type" && key !== "models")) {
      return null;
    }
    if (record.type !== "pi" || !Array.isArray(record.models)) {
      return null;
    }
    if (
      !record.models.every(
        (item) => !!item && typeof item === "object" && !Array.isArray(item),
      )
    ) {
      return null;
    }
    return {
      type: record.type,
      models: record.models as Array<Record<string, unknown>>,
    };
  } catch {
    return null;
  }
}

function modelId(model: Record<string, unknown>) {
  return typeof model.id === "string" ? model.id : "";
}

function diffCounts(
  standard: PiConfigExportResponse,
  custom: PiConfigExportResponse,
) {
  const before = new Map(
    standard.models.map((model) => [modelId(model), model]),
  );
  const after = new Map(custom.models.map((model) => [modelId(model), model]));
  let modified = 0;
  let removed = 0;
  let added = 0;
  for (const [id, model] of before) {
    const next = after.get(id);
    if (!next) {
      removed += 1;
    } else if (JSON.stringify(model) !== JSON.stringify(next)) {
      modified += 1;
    }
  }
  for (const id of after.keys()) {
    if (!before.has(id)) {
      added += 1;
    }
  }
  return { modified, removed, added };
}

function downloadText(filename: string, text: string) {
  const blob = new Blob([text], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function modelsConfigPath(custom: boolean) {
  return custom
    ? "/v1/models/config?type=pi&custom=1"
    : "/v1/models/config?type=pi";
}

function modelsConfigCurl(apiKey: string, custom: boolean) {
  return `curl -H "Authorization: Bearer ${apiKey}" "${window.location.origin}${modelsConfigPath(custom)}"`;
}

export function GatewayApiKeyConfigDialog({
  locale,
  gatewayKey,
  onClose,
  onSaved,
}: {
  locale: Locale;
  gatewayKey: GatewayApiKey;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [view, setView] = useState<ConfigView>("split");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [saving, setSaving] = useState(false);
  const [standard, setStandard] = useState<PiConfigExportResponse | null>(null);
  const [savedCustom, setSavedCustom] = useState<PiConfigExportResponse | null>(
    null,
  );
  const [draft, setDraft] = useState("");

  const parsedDraft = useMemo(() => parsePiConfig(draft), [draft]);
  const standardText = standard ? prettyJson(standard) : "";
  const savedText = savedCustom ? prettyJson(savedCustom) : "";
  const dirty = draft !== (savedCustom ? savedText : standardText);
  const counts =
    standard && parsedDraft ? diffCounts(standard, parsedDraft) : null;

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setLoadError("");
      try {
        const standardConfig = await apiRequest<PiConfigExportResponse>(
          `/admin/gateway-api-keys/${gatewayKey.id}/models/config?type=pi`,
        );
        let customConfig: PiConfigExportResponse | null = null;
        try {
          customConfig = await apiRequest<PiConfigExportResponse>(
            `/admin/gateway-api-keys/${gatewayKey.id}/models/config?type=pi&custom=1`,
          );
        } catch (requestError) {
          if (
            !(requestError instanceof ApiError) ||
            requestError.status !== 404
          ) {
            throw requestError;
          }
        }
        if (cancelled) {
          return;
        }
        setStandard(standardConfig);
        setSavedCustom(customConfig);
        setDraft(prettyJson(customConfig ?? standardConfig));
      } catch (requestError) {
        if (cancelled) {
          return;
        }
        setLoadError(
          requestError instanceof ApiError
            ? requestError.message
            : titleForLocale(
                locale,
                "加载模型配置失败",
                "Failed to load model config",
              ),
        );
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [gatewayKey.id, locale]);

  async function copyText(value: string, ok: string) {
    try {
      await navigator.clipboard.writeText(value);
      toast.success(ok);
    } catch {
      toast.error(titleForLocale(locale, "复制失败", "Copy failed"));
    }
  }

  async function save() {
    if (!parsedDraft) {
      return;
    }
    setSaving(true);
    try {
      const saved = await apiRequest<PiConfigExportResponse>(
        `/admin/gateway-api-keys/${gatewayKey.id}/models/config?type=pi&custom=1`,
        {
          method: "PUT",
          body: JSON.stringify(parsedDraft),
        },
      );
      const text = prettyJson(saved);
      setSavedCustom(saved);
      setDraft(text);
      toast.success(
        titleForLocale(locale, "自定义配置已保存", "Custom config saved"),
      );
      await onSaved();
    } catch (requestError) {
      const message =
        requestError instanceof ApiError
          ? requestError.message
          : titleForLocale(
              locale,
              "保存自定义配置失败",
              "Failed to save custom config",
            );
      toast.error(message);
    } finally {
      setSaving(false);
    }
  }

  function formatDraft() {
    if (!parsedDraft) {
      toast.error(titleForLocale(locale, "JSON 语法错误", "Invalid JSON"));
      return;
    }
    setDraft(prettyJson(parsedDraft));
  }

  function syncFromStandard() {
    if (!standard) {
      return;
    }
    setDraft(prettyJson(standard));
  }

  const canEdit = view !== "standard";

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next) {
          onClose();
        }
      }}
    >
      <AppDialogContent
        className="max-w-5xl sm:max-w-5xl"
        title={
          <span className="flex items-center gap-2">
            <span>
              {titleForLocale(
                locale,
                "下游模型配置",
                "Downstream model config",
              )}
            </span>
            <Badge variant="outline">
              {gatewayKey.remark || titleForLocale(locale, "未命名", "Unnamed")}
            </Badge>
          </span>
        }
        footer={
          <>
            <div className="flex flex-col-reverse gap-2 sm:mr-auto sm:flex-row">
              <Button
                type="button"
                variant="outline"
                disabled={!standard}
                onClick={() =>
                  downloadText(
                    `pi-models-standard-${gatewayKey.id}.json`,
                    standardText,
                  )
                }
              >
                <Download data-icon="inline-start" />
                {titleForLocale(locale, "下载标准配置", "Download standard")}
              </Button>
              <Button
                type="button"
                variant="outline"
                disabled={!parsedDraft}
                onClick={() =>
                  downloadText(
                    `pi-models-custom-${gatewayKey.id}.json`,
                    parsedDraft ? prettyJson(parsedDraft) : draft,
                  )
                }
              >
                <Download data-icon="inline-start" />
                {titleForLocale(locale, "下载自定义配置", "Download custom")}
              </Button>
            </div>
            <Button
              type="button"
              onClick={() => void save()}
              disabled={loading || saving || !parsedDraft || !dirty}
            >
              {saving
                ? titleForLocale(locale, "保存中...", "Saving...")
                : titleForLocale(locale, "保存自定义配置", "Save custom")}
            </Button>
            <Button type="button" variant="outline" onClick={onClose}>
              {titleForLocale(locale, "关闭", "Close")}
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <EndpointCard
              locale={locale}
              label={titleForLocale(locale, "标准配置", "Standard")}
              path={modelsConfigPath(false)}
              curl={modelsConfigCurl(gatewayKey.api_key, false)}
              onCopy={() =>
                void copyText(
                  modelsConfigCurl(gatewayKey.api_key, false),
                  titleForLocale(locale, "cURL 已复制", "cURL copied"),
                )
              }
            />
            <EndpointCard
              locale={locale}
              label={titleForLocale(locale, "自定义配置", "Custom")}
              path={modelsConfigPath(true)}
              curl={modelsConfigCurl(gatewayKey.api_key, true)}
              onCopy={() =>
                void copyText(
                  modelsConfigCurl(gatewayKey.api_key, true),
                  titleForLocale(locale, "cURL 已复制", "cURL copied"),
                )
              }
            />
          </div>

          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <Tabs
              value={view}
              onValueChange={(value) => setView(value as ConfigView)}
              className="gap-0"
            >
              <TabsList>
                <TabsTrigger value="split">
                  {titleForLocale(locale, "对比", "Diff")}
                </TabsTrigger>
                <TabsTrigger value="standard">
                  {titleForLocale(locale, "标准配置", "Standard")}
                </TabsTrigger>
                <TabsTrigger value="custom">
                  {titleForLocale(locale, "自定义配置", "Custom")}
                </TabsTrigger>
              </TabsList>
            </Tabs>
            <div className="flex items-center gap-2">
              {dirty ? (
                <span className="text-xs text-muted-foreground">
                  {titleForLocale(locale, "未保存", "Unsaved")}
                </span>
              ) : null}
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={!canEdit || !standard}
                onClick={syncFromStandard}
              >
                {titleForLocale(locale, "从标准配置同步", "Sync from standard")}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={!canEdit}
                onClick={formatDraft}
              >
                {titleForLocale(locale, "格式化", "Format")}
              </Button>
            </div>
          </div>

          {loading || loadError || !standard ? (
            <div className="rounded-lg border py-16 text-center text-sm text-muted-foreground">
              {loadError || titleForLocale(locale, "加载中...", "Loading...")}
            </div>
          ) : (
            <div
              className={cn(
                "grid gap-3",
                view === "split" ? "lg:grid-cols-2" : "grid-cols-1",
              )}
            >
              {view !== "custom" ? (
                <JsonPane
                  title={titleForLocale(locale, "标准配置", "Standard")}
                  path="?type=pi"
                  onCopy={() =>
                    void copyText(
                      standardText,
                      titleForLocale(locale, "JSON 已复制", "JSON copied"),
                    )
                  }
                >
                  <pre className="m-0 min-h-80 max-h-[420px] overflow-auto whitespace-pre-wrap break-words p-3 font-mono text-xs leading-6">
                    {standardText}
                  </pre>
                </JsonPane>
              ) : null}
              {view !== "standard" ? (
                <JsonPane
                  title={titleForLocale(locale, "自定义配置", "Custom")}
                  path="?type=pi&custom=1"
                  onCopy={() =>
                    void copyText(
                      draft,
                      titleForLocale(locale, "JSON 已复制", "JSON copied"),
                    )
                  }
                >
                  <Textarea
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    spellCheck={false}
                    className="min-h-80 max-h-[420px] resize-none rounded-none border-0 bg-transparent font-mono text-xs leading-6 shadow-none focus-visible:ring-0"
                  />
                </JsonPane>
              ) : null}
            </div>
          )}

          {standard ? (
            <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
              <div className="flex flex-wrap items-center gap-1.5">
                {counts &&
                (counts.modified || counts.removed || counts.added) ? (
                  <>
                    {counts.modified > 0 ? (
                      <Badge variant="outline">
                        {titleForLocale(
                          locale,
                          `${counts.modified} 个已修改`,
                          `${counts.modified} modified`,
                        )}
                      </Badge>
                    ) : null}
                    {counts.removed > 0 ? (
                      <Badge variant="outline">
                        {titleForLocale(
                          locale,
                          `${counts.removed} 个已排除`,
                          `${counts.removed} removed`,
                        )}
                      </Badge>
                    ) : null}
                    {counts.added > 0 ? (
                      <Badge variant="outline">
                        {titleForLocale(
                          locale,
                          `${counts.added} 个新增`,
                          `${counts.added} added`,
                        )}
                      </Badge>
                    ) : null}
                  </>
                ) : parsedDraft ? (
                  <span>
                    {titleForLocale(
                      locale,
                      "两份配置一致",
                      "Configs are identical",
                    )}
                  </span>
                ) : null}
              </div>
              {parsedDraft ? null : (
                <span className="text-destructive">
                  {titleForLocale(locale, "JSON 语法错误", "Invalid JSON")}
                </span>
              )}
            </div>
          ) : null}
        </div>
      </AppDialogContent>
    </Dialog>
  );
}

function EndpointCard({
  locale,
  label,
  path,
  curl,
  onCopy,
}: {
  locale: Locale;
  label: string;
  path: string;
  curl: string;
  onCopy: () => void;
}) {
  return (
    <div className="space-y-2 rounded-lg border bg-muted/20 p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm font-medium">{label}</div>
          <div className="truncate font-mono text-[11px] text-muted-foreground">
            {path}
          </div>
        </div>
        <Button type="button" variant="outline" size="xs" onClick={onCopy}>
          <Copy data-icon="inline-start" />
          {titleForLocale(locale, "复制 cURL", "Copy cURL")}
        </Button>
      </div>
      <div className="truncate rounded-md border bg-background px-2 py-1.5 font-mono text-[11px] text-muted-foreground">
        {curl}
      </div>
    </div>
  );
}

function JsonPane({
  title,
  path,
  onCopy,
  children,
}: {
  title: string;
  path: string;
  onCopy: () => void;
  children: ReactNode;
}) {
  return (
    <div className="overflow-hidden rounded-lg border">
      <div className="flex items-center justify-between gap-2 border-b bg-muted/30 px-3 py-2 text-xs">
        <div className="min-w-0">
          <span className="font-medium text-foreground">{title}</span>
          <span className="ml-2 font-mono text-muted-foreground">{path}</span>
        </div>
        <Button type="button" variant="outline" size="xs" onClick={onCopy}>
          <Copy />
        </Button>
      </div>
      {children}
    </div>
  );
}
