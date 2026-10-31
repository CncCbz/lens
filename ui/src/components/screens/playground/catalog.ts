import type { ModelGroup, Site, SiteModel } from "@/lib/api";

// A playground target is picked along the same three levels the gateway uses:
// site (渠道) → protocol config (组合) → model inside that config.
export type PlaygroundModel = {
  key: string;
  siteId: string;
  siteName: string;
  comboId: string;
  comboName: string;
  channelId: string;
  channelName: string;
  modelName: string;
  credentialId: string;
  credentialName: string;
  groupId: string;
  groupName: string;
  enabled: boolean;
};

export type PlaygroundOption = {
  id: string;
  name: string;
};

const PROTOCOL = "openai_chat";

function comboName(site: Site, index: number): string {
  const combo = site.protocols[index];
  if (combo.name.trim()) {
    return combo.name.trim();
  }
  // Single-combo sites are shown by their site name alone, matching the
  // runtime channel name the gateway builds for them.
  return site.protocols.length > 1 ? `组合 ${index + 1}` : site.name;
}

function channelId(comboId: string): string {
  return `${comboId}_${PROTOCOL}`;
}

function modelsOf(combo: Site["protocols"][number]): SiteModel[] {
  return combo.models.filter((model) => model.protocol === PROTOCOL);
}

export function buildPlaygroundCatalog(
  sites: Site[],
  groups: ModelGroup[],
): PlaygroundModel[] {
  const groupsByChannel = new Map<string, ModelGroup>();
  for (const group of groups) {
    for (const item of group.items) {
      if (!groupsByChannel.has(item.channel_id)) {
        groupsByChannel.set(item.channel_id, group);
      }
    }
  }

  const catalog: PlaygroundModel[] = [];
  for (const site of sites) {
    site.protocols.forEach((combo, index) => {
      const id = channelId(combo.id);
      const group = groupsByChannel.get(id);
      const name = `${site.name} / ${comboName(site, index)}`;
      for (const model of modelsOf(combo)) {
        const credential =
          site.credentials.find((item) => item.id === model.credential_id) ??
          site.credentials[0];
        if (!credential) {
          continue;
        }
        catalog.push({
          key: `${id}|${model.credential_id}|${model.model_name}`,
          siteId: site.id,
          siteName: site.name,
          comboId: combo.id,
          comboName: comboName(site, index),
          channelId: id,
          channelName: name,
          modelName: model.model_name,
          credentialId: model.credential_id,
          credentialName: model.credential_name || credential.name,
          groupId: group?.id ?? "",
          groupName: group?.name ?? "",
          enabled: combo.enabled && model.enabled && Boolean(group),
        });
      }
    });
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
