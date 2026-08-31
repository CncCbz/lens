"use client";

import { useMemo, useState } from "react";
import type { Dispatch, FormEventHandler, SetStateAction } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertCircle,
  ChevronDown,
  ChevronsUpDown,
  Plus,
  RefreshCcw,
  Search,
  Sparkles,
  X,
} from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { AppDialogContent, Dialog } from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Combobox, ComboboxOption } from "@/components/ui/combobox";
import { ProtocolMultiSelect } from "@/components/ui/protocol-multi-select";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { ModalityToggleRow } from "@/components/screens/multimodal-relay/modality-toggle-row";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  apiRequest,
  type GatewayApiKey,
  type ModelGroup,
  type ModelGroupCandidateItem,
  type ProtocolKind,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  CandidateRow,
  EditablePriceRow,
  FoldedMemberRow,
  PriorityLevelCard,
  StrategyToggle,
} from "./components";
import {
  applyWeightPreset,
  membersSharePct,
  modelFoldKey,
  protocolConfigIdFromChannelId,
  protocolOptions,
  type CandidateChannelGroup,
  type CandidateSearchMode,
  type FoldedMember,
  type FormItem,
  type FormState,
} from "./shared";

function hasConfiguredJson(value: string): boolean {
  try {
    const parsed: unknown = JSON.parse(value.trim() || "{}");
    return (
      !!parsed &&
      typeof parsed === "object" &&
      !Array.isArray(parsed) &&
      Object.keys(parsed as Record<string, unknown>).length > 0
    );
  } catch {
    return value.trim().length > 0;
  }
}

function gatewayKeyLabel(
  key: Pick<GatewayApiKey, "id" | "remark" | "api_key">,
) {
  const remark = key.remark.trim();
  if (remark) return remark;
  const secret = key.api_key.trim();
  if (!secret) return key.id;
  if (secret.length <= 8) return secret;
  return `${secret.slice(0, 8)}…`;
}

function hasConfiguredPricing(form: FormState): boolean {
  const values = [
    form.input_price_per_million,
    form.output_price_per_million,
    form.cache_read_price_per_million,
    form.cache_write_price_per_million,
  ];
  return values.some((value) => value.trim() !== "" && value.trim() !== "0");
}

