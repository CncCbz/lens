import type {
  ModelGroup,
  ModelGroupCandidateItem,
  ModelGroupMultimodalMode,
  ModelGroupPayload,
  ModelGroupSyncFilterMode,
  ProtocolKind,
  RoutingStrategy,
  Site,
} from "@/lib/api";
import { isGeneratedCredentialName } from "@/lib/utils";
import { protocolLabel, protocolOptions } from "@/lib/protocols";
export { isGeneratedCredentialName, protocolLabel, protocolOptions };
export { protocolBadgeClassName } from "@/lib/protocols";

export type FormItem = {
  channel_id: string;
  channel_name: string;
  protocol?: ProtocolKind | null;
  credential_id: string;
  credential_name: string;
  credential_number: number;
  model_name: string;
  enabled: boolean;
  priority: number;
  weight: number;
};

export type FormState = {
  name: string;
  protocols: ProtocolKind[];
  strategy: RoutingStrategy;
  route_group_id: string;
  headers: string;
  param_override: string;
  pi_config: string;
  pi_config_edited: boolean;
  sync_filter_mode: ModelGroupSyncFilterMode;
  sync_filter_query: string;
  multimodal: ModelGroupMultimodalMode;
  multimodal_overrides: Record<string, boolean>;
  autodetected_modalities: Record<string, boolean>;
  input_price_per_million: string;
  output_price_per_million: string;
  cache_read_price_per_million: string;
  cache_write_price_per_million: string;
  context_window: string;
  items: FormItem[];
};

export type CandidateChannelGroup = {
  key: string;
  site_id: string;
  channel_name: string;
  candidates: ModelGroupCandidateItem[];
};

export type FoldedMember = {
  key: string;
  protocolConfigId: string;
  model_name: string;
  credential_id: string;
  credential_name: string;
  credential_number: number;
  protocols: ProtocolKind[];
  subItems: FormItem[];
  enabled: boolean;
  invalid: boolean;
  priority: number;
  weight: number;
};

export type GroupDisplayMember = {
  key: string;
  model_name: string;
  credential_name: string;
  credential_number: number;
  channel_names: string[];
  protocols: ProtocolKind[];
  items: ModelGroup["items"];
  enabled: boolean;
  has_disabled_channel: boolean;
};

export type GroupSort =
  "members-desc" | "enabled-desc" | "name-asc" | "name-desc";
export type CandidateSearchMode = Exclude<ModelGroupSyncFilterMode, "">;

export type GroupRow = ModelGroup & {
  member_count: number;
  enabled_member_count: number;
  disabled_channel_member_count: number;
  channel_summary: string;
  channel_names: string[];
  display_members: GroupDisplayMember[];
  is_route_group: boolean;
};

export type {
  ModelPrefixOption,
  SelectedModelPrefix,
} from "@/lib/model-prefix";

export const emptyForm: FormState = {
  name: "",
  protocols: ["openai_chat"],
  strategy: "round_robin",
  route_group_id: "",
  headers: "{}",
  param_override: "{}",
  pi_config: "",
  pi_config_edited: false,
  sync_filter_mode: "",
  sync_filter_query: "",
  multimodal: "auto",
  multimodal_overrides: {},
  autodetected_modalities: {},
  input_price_per_million: "0",
  output_price_per_million: "0",
  cache_read_price_per_million: "0",
  cache_write_price_per_million: "0",
  context_window: "",
  items: [],
};

export function buildCandidateHaystack(
  item: ModelGroupCandidateItem,
  channelMap: Map<string, ProtocolMeta>,
  locale: "zh-CN" | "en-US",
) {
  const channel = channelMap.get(item.channel_id);
  const channelName = channel?.name || item.channel_name;
  const endpoint = item.base_url || channelEndpoint(channel);
  return `${item.model_name} ${channelName} ${protocolLabel(item.protocol, locale)} ${credentialDisplayLabel(item, locale)} ${item.credential_name} ${endpoint}`;
}

