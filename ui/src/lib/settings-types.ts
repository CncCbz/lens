export type ParamOverrideMatchType = "exact" | "regex";

export interface UpstreamParamOverrideRuleDraft {
  id: string;
  enabled: boolean;
  name: string;
  matchType: ParamOverrideMatchType;
  models: string;
  pattern: string;
  override: string;
}

export interface UpstreamParamOverrideDraft {
  global: string;
  rules: UpstreamParamOverrideRuleDraft[];
}

export type RouterErrorCooldownScope =
  | "none"
  | "credential"
  | "target"
  | "channel";

export interface RouterErrorPolicyFields {
  same_target_retries: number;
  fallback: boolean;
  cooldown_scope: RouterErrorCooldownScope;
  failure_threshold: number;
  cooldown_seconds: number;
  max_cooldown_seconds: number;
  respect_retry_after: boolean;
  count_toward_failure_rate: boolean;
}

export interface RouterErrorPolicyRow extends RouterErrorPolicyFields {
  key: string;
  isDefault: boolean;
  overridden: boolean;
}

export interface RouterErrorPolicyDraft {
  rows: RouterErrorPolicyRow[];
}