export function GroupEditorDialog({
  dialogOpen,
  setDialogOpen,
  editingId,
  locale,
  submit,
  form,
  setForm,
  toggleProtocol,
  routeTargetOptions,
  changeRouteTarget,
  candidateSearchMode,
  changeCandidateSearchMode,
  candidateSearch,
  changeCandidateSearch,
  addMatchedItems,
  candidateRegexInvalid,
  filteredCandidates,
  refetchCandidates,
  isFetchingCandidates,
  applySavedFilter,
  clearSavedFilter,
  groupedCandidates,
  expandedChannels,
  toggleChannel,
  foldedMembers,
  addCandidate,
  sitesIsError,
  candidateIsError,
  candidateListError,
  invalidSelectedMemberCount,
  removeInvalidItems,
  setAllMembersEnabled,
  showEnabledOnly,
  setShowEnabledOnly,
  visibleFoldedMembers,
  draggingIndex,
  testingModel,
  toggleFoldedMember,
  removeFoldedMember,
  setDraggingIndex,
  moveFoldedMember,
  onOpenModelTest,
  onGeneratePiConfig,
  generatingPiConfig,
}: {
  dialogOpen: boolean;
  setDialogOpen: Dispatch<SetStateAction<boolean>>;
  editingId: string | null;
  locale: "zh-CN" | "en-US";
  submit: FormEventHandler<HTMLFormElement>;
  form: FormState;
  setForm: Dispatch<SetStateAction<FormState>>;
  toggleProtocol: (protocol: ProtocolKind) => void;
  routeTargetOptions: ModelGroup[];
  changeRouteTarget: (routeGroupId: string) => void;
  candidateSearchMode: CandidateSearchMode;
  changeCandidateSearchMode: (mode: CandidateSearchMode) => void;
  candidateSearch: string;
  changeCandidateSearch: (value: string) => void;
  addMatchedItems: () => void;
  candidateRegexInvalid: boolean;
  filteredCandidates: ModelGroupCandidateItem[];
  refetchCandidates: () => unknown;
  isFetchingCandidates: boolean;
  applySavedFilter: () => void;
  clearSavedFilter: () => void;
  groupedCandidates: CandidateChannelGroup[];
  expandedChannels: string[];
  toggleChannel: (channelId: string) => void;
  foldedMembers: FoldedMember[];
  addCandidate: (candidate: ModelGroupCandidateItem) => void;
  sitesIsError: boolean;
  candidateIsError: boolean;
  candidateListError: unknown;
  invalidSelectedMemberCount: number;
  removeInvalidItems: () => void;
  setAllMembersEnabled: (enabled: boolean) => void;
  showEnabledOnly: boolean;
  setShowEnabledOnly: Dispatch<SetStateAction<boolean>>;
  visibleFoldedMembers: Array<{ member: FoldedMember; index: number }>;
  draggingIndex: number | null;
  testingModel: boolean;
  toggleFoldedMember: (foldKey: string, enabled: boolean) => void;
  removeFoldedMember: (foldKey: string) => void;
  setDraggingIndex: Dispatch<SetStateAction<number | null>>;
  moveFoldedMember: (fromIndex: number, toIndex: number) => void;
  onOpenModelTest: (member: FoldedMember) => void;
  onGeneratePiConfig: () => void;
  generatingPiConfig: boolean;
}) {
  const [candidateDrawerOpen, setCandidateDrawerOpen] = useState(false);
  const [keyPickerOpen, setKeyPickerOpen] = useState(false);
  const { data: gatewayKeys = [] } = useQuery({
    queryKey: ["gateway-api-keys"],
    queryFn: () => apiRequest<GatewayApiKey[]>("/admin/gateway-api-keys"),
    staleTime: 30_000,
  });
  const selectedGatewayKeys = gatewayKeys.filter((key) =>
    form.allowedKeyIds.includes(key.id),
  );

  const priorityLevels = useMemo(() => {
    const byPriority = new Map<number, FoldedMember[]>();
    for (const member of foldedMembers) {
      const list = byPriority.get(member.priority) ?? [];
      list.push(member);
      byPriority.set(member.priority, list);
    }
    return Array.from(byPriority.entries())
      .sort((a, b) => a[0] - b[0])
      .map(([priority, members]) => ({ priority, members }));
  }, [foldedMembers]);

  function foldedMemberKey(item: FormItem): string {
    return modelFoldKey(
      protocolConfigIdFromChannelId(item.channel_id),
      item.credential_id,
      item.model_name,
    );
  }

  function setMemberWeight(member: FoldedMember, weight: number) {
    const clamped = Math.max(1, Math.floor(weight) || 1);
    setForm((current) => ({
      ...current,
      items: current.items.map((item) =>
        foldedMemberKey(item) === member.key
          ? { ...item, weight: clamped }
          : item,
      ),
    }));
  }

  function applyWeightPresetToForm(kind: "equal" | "seq" | "main") {
    setForm((current) => {
      const orderedKeys: string[] = [];
      const seen = new Set<string>();
      for (const item of current.items) {
        const key = foldedMemberKey(item);
        if (!seen.has(key)) {
          seen.add(key);
          orderedKeys.push(key);
        }
      }
      const preset = orderedKeys.map(() => ({ weight: 1 }));
      applyWeightPreset(preset, kind);
      const weightByKey = new Map<string, number>();
      orderedKeys.forEach((key, index) =>
        weightByKey.set(key, preset[index].weight),
      );
      return {
        ...current,
        items: current.items.map((item) => ({
          ...item,
          weight: weightByKey.get(foldedMemberKey(item)) ?? item.weight,
        })),
      };
    });
  }

  function makeFirstPrimary() {
    setForm((current) => {
      const orderedKeys: string[] = [];
      const seen = new Set<string>();
      const itemsByKey = new Map<string, FormItem[]>();
      for (const item of current.items) {
        const key = foldedMemberKey(item);
        if (!seen.has(key)) {
          seen.add(key);
          orderedKeys.push(key);
        }
        const list = itemsByKey.get(key) ?? [];
        list.push(item);
        itemsByKey.set(key, list);
      }
      const firstEnabledIndex = orderedKeys.findIndex((key) =>
        (itemsByKey.get(key) ?? []).some((item) => item.enabled),
      );
      if (firstEnabledIndex <= 0) return current;
      const [key] = orderedKeys.splice(firstEnabledIndex, 1);
      orderedKeys.unshift(key);
      const priorityByKey = new Map(orderedKeys.map((k, i) => [k, i]));
      const nextItems = orderedKeys.flatMap((k) =>
        (itemsByKey.get(k) ?? []).map((item) => ({
          ...item,
          priority: priorityByKey.get(foldedMemberKey(item)) ?? item.priority,
        })),
      );
      return { ...current, items: nextItems };
    });
  }

  function addPriorityLevel() {
    setForm((current) => {
      if (!current.items.length) return current;
      const minPriority = Math.min(
        ...current.items.map((item) => item.priority),
      );
      const newPriority =
        Math.max(...current.items.map((item) => item.priority)) + 1;
      let moved = false;
      const items = current.items.map((item) => {
        if (!moved && item.priority === minPriority) {
          moved = true;
          return { ...item, priority: newPriority };
        }
        return item;
      });
      if (!moved) return current;
      return { ...current, items };
    });
  }

  function setLevelPriority(oldPriority: number, newPriority: number) {
    const clamped = Math.max(0, Math.floor(newPriority) || 0);
    if (clamped === oldPriority) return;
    setForm((current) => ({
      ...current,
      items: current.items.map((item) =>
        item.priority === oldPriority ? { ...item, priority: clamped } : item,
      ),
    }));
  }

  function removeLevel(priority: number) {
    setForm((current) => ({
      ...current,
      items: current.items.filter((item) => item.priority !== priority),
    }));
  }

  const advancedConfiguredCount =
    (hasConfiguredJson(form.headers) ? 1 : 0) +
    (hasConfiguredJson(form.param_override) ? 1 : 0) +
    (form.multimodal !== "auto" ? 1 : 0) +
    (hasConfiguredPricing(form) ? 1 : 0) +
    (form.context_window.trim() !== "" ? 1 : 0) +
    (form.pi_config.trim() !== "" ? 1 : 0);

  return (
    <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
      <AppDialogContent
        className="max-w-6xl"
        title={
          editingId
            ? locale === "zh-CN"
              ? "编辑模型组"
              : "Edit group"
            : locale === "zh-CN"
              ? "新建模型组"
              : "Create group"
        }
        footer={
          <>
            <Button
              variant="outline"
              type="button"
              onClick={() => setDialogOpen(false)}
            >
              {locale === "zh-CN" ? "取消" : "Cancel"}
            </Button>
            <Button
              type="submit"
              form="group-editor-form"
              disabled={form.protocols.length === 0}
            >
              {editingId
                ? locale === "zh-CN"
                  ? "保存模型组"
                  : "Save group"
                : locale === "zh-CN"
                  ? "创建模型组"
                  : "Create group"}
            </Button>
          </>
        }
      >
        <form
          id="group-editor-form"
          className="flex flex-col gap-4 pr-1"
          onSubmit={submit}
        >
          <div className="flex flex-col gap-4">
            <section className="grid gap-4">
              <div className="text-base font-semibold text-foreground">
                {locale === "zh-CN" ? "基本信息" : "Group settings"}
              </div>
              <FieldGroup className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                <Field>
                  <FieldLabel>
                    {locale === "zh-CN" ? "协议" : "External Protocols"}
                  </FieldLabel>
                  <ProtocolMultiSelect
                    value={form.protocols}
                    onChange={(next) => {
                      const changedProtocols = protocolOptions(locale)
                        .map((option) => option.value)
                        .filter(
                          (protocol) =>
                            form.protocols.includes(protocol) !==
                            next.includes(protocol),
                        );
                      if (changedProtocols.length === 1) {
                        toggleProtocol(changedProtocols[0]);
                        return;
                      }
                      setForm((current) => ({
                        ...current,
                        protocols: next,
                      }));
                    }}
                    locale={locale}
                    invalid={form.protocols.length === 0}
                  />
                  {form.protocols.length === 0 ? (
                    <p className="text-sm text-destructive">
                      {locale === "zh-CN"
                        ? "至少需要选择一项协议。"
                        : "At least one protocol is required."}
                    </p>
                  ) : null}
                </Field>
                <Field>
                  <FieldLabel htmlFor="group-name">
                    {locale === "zh-CN" ? "模型组名称" : "Group name"}
                  </FieldLabel>
                  <Input
                    id="group-name"
                    placeholder={
                      locale === "zh-CN" ? "输入模型组名称" : "Enter group name"
                    }
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="group-route-target">
                    {locale === "zh-CN"
                      ? "路由目标模型组"
                      : "Route target group"}
                  </FieldLabel>
                  <Combobox
                    id="group-route-target"
                    className="w-full"
                    value={form.route_group_id}
                    onChange={(event) => changeRouteTarget(event.target.value)}
                  >
                    <ComboboxOption value="">
                      {locale === "zh-CN"
                        ? "不启用模型组路由"
                        : "No group routing"}
                    </ComboboxOption>
                    {routeTargetOptions.map((group) => (
                      <ComboboxOption key={group.id} value={group.id}>
                        {group.name}
                      </ComboboxOption>
                    ))}
                  </Combobox>
                </Field>
                <Field>
                  <FieldLabel>
                    {locale === "zh-CN" ? "模型组策略" : "Group strategy"}
                  </FieldLabel>
                  <StrategyToggle
                    value={form.strategy}
                    locale={locale}
                    disabled={Boolean(form.route_group_id)}
                    onChange={(value) =>
                      setForm((current) => ({ ...current, strategy: value }))
                    }
                  />
                </Field>
              </FieldGroup>
              <Field>
                <FieldLabel>
                  {locale === "zh-CN" ? "密钥可见性" : "Key access"}
                </FieldLabel>
                <SegmentedControl
                  value={form.keyAccessMode}
                  onValueChange={(value) =>
                    setForm((current) => ({
                      ...current,
                      keyAccessMode: value as FormState["keyAccessMode"],
                    }))
                  }
                  options={[
                    {
                      value: "all",
                      label: locale === "zh-CN" ? "全部密钥" : "All keys",
                    },
                    {
                      value: "selected",
                      label: locale === "zh-CN" ? "指定密钥" : "Selected keys",
                    },
                  ]}
                />
              </Field>
              {form.keyAccessMode === "selected" ? (
                <Field>
                  <FieldLabel>
                    {locale === "zh-CN" ? "可见密钥" : "Visible to"}
                  </FieldLabel>
                  <Popover open={keyPickerOpen} onOpenChange={setKeyPickerOpen}>
                    <PopoverTrigger asChild>
                      <Button
                        type="button"
                        variant="outline"
                        className="w-full justify-between"
                      >
                        <span className="truncate text-left">
                          {selectedGatewayKeys.length > 0
                            ? selectedGatewayKeys
                                .map((key) => gatewayKeyLabel(key))
                                .join(", ")
                            : locale === "zh-CN"
                              ? "选择密钥"
                              : "Select keys"}
                        </span>
                        <ChevronsUpDown className="text-muted-foreground" />
                      </Button>
                    </PopoverTrigger>
                    <PopoverContent
                      align="start"
                      className="w-[calc(100vw-2rem)] p-0 sm:w-[360px]"
                    >
                      <Command>
                        <CommandInput
                          placeholder={
                            locale === "zh-CN"
                              ? "搜索密钥..."
                              : "Search keys..."
                          }
                        />
                        <CommandList>
                          <CommandEmpty>
                            {gatewayKeys.length > 0
                              ? locale === "zh-CN"
                                ? "没有匹配的密钥"
                                : "No matching keys"
                              : locale === "zh-CN"
                                ? "当前没有可用密钥"
                                : "No API keys available"}
                          </CommandEmpty>
                          <CommandGroup>
                            {gatewayKeys.map((key) => {
                              const checked = form.allowedKeyIds.includes(
                                key.id,
                              );
                              return (
                                <CommandItem
                                  key={key.id}
                                  value={`${gatewayKeyLabel(key)} ${key.api_key} ${key.id}`}
                                  onSelect={() =>
                                    setForm((current) => ({
                                      ...current,
                                      allowedKeyIds: checked
                                        ? current.allowedKeyIds.filter(
                                            (id) => id !== key.id,
                                          )
                                        : [...current.allowedKeyIds, key.id],
                                    }))
                                  }
                                  className="items-start gap-3"
                                >
                                  <Checkbox
                                    checked={checked}
                                    className="mt-0.5 pointer-events-none"
                                  />
                                  <div className="min-w-0 flex-1">
                                    <div className="truncate font-medium text-foreground">
                                      {gatewayKeyLabel(key)}
                                    </div>
                                    {key.remark.trim() ? (
                                      <div className="truncate text-xs text-muted-foreground">
                                        {key.api_key.slice(0, 8)}…
                                      </div>
                                    ) : null}
                                  </div>
                                </CommandItem>
                              );
                            })}
                          </CommandGroup>
                        </CommandList>
                      </Command>
                    </PopoverContent>
                  </Popover>
                  {selectedGatewayKeys.length > 0 ? (
                    <div className="flex flex-wrap gap-1">
                      {selectedGatewayKeys.map((key) => (
                        <Badge key={key.id} variant="outline">
                          {gatewayKeyLabel(key)}
                        </Badge>
                      ))}
                    </div>
                  ) : null}
                </Field>
              ) : null}
            </section>

            <Tabs
              key={form.route_group_id ? "route-group" : "standard-group"}
              defaultValue={form.route_group_id ? "advanced" : "members"}
            >
              <TabsList
                className={cn(
                  "grid w-full",
                  form.route_group_id ? "grid-cols-1" : "grid-cols-2",
                )}
              >
                {!form.route_group_id ? (
                  <TabsTrigger value="members">
                    {locale === "zh-CN" ? "成员管理" : "Members"}
                  </TabsTrigger>
                ) : null}
                <TabsTrigger value="advanced">
                  {locale === "zh-CN" ? "高级配置" : "Advanced"}
                  {advancedConfiguredCount > 0 ? (
                    <Badge className="ml-1 h-4 min-w-4 rounded-full px-1 text-[10px]">
                      {advancedConfiguredCount}
                    </Badge>
                  ) : null}
                </TabsTrigger>
              </TabsList>
              {!form.route_group_id ? (
                <TabsContent value="members">
                  <Popover
                    open={candidateDrawerOpen}
                    onOpenChange={setCandidateDrawerOpen}
                  >
                    <div className="relative flex flex-col gap-4">
                      {/* candidate picker */}
                      <PopoverContent
                        align="end"
                        side="bottom"
                        sideOffset={8}
                        className="w-[min(520px,calc(100vw-2rem))] max-h-[calc(100dvh-2rem)] gap-0 overflow-hidden p-0"
                      >
                        <div className="flex items-center justify-between border-b px-4 py-3">
                          <span className="text-sm font-semibold text-foreground">
                            {locale === "zh-CN" ? "候选节点" : "Candidates"}
                          </span>
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            onClick={() => setCandidateDrawerOpen(false)}
                          >
                            <X size={15} />
                          </Button>
                        </div>
                        <section className="flex flex-col bg-muted/10">
                          <div className="grid gap-3 px-3 py-2">
                            <div className="grid min-w-0 gap-2 sm:grid-cols-[128px_minmax(0,1fr)]">
                              <Select
                                value={candidateSearchMode}
                                onValueChange={(value) =>
                                  changeCandidateSearchMode(
                                    value as CandidateSearchMode,
                                  )
                                }
                              >
                                <SelectTrigger className="h-7 w-full">
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  <SelectItem value="contains">
                                    {locale === "zh-CN" ? "包含" : "Contains"}
                                  </SelectItem>
                                  <SelectItem value="regex">
                                    {locale === "zh-CN" ? "正则" : "Regex"}
                                  </SelectItem>
                                </SelectContent>
                              </Select>
                              <div className="flex min-w-0 items-center gap-2 rounded-md border bg-background px-3">
                                <Search
                                  size={14}
                                  className="text-muted-foreground"
                                />
                                <Input
                                  className="min-w-0 flex-1 border-0 bg-transparent px-0 py-0 text-sm shadow-none focus-visible:ring-0"
                                  value={candidateSearch}
                                  onChange={(e) =>
                                    changeCandidateSearch(e.target.value)
                                  }
                                  placeholder={
                                    candidateSearchMode === "regex"
                                      ? locale === "zh-CN"
                                        ? "输入正则表达式"
                                        : "Enter regular expression"
                                      : locale === "zh-CN"
                                        ? "输入包含条件"
                                        : "Enter contains filter"
                                  }
                                />
                              </div>
                            </div>
                            <div className="flex min-w-0 flex-wrap items-center justify-start gap-2">
                              <Button
                                type="button"
                                variant="outline"
                                onClick={addMatchedItems}
                                disabled={
                                  form.protocols.length === 0 ||
                                  candidateRegexInvalid ||
                                  (!filteredCandidates.length &&
                                    !candidateSearch.trim())
                                }
                              >
                                <Sparkles size={13} />
                                {candidateSearch.trim()
                                  ? locale === "zh-CN"
                                    ? `加入并保存筛选 ${filteredCandidates.length}`
                                    : `Add and save filter ${filteredCandidates.length}`
                                  : locale === "zh-CN"
                                    ? `加入全部 ${filteredCandidates.length}`
                                    : `Add all ${filteredCandidates.length}`}
                              </Button>
                              <Button
                                type="button"
                                variant="outline"
                                onClick={() => void refetchCandidates()}
                                disabled={
                                  isFetchingCandidates ||
                                  form.protocols.length === 0
                                }
                              >
                                <RefreshCcw size={13} />
                                {locale === "zh-CN" ? "刷新列表" : "Refresh"}
                              </Button>
                            </div>
                          </div>
                          {candidateRegexInvalid ? (
                            <div className="px-2 text-sm text-destructive">
                              {locale === "zh-CN"
                                ? "正则表达式无效"
                                : "Invalid regex"}
                            </div>
                          ) : null}
                          {form.sync_filter_mode && form.sync_filter_query ? (
                            <div className="mx-2 mb-2 flex flex-col gap-2 rounded-md border bg-muted/20 px-3 py-2 sm:flex-row sm:items-center sm:justify-between">
                              <div className="min-w-0 text-sm text-muted-foreground">
                                <span className="text-foreground">
                                  {locale === "zh-CN"
                                    ? "已保存筛选"
                                    : "Saved filter"}
                                </span>
                                <span className="mx-2">·</span>
                                <span>
                                  {form.sync_filter_mode === "regex"
                                    ? locale === "zh-CN"
                                      ? "正则"
                                      : "Regex"
                                    : locale === "zh-CN"
                                      ? "包含"
                                      : "Contains"}
                                </span>
                                <span className="mx-2">·</span>
                                <span className="break-all">
                                  {form.sync_filter_query}
                                </span>
                              </div>
                              <div className="flex shrink-0 flex-wrap items-center gap-2">
                                <Button
                                  type="button"
                                  variant="outline"
                                  size="sm"
                                  onClick={() => void applySavedFilter()}
                                >
                                  <RefreshCcw data-icon="inline-start" />
                                  {locale === "zh-CN"
                                    ? "按规则更新"
                                    : "Update by rule"}
                                </Button>
                                <Button
                                  type="button"
                                  variant="outline"
                                  size="sm"
                                  className="text-muted-foreground"
                                  onClick={clearSavedFilter}
                                >
                                  <X data-icon="inline-start" />
                                  {locale === "zh-CN"
                                    ? "清除规则"
                                    : "Clear rule"}
                                </Button>
                              </div>
                            </div>
                          ) : null}

                          <div className="max-h-[min(420px,42vh)] overflow-y-auto px-2 pb-2">
                            <div className="flex flex-col">
                              {groupedCandidates.map((channelGroup) => {
                                const channelKey = channelGroup.key;
                                const isOpen =
                                  expandedChannels.includes(channelKey);
                                return (
                                  <div
                                    key={channelKey}
                                    className="border-b last:border-b-0"
                                  >
                                    <Button
                                      type="button"
                                      variant="ghost"
                                      className="h-auto min-h-11 w-full justify-start gap-3 rounded-none px-3 py-2 text-left hover:bg-muted"
                                      onClick={() => toggleChannel(channelKey)}
                                    >
                                      <div className="min-w-0 flex-1">
                                        <div className="truncate text-sm font-medium text-foreground">
                                          {channelGroup.channel_name}
                                        </div>
                                      </div>
                                      <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                                        {channelGroup.candidates.length}
                                      </span>
                                      <ChevronDown
                                        size={15}
                                        className={cn(
                                          "text-muted-foreground transition-transform",
                                          isOpen && "rotate-180",
                                        )}
                                      />
                                    </Button>
                                    {isOpen ? (
                                      <div className="flex flex-col gap-0.5 px-3 pb-2 pt-1">
                                        <Separator className="mb-1" />
                                        {channelGroup.candidates.map(
                                          (candidate) => {
                                            const fk = modelFoldKey(
                                              candidate.protocol_config_id,
                                              candidate.credential_id,
                                              candidate.model_name,
                                            );
                                            const isActive = foldedMembers.some(
                                              (m) => m.key === fk,
                                            );
                                            return (
                                              <CandidateRow
                                                key={`${candidate.protocol_config_id}-${candidate.credential_id}-${candidate.model_name}`}
                                                candidate={candidate}
                                                active={isActive}
                                                selectedProtocols={
                                                  form.protocols
                                                }
                                                locale={locale}
                                                onClick={() =>
                                                  addCandidate(candidate)
                                                }
                                              />
                                            );
                                          },
                                        )}
                                      </div>
                                    ) : null}
                                  </div>
                                );
                              })}
                              {sitesIsError || candidateIsError ? (
                                <Alert variant="destructive" className="my-2">
                                  <AlertCircle />
                                  <AlertTitle>
                                    {candidateIsError
                                      ? locale === "zh-CN"
                                        ? "候选模型加载失败"
                                        : "Failed to load candidates"
                                      : locale === "zh-CN"
                                        ? "渠道加载失败"
                                        : "Failed to load channels"}
                                  </AlertTitle>
                                  <AlertDescription>
                                    {candidateListError instanceof Error
                                      ? candidateListError.message
                                      : locale === "zh-CN"
                                        ? "无法读取候选模型"
                                        : "Unable to read candidates"}
                                  </AlertDescription>
                                </Alert>
                              ) : !groupedCandidates.length ? (
                                <p className="px-1 py-6 text-center text-sm text-muted-foreground">
                                  {form.protocols.length === 0
                                    ? locale === "zh-CN"
                                      ? "请先在上方选择对外协议以加载候选节点。"
                                      : "Select external protocols above to load candidates."
                                    : locale === "zh-CN"
                                      ? "暂无可选模型"
                                      : "No candidates found"}
                                </p>
                              ) : null}
                            </div>
                          </div>
                        </section>
                        <div className="mt-auto border-t px-4 py-3">
                          <Button
                            type="button"
                            className="w-full"
                            onClick={() => setCandidateDrawerOpen(false)}
                          >
                            {locale === "zh-CN" ? "完成" : "Done"}
                          </Button>
                        </div>
                      </PopoverContent>

                      <section className="flex flex-col rounded-lg bg-muted/10">
                        <div className="flex flex-col items-start justify-between gap-3 px-2 py-1 sm:flex-row sm:items-center">
                          <div className="flex items-center gap-2 text-sm font-medium text-foreground">
                            {locale === "zh-CN"
                              ? "已选模型"
                              : "Selected models"}
                            <span className="rounded-full bg-muted px-2.5 py-0.5 text-xs text-muted-foreground">
                              {visibleFoldedMembers.length}/
                              {foldedMembers.length}
                            </span>
                          </div>
                          <div className="flex flex-wrap items-center justify-end gap-2">
                            <PopoverTrigger asChild>
                              <Button type="button">
                                <Plus size={13} />
                                {locale === "zh-CN"
                                  ? "添加成员"
                                  : "Add members"}
                              </Button>
                            </PopoverTrigger>
                            {form.strategy === "round_robin" ? (
                              <>
                                <Button
                                  type="button"
                                  variant="outline"
                                  onClick={() =>
                                    applyWeightPresetToForm("equal")
                                  }
                                >
                                  {locale === "zh-CN"
                                    ? "平权 1:1:1"
                                    : "Equal 1:1:1"}
                                </Button>
                                <Button
                                  type="button"
                                  variant="outline"
                                  onClick={() => applyWeightPresetToForm("seq")}
                                >
                                  {locale === "zh-CN"
                                    ? "按序 3:2:1"
                                    : "Sequence 3:2:1"}
                                </Button>
                                <Button
                                  type="button"
                                  variant="outline"
                                  onClick={() =>
                                    applyWeightPresetToForm("main")
                                  }
                                >
                                  {locale === "zh-CN" ? "主备 9:1" : "Main 9:1"}
                                </Button>
                              </>
                            ) : null}
                            {form.strategy === "failover" ? (
                              <Button
                                type="button"
                                variant="outline"
                                onClick={makeFirstPrimary}
                              >
                                {locale === "zh-CN"
                                  ? "置顶为主"
                                  : "Make primary"}
                              </Button>
                            ) : null}
                            {form.strategy === "priority_weighted" ? (
                              <Button
                                type="button"
                                variant="outline"
                                onClick={addPriorityLevel}
                              >
                                {locale === "zh-CN"
                                  ? "新增优先级层"
                                  : "Add level"}
                              </Button>
                            ) : null}
                            {invalidSelectedMemberCount > 0 ? (
                              <Button
                                type="button"
                                variant="outline"
                                className="text-destructive"
                                onClick={removeInvalidItems}
                              >
                                <AlertCircle size={13} />
                                {locale === "zh-CN"
                                  ? "一键移除失效节点"
                                  : "Remove invalid items"}
                              </Button>
                            ) : null}
                            <Button
                              type="button"
                              variant="outline"
                              className="text-muted-foreground"
                              onClick={() => setAllMembersEnabled(true)}
                            >
                              {locale === "zh-CN" ? "全开" : "Enable all"}
                            </Button>
                            <Button
                              type="button"
                              variant="outline"
                              className="text-muted-foreground"
                              onClick={() => setAllMembersEnabled(false)}
                            >
                              {locale === "zh-CN" ? "全关" : "Disable all"}
                            </Button>
                            <Button
                              type="button"
                              variant={showEnabledOnly ? "default" : "outline"}
                              className={cn(
                                !showEnabledOnly && "text-muted-foreground",
                              )}
                              onClick={() =>
                                setShowEnabledOnly((current) => !current)
                              }
                            >
                              {locale === "zh-CN" ? "仅看启用" : "Enabled only"}
                            </Button>
                          </div>
                        </div>
                        <div className="max-h-[min(420px,42vh)] overflow-y-auto px-2 pb-2 pt-1">
                          {form.strategy === "priority_weighted" ? (
                            <div className="flex flex-col gap-2">
                              {priorityLevels.map((level, levelIndex) => (
                                <PriorityLevelCard
                                  key={level.priority}
                                  priority={level.priority}
                                  isTop={levelIndex === 0}
                                  memberCount={level.members.length}
                                  locale={locale}
                                  onPriorityChange={(value) =>
                                    setLevelPriority(level.priority, value)
                                  }
                                  onRemoveLevel={() =>
                                    removeLevel(level.priority)
                                  }
                                >
                                  {level.members.map((member, index) => (
                                    <FoldedMemberRow
                                      key={member.key}
                                      member={member}
                                      index={index}
                                      mode="priority_weighted"
                                      weight={member.weight}
                                      onWeightChange={(weight) =>
                                        setMemberWeight(member, weight)
                                      }
                                      dragging={false}
                                      busy={false}
                                      testingDisabled={testingModel}
                                      onTest={() => onOpenModelTest(member)}
                                      onToggle={() =>
                                        toggleFoldedMember(
                                          member.key,
                                          !member.enabled,
                                        )
                                      }
                                      onRemove={() =>
                                        removeFoldedMember(member.key)
                                      }
                                      draggable={false}
                                      onDragStart={() => {}}
                                      onDragEnter={() => {}}
                                      onDragEnd={() => {}}
                                      locale={locale}
                                    />
                                  ))}
                                </PriorityLevelCard>
                              ))}
                            </div>
                          ) : (
                            <div className="flex flex-col gap-1.5">
                              {visibleFoldedMembers.length ? (
                                visibleFoldedMembers.map(
                                  ({ member, index }) => (
                                    <FoldedMemberRow
                                      key={member.key}
                                      member={member}
                                      index={index}
                                      mode={form.strategy}
                                      weight={member.weight}
                                      onWeightChange={(weight) =>
                                        setMemberWeight(member, weight)
                                      }
                                      sharePct={
                                        form.strategy === "round_robin"
                                          ? membersSharePct(
                                              visibleFoldedMembers.map(
                                                (visible) => visible.member,
                                              ),
                                              index,
                                            )
                                          : undefined
                                      }
                                      dragging={draggingIndex === index}
                                      busy={false}
                                      testingDisabled={testingModel}
                                      onTest={() => onOpenModelTest(member)}
                                      onToggle={() =>
                                        toggleFoldedMember(
                                          member.key,
                                          !member.enabled,
                                        )
                                      }
                                      onRemove={() =>
                                        removeFoldedMember(member.key)
                                      }
                                      onDragStart={() =>
                                        setDraggingIndex(index)
                                      }
                                      onDragEnter={() => {
                                        if (
                                          draggingIndex === null ||
                                          draggingIndex === index
                                        )
                                          return;
                                        moveFoldedMember(draggingIndex, index);
                                        setDraggingIndex(index);
                                      }}
                                      onDragEnd={() => setDraggingIndex(null)}
                                      locale={locale}
                                    />
                                  ),
                                )
                              ) : (
                                <p className="px-1 py-6 text-center text-sm text-muted-foreground">
                                  {locale === "zh-CN"
                                    ? "当前筛选下没有成员"
                                    : "No members under current filter"}
                                </p>
                              )}
                            </div>
                          )}
                        </div>
                      </section>
                    </div>
                  </Popover>
                </TabsContent>
              ) : null}
              <TabsContent value="advanced">
                <div className="flex flex-col gap-4">
                  <div className="flex flex-col gap-2">
                    <div className="flex items-center gap-2 text-base font-semibold text-foreground">
                      {locale === "zh-CN" ? "请求头覆盖" : "Request headers"}
                      <Badge
                        variant={
                          hasConfiguredJson(form.headers)
                            ? "default"
                            : "secondary"
                        }
                      >
                        {locale === "zh-CN"
                          ? hasConfiguredJson(form.headers)
                            ? "已配置"
                            : "未配置"
                          : hasConfiguredJson(form.headers)
                            ? "Configured"
                            : "Not set"}
                      </Badge>
                    </div>
                    <Textarea
                      id="group-headers"
                      value={form.headers}
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          headers: event.target.value,
                        }))
                      }
                      className="min-h-24 font-mono text-xs"
                    />
                    <p className="text-sm text-muted-foreground">
                      {locale === "zh-CN"
                        ? "大小写不敏感；authorization / host 等系统头不可覆盖"
                        : "Case-insensitive; system headers such as authorization/host are protected"}
                    </p>
                  </div>
                  <Separator />
                  <div className="flex flex-col gap-2">
                    <div className="flex items-center gap-2 text-base font-semibold text-foreground">
                      {locale === "zh-CN" ? "参数覆盖" : "Parameter override"}
                      <Badge
                        variant={
                          hasConfiguredJson(form.param_override)
                            ? "default"
                            : "secondary"
                        }
                      >
                        {locale === "zh-CN"
                          ? hasConfiguredJson(form.param_override)
                            ? "已配置"
                            : "未配置"
                          : hasConfiguredJson(form.param_override)
                            ? "Configured"
                            : "Not set"}
                      </Badge>
                    </div>
                    <Textarea
                      id="group-param-override"
                      value={form.param_override}
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          param_override: event.target.value,
                        }))
                      }
                      className="min-h-24 font-mono text-xs"
                    />
                    <p className="text-sm text-muted-foreground">
                      {locale === "zh-CN"
                        ? "递归合并，禁止覆盖 model"
                        : "Deep-merged; model is not overridable"}
                    </p>
                  </div>
                  {!form.route_group_id ? (
                    <>
                      <Separator />
                      <div className="flex flex-col gap-3">
                        <div className="flex items-center gap-2 text-base font-semibold text-foreground">
                          {locale === "zh-CN"
                            ? "多模态能力"
                            : "Multimodal capability"}
                          <Badge
                            variant={
                              form.multimodal !== "auto"
                                ? "default"
                                : "secondary"
                            }
                          >
                            {locale === "zh-CN"
                              ? form.multimodal !== "auto"
                                ? "手动"
                                : "自动"
                              : form.multimodal !== "auto"
                                ? "Manual"
                                : "Auto"}
                          </Badge>
                        </div>
                        <SegmentedControl
                          value={form.multimodal === "auto" ? "auto" : "manual"}
                          onValueChange={(value) =>
                            setForm((current) => ({
                              ...current,
                              multimodal:
                                value === "auto"
                                  ? "auto"
                                  : current.multimodal === "auto"
                                    ? "manual"
                                    : current.multimodal,
                            }))
                          }
                          options={[
                            {
                              value: "auto",
                              label: locale === "zh-CN" ? "自动获取" : "Auto",
                            },
                            {
                              value: "manual",
                              label: locale === "zh-CN" ? "手动设置" : "Manual",
                            },
                          ]}
                        />
                        <ModalityToggleRow
                          value={
                            form.multimodal === "auto"
                              ? form.autodetected_modalities
                              : form.multimodal_overrides
                          }
                          onChange={(next) =>
                            setForm((current) => ({
                              ...current,
                              multimodal_overrides: next,
                            }))
                          }
                          disabled={form.multimodal === "auto"}
                        />
                        <p className="text-sm text-muted-foreground">
                          {locale === "zh-CN"
                            ? "按下的图标表示该组支持对应模态；自动模式下按 models.dev 同步结果判定，手动设置优先"
                            : "Pressed icons mark supported modalities; auto mode follows models.dev sync, manual overrides it"}
                        </p>
                      </div>
                      <Separator />
                      <div className="flex flex-col gap-3">
                        <div className="flex items-center gap-2 text-base font-semibold text-foreground">
                          {locale === "zh-CN" ? "价格" : "Pricing"}
                          <Badge
                            variant={
                              hasConfiguredPricing(form)
                                ? "default"
                                : "secondary"
                            }
                          >
                            {locale === "zh-CN"
                              ? hasConfiguredPricing(form)
                                ? "已配置"
                                : "未配置"
                              : hasConfiguredPricing(form)
                                ? "Configured"
                                : "Not set"}
                          </Badge>
                        </div>
                        <div className="grid gap-3 xl:grid-cols-2">
                          <EditablePriceRow
                            locale={locale}
                            primaryLabel="input"
                            primaryValue={form.input_price_per_million}
                            secondaryLabel="cache_read"
                            secondaryValue={form.cache_read_price_per_million}
                            onPrimaryChange={(value) =>
                              setForm((current) => ({
                                ...current,
                                input_price_per_million: value,
                              }))
                            }
                            onSecondaryChange={(value) =>
                              setForm((current) => ({
                                ...current,
                                cache_read_price_per_million: value,
                              }))
                            }
                          />
                          <EditablePriceRow
                            locale={locale}
                            primaryLabel="output"
                            primaryValue={form.output_price_per_million}
                            secondaryLabel="cache_write"
                            secondaryValue={form.cache_write_price_per_million}
                            onPrimaryChange={(value) =>
                              setForm((current) => ({
                                ...current,
                                output_price_per_million: value,
                              }))
                            }
                            onSecondaryChange={(value) =>
                              setForm((current) => ({
                                ...current,
                                cache_write_price_per_million: value,
                              }))
                            }
                          />
                        </div>
                        <div className="flex items-center gap-3">
                          <FieldLabel
                            htmlFor="group-context-window"
                            className="min-w-24"
                          >
                            {locale === "zh-CN"
                              ? "上下文窗口"
                              : "Context window"}
                          </FieldLabel>
                          <Input
                            id="group-context-window"
                            type="number"
                            min={1}
                            step={1}
                            placeholder={locale === "zh-CN" ? "自动" : "Auto"}
                            value={form.context_window}
                            onChange={(event) =>
                              setForm((current) => ({
                                ...current,
                                context_window: event.target.value,
                              }))
                            }
                            className="max-w-56 font-mono text-xs"
                          />
                          <p className="text-sm text-muted-foreground">
                            {locale === "zh-CN"
                              ? "留空则用 models.dev 同步值"
                              : "Leave empty to use the models.dev synced value"}
                          </p>
                        </div>
                      </div>
                    </>
                  ) : null}
                  <Separator />
                  <div className="flex flex-col gap-3">
                    <div className="flex items-center gap-2 text-base font-semibold text-foreground">
                      {locale === "zh-CN" ? "pi.dev 配置" : "pi.dev config"}
                      <Badge
                        variant={
                          form.pi_config.trim() !== "" ? "default" : "secondary"
                        }
                      >
                        {locale === "zh-CN"
                          ? form.pi_config.trim() !== ""
                            ? "已配置"
                            : "未配置"
                          : form.pi_config.trim() !== ""
                            ? "Configured"
                            : "Not set"}
                      </Badge>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="ml-auto"
                        disabled={generatingPiConfig}
                        onClick={() => onGeneratePiConfig()}
                      >
                        <RefreshCcw className="mr-1 size-3.5" />
                        {generatingPiConfig
                          ? locale === "zh-CN"
                            ? "生成中..."
                            : "Generating..."
                          : locale === "zh-CN"
                            ? "从 pi.dev 生成"
                            : "Generate from pi.dev"}
                      </Button>
                    </div>
                    <Textarea
                      id="group-pi-config"
                      value={form.pi_config}
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          pi_config: event.target.value,
                          pi_config_edited: true,
                        }))
                      }
                      className="min-h-48 font-mono text-xs"
                      placeholder={'{"id": "model-name", "name": "...", ...}'}
                    />
                    <p className="text-sm text-muted-foreground">
                      {locale === "zh-CN"
                        ? "按组名匹配 pi.dev 目录，取官方渠道 models[0] 配置；定时同步自动更新，可手动修改；/v1/models/config?type=pi 聚合导出"
                        : "Matched from the pi.dev catalog by group name (official channel models[0]); auto-updated by the sync task, editable. Aggregated by /v1/models/config?type=pi"}
                    </p>
                  </div>
                </div>
              </TabsContent>
            </Tabs>
          </div>
        </form>
      </AppDialogContent>
    </Dialog>
  );
}

