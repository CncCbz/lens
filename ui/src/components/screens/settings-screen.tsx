"use client";

import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Palette,
  RotateCcw,
  Save,
  ServerCog,
  ShieldAlert,
  TestTubeDiagonal,
  UserRound,
  TimerReset,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Combobox, ComboboxOption } from "@/components/ui/combobox";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  ApiError,
  type AdminProfile,
  type AdminProfileUpdatePayload,
  type AdminProfileUpdateResponse,
  type SettingItem,
  apiRequest,
} from "@/lib/api";
import { setStoredToken } from "@/lib/auth";
import { titleForLocale, useI18n } from "@/lib/i18n";
import {
  DEFAULT_MODEL_TEST_PROMPTS,
  MODEL_TEST_PROMPTS_SETTING_KEY,
  parseModelTestPrompts,
  serializeModelTestPrompts,
} from "@/lib/model-test-prompts";
import { cn } from "@/lib/utils";
import { DashboardHeaderActions } from "@/components/shell/dashboard-header-actions";
import { AppearanceSettings } from "@/components/settings/appearance-settings";
import { AccountSettings } from "@/components/settings/account-settings";
import { GatewaySettings } from "@/components/settings/gateway-settings";
import { ErrorPolicySettings } from "@/components/settings/error-policy-settings";
import {
  emptyErrorPolicyDraft,
  parseErrorPolicyConfig,
  serializeErrorPolicyConfig,
  validateErrorPolicyDraft,
} from "@/lib/error-policy-config";
import type { RouterErrorPolicyDraft } from "@/lib/settings-types";

const PROXY_URL = "proxy_url";
const CORS_ALLOW_ORIGINS = "cors_allow_origins";
const CIRCUIT_BREAKER_THRESHOLD = "circuit_breaker_threshold";
const CIRCUIT_BREAKER_COOLDOWN = "circuit_breaker_cooldown";
const CIRCUIT_BREAKER_MAX_COOLDOWN = "circuit_breaker_max_cooldown";
const ROUTER_CIRCUIT_MINIMUM_REQUESTS = "router_circuit_minimum_requests";
const ROUTER_CIRCUIT_FAILURE_RATE_THRESHOLD =
  "router_circuit_failure_rate_threshold";
const HEALTH_WINDOW_SECONDS = "health_window_seconds";
const HEALTH_PENALTY_WEIGHT = "health_penalty_weight";
const HEALTH_MIN_SAMPLES = "health_min_samples";
const RELAY_LOG_BODY_ENABLED = "relay_log_body_enabled";
const MODEL_LIST_COMPAT_MODE_ENABLED = "model_list_compat_mode_enabled";
const ROUTER_ERROR_POLICY_CONFIG = "router_error_policy_config";
const SITE_NAME = "site_name";
const SITE_LOGO_URL = "site_logo_url";
const TIME_ZONE = "time_zone";

const TIME_ZONE_OPTIONS = [
  { value: "Asia/Shanghai", label: "Asia/Shanghai" },
  { value: "UTC", label: "UTC" },
  { value: "Asia/Tokyo", label: "Asia/Tokyo" },
  { value: "Europe/London", label: "Europe/London" },
  { value: "America/New_York", label: "America/New_York" },
] as const;

type DraftState = {
  proxyUrl: string;
  corsAllowOrigins: string;
  circuitBreakerThreshold: string;
  circuitBreakerCooldown: string;
  circuitBreakerMaxCooldown: string;
  routerCircuitMinimumRequests: string;
  routerCircuitFailureRateThreshold: string;
  healthWindowSeconds: string;
  healthPenaltyWeight: string;
  healthMinSamples: string;
  relayLogBodyEnabled: boolean;
  modelListCompatModeEnabled: boolean;
  siteName: string;
  siteLogoUrl: string;
  timeZone: string;
  modelTestPrompts: string;
  routerErrorPolicyConfig: RouterErrorPolicyDraft;
};

const EMPTY_DRAFT: DraftState = {
  proxyUrl: "",
  corsAllowOrigins: "*",
  circuitBreakerThreshold: "3",
  circuitBreakerCooldown: "60",
  circuitBreakerMaxCooldown: "600",
  routerCircuitMinimumRequests: "5",
  routerCircuitFailureRateThreshold: "0.6",
  healthWindowSeconds: "300",
  healthPenaltyWeight: "0.5",
  healthMinSamples: "10",
  relayLogBodyEnabled: false,
  modelListCompatModeEnabled: false,
  siteName: "Lens",
  siteLogoUrl: "",
  timeZone: "Asia/Shanghai",
  modelTestPrompts: DEFAULT_MODEL_TEST_PROMPTS.join("\n"),
  routerErrorPolicyConfig: emptyErrorPolicyDraft(),
};

