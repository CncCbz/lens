import type { ModelGroup } from "@/lib/api";

export type PlaygroundModel = {
  key: string;
  groupId: string;
  groupName: string;
  channelId: string;
  channelName: string;
  modelName: string;
  credentialId: string;
  credentialName: string;
  enabled: boolean;
};

export type PlaygroundOption = {
  id: string;
  name: string;
};

export function buildPlaygroundCatalog(
  groups: ModelGroup[],
): PlaygroundModel[] {
  const byId = new Map(groups.map((group) => [group.id, group]));
  const catalog: PlaygroundModel[] = [];

  for (const group of groups) {
    if (!group.protocols.includes("openai_chat")) {
      continue;
    }
    const resolved = group.route_group_id
      ? byId.get(group.route_group_id)
      : group;
    if (!resolved || !resolved.protocols.includes("openai_chat")) {
      continue;
    }
    for (const item of resolved.items) {
      catalog.push({
        key: `${group.id}|${item.channel_id}|${item.credential_id}|${item.model_name}`,
        groupId: group.id,
        groupName: group.name,
        channelId: item.channel_id,
        channelName: item.channel_name || item.channel_id,
        modelName: item.model_name,
        credentialId: item.credential_id,
        credentialName: item.credential_name,
        enabled: item.enabled,
      });
    }
  }

  return catalog;
}

export function uniqueOptions(
  models: PlaygroundModel[],
  pick: (model: PlaygroundModel) => string,
  label: (model: PlaygroundModel) => string,
): PlaygroundOption[] {
  const seen = new Map<string, string>();
  for (const model of models) {
    const id = pick(model);
    if (!seen.has(id)) {
      seen.set(id, label(model));
    }
  }
  return Array.from(seen, ([id, name]) => ({ id, name }));
}

export function firstUsableModel(
  models: PlaygroundModel[],
): PlaygroundModel | null {
  return models.find((model) => model.enabled) ?? null;
}