export function compileCandidateRegex(value: string) {
  const normalizedValue = value.trim();
  const pattern = normalizedValue.startsWith("(?i)")
    ? normalizedValue.slice(4)
    : normalizedValue;
  try {
    return new RegExp(pattern, "i");
  } catch {
    return null;
  }
}

export function matchesCandidateSearch(
  item: ModelGroupCandidateItem,
  mode: CandidateSearchMode,
  query: string,
  channelMap: Map<string, ProtocolMeta>,
  locale: "zh-CN" | "en-US",
) {
  const normalizedQuery = query.trim();
  if (!normalizedQuery) {
    return true;
  }
  if (mode === "regex") {
    const regex = compileCandidateRegex(normalizedQuery);
    if (!regex) {
      return false;
    }
    return regex.test(item.model_name);
  }
  const haystack = buildCandidateHaystack(item, channelMap, locale);
  return haystack.toLowerCase().includes(normalizedQuery.toLowerCase());
}

export function candidatePayloadItemProtocol(
  candidate: ModelGroupCandidateItem,
  channelId: string,
  channelMap?: Map<string, ProtocolMeta>,
): ProtocolKind | undefined {
  const channelProtocol = channelMap?.get(channelId)?.protocol;
  if (channelProtocol) {
    return channelProtocol;
  }
  const protocolEntry = Object.entries(candidate.protocol_channels).find(
    ([, candidateChannelId]) => candidateChannelId === channelId,
  );
  if (protocolEntry) {
    return protocolEntry[0] as ProtocolKind;
  }
  return channelId === candidate.channel_id ? candidate.protocol : undefined;
}

export function candidatePayloadToFormItems(
  candidate: ModelGroupCandidateItem,
  channelMap?: Map<string, ProtocolMeta>,
): FormItem[] {
  return candidate.items.map((payloadItem) => {
    const channelName =
      channelMap?.get(payloadItem.channel_id)?.name || candidate.channel_name;
    return {
      channel_id: payloadItem.channel_id,
      channel_name: channelName,
      protocol: candidatePayloadItemProtocol(
        candidate,
        payloadItem.channel_id,
        channelMap,
      ),
      credential_id: payloadItem.credential_id,
      credential_name: candidate.credential_name,
      credential_number: candidate.credential_number,
      model_name: payloadItem.model_name,
      enabled: true,
      priority: 0,
      weight: 1,
    };
  });
}

export const PROTOCOL_SUFFIXES: ProtocolKind[] = [
  "openai_chat",
  "openai_responses",
  "openai_embedding",
  "openai_image",
  "rerank",
  "anthropic",
  "gemini",
];

export function protocolConfigIdFromChannelId(channelId: string): string {
  for (const suffix of PROTOCOL_SUFFIXES) {
    if (channelId.endsWith(`_${suffix}`)) {
      return channelId.slice(0, channelId.length - suffix.length - 1);
    }
  }
  return channelId;
}

export function modelFoldKey(
  protocolConfigId: string,
  credentialId: string,
  modelName: string,
): string {
  return `${protocolConfigId}::${credentialId}::${modelName}`;
}

export function buildGroupDisplayMembers(
  items: ModelGroup["items"],
  channelMap: Map<string, ProtocolMeta>,
): GroupDisplayMember[] {
  const orderMap = new Map<string, number>();
  const memberMap = new Map<string, GroupDisplayMember>();

  for (const item of items) {
    const protocolConfigId = protocolConfigIdFromChannelId(item.channel_id);
    const key = modelFoldKey(
      protocolConfigId,
      item.credential_id,
      item.model_name,
    );
    const channel = channelMap.get(item.channel_id);
    const channelName = channel?.name || item.channel_name || item.channel_id;
    const protocol = item.protocol || channel?.protocol;

    if (!memberMap.has(key)) {
      orderMap.set(key, orderMap.size);
      memberMap.set(key, {
        key,
        model_name: item.model_name,
        credential_name: item.credential_name,
        credential_number: item.credential_number,
        channel_names: [],
        protocols: [],
        items: [],
        enabled: false,
        has_disabled_channel: false,
      });
    }

    const member = memberMap.get(key)!;
    member.items.push(item);
    if (item.enabled) member.enabled = true;
    if (item.enabled && channel && !channel.enabled) {
      member.has_disabled_channel = true;
    }
    if (channelName && !member.channel_names.includes(channelName)) {
      member.channel_names.push(channelName);
    }
    if (protocol && !member.protocols.includes(protocol)) {
      member.protocols.push(protocol);
    }
  }

  return Array.from(orderMap.entries())
    .sort((a, b) => a[1] - b[1])
    .map(([key]) => memberMap.get(key)!);
}