function parseSettings(items: SettingItem[] | undefined) {
  const mapping = new Map((items ?? []).map((item) => [item.key, item.value]));
  return {
    proxyUrl: mapping.get(PROXY_URL) ?? "",
    corsAllowOrigins: mapping.get(CORS_ALLOW_ORIGINS) ?? "*",
    circuitBreakerThreshold: mapping.get(CIRCUIT_BREAKER_THRESHOLD) ?? "3",
    circuitBreakerCooldown: mapping.get(CIRCUIT_BREAKER_COOLDOWN) ?? "60",
    circuitBreakerMaxCooldown:
      mapping.get(CIRCUIT_BREAKER_MAX_COOLDOWN) ?? "600",
    routerCircuitMinimumRequests:
      mapping.get(ROUTER_CIRCUIT_MINIMUM_REQUESTS) ?? "5",
    routerCircuitFailureRateThreshold:
      mapping.get(ROUTER_CIRCUIT_FAILURE_RATE_THRESHOLD) ?? "0.6",
    healthWindowSeconds: mapping.get(HEALTH_WINDOW_SECONDS) ?? "300",
    healthPenaltyWeight: mapping.get(HEALTH_PENALTY_WEIGHT) ?? "0.5",
    healthMinSamples: mapping.get(HEALTH_MIN_SAMPLES) ?? "10",
    relayLogBodyEnabled:
      (mapping.get(RELAY_LOG_BODY_ENABLED) ?? "false").trim().toLowerCase() ===
      "true",
    modelListCompatModeEnabled:
      (mapping.get(MODEL_LIST_COMPAT_MODE_ENABLED) ?? "false")
        .trim()
        .toLowerCase() === "true",
    siteName: mapping.get(SITE_NAME) ?? "Lens",
    siteLogoUrl: mapping.get(SITE_LOGO_URL) ?? "",
    timeZone: mapping.get(TIME_ZONE) ?? "Asia/Shanghai",
    modelTestPrompts: parseModelTestPrompts(
      mapping.get(MODEL_TEST_PROMPTS_SETTING_KEY),
    ).join("\n"),
    routerErrorPolicyConfig: parseErrorPolicyConfig(
      mapping.get(ROUTER_ERROR_POLICY_CONFIG),
      {
        threshold: Number(mapping.get(CIRCUIT_BREAKER_THRESHOLD) ?? "3") || 3,
        cooldown: Number(mapping.get(CIRCUIT_BREAKER_COOLDOWN) ?? "60") || 60,
        maxCooldown:
          Number(mapping.get(CIRCUIT_BREAKER_MAX_COOLDOWN) ?? "600") || 600,
      },
    ),
  } satisfies DraftState;
}

function normalizeOriginList(rawValue: string) {
  const items: string[] = [];
  const seen = new Set<string>();
  for (const chunk of rawValue
    .replace(/\r/g, "\n")
    .replaceAll("，", ",")
    .split("\n")) {
    for (const part of chunk.split(",")) {
      const normalized = part.trim();
      if (!normalized || seen.has(normalized)) {
        continue;
      }
      seen.add(normalized);
      items.push(normalized);
    }
  }
  if (items.includes("*")) {
    return "*";
  }
  return items.join(",");
}

function SettingCard({
  title,
  className,
  children,
}: {
  title: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section
      className={cn(
        "min-w-0 rounded-2xl border bg-card px-4 py-4 shadow-sm sm:px-6 sm:py-5",
        className,
      )}
    >
      <header className="border-b pb-4">
        <h2 className="text-base font-semibold text-foreground">{title}</h2>
      </header>
      <div className="flex max-w-2xl flex-col gap-4 pt-5">{children}</div>
    </section>
  );
}

