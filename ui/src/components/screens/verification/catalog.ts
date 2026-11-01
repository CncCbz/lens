"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { apiRequest, type ModelGroup, type Site } from "@/lib/api";
import {
  buildPlaygroundCatalog,
  type PlaygroundModel,
} from "@/components/screens/playground/catalog";

// Only these return plain text, so only these can be fingerprinted.
const TEXT_PROTOCOLS = new Set([
  "openai_chat",
  "openai_responses",
  "anthropic",
  "gemini",
]);

const ALL_EFFORTS = [
  "none",
  "minimal",
  "low",
  "medium",
  "high",
  "xhigh",
  "max",
] as const;

export type EffortOption = {
  level: string;
  /** What goes on the wire; empty means nothing is sent. */
  wire: string;
};

export type VerificationTarget = PlaygroundModel & {
  protocol: string;
  verifiable: boolean;
  /** Levels the group declares for this model, with their wire spellings. */
  efforts: EffortOption[];
  defaultEffort: string;
};

type ThinkingMap = Record<string, unknown>;

/**
 * `thinkingLevelMap` pins every level: a value is the spelling to send, null
 * means the level is unsupported. A model without a map does not reason.
 */
function readEfforts(piConfig: string): {
  efforts: EffortOption[];
  defaultEffort: string;
} {
  let map: ThinkingMap | null = null;
  try {
    const parsed = JSON.parse(piConfig || "{}") as {
      reasoning?: unknown;
      thinkingLevelMap?: unknown;
    };
    if (
      parsed.thinkingLevelMap &&
      typeof parsed.thinkingLevelMap === "object"
    ) {
      map = parsed.thinkingLevelMap as ThinkingMap;
    } else if (parsed.reasoning !== true) {
      return { efforts: [{ level: "none", wire: "" }], defaultEffort: "none" };
    }
  } catch {
    return { efforts: [{ level: "none", wire: "" }], defaultEffort: "none" };
  }
  if (!map) {
    return {
      efforts: ALL_EFFORTS.map((level) => ({ level, wire: level })),
      defaultEffort: "medium",
    };
  }
  const efforts: EffortOption[] = [];
  for (const level of ALL_EFFORTS) {
    if (level === "none") continue;
    const wire = map[level];
    if (typeof wire === "string" && wire.trim()) {
      efforts.push({ level, wire: wire.trim() });
    }
  }
  // "off" disables reasoning: a null there means nothing goes on the wire,
  // which is the same thing as not asking for a level at all.
  const off = map.off;
  return {
    efforts: [
      { level: "none", wire: typeof off === "string" ? off.trim() : "" },
      ...efforts,
    ],
    defaultEffort: efforts[0]?.level ?? "none",
  };
}

export function buildVerificationTargets(
  sites: Site[],
  groups: ModelGroup[],
): VerificationTarget[] {
  const groupByName = new Map<string, ModelGroup>();
  for (const group of groups) {
    if (!(group.route_group_id ?? "").trim()) {
      groupByName.set(group.name, group);
    }
  }
  const protocolByCombo = new Map<string, string>();
  for (const site of sites) {
    for (const combo of site.protocols) {
      protocolByCombo.set(combo.id, combo.models[0]?.protocol ?? "");
    }
  }
  return buildPlaygroundCatalog(sites, groups).map((model) => {
    const protocol = protocolByCombo.get(model.comboId) ?? "";
    const group = groupByName.get(model.groupName) ?? null;
    const { efforts, defaultEffort } = readEfforts(group?.pi_config ?? "");
    return {
      ...model,
      protocol,
      verifiable: model.enabled && TEXT_PROTOCOLS.has(protocol),
      efforts,
      defaultEffort,
    };
  });
}

export function useVerificationCatalog() {
  const sitesQuery = useQuery<Site[]>({
    queryKey: ["sites", "full"],
    queryFn: () => apiRequest<Site[]>("/admin/sites/full"),
  });
  const groupsQuery = useQuery<ModelGroup[]>({
    queryKey: ["model-groups"],
    queryFn: () => apiRequest<ModelGroup[]>("/admin/model-groups"),
  });

  const targets = useMemo(
    () =>
      buildVerificationTargets(sitesQuery.data ?? [], groupsQuery.data ?? []),
    [sitesQuery.data, groupsQuery.data],
  );

  return {
    targets,
    isLoading: sitesQuery.isLoading || groupsQuery.isLoading,
    error: sitesQuery.error ?? groupsQuery.error,
  };
}
