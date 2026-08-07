import type {
  RouterErrorCooldownScope,
  RouterErrorPolicyDraft,
  RouterErrorPolicyFields,
  RouterErrorPolicyRow,
} from "@/lib/settings-types";

/** Keys always shown in the editor (distinct defaults only). */
const VISIBLE_PRESET_KEYS = [
  "4xx",
  "5xx",
  "401",
  "403",
  "404",
  "408",
  "425",
  "429",
  "503",
  "504",
  "529",
  "timeout",
  "transport_error",
] as const;

/** All keys that have builtin exact/category defaults. */
const PRESET_KEYS = [
  "4xx",
  "400",
  "401",
  "403",
  "404",
  "408",
  "422",
  "425",
  "429",
  "5xx",
  "500",
  "502",
  "503",
  "504",
  "529",
  "timeout",
  "transport_error",
] as const;

type BuiltinMap = Record<string, RouterErrorPolicyFields>;

const CATEGORY_DEFAULTS: BuiltinMap = {
  "4xx": {
    same_target_retries: 0,
    fallback: false,
    cooldown_scope: "none",
    failure_threshold: 1,
    cooldown_seconds: 0,
    max_cooldown_seconds: 0,
    respect_retry_after: false,
    count_toward_failure_rate: false,
  },
  "5xx": {
    same_target_retries: 0,
    fallback: true,
    cooldown_scope: "target",
    failure_threshold: 3,
    cooldown_seconds: 60,
    max_cooldown_seconds: 600,
    respect_retry_after: false,
    count_toward_failure_rate: true,
  },
};

const EXACT_DEFAULTS: BuiltinMap = {
  "400": { ...CATEGORY_DEFAULTS["4xx"] },
  "401": {
    same_target_retries: 0,
    fallback: true,
    cooldown_scope: "credential",
    failure_threshold: 1,
    cooldown_seconds: 300,
    max_cooldown_seconds: 600,
    respect_retry_after: false,
    count_toward_failure_rate: false,
  },
  "403": {
    same_target_retries: 0,
    fallback: true,
    cooldown_scope: "credential",
    failure_threshold: 1,
    cooldown_seconds: 300,
    max_cooldown_seconds: 600,
    respect_retry_after: false,
    count_toward_failure_rate: false,
  },
  "404": {
    same_target_retries: 0,
    fallback: true,
    cooldown_scope: "target",
    failure_threshold: 1,
    cooldown_seconds: 300,
    max_cooldown_seconds: 600,
    respect_retry_after: false,
    count_toward_failure_rate: false,
  },
  "408": {
    same_target_retries: 0,
    fallback: true,
    cooldown_scope: "target",
    failure_threshold: 2,
    cooldown_seconds: 60,
    max_cooldown_seconds: 600,
    respect_retry_after: false,
    count_toward_failure_rate: true,
  },
  "422": { ...CATEGORY_DEFAULTS["4xx"] },
  "425": {
    same_target_retries: 1,
    fallback: true,
    cooldown_scope: "target",
    failure_threshold: 1,
    cooldown_seconds: 5,
    max_cooldown_seconds: 600,
    respect_retry_after: false,
    count_toward_failure_rate: true,
  },
  "429": {
    same_target_retries: 0,
    fallback: true,
    cooldown_scope: "credential",
    failure_threshold: 1,
    cooldown_seconds: 60,
    max_cooldown_seconds: 600,
    respect_retry_after: true,
    count_toward_failure_rate: false,
  },
  "500": { ...CATEGORY_DEFAULTS["5xx"] },
  "502": { ...CATEGORY_DEFAULTS["5xx"] },
  "503": { ...CATEGORY_DEFAULTS["5xx"], respect_retry_after: true },
  "504": {
    same_target_retries: 0,
    fallback: true,
    cooldown_scope: "target",
    failure_threshold: 2,
    cooldown_seconds: 60,
    max_cooldown_seconds: 600,
    respect_retry_after: false,
    count_toward_failure_rate: true,
  },
  "529": {
    same_target_retries: 0,
    fallback: true,
    cooldown_scope: "target",
    failure_threshold: 1,
    cooldown_seconds: 60,
    max_cooldown_seconds: 600,
    respect_retry_after: true,
    count_toward_failure_rate: true,
  },
  timeout: {
    same_target_retries: 0,
    fallback: true,
    cooldown_scope: "target",
    failure_threshold: 2,
    cooldown_seconds: 60,
    max_cooldown_seconds: 600,
    respect_retry_after: false,
    count_toward_failure_rate: true,
  },
  transport_error: {
    same_target_retries: 0,
    fallback: true,
    cooldown_scope: "target",
    failure_threshold: 2,
    cooldown_seconds: 60,
    max_cooldown_seconds: 600,
    respect_retry_after: false,
    count_toward_failure_rate: true,
  },
};