export function SettingsScreen() {
  const queryClient = useQueryClient();
  const { locale } = useI18n();
  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: () => apiRequest<SettingItem[]>("/admin/settings"),
    staleTime: 5 * 60_000,
    refetchOnMount: "always",
  });
  const { data: profile } = useQuery({
    queryKey: ["auth-me"],
    queryFn: () => apiRequest<AdminProfile>("/admin/session"),
    staleTime: 5 * 60_000,
    refetchOnMount: "always",
  });

  const [draft, setDraft] = useState<DraftState>(EMPTY_DRAFT);
  const [accountForm, setAccountForm] = useState({
    username: "admin",
    currentPassword: "",
    newPassword: "",
    confirmPassword: "",
  });
  const [saving, setSaving] = useState(false);
  const [updatingAccount, setUpdatingAccount] = useState(false);

  useEffect(() => {
    if (settingsQuery.isSuccess) {
      setDraft(parseSettings(settingsQuery.data));
    }
  }, [settingsQuery.data, settingsQuery.isSuccess]);

  useEffect(() => {
    setAccountForm((current) => ({
      ...current,
      username: profile?.username || "admin",
    }));
  }, [profile?.username]);

  useEffect(() => {
    if (!settingsQuery.isError) return;
    toast.error(
      titleForLocale(locale, "设置加载失败", "Failed to load settings"),
      {
        id: "settings-load-error",
        description:
          settingsQuery.error instanceof Error
            ? settingsQuery.error.message
            : titleForLocale(
                locale,
                "无法读取系统设置",
                "Unable to read system settings",
              ),
      },
    );
  }, [locale, settingsQuery.error, settingsQuery.isError]);

  function setDraftValue<K extends keyof DraftState>(
    key: K,
    value: DraftState[K],
  ) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  async function invalidateSettingsDerived() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["public-branding"] }),
      queryClient.invalidateQueries({ queryKey: ["app-info"] }),
      queryClient.invalidateQueries({ queryKey: ["model-groups"] }),
      queryClient.invalidateQueries({ queryKey: ["cronjobs"] }),
      queryClient.invalidateQueries({ queryKey: ["overview-summary"] }),
      queryClient.invalidateQueries({ queryKey: ["overview-daily"] }),
      queryClient.invalidateQueries({ queryKey: ["overview-health"] }),
      queryClient.invalidateQueries({ queryKey: ["overview-usage"] }),
      queryClient.invalidateQueries({ queryKey: ["overview-performance"] }),
    ]);
  }

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["settings"] }),
      invalidateSettingsDerived(),
    ]);
  }

  async function submitSettings() {
    if (!settingsQuery.isSuccess) {
      return;
    }
    const errorPolicyError = validateErrorPolicyDraft(
      draft.routerErrorPolicyConfig,
      locale === "zh-CN" ? "zh-CN" : "en-US",
    );
    if (errorPolicyError) {
      toast.error(errorPolicyError);
      return;
    }
    setSaving(true);
    try {
      const items: SettingItem[] = [
        { key: PROXY_URL, value: draft.proxyUrl.trim() },
        {
          key: CORS_ALLOW_ORIGINS,
          value: normalizeOriginList(draft.corsAllowOrigins) || "*",
        },
        {
          key: CIRCUIT_BREAKER_THRESHOLD,
          value: draft.circuitBreakerThreshold.trim() || "3",
        },
        {
          key: CIRCUIT_BREAKER_COOLDOWN,
          value: draft.circuitBreakerCooldown.trim() || "60",
        },
        {
          key: CIRCUIT_BREAKER_MAX_COOLDOWN,
          value: draft.circuitBreakerMaxCooldown.trim() || "600",
        },
        {
          key: ROUTER_CIRCUIT_MINIMUM_REQUESTS,
          value: draft.routerCircuitMinimumRequests.trim() || "5",
        },
        {
          key: ROUTER_CIRCUIT_FAILURE_RATE_THRESHOLD,
          value: draft.routerCircuitFailureRateThreshold.trim() || "0.6",
        },
        {
          key: HEALTH_WINDOW_SECONDS,
          value: draft.healthWindowSeconds.trim() || "300",
        },
        {
          key: HEALTH_PENALTY_WEIGHT,
          value: draft.healthPenaltyWeight.trim() || "0.5",
        },
        {
          key: HEALTH_MIN_SAMPLES,
          value: draft.healthMinSamples.trim() || "10",
        },
        {
          key: RELAY_LOG_BODY_ENABLED,
          value: draft.relayLogBodyEnabled ? "true" : "false",
        },
        {
          key: MODEL_LIST_COMPAT_MODE_ENABLED,
          value: draft.modelListCompatModeEnabled ? "true" : "false",
        },
        { key: SITE_NAME, value: draft.siteName.trim() || "Lens" },
        { key: SITE_LOGO_URL, value: draft.siteLogoUrl.trim() },
        { key: TIME_ZONE, value: draft.timeZone.trim() || "Asia/Shanghai" },
        {
          key: MODEL_TEST_PROMPTS_SETTING_KEY,
          value: serializeModelTestPrompts(draft.modelTestPrompts),
        },
        {
          key: ROUTER_ERROR_POLICY_CONFIG,
          value: serializeErrorPolicyConfig(draft.routerErrorPolicyConfig, {
            threshold: Number(draft.circuitBreakerThreshold.trim() || "3") || 3,
            cooldown: Number(draft.circuitBreakerCooldown.trim() || "60") || 60,
            maxCooldown:
              Number(draft.circuitBreakerMaxCooldown.trim() || "600") || 600,
          }),
        },
      ];
      const updatedSettings = await apiRequest<SettingItem[]>(
        "/admin/settings",
        {
          method: "PUT",
          body: JSON.stringify({ items }),
        },
      );
      queryClient.setQueryData<SettingItem[]>(["settings"], updatedSettings);
      toast.success(titleForLocale(locale, "设置已保存", "Settings saved"));
      await invalidateSettingsDerived();
    } catch (requestError) {
      const message =
        requestError instanceof ApiError
          ? requestError.message
          : titleForLocale(locale, "保存设置失败", "Failed to save settings");
      toast.error(message);
    } finally {
      setSaving(false);
    }
  }

  async function submitAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const nextUsername = accountForm.username.trim();
    const wantsPasswordUpdate = Boolean(
      accountForm.currentPassword ||
      accountForm.newPassword ||
      accountForm.confirmPassword,
    );
    const usernameChanged = nextUsername !== (profile?.username || "admin");

    if (!nextUsername) {
      toast.error(
        titleForLocale(locale, "用户名不能为空", "Username is required"),
      );
      return;
    }

    if (!usernameChanged && !wantsPasswordUpdate) {
      toast.success(
        titleForLocale(
          locale,
          "没有需要保存的账号变更",
          "No account changes to save",
        ),
      );
      return;
    }

    if (
      wantsPasswordUpdate &&
      (!accountForm.currentPassword || !accountForm.newPassword)
    ) {
      toast.error(
        titleForLocale(
          locale,
          "请填写完整密码",
          "Please fill in both passwords",
        ),
      );
      return;
    }

    if (accountForm.newPassword !== accountForm.confirmPassword) {
      toast.error(
        titleForLocale(
          locale,
          "两次新密码不一致",
          "The new passwords do not match",
        ),
      );
      return;
    }

    const payload: AdminProfileUpdatePayload = {
      username: nextUsername,
      current_password: accountForm.currentPassword,
      new_password: accountForm.newPassword,
    };
    setUpdatingAccount(true);
    try {
      const response = await apiRequest<AdminProfileUpdateResponse>(
        "/admin/profile",
        {
          method: "PUT",
          body: JSON.stringify(payload),
        },
      );
      setStoredToken(response.access_token);
      window.sessionStorage.removeItem("lens_admin_profile_cache");
      queryClient.setQueryData(["auth-me"], response.profile);
      await queryClient.invalidateQueries({ queryKey: ["auth-me"] });
      toast.success(titleForLocale(locale, "账号已更新", "Account updated"));
      setAccountForm({
        username: response.profile.username,
        currentPassword: "",
        newPassword: "",
        confirmPassword: "",
      });
    } catch (requestError) {
      const message =
        requestError instanceof ApiError
          ? requestError.message
          : titleForLocale(locale, "更新账号失败", "Failed to update account");
      toast.error(message);
    } finally {
      setUpdatingAccount(false);
    }
  }

  const refreshLabel = titleForLocale(locale, "刷新", "Refresh");
  const saveSettingsLabel = saving
    ? titleForLocale(locale, "保存中...", "Saving...")
    : titleForLocale(locale, "保存设置", "Save settings");
  const settingsTabs = [
    {
      value: "appearance",
      label: titleForLocale(locale, "站点外观", "Appearance"),
      icon: Palette,
    },
    {
      value: "account",
      label: titleForLocale(locale, "账号", "Account"),
      icon: UserRound,
    },
    {
      value: "time",
      label: titleForLocale(locale, "时间", "Time"),
      icon: TimerReset,
    },
    {
      value: "gateway",
      label: titleForLocale(locale, "网关", "Gateway"),
      icon: ServerCog,
    },
    {
      value: "model-test",
      label: titleForLocale(locale, "模型测试", "Model test"),
      icon: TestTubeDiagonal,
    },
    {
      value: "circuit-breaker",
      label: titleForLocale(locale, "路由", "Routing"),
      icon: ShieldAlert,
    },
  ] as const;

  return (
    <>
      <DashboardHeaderActions>
        <div className="flex items-center justify-end gap-2">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                type="button"
                aria-label={refreshLabel}
                onClick={() => void refresh()}
              >
                <RotateCcw data-icon="inline-start" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom" align="end">
              {refreshLabel}
            </TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label={saveSettingsLabel}
                disabled={saving || !settingsQuery.isSuccess}
                onClick={() => void submitSettings()}
              >
                <Save data-icon="inline-start" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom" align="end">
              {saveSettingsLabel}
            </TooltipContent>
          </Tooltip>
        </div>
      </DashboardHeaderActions>

      <section className="min-w-0">
        <Tabs
          defaultValue="appearance"
          orientation="vertical"
          className="grid min-w-0 gap-6 lg:grid-cols-[220px_minmax(0,760px)] lg:items-start"
        >
          <TabsList className="flex h-auto w-full flex-row justify-start gap-1 overflow-x-auto rounded-none bg-transparent p-0 text-foreground lg:sticky lg:top-4 lg:flex-col lg:items-start lg:overflow-visible">
            {settingsTabs.map((item) => {
              const Icon = item.icon;
              return (
                <TabsTrigger
                  key={item.value}
                  value={item.value}
                  className="h-9 w-40 shrink-0 justify-start gap-2 rounded-md px-3 text-sm data-[state=active]:bg-sidebar-accent data-[state=active]:shadow-none"
                >
                  <Icon className="size-4" />
                  <span>{item.label}</span>
                </TabsTrigger>
              );
            })}
          </TabsList>

          <div className="min-w-0">
            <TabsContent value="appearance" className="mt-0">
              <SettingCard
                title={titleForLocale(locale, "站点外观", "Appearance")}
              >
                <AppearanceSettings
                  siteName={draft.siteName}
                  siteLogoUrl={draft.siteLogoUrl}
                  onSiteNameChange={(value) => setDraftValue("siteName", value)}
                  onSiteLogoUrlChange={(value) =>
                    setDraftValue("siteLogoUrl", value)
                  }
                />
              </SettingCard>
            </TabsContent>

            <TabsContent value="account" className="mt-0">
              <SettingCard title={titleForLocale(locale, "账号", "Account")}>
                <AccountSettings
                  username={accountForm.username}
                  currentPassword={accountForm.currentPassword}
                  newPassword={accountForm.newPassword}
                  confirmPassword={accountForm.confirmPassword}
                  updatingAccount={updatingAccount}
                  onUsernameChange={(value) =>
                    setAccountForm((current) => ({
                      ...current,
                      username: value,
                    }))
                  }
                  onCurrentPasswordChange={(value) =>
                    setAccountForm((current) => ({
                      ...current,
                      currentPassword: value,
                    }))
                  }
                  onNewPasswordChange={(value) =>
                    setAccountForm((current) => ({
                      ...current,
                      newPassword: value,
                    }))
                  }
                  onConfirmPasswordChange={(value) =>
                    setAccountForm((current) => ({
                      ...current,
                      confirmPassword: value,
                    }))
                  }
                  onSubmit={submitAccount}
                />
              </SettingCard>
            </TabsContent>

            <TabsContent value="time" className="mt-0">
              <SettingCard title={titleForLocale(locale, "时间", "Time")}>
                <FieldGroup>
                  <Field>
                    <FieldLabel>
                      {titleForLocale(locale, "时区", "Time zone")}
                    </FieldLabel>
                    <Combobox
                      className="w-full"
                      value={draft.timeZone || "Asia/Shanghai"}
                      onChange={(event) =>
                        setDraftValue("timeZone", event.target.value)
                      }
                    >
                      {TIME_ZONE_OPTIONS.map((option) => (
                        <ComboboxOption key={option.value} value={option.value}>
                          {option.label}
                        </ComboboxOption>
                      ))}
                    </Combobox>
                  </Field>
                </FieldGroup>
              </SettingCard>
            </TabsContent>

            <TabsContent value="gateway" className="mt-0">
              <SettingCard title={titleForLocale(locale, "网关", "Gateway")}>
                <GatewaySettings
                  proxyUrl={draft.proxyUrl}
                  corsAllowOrigins={draft.corsAllowOrigins}
                  relayLogBodyEnabled={draft.relayLogBodyEnabled}
                  modelListCompatModeEnabled={draft.modelListCompatModeEnabled}
                  onProxyUrlChange={(value) => setDraftValue("proxyUrl", value)}
                  onCorsAllowOriginsChange={(value) =>
                    setDraftValue("corsAllowOrigins", value)
                  }
                  onRelayLogBodyEnabledChange={(checked) =>
                    setDraftValue("relayLogBodyEnabled", checked)
                  }
                  onModelListCompatModeEnabledChange={(checked) =>
                    setDraftValue("modelListCompatModeEnabled", checked)
                  }
                />
              </SettingCard>
            </TabsContent>

            <TabsContent value="model-test" className="mt-0">
              <SettingCard
                title={titleForLocale(locale, "模型测试", "Model test")}
              >
                <FieldGroup>
                  <Field>
                    <FieldLabel>
                      {titleForLocale(locale, "预设问题", "Preset prompts")}
                    </FieldLabel>
                    <Textarea
                      className="min-h-[132px]"
                      value={draft.modelTestPrompts}
                      onChange={(event) =>
                        setDraftValue("modelTestPrompts", event.target.value)
                      }
                      placeholder={DEFAULT_MODEL_TEST_PROMPTS.join("\n")}
                    />
                  </Field>
                </FieldGroup>
              </SettingCard>
            </TabsContent>

            <TabsContent value="circuit-breaker" className="mt-0">
              <SettingCard title={titleForLocale(locale, "路由", "Routing")}>
                <div className="space-y-6">
                  <div>
                    <div className="mb-2 text-sm font-medium">
                      {titleForLocale(locale, "健康评分", "Health scoring")}
                    </div>
                    <FieldGroup className="grid gap-4 sm:grid-cols-3">
                      <Field>
                        <FieldLabel>
                          {titleForLocale(locale, "窗口秒数", "Window seconds")}
                        </FieldLabel>
                        <Input
                          type="number"
                          min="1"
                          value={draft.healthWindowSeconds}
                          onChange={(event) =>
                            setDraftValue(
                              "healthWindowSeconds",
                              event.target.value,
                            )
                          }
                        />
                      </Field>
                      <Field>
                        <FieldLabel>
                          {titleForLocale(locale, "惩罚权重", "Penalty weight")}
                        </FieldLabel>
                        <Input
                          type="number"
                          min="0"
                          step="0.1"
                          value={draft.healthPenaltyWeight}
                          onChange={(event) =>
                            setDraftValue(
                              "healthPenaltyWeight",
                              event.target.value,
                            )
                          }
                        />
                      </Field>
                      <Field>
                        <FieldLabel>
                          {titleForLocale(locale, "最小样本数", "Min samples")}
                        </FieldLabel>
                        <Input
                          type="number"
                          min="1"
                          value={draft.healthMinSamples}
                          onChange={(event) =>
                            setDraftValue(
                              "healthMinSamples",
                              event.target.value,
                            )
                          }
                        />
                      </Field>
                    </FieldGroup>
                  </div>

                  <div className="border-t pt-6">
                    <ErrorPolicySettings
                      locale={locale}
                      draft={draft.routerErrorPolicyConfig}
                      globals={{
                        threshold:
                          Number(draft.circuitBreakerThreshold.trim() || "3") ||
                          3,
                        cooldown:
                          Number(draft.circuitBreakerCooldown.trim() || "60") ||
                          60,
                        maxCooldown:
                          Number(
                            draft.circuitBreakerMaxCooldown.trim() || "600",
                          ) || 600,
                      }}
                      onChange={(next) =>
                        setDraft((current) => ({
                          ...current,
                          routerErrorPolicyConfig: next,
                        }))
                      }
                    />
                  </div>
                </div>
              </SettingCard>
            </TabsContent>
          </div>
        </Tabs>
      </section>
    </>
  );
}