export const strategyOptions: Array<{
  value: RoutingStrategy;
  zh: string;
  en: string;
}> = [
  { value: "round_robin", zh: "轮询", en: "Round Robin" },
  { value: "failover", zh: "故障转移", en: "Failover" },
  { value: "priority_weighted", zh: "优先级负载均衡", en: "Priority Weighted" },
];

export function credentialDisplayLabel(
  item: Pick<
    FormItem | ModelGroupCandidateItem,
    "credential_name" | "credential_number"
  >,
  locale: "zh-CN" | "en-US",
) {
  const name = item.credential_name.trim();
  if (name && !isGeneratedCredentialName(name)) {
    return name;
  }
  const number = item.credential_number > 0 ? item.credential_number : 1;
  return locale === "zh-CN" ? `密钥 ${number}` : `Key ${number}`;
}

export function credentialNumberLabel(
  item: Pick<FormItem | ModelGroupCandidateItem, "credential_number">,
  locale: "zh-CN" | "en-US",
) {
  const number = item.credential_number > 0 ? item.credential_number : 1;
  return locale === "zh-CN" ? `密钥 ${number}` : `Key ${number}`;
}

export function foldedMemberSourceLabel(
  member: FoldedMember,
  locale: "zh-CN" | "en-US",
) {
  const channelNames = Array.from(
    new Set(member.subItems.map((item) => item.channel_name).filter(Boolean)),
  );
  const credentialLabel = credentialDisplayLabel(member, locale);
  return [...channelNames, credentialLabel].join(" · ");
}

export function formatMoney(value: number) {
  if (value === 0) return "0";
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: value >= 1 ? 2 : 0,
    maximumFractionDigits: 4,
  }).format(value);
}

export function metricLabel(
  key: "input" | "output" | "cache_read" | "cache_write",
  locale: "zh-CN" | "en-US",
) {
  const labels: Record<
    "input" | "output" | "cache_read" | "cache_write",
    { zh: string; en: string }
  > = {
    input: { zh: "输入", en: "Input" },
    output: { zh: "输出", en: "Output" },
    cache_read: { zh: "缓存读取", en: "Cache Read" },
    cache_write: { zh: "缓存写入", en: "Cache Write" },
  };

  return labels[key][locale === "zh-CN" ? "zh" : "en"];
}

export function apiErrorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

export function itemKey(
  item: Pick<FormItem, "channel_id" | "credential_id" | "model_name">,
) {
  return `${item.channel_id}::${item.credential_id}::${item.model_name}`;
}

export function isGroupEnabled(
  group: Pick<GroupRow, "enabled_member_count" | "is_route_group">,
) {
  return group.is_route_group || group.enabled_member_count > 0;
}

export function moveItems<T>(items: T[], fromIndex: number, toIndex: number) {
  if (
    fromIndex === toIndex ||
    fromIndex < 0 ||
    toIndex < 0 ||
    fromIndex >= items.length ||
    toIndex >= items.length
  ) {
    return items;
  }
  const nextItems = items.slice();
  const [target] = nextItems.splice(fromIndex, 1);
  nextItems.splice(toIndex, 0, target);
  return nextItems;
}

export function membersSharePct(
  items: Array<{ weight: number }>,
  index: number,
): number {
  const total = items.reduce((sum, item) => sum + Math.max(item.weight, 1), 0);
  if (total <= 0 || index < 0 || index >= items.length) return 0;
  return Math.round((Math.max(items[index].weight, 1) / total) * 100);
}