function categoryFor(key: string): "4xx" | "5xx" | null {
  if (key === "4xx" || key === "5xx") return key;
  if (/^\d{3}$/.test(key)) {
    const code = Number(key);
    if (code >= 400 && code < 500) return "4xx";
    if (code >= 500 && code < 600) return "5xx";
  }
  return null;
}

function sortKey(key: string): [number, string] {
  if (key === "4xx") return [0, key];
  if (key === "5xx") return [1, key];
  if (/^\d{3}$/.test(key)) return [2, key];
  if (key === "timeout") return [3, key];
  if (key === "transport_error") return [4, key];
  return [9, key];
}

function isValidKey(key: string): boolean {
  if (key === "4xx" || key === "5xx" || key === "timeout" || key === "transport_error") {
    return true;
  }
  if (!/^\d{3}$/.test(key)) return false;
  const code = Number(key);
  return code >= 400 && code <= 599;
}

function builtinFor(
  key: string,
  globals: { threshold: number; cooldown: number; maxCooldown: number },
): RouterErrorPolicyFields {
  const category = categoryFor(key);
  let base =
    category === "5xx"
      ? {
          ...CATEGORY_DEFAULTS["5xx"],
          failure_threshold: Math.max(globals.threshold, 1),
          cooldown_seconds: Math.max(globals.cooldown, 0),
          max_cooldown_seconds: Math.max(globals.maxCooldown, globals.cooldown, 0),
        }
      : category === "4xx"
        ? { ...CATEGORY_DEFAULTS["4xx"] }
        : EXACT_DEFAULTS[key]
          ? { ...EXACT_DEFAULTS[key] }
          : { ...CATEGORY_DEFAULTS["4xx"] };

  if (key === "503") {
    base = {
      ...base,
      failure_threshold: Math.max(globals.threshold, 1),
      cooldown_seconds: Math.max(globals.cooldown, 0),
      max_cooldown_seconds: Math.max(globals.maxCooldown, globals.cooldown, 0),
      respect_retry_after: true,
    };
  } else if (EXACT_DEFAULTS[key] && category !== "5xx") {
    base = { ...EXACT_DEFAULTS[key] };
  } else if (EXACT_DEFAULTS[key] && !["500", "502", "503"].includes(key)) {
    base = { ...EXACT_DEFAULTS[key] };
  }

  return base;
}

function diffFields(
  effective: RouterErrorPolicyFields,
  defaults: RouterErrorPolicyFields,
): Partial<RouterErrorPolicyFields> {
  const out: Partial<RouterErrorPolicyFields> = {};
  (Object.keys(effective) as (keyof RouterErrorPolicyFields)[]).forEach((field) => {
    if (effective[field] !== defaults[field]) {
      // @ts-expect-error indexed assign
      out[field] = effective[field];
    }
  });
  return out;
}

export function emptyErrorPolicyDraft(
  globals = { threshold: 3, cooldown: 60, maxCooldown: 600 },
): RouterErrorPolicyDraft {
  return {
    rows: VISIBLE_PRESET_KEYS.map((key) => {
      const fields = builtinFor(key, globals);
      return {
        key,
        isDefault: true,
        overridden: false,
        ...fields,
      };
    }),
  };
}

export function parseErrorPolicyConfig(
  raw: string | undefined,
  globals = { threshold: 3, cooldown: 60, maxCooldown: 600 },
): RouterErrorPolicyDraft {
  let overrides: Record<string, Partial<RouterErrorPolicyFields>> = {};
  const text = (raw ?? "").trim();
  if (text) {
    try {
      const payload = JSON.parse(text) as {
        overrides?: Record<string, Partial<RouterErrorPolicyFields>>;
      };
      overrides = payload.overrides ?? {};
    } catch {
      overrides = {};
    }
  }

  const keys = new Set<string>([
    ...VISIBLE_PRESET_KEYS,
    ...Object.keys(overrides),
  ]);
  const rows: RouterErrorPolicyRow[] = [...keys]
    .filter(isValidKey)
    .sort((a, b) => {
      const [aa, ab] = sortKey(a);
      const [ba, bb] = sortKey(b);
      return aa === ba ? ab.localeCompare(bb) : aa - ba;
    })
    .map((key) => {
      const defaults = builtinFor(key, globals);
      const category = categoryFor(key);
      const merged = {
        ...defaults,
        ...(category ? (overrides[category] ?? {}) : {}),
        ...(overrides[key] ?? {}),
      };
      const overridden = Object.keys(overrides[key] ?? {}).length > 0;
      return {
        key,
        isDefault: (VISIBLE_PRESET_KEYS as readonly string[]).includes(key),
        overridden,
        ...merged,
      };
    });

  return { rows };
}

