"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Save } from "lucide-react";
import { toast } from "sonner";

import { DashboardHeaderActions } from "@/components/shell/dashboard-header-actions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Field, FieldContent, FieldDescription } from "@/components/ui/field";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  ApiError,
  type MultimodalRelayConfig,
  type MultimodalRelayUpdate,
  apiRequest,
} from "@/lib/api";
import { ModalityIcons } from "@/components/screens/multimodal-relay/modality-icons";
import { titleForLocale, useI18n } from "@/lib/i18n";
import { Switch } from "@/components/ui/switch";

const NONE_VALUE = "__none__";

export function MultimodalRelayScreen() {
  const { locale } = useI18n();
  const [draft, setDraft] = useState<MultimodalRelayUpdate | null>(null);

  const { data, isSuccess, refetch } = useQuery({
    queryKey: ["multimodal-relay"],
    queryFn: () => apiRequest<MultimodalRelayConfig>("/admin/multimodal-relay"),
    staleTime: 30_000,
  });

  const config = data ?? null;
  const value: MultimodalRelayUpdate | null =
    draft ??
    (config
      ? {
          enabled: config.enabled,
          image_group_id: config.image_group_id,
          audio_group_id: config.audio_group_id,
        }
      : null);
  const dirty = useMemo(() => {
    if (!draft || !config) return false;
    return (
      draft.enabled !== config.enabled ||
      draft.image_group_id !== config.image_group_id ||
      draft.audio_group_id !== config.audio_group_id
    );
  }, [draft, config]);

  const executionGroups = useMemo(
    () => (config?.groups ?? []).filter((group) => !group.route_group_id),
    [config],
  );
  const imageGroups = useMemo(
    () => executionGroups.filter((group) => group.effective_supports_image),
    [executionGroups],
  );
  const audioGroups = useMemo(
    () => executionGroups.filter((group) => group.effective_supports_audio),
    [executionGroups],
  );

  const imageRelayConfigured = Boolean(value?.image_group_id);
  const audioRelayConfigured = Boolean(value?.audio_group_id);
  const affectedGroups = useMemo(
    () =>
      executionGroups.filter(
        (group) =>
          (imageRelayConfigured && !group.effective_supports_image) ||
          (audioRelayConfigured && !group.effective_supports_audio),
      ),
    [executionGroups, imageRelayConfigured, audioRelayConfigured],
  );

  function patch(update: Partial<MultimodalRelayUpdate>) {
    setDraft((current) => ({
      enabled: config?.enabled ?? false,
      image_group_id: config?.image_group_id ?? "",
      audio_group_id: config?.audio_group_id ?? "",
      ...current,
      ...update,
    }));
  }

  async function submit() {
    if (!value) return;
    try {
      const updated = await apiRequest<MultimodalRelayConfig>(
        "/admin/multimodal-relay",
        {
          method: "PUT",
          body: JSON.stringify(value),
        },
      );
      setDraft(null);
      toast.success(titleForLocale(locale, "已保存", "Saved"));
      void refetch();
      return updated;
    } catch (requestError) {
      const message =
        requestError instanceof ApiError
          ? requestError.message
          : titleForLocale(locale, "保存失败", "Failed to save");
      toast.error(message);
    }
  }

  const saveLabel = titleForLocale(locale, "保存", "Save");
  const enabled = value?.enabled ?? false;
  const imageGroupName = value?.image_group_id
    ? (executionGroups.find((g) => g.group_id === value.image_group_id)?.name ??
      value.image_group_id)
    : "";
  const audioGroupName = value?.audio_group_id
    ? (executionGroups.find((g) => g.group_id === value.audio_group_id)?.name ??
      value.audio_group_id)
    : "";

  return (
    <>
      <DashboardHeaderActions>
        <div className="flex items-center justify-end gap-2">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label={saveLabel}
                disabled={!dirty || !isSuccess}
                onClick={() => void submit()}
              >
                <Save data-icon="inline-start" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom" align="end">
              {saveLabel}
            </TooltipContent>
          </Tooltip>
        </div>
      </DashboardHeaderActions>

      <section className="flex min-w-0 flex-col gap-4">
        {/* Status card */}
        <Card className="px-4">
          <div className="flex items-center justify-between px-4">
            <div className="flex items-center gap-3">
              <div className="flex size-8 items-center justify-center rounded-lg bg-emerald-500/15 text-emerald-500">
                <CheckCircle2 className="size-4" />
              </div>
              <div>
                <div className="text-sm font-semibold">
                  {enabled
                    ? titleForLocale(
                        locale,
                        "多模态降级已启用",
                        "Relay enabled",
                      )
                    : titleForLocale(
                        locale,
                        "多模态降级未启用",
                        "Relay disabled",
                      )}
                </div>
                <div className="text-xs text-muted-foreground">
                  {enabled
                    ? titleForLocale(
                        locale,
                        `图片 → ${imageGroupName || "未配置"} · 音频 → ${
                          audioGroupName || "未配置"
                        } · ${affectedGroups.length} 个模型组受影响`,
                        `Image → ${imageGroupName || "unset"} · Audio → ${
                          audioGroupName || "unset"
                        } · ${affectedGroups.length} groups affected`,
                      )
                    : titleForLocale(
                        locale,
                        "不支持多模态的模型组收到图片/音频时将原样透传",
                        "Groups without multimodal support pass media through untouched",
                      )}
                </div>
              </div>
            </div>
            <Switch
              checked={enabled}
              onCheckedChange={(checked) => patch({ enabled: checked })}
            />
          </div>
        </Card>

        {/* Channel cards */}
        <div className="grid gap-4 md:grid-cols-2">
          <Card className="px-4">
            <CardTitle className="flex items-center justify-between">
              <span>
                {titleForLocale(locale, "图片处理组", "Image helper group")}
              </span>
              <Badge variant={value?.image_group_id ? "default" : "secondary"}>
                {value?.image_group_id
                  ? titleForLocale(locale, "已配置", "Set")
                  : titleForLocale(locale, "未配置", "Unset")}
              </Badge>
            </CardTitle>
            <Field>
              <FieldContent>
                <Select
                  value={value?.image_group_id || NONE_VALUE}
                  onValueChange={(group_id) =>
                    patch({
                      image_group_id: group_id === NONE_VALUE ? "" : group_id,
                    })
                  }
                >
                  <SelectTrigger aria-label="image-group">
                    <SelectValue
                      placeholder={titleForLocale(
                        locale,
                        "选择支持图片的模型组",
                        "Select a group that supports images",
                      )}
                    />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NONE_VALUE}>
                      {titleForLocale(locale, "未配置", "Unset")}
                    </SelectItem>
                    {imageGroups.map((group) => (
                      <SelectItem key={group.group_id} value={group.group_id}>
                        {group.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </FieldContent>
              <FieldDescription>
                {titleForLocale(
                  locale,
                  "仅列出支持 image 输入的模型组",
                  "Only groups that support image input are listed",
                )}
              </FieldDescription>
            </Field>
          </Card>

          <Card className="px-4">
            <CardTitle className="flex items-center justify-between">
              <span>
                {titleForLocale(locale, "音频处理组", "Audio helper group")}
              </span>
              <Badge variant={value?.audio_group_id ? "default" : "secondary"}>
                {value?.audio_group_id
                  ? titleForLocale(locale, "已配置", "Set")
                  : titleForLocale(locale, "未配置", "Unset")}
              </Badge>
            </CardTitle>
            <Field>
              <FieldContent>
                <Select
                  value={value?.audio_group_id || NONE_VALUE}
                  onValueChange={(group_id) =>
                    patch({
                      audio_group_id: group_id === NONE_VALUE ? "" : group_id,
                    })
                  }
                >
                  <SelectTrigger aria-label="audio-group">
                    <SelectValue
                      placeholder={titleForLocale(
                        locale,
                        "选择支持音频的模型组",
                        "Select a group that supports audio",
                      )}
                    />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NONE_VALUE}>
                      {titleForLocale(locale, "未配置", "Unset")}
                    </SelectItem>
                    {audioGroups.map((group) => (
                      <SelectItem key={group.group_id} value={group.group_id}>
                        {group.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </FieldContent>
              <FieldDescription>
                {titleForLocale(
                  locale,
                  "未配置时，请求中的音频内容不降级、原样透传",
                  "Audio is passed through untouched when unset",
                )}
              </FieldDescription>
            </Field>
          </Card>
        </div>

        {/* Affected groups */}
        <Card className="px-4">
          <CardTitle className="flex items-center justify-between">
            <span>
              {titleForLocale(locale, "受影响的模型组", "Affected groups")}
            </span>
            <Badge variant="secondary">{affectedGroups.length}</Badge>
          </CardTitle>
          <CardDescription>
            {titleForLocale(
              locale,
              "以下模型组收到图片/音频时将被降级处理，可调整每组的多模态能力判定",
              "Groups listed below are downgraded when they receive media; adjust each group capability here",
            )}
          </CardDescription>
          <div className="flex flex-col">
            {affectedGroups.map((group) => (
              <div
                key={group.group_id}
                className="flex items-center justify-between gap-3 border-t py-3 first:border-t-0"
              >
                <div className="flex min-w-0 flex-col gap-1.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{group.name}</span>
                    <ModalityIcons
                      modalities={group.effective_modalities}
                      className="gap-1.5"
                    />
                    {group.multimodal === "manual" ||
                    group.multimodal === "on" ||
                    group.multimodal === "off" ? (
                      <Badge variant="secondary">
                        {titleForLocale(locale, "手动", "Manual")}
                      </Badge>
                    ) : null}
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {imageRelayConfigured && !group.effective_supports_image ? (
                      <Badge
                        variant="outline"
                        className="border-transparent bg-amber-500/12 px-2 py-0.5 text-amber-700 dark:text-amber-300"
                      >
                        {titleForLocale(locale, "图片降级", "Image relay")}
                      </Badge>
                    ) : null}
                    {audioRelayConfigured && !group.effective_supports_audio ? (
                      <Badge
                        variant="outline"
                        className="border-transparent bg-amber-500/12 px-2 py-0.5 text-amber-700 dark:text-amber-300"
                      >
                        {titleForLocale(locale, "音频降级", "Audio relay")}
                      </Badge>
                    ) : null}
                  </div>
                </div>
              </div>
            ))}
            {affectedGroups.length === 0 ? (
              <div className="py-6 text-center text-sm text-muted-foreground">
                {titleForLocale(
                  locale,
                  imageRelayConfigured || audioRelayConfigured
                    ? "所有模型组均支持已配置的降级模态"
                    : "未配置降级处理组",
                  imageRelayConfigured || audioRelayConfigured
                    ? "All groups support the configured relay modalities"
                    : "No relay helper groups configured",
                )}
              </div>
            ) : null}
          </div>
        </Card>
      </section>
    </>
  );
}