export function DeleteGroupDialog({
  deleteTarget,
  locale,
  busyId,
  setDeleteTarget,
  remove,
}: {
  deleteTarget: ModelGroup | null;
  locale: "zh-CN" | "en-US";
  busyId: string | null;
  setDeleteTarget: Dispatch<SetStateAction<ModelGroup | null>>;
  remove: (item: ModelGroup) => void;
}) {
  return (
    <Dialog
      open={Boolean(deleteTarget)}
      onOpenChange={(open) => {
        if (!open) setDeleteTarget(null);
      }}
    >
      <AppDialogContent
        className="max-w-lg"
        title={locale === "zh-CN" ? "确认删除模型组" : "Delete group"}
        description={
          locale === "zh-CN"
            ? "删除后，该模型组名称将不再参与路由匹配。"
            : "This group will no longer participate in routing."
        }
        footer={
          <>
            <Button
              variant="outline"
              type="button"
              onClick={() => setDeleteTarget(null)}
            >
              {locale === "zh-CN" ? "取消" : "Cancel"}
            </Button>
            <Button
              variant="destructive"
              type="button"
              onClick={() => deleteTarget && void remove(deleteTarget)}
              disabled={busyId === deleteTarget?.id}
            >
              {busyId === deleteTarget?.id
                ? locale === "zh-CN"
                  ? "删除中..."
                  : "Deleting..."
                : locale === "zh-CN"
                  ? "确认删除"
                  : "Delete"}
            </Button>
          </>
        }
      >
        <div className="rounded-md border bg-muted/30 p-4">
          <strong>{deleteTarget?.name}</strong>
        </div>
      </AppDialogContent>
    </Dialog>
  );
}