export function serializeErrorPolicyConfig(
  draft: RouterErrorPolicyDraft,
  globals = { threshold: 3, cooldown: 60, maxCooldown: 600 },
): string {
  const overrides: Record<string, Partial<RouterErrorPolicyFields>> = {};
  for (const row of draft.rows) {
    const defaults = builtinFor(row.key, globals);
    const fields: RouterErrorPolicyFields = {
      same_target_retries: row.same_target_retries,
      fallback: row.fallback,
      cooldown_scope: row.cooldown_scope,
      failure_threshold: row.failure_threshold,
      cooldown_seconds: row.cooldown_seconds,
      max_cooldown_seconds: row.max_cooldown_seconds,
      respect_retry_after: row.respect_retry_after,
      count_toward_failure_rate: row.count_toward_failure_rate,
    };
    const diff = diffFields(fields, defaults);
    if (Object.keys(diff).length > 0) {
      overrides[row.key] = diff;
    }
  }
  const ordered = Object.fromEntries(
    Object.entries(overrides).sort((a, b) => {
      const [aa, ab] = sortKey(a[0]);
      const [ba, bb] = sortKey(b[0]);
      return aa === ba ? ab.localeCompare(bb) : aa - ba;
    }),
  );
  return JSON.stringify({ overrides: ordered });
}

export function validateErrorPolicyDraft(
  draft: RouterErrorPolicyDraft,
  locale: "zh-CN" | "en-US" = "zh-CN",
): string | null {
  const seen = new Set<string>();
  for (const row of draft.rows) {
    if (!isValidKey(row.key)) {
      return locale === "zh-CN"
        ? `无效的策略键: ${row.key}`
        : `Invalid policy key: ${row.key}`;
    }
    if (seen.has(row.key)) {
      return locale === "zh-CN"
        ? `重复的策略键: ${row.key}`
        : `Duplicate policy key: ${row.key}`;
    }
    seen.add(row.key);
    if (row.same_target_retries < 0 || row.same_target_retries > 5) {
      return locale === "zh-CN"
        ? `${row.key}: 同目标重试次数须在 0..5`
        : `${row.key}: same-target retries must be 0..5`;
    }
    if (row.failure_threshold < 1 || row.failure_threshold > 100) {
      return locale === "zh-CN"
        ? `${row.key}: 失败阈值须在 1..100`
        : `${row.key}: failure threshold must be 1..100`;
    }
    if (row.cooldown_seconds < 0 || row.max_cooldown_seconds < 0) {
      return locale === "zh-CN"
        ? `${row.key}: 冷却秒数不能为负`
        : `${row.key}: cooldown seconds cannot be negative`;
    }
    if (row.cooldown_seconds > row.max_cooldown_seconds) {
      return locale === "zh-CN"
        ? `${row.key}: 基础冷却不能大于最大冷却`
        : `${row.key}: base cooldown cannot exceed max cooldown`;
    }
  }
  return null;
}

export function createErrorPolicyRow(
  key: string,
  globals = { threshold: 3, cooldown: 60, maxCooldown: 600 },
): RouterErrorPolicyRow {
  const fields = builtinFor(key, globals);
  return {
    key,
    isDefault: (VISIBLE_PRESET_KEYS as readonly string[]).includes(key),
    overridden: false,
    ...fields,
  };
}

export function policyKeyLabel(key: string, locale: "zh-CN" | "en-US"): string {
  const zh: Record<string, string> = {
    "4xx": "4xx 客户端错误",
    "5xx": "5xx 服务端错误",
    timeout: "超时",
    transport_error: "网络错误",
  };
  const en: Record<string, string> = {
    "4xx": "4xx client errors",
    "5xx": "5xx server errors",
    timeout: "Timeout",
    transport_error: "Transport error",
  };
  if (locale === "zh-CN") return zh[key] ?? key;
  return en[key] ?? key;
}

export const ERROR_POLICY_SCOPE_OPTIONS: {
  value: RouterErrorCooldownScope;
  zh: string;
  en: string;
}[] = [
  { value: "none", zh: "无", en: "None" },
  { value: "credential", zh: "凭证", en: "Credential" },
  { value: "target", zh: "目标", en: "Target" },
  { value: "channel", zh: "渠道", en: "Channel" },
];