export function applyWeightPreset(
  items: Array<{ weight: number }>,
  kind: "equal" | "seq" | "main",
): void {
  items.forEach((item, index) => {
    if (kind === "equal") {
      item.weight = 1;
    } else if (kind === "seq") {
      item.weight = items.length - index;
    } else {
      item.weight = index === 0 ? 9 : 1;
    }
  });
}

export function renumberPriorities(members: FoldedMember[]): void {
  members.forEach((member, index) => {
    member.priority = index;
    member.subItems.forEach((sub) => {
      sub.priority = index;
    });
  });
}

export type ProtocolMeta = {
  id: string;
  site_id: string;
  name: string;
  base_url: string;
  protocol: ProtocolKind;
  enabled: boolean;
};

export function channelEndpoint(channel?: ProtocolMeta) {
  if (!channel) return "";
  return channel.base_url || "";
}

export function protocolBaseUrl(site: Site, baseUrlId: string) {
  const bound = site.base_urls.find((item) => item.id === baseUrlId);
  return bound?.url || "";
}

export function toForm(group: ModelGroup): FormState {
  return {
    name: group.name,
    protocols: group.protocols,
    strategy: group.strategy,
    route_group_id: group.route_group_id ?? "",
    headers: JSON.stringify(group.headers ?? {}, null, 2),
    param_override: JSON.stringify(group.param_override ?? {}, null, 2),
    sync_filter_mode: group.sync_filter_mode,
    sync_filter_query: group.sync_filter_query,
    multimodal: group.multimodal ?? "auto",
    multimodal_overrides: group.multimodal_overrides ?? {},
    autodetected_modalities: group.multimodal_resolved ?? {},
    input_price_per_million: String(group.input_price_per_million),
    output_price_per_million: String(group.output_price_per_million),
    cache_read_price_per_million: String(group.cache_read_price_per_million),
    cache_write_price_per_million: String(group.cache_write_price_per_million),
    context_window:
      group.context_window == null ? "" : String(group.context_window),
    pi_config: group.pi_config ?? "",
    pi_config_edited: false,
    items: group.items
      .slice()
      .sort((a, b) => a.priority - b.priority)
      .map((item) => ({
        channel_id: item.channel_id,
        channel_name: item.channel_name,
        protocol: item.protocol,
        credential_id: item.credential_id,
        credential_name: item.credential_name,
        credential_number: item.credential_number,
        model_name: item.model_name,
        enabled: item.enabled,
        priority: item.priority ?? 0,
        weight: item.weight ?? 1,
      })),
  };
}

function parseJsonObject(
  value: string,
  field: string,
): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value.trim() || "{}");
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${field} must be a JSON object`);
  }
  return parsed as Record<string, unknown>;
}

function parseHeaders(value: string): Record<string, string> {
  const parsed = parseJsonObject(value, "headers");
  for (const headerValue of Object.values(parsed)) {
    if (typeof headerValue !== "string") {
      throw new Error("headers values must be strings");
    }
  }
  return parsed as Record<string, string>;
}

export function toPayload(form: FormState): ModelGroupPayload {
  return {
    name: form.name.trim(),
    protocols: form.protocols,
    strategy: form.strategy,
    route_group_id: form.route_group_id.trim(),
    headers: parseHeaders(form.headers),
    param_override: parseJsonObject(form.param_override, "param_override"),
    pi_config:
      form.route_group_id.trim() && !form.pi_config_edited
        ? undefined
        : form.pi_config.trim(),
    sync_filter_mode:
      form.route_group_id.trim() || !form.sync_filter_query.trim()
        ? ""
        : form.sync_filter_mode,
    sync_filter_query: form.route_group_id.trim()
      ? ""
      : form.sync_filter_query.trim(),
    multimodal: form.multimodal,
    multimodal_overrides: form.multimodal_overrides,
    items: form.items.map((item) => ({
      channel_id: item.channel_id,
      credential_id: item.credential_id,
      model_name: item.model_name,
      enabled: item.enabled,
      priority: item.priority,
      weight: item.weight,
    })),
  };
}
