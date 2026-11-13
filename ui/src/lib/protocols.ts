import type { ProtocolKind } from "@/lib/api";

export const PROTOCOL_LIST: ProtocolKind[] = [
  "openai_chat",
  "openai_responses",
  "openai_embedding",
  "openai_image",
  "rerank",
  "anthropic",
  "gemini",
  "mistral_conversations",
  "azure_openai_responses",
  "openai_codex_responses",
  "google_vertex",
  "bedrock_converse_stream",
  "pi_messages",
  "openrouter_images",
  "typesafe_system_one",
  "cloudflare_workers_ai_system_one",
  "llama_cpp_classify",
  "openai_decisions",
];

export const PROTOCOL_LABELS: Record<ProtocolKind, { zh: string; en: string }> =
  {
    openai_chat: { zh: "OpenAI Chat", en: "OpenAI Chat" },
    openai_responses: { zh: "OpenAI Responses", en: "OpenAI Responses" },
    openai_embedding: { zh: "OpenAI Embedding", en: "OpenAI Embedding" },
    openai_image: { zh: "OpenAI Image", en: "OpenAI Image" },
    rerank: { zh: "Rerank", en: "Rerank" },
    anthropic: { zh: "Anthropic", en: "Anthropic" },
    gemini: { zh: "Gemini", en: "Gemini" },
    mistral_conversations: {
      zh: "Mistral Conversations",
      en: "Mistral Conversations",
    },
    azure_openai_responses: {
      zh: "Azure OpenAI Responses",
      en: "Azure OpenAI Responses",
    },
    openai_codex_responses: {
      zh: "OpenAI Codex Responses",
      en: "OpenAI Codex Responses",
    },
    google_vertex: { zh: "Google Vertex", en: "Google Vertex" },
    bedrock_converse_stream: {
      zh: "Bedrock Converse Stream",
      en: "Bedrock Converse Stream",
    },
    pi_messages: { zh: "Pi Messages", en: "Pi Messages" },
    openrouter_images: { zh: "OpenRouter Images", en: "OpenRouter Images" },
    typesafe_system_one: {
      zh: "TypeSafe System One",
      en: "TypeSafe System One",
    },
    cloudflare_workers_ai_system_one: {
      zh: "Cloudflare Workers AI System One",
      en: "Cloudflare Workers AI System One",
    },
    llama_cpp_classify: { zh: "llama.cpp Classify", en: "llama.cpp Classify" },
    openai_decisions: { zh: "OpenAI Decisions", en: "OpenAI Decisions" },
  };

export function compactProtocolLabel(protocol: ProtocolKind): string {
  switch (protocol) {
    case "openai_chat":
      return "chat";
    case "openai_responses":
      return "responses";
    case "openai_embedding":
      return "embeddings";
    case "openai_image":
      return "image";
    case "rerank":
      return "rerank";
    case "anthropic":
      return "anthropic";
    case "gemini":
      return "gemini";
    case "mistral_conversations":
      return "mistral";
    case "azure_openai_responses":
      return "azure responses";
    case "openai_codex_responses":
      return "codex responses";
    case "google_vertex":
      return "vertex";
    case "bedrock_converse_stream":
      return "bedrock";
    case "pi_messages":
      return "pi messages";
    case "openrouter_images":
      return "openrouter images";
    case "typesafe_system_one":
      return "typesafe";
    case "cloudflare_workers_ai_system_one":
      return "cloudflare";
    case "llama_cpp_classify":
      return "llama.cpp classify";
    case "openai_decisions":
      return "decisions";
    default:
      return protocol;
  }
}

export function protocolLabel(
  protocol: ProtocolKind,
  locale: "zh-CN" | "en-US",
): string {
  return PROTOCOL_LABELS[protocol][locale === "zh-CN" ? "zh" : "en"];
}

export function protocolOptions(locale: "zh-CN" | "en-US") {
  return PROTOCOL_LIST.map((value) => ({
    value,
    label: protocolLabel(value, locale),
  }));
}

export function protocolBadgeClassName(protocol: ProtocolKind) {
  switch (protocol) {
    case "openai_chat":
      return "border-transparent bg-sky-500/10 text-sky-700";
    case "openai_responses":
    case "azure_openai_responses":
    case "openai_codex_responses":
      return "border-transparent bg-indigo-500/10 text-indigo-700";
    case "openai_embedding":
      return "border-transparent bg-cyan-500/10 text-cyan-700";
    case "openai_image":
    case "openrouter_images":
      return "border-transparent bg-rose-500/10 text-rose-700";
    case "rerank":
    case "typesafe_system_one":
    case "cloudflare_workers_ai_system_one":
    case "llama_cpp_classify":
    case "openai_decisions":
      return "border-transparent bg-violet-500/10 text-violet-700";
    case "anthropic":
    case "mistral_conversations":
    case "bedrock_converse_stream":
    case "pi_messages":
      return "border-transparent bg-amber-500/10 text-amber-700";
    case "gemini":
    case "google_vertex":
      return "border-transparent bg-emerald-500/10 text-emerald-700";
    default:
      return "border-transparent bg-secondary text-secondary-foreground";
  }
}

export const PROTOCOL_DOT_CLASS: Record<ProtocolKind, string> = {
  openai_chat: "bg-sky-500",
  openai_responses: "bg-indigo-500",
  openai_embedding: "bg-muted-foreground/60",
  openai_image: "bg-rose-500",
  rerank: "bg-muted-foreground/60",
  anthropic: "bg-amber-500",
  gemini: "bg-emerald-500",
  mistral_conversations: "bg-amber-500",
  azure_openai_responses: "bg-indigo-500",
  openai_codex_responses: "bg-indigo-500",
  google_vertex: "bg-emerald-500",
  bedrock_converse_stream: "bg-amber-500",
  pi_messages: "bg-amber-500",
  openrouter_images: "bg-rose-500",
  typesafe_system_one: "bg-violet-500",
  cloudflare_workers_ai_system_one: "bg-violet-500",
  llama_cpp_classify: "bg-violet-500",
  openai_decisions: "bg-violet-500",
};
