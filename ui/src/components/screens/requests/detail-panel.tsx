"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import type { RequestLogDetail, RequestLogItem } from "@/lib/api";
import { cn } from "@/lib/utils";
import { ProtocolBadge, RequestOutcomeBadge } from "./components";
import {
  formatChannelCredentialLabel,
  formatErrorDisplay,
  formatMaybeMoney,
  formatMs,
  formatSseAsJson,
  formatTps,
  getModelChain,
  getResolvedGroupName,
  isSseText,
  titleForLocale,
} from "./shared";
import { JsonViewer } from "./viewer";

function shortRequestId(value: string) {
  if (value.length <= 14) return value;
  return `${value.slice(0, 8)}...${value.slice(-4)}`;
}

function SummaryStat({ label, value }: { label: string; value: string }) {
  return (
    <span className="text-xs text-muted-foreground">
      {label}
      <span className="ml-1 font-semibold text-foreground">{value}</span>
    </span>
  );
}

type TraceKey =
  | "inbound-request"
  | "upstream-request"
  | "upstream-response"
  | "client-response";

type TraceDefinition = {
  key: TraceKey;
  step: string;
  directionZh: string;
  directionEn: string;
  titleZh: string;
  titleEn: string;
  protocolZh: string;
  protocolEn: string;
  tone: "chat" | "responses";
  request: boolean;
};

const TRACE_DEFINITIONS: TraceDefinition[] = [
  {
    key: "inbound-request",
    step: "01",
    directionZh: "下游 → Lens",
    directionEn: "Downstream → Lens",
    titleZh: "下游请求",
    titleEn: "Client request",
    protocolZh: "Chat",
    protocolEn: "Chat",
    tone: "chat",
    request: true,
  },
  {
    key: "upstream-request",
    step: "02",
    directionZh: "Lens → 上游",
    directionEn: "Lens → Upstream",
    titleZh: "上游请求",
    titleEn: "Upstream request",
    protocolZh: "上游",
    protocolEn: "Upstream",
    tone: "responses",
    request: true,
  },
  {
    key: "upstream-response",
    step: "03",
    directionZh: "上游 → Lens",
    directionEn: "Upstream → Lens",
    titleZh: "上游响应",
    titleEn: "Upstream response",
    protocolZh: "上游",
    protocolEn: "Upstream",
    tone: "responses",
    request: false,
  },
  {
    key: "client-response",
    step: "04",
    directionZh: "Lens → 下游",
    directionEn: "Lens → Downstream",
    titleZh: "下游响应",
    titleEn: "Client response",
    protocolZh: "Chat",
    protocolEn: "Chat",
    tone: "chat",
    request: false,
  },
];

const PROTOCOL_LABELS: Record<RequestLogItem["protocol"], [string, string]> = {
  openai_chat: ["Chat", "Chat"],
  openai_responses: ["Responses", "Responses"],
  openai_embedding: ["Embedding", "Embedding"],
  openai_image: ["Image", "Image"],
  rerank: ["Rerank", "Rerank"],
  anthropic: ["Anthropic", "Anthropic"],
  gemini: ["Gemini", "Gemini"],
};

const PROTOCOL_ROUTES: Partial<Record<RequestLogItem["protocol"], string>> = {
  openai_chat: "/v1/chat/completions",
  openai_responses: "/v1/responses",
  openai_embedding: "/v1/embeddings",
  openai_image: "/v1/images/{generations|edits}",
  rerank: "/v1/rerank",
  anthropic: "/v1/messages",
  gemini: "/v1beta/models/{model}:generateContent",
};

function getTraceDefinition(key: TraceKey) {
  return (
    TRACE_DEFINITIONS.find((trace) => trace.key === key) ?? TRACE_DEFINITIONS[0]
  );
}

function protocolLabel(
  protocol: RequestLogItem["protocol"],
  locale: "zh-CN" | "en-US",
) {
  return titleForLocale(locale, ...PROTOCOL_LABELS[protocol]);
}

function traceProtocolLabel(
  trace: TraceDefinition,
  detail: RequestLogDetail,
  locale: "zh-CN" | "en-US",
) {
  const clientProtocol =
    trace.key === "inbound-request" || trace.key === "client-response";
  if (clientProtocol) return protocolLabel(detail.protocol, locale);
  return detail.upstream_protocol
    ? protocolLabel(detail.upstream_protocol, locale)
    : titleForLocale(locale, trace.protocolZh, trace.protocolEn);
}

function traceBadgeLabel(
  trace: TraceDefinition,
  detail: RequestLogDetail,
  locale: "zh-CN" | "en-US",
) {
  if (trace.tone === "responses") {
    const upstreamProtocol = detail.upstream_protocol
      ? protocolLabel(detail.upstream_protocol, locale)
      : titleForLocale(locale, "上游", "Upstream");
    return `${upstreamProtocol} ${titleForLocale(
      locale,
      trace.request ? "请求" : "响应",
      trace.request ? "request" : "response",
    )}`;
  }
  return `${traceProtocolLabel(trace, detail, locale)} ${titleForLocale(
    locale,
    trace.request ? "请求" : "响应",
    trace.request ? "request" : "response",
  )}`;
}

function inboundRoute(
  protocol: RequestLogItem["protocol"],
  locale: "zh-CN" | "en-US",
) {
  const route = PROTOCOL_ROUTES[protocol];
  return route
    ? `POST ${route}`
    : titleForLocale(locale, "请求协议", "Request protocol");
}

function traceRoute(
  trace: TraceDefinition,
  detail: RequestLogDetail,
  locale: "zh-CN" | "en-US",
) {
  if (trace.key === "inbound-request") {
    return inboundRoute(detail.protocol, locale);
  }
  if (trace.key === "upstream-request") {
    return detail.channel_name || detail.channel_id || "Upstream channel";
  }
  if (trace.key === "upstream-response") {
    return detail.status_code
      ? `${detail.status_code} · ${titleForLocale(locale, "上游响应", "Upstream response")}`
      : titleForLocale(locale, "上游响应", "Upstream response");
  }
  const responseLabel = detail.is_stream
    ? titleForLocale(locale, "流式响应", "Streaming response")
    : titleForLocale(locale, "非流响应", "Response");
  return detail.status_code
    ? `${detail.status_code} · ${responseLabel}`
    : responseLabel;
}

function TraceCard({
  trace,
  detail,
  locale,
  active,
  onSelect,
  className,
}: {
  trace: TraceDefinition;
  detail: RequestLogDetail;
  locale: "zh-CN" | "en-US";
  active: boolean;
  onSelect: () => void;
  className: string;
}) {
  const toneClass =
    trace.tone === "chat"
      ? "bg-primary/10 text-primary"
      : "bg-violet-500/10 text-violet-700 dark:text-violet-300";

  return (
    <button
      type="button"
      className={cn(
        "absolute z-10 min-h-[46px] rounded-md border bg-background/95 px-2 py-1.5 text-left shadow-sm transition",
        "hover:-translate-y-px hover:border-primary/60 hover:shadow-md",
        active && "border-primary shadow-md ring-1 ring-primary/15",
        className,
      )}
      onClick={onSelect}
      aria-label={titleForLocale(
        locale,
        `查看 ${trace.step} ${trace.directionZh} 的${trace.titleZh}`,
        `View ${trace.step} ${trace.directionEn} ${trace.titleEn}`,
      )}
    >
      <span className="flex items-center justify-between gap-1.5">
        <span className="min-w-0 truncate text-[10px] font-semibold">
          {titleForLocale(locale, trace.directionZh, trace.directionEn)}
        </span>
        <Badge
          variant="secondary"
          className={cn(
            "h-4 shrink-0 px-1 text-[9px] font-semibold",
            toneClass,
          )}
        >
          {traceBadgeLabel(trace, detail, locale)}
        </Badge>
      </span>
      <span className="mt-0.5 flex min-w-0 items-center gap-1 text-[9px] text-muted-foreground">
        <span className="shrink-0 font-bold tabular-nums">{trace.step}</span>
        <span aria-hidden="true">·</span>
        <span className="truncate font-mono">
          {traceRoute(trace, detail, locale)}
        </span>
      </span>
    </button>
  );
}

function FlowLane({
  left,
  title,
  subtitle,
  tone,
  connectionYs,
}: {
  left: string;
  title: string;
  subtitle: string;
  tone: "client" | "gateway" | "upstream";
  connectionYs: number[];
}) {
  const toneClass = {
    client: "bg-primary",
    gateway: "bg-stone-400",
    upstream: "bg-violet-600",
  }[tone];

  const headerLeft = tone === "upstream" ? "88%" : `calc(${left} + 8px)`;

  return (
    <>
      <div
        className="absolute inset-y-0 w-px bg-border/60"
        style={{ left }}
        aria-hidden="true"
      >
        {connectionYs.map((connectionY) => (
          <span
            key={connectionY}
            className={cn("absolute -left-px h-7 w-1 rounded-full", toneClass)}
            style={{ top: connectionY - 14 }}
          />
        ))}
      </div>
      <div
        className="absolute top-2.5 max-w-[180px] text-[11px]"
        style={{ left: headerLeft }}
      >
        <strong className="block whitespace-nowrap font-semibold">
          {title}
        </strong>
        <small className="block max-w-[155px] truncate text-[9px] text-muted-foreground">
          {subtitle}
        </small>
      </div>
    </>
  );
}

function TraceDetail({
  detail,
  trace,
  locale,
}: {
  detail: RequestLogDetail;
  trace: TraceDefinition;
  locale: "zh-CN" | "en-US";
}) {
  const isInboundRequest = trace.key === "inbound-request";
  const isUpstreamRequest = trace.key === "upstream-request";
  const isUpstreamResponse = trace.key === "upstream-response";
  const headers = isInboundRequest
    ? detail.request_headers
    : isUpstreamRequest
      ? detail.upstream_headers
      : isUpstreamResponse
        ? detail.upstream_response_headers
        : detail.client_response_headers;
  const rawContent = isInboundRequest
    ? detail.client_request_content
    : isUpstreamRequest && detail.channel_id
      ? (detail.upstream_request_content ?? detail.request_content)
      : isUpstreamResponse
        ? detail.upstream_response_content
        : detail.response_content;
  const content = isUpstreamResponse
    ? (detail.upstream_response_distilled ??
      (rawContent && isSseText(rawContent)
        ? formatSseAsJson(rawContent)
        : rawContent))
    : rawContent;
  const headerTitle = isInboundRequest
    ? titleForLocale(locale, "入站请求头", "Inbound headers")
    : isUpstreamRequest
      ? titleForLocale(locale, "上游请求头", "Upstream headers")
      : isUpstreamResponse
        ? titleForLocale(locale, "上游响应头", "Upstream response headers")
        : titleForLocale(locale, "下游响应头", "Client response headers");
  const contentTitle = isInboundRequest
    ? titleForLocale(locale, "入站请求体", "Inbound body")
    : isUpstreamRequest
      ? titleForLocale(locale, "上游请求体", "Upstream body")
      : isUpstreamResponse
        ? titleForLocale(locale, "上游响应体", "Upstream body")
        : titleForLocale(locale, "下游响应体", "Client response body");
  const headerEmptyText = isInboundRequest
    ? titleForLocale(
        locale,
        "当前日志未保存下游请求头",
        "Client request headers are not stored",
      )
    : isUpstreamRequest
      ? titleForLocale(
          locale,
          "当前日志未保存上游请求头",
          "Upstream request headers are not stored",
        )
      : isUpstreamResponse
        ? titleForLocale(
            locale,
            "当前日志未保存上游响应头",
            "Upstream response headers are not stored",
          )
        : titleForLocale(
            locale,
            "当前日志未保存下游响应头",
            "Client response headers are not stored",
          );
  const contentEmptyText = isInboundRequest
    ? titleForLocale(
        locale,
        "当前日志未保存下游原始请求体",
        "Raw client request body is not stored",
      )
    : isUpstreamResponse
      ? titleForLocale(
          locale,
          "当前日志未保存上游原始响应体",
          "Raw upstream response body is not stored",
        )
      : titleForLocale(locale, "无内容", "No content");

  return (
    <section className="flex min-h-[clamp(240px,36dvh,380px)] min-w-0 flex-1 flex-col overflow-hidden rounded-xl border">
      <header className="flex shrink-0 flex-wrap items-start justify-between gap-2 border-b px-3 py-2.5 sm:px-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <h2 className="text-sm font-semibold">
              {trace.step} ·{" "}
              {titleForLocale(locale, trace.directionZh, trace.directionEn)}
            </h2>
            <Badge
              variant="secondary"
              className={cn(
                "h-5 px-1.5 text-[10px] font-semibold",
                trace.tone === "chat"
                  ? "bg-primary/10 text-primary"
                  : "bg-violet-500/10 text-violet-700 dark:text-violet-300",
              )}
            >
              {traceBadgeLabel(trace, detail, locale)}
            </Badge>
            <Badge variant="outline" className="h-5 px-1.5 text-[10px]">
              {titleForLocale(
                locale,
                isInboundRequest || isUpstreamResponse ? "原始" : "已转换",
                isInboundRequest || isUpstreamResponse ? "Raw" : "Converted",
              )}
            </Badge>
          </div>
          <div className="mt-1 truncate font-mono text-[10px] text-muted-foreground">
            {traceRoute(trace, detail, locale)}
          </div>
        </div>
        <span className="text-[10px] text-muted-foreground">
          {titleForLocale(
            locale,
            trace.request ? "请求阶段" : "响应阶段",
            trace.request ? "Request stage" : "Response stage",
          )}
        </span>
      </header>
      <div className="grid min-h-0 flex-1 sm:grid-cols-2">
        <JsonViewer
          key={`${trace.key}-headers-${detail.id}`}
          className="h-full !min-h-0"
          title={headerTitle}
          content={headers}
          emptyText={headerEmptyText}
          locale={locale}
        />
        <JsonViewer
          key={`${trace.key}-content-${detail.id}`}
          className="h-full !min-h-0 border-t sm:border-l sm:border-t-0"
          title={contentTitle}
          content={content}
          emptyText={contentEmptyText}
          locale={locale}
        />
      </div>
      <footer className="flex flex-wrap items-center justify-between gap-2 border-t bg-muted/20 px-3 py-2 text-[10px] text-muted-foreground sm:px-4">
        <span>
          {isInboundRequest
            ? titleForLocale(locale, "Lens 接收到的格式：", "Lens received: ") +
              protocolLabel(detail.protocol, locale)
            : isUpstreamRequest
              ? titleForLocale(
                  locale,
                  "上游请求由 Lens 转换生成",
                  "Upstream request generated by Lens",
                )
              : isUpstreamResponse
                ? titleForLocale(
                    locale,
                    "当前日志展示下游最终响应内容",
                    "Current log stores the final client response",
                  )
                : titleForLocale(
                    locale,
                    "下游最终收到的格式：",
                    "Client received: ",
                  ) + protocolLabel(detail.protocol, locale)}
        </span>
        <span>
          {isInboundRequest || isUpstreamRequest
            ? titleForLocale(locale, "下一跳：", "Next: ") +
              (isInboundRequest ? "Lens → 上游" : "上游")
            : titleForLocale(locale, "上一跳：", "Previous: ") +
              (isUpstreamResponse ? "Lens → 下游" : "上游 → Lens")}
        </span>
      </footer>
    </section>
  );
}

export function RequestLogDetailPanel({
  detail,
  locale,
}: {
  detail: RequestLogDetail;
  locale: "zh-CN" | "en-US";
}) {
  const [activeTrace, setActiveTrace] = useState<TraceKey>("inbound-request");
  const errorDisplay = formatErrorDisplay(detail.error_message);
  const running =
    detail.lifecycle_status === "connecting" ||
    detail.lifecycle_status === "streaming";
  const modelName = detail.reasoning_effort
    ? `${getModelChain(detail)} ${detail.reasoning_effort}`
    : getModelChain(detail);
  const trace = getTraceDefinition(activeTrace);
  const channelLabel = detail.channel_name || detail.channel_id || "n/a";

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border bg-muted/35 px-3 py-2.5">
        <ProtocolBadge protocol={detail.protocol} />
        <RequestOutcomeBadge
          status={detail.lifecycle_status}
          success={detail.success}
          statusCode={detail.status_code}
          locale={locale}
          errorMessage={errorDisplay}
        />
        {detail.request_id ? (
          <Badge
            variant="secondary"
            className="max-w-[150px] truncate font-mono text-[11px]"
            title={detail.request_id}
          >
            {shortRequestId(detail.request_id)}
          </Badge>
        ) : null}
        <span className="hidden h-3.5 w-px bg-border sm:block" />
        <SummaryStat
          label={titleForLocale(locale, "模型", "Model")}
          value={modelName || getResolvedGroupName(detail)}
        />
        <SummaryStat
          label={titleForLocale(locale, "渠道", "Channel")}
          value={formatChannelCredentialLabel(detail)}
        />
        <SummaryStat
          label={titleForLocale(locale, "首字", "First token")}
          value={formatMs(detail.first_token_latency_ms)}
        />
        <SummaryStat
          label={titleForLocale(locale, "总耗时", "Total")}
          value={formatMs(detail.latency_ms)}
        />
        <SummaryStat
          label={titleForLocale(locale, "TPS", "TPS")}
          value={formatTps(detail.tokens_per_second, running)}
        />
        <SummaryStat
          label={titleForLocale(locale, "费用", "Cost")}
          value={formatMaybeMoney(detail.total_cost_usd, running)}
        />
      </div>

      <section className="min-w-0 shrink-0 overflow-hidden rounded-xl border">
        <header className="flex flex-wrap items-end justify-between gap-2 border-b px-3 py-2.5 sm:px-4">
          <div>
            <h2 className="text-sm font-semibold">
              {titleForLocale(locale, "网络链路", "Network flow")}
            </h2>
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              {titleForLocale(
                locale,
                "纵向泳道表示参与方，事件卡片表示一次网络传输",
                "Lanes are participants; cards are network transfers",
              )}
            </p>
          </div>
          <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">
            {titleForLocale(
              locale,
              "点击事件卡片查看详情",
              "Click a card for details",
            )}
          </Badge>
        </header>
        <div className="overflow-x-auto">
          <div
            className="relative h-[360px] min-w-[800px] overflow-hidden bg-muted/10"
            style={{
              backgroundImage:
                "radial-gradient(circle, var(--border) 0.75px, transparent 0.75px)",
              backgroundSize: "16px 16px",
            }}
          >
            <FlowLane
              left="5%"
              title={titleForLocale(locale, "下游客户端", "Client")}
              subtitle={
                detail.user_agent || titleForLocale(locale, "客户端", "Client")
              }
              tone="client"
              connectionYs={[78, 290]}
            />
            <FlowLane
              left="50%"
              title={titleForLocale(locale, "Lens 网关", "Lens")}
              subtitle={titleForLocale(
                locale,
                "路由 · 协议转换",
                "Routing · conversion",
              )}
              tone="gateway"
              connectionYs={[78, 138, 214, 290]}
            />
            <FlowLane
              left="95%"
              title={titleForLocale(locale, "上游渠道", "Upstream")}
              subtitle={channelLabel}
              tone="upstream"
              connectionYs={[138, 214]}
            />

            <svg
              className="pointer-events-none absolute inset-0 h-full w-full"
              viewBox="0 0 1000 360"
              preserveAspectRatio="none"
              aria-hidden="true"
            >
              <defs>
                <marker
                  id="trace-arrow-right"
                  markerWidth="8"
                  markerHeight="8"
                  refX="7"
                  refY="4"
                  orient="auto"
                >
                  <path
                    d="M 0 0 L 8 4 L 0 8 z"
                    fill="var(--muted-foreground)"
                  />
                </marker>
              </defs>
              <g
                stroke="var(--muted-foreground)"
                strokeOpacity="0.55"
                strokeWidth="1.8"
              >
                <line x1="50" y1="78" x2="100" y2="78" />
                <line
                  x1="350"
                  y1="78"
                  x2="500"
                  y2="78"
                  markerEnd="url(#trace-arrow-right)"
                />
                <line x1="500" y1="138" x2="560" y2="138" />
                <line
                  x1="810"
                  y1="138"
                  x2="950"
                  y2="138"
                  markerEnd="url(#trace-arrow-right)"
                />
                <line x1="950" y1="214" x2="810" y2="214" />
                <line
                  x1="560"
                  y1="214"
                  x2="500"
                  y2="214"
                  markerEnd="url(#trace-arrow-right)"
                />
                <line x1="500" y1="290" x2="350" y2="290" />
                <line
                  x1="100"
                  y1="290"
                  x2="50"
                  y2="290"
                  markerEnd="url(#trace-arrow-right)"
                />
              </g>
            </svg>

            <TraceCard
              trace={TRACE_DEFINITIONS[0]}
              detail={detail}
              locale={locale}
              active={activeTrace === "inbound-request"}
              onSelect={() => setActiveTrace("inbound-request")}
              className="left-[10%] top-[54px] w-[25%]"
            />
            <TraceCard
              trace={TRACE_DEFINITIONS[1]}
              detail={detail}
              locale={locale}
              active={activeTrace === "upstream-request"}
              onSelect={() => setActiveTrace("upstream-request")}
              className="left-[56%] top-[114px] w-[25%]"
            />
            <TraceCard
              trace={TRACE_DEFINITIONS[2]}
              detail={detail}
              locale={locale}
              active={activeTrace === "upstream-response"}
              onSelect={() => setActiveTrace("upstream-response")}
              className="left-[56%] top-[190px] w-[25%]"
            />
            <TraceCard
              trace={TRACE_DEFINITIONS[3]}
              detail={detail}
              locale={locale}
              active={activeTrace === "client-response"}
              onSelect={() => setActiveTrace("client-response")}
              className="left-[10%] top-[266px] w-[25%]"
            />
          </div>
        </div>
      </section>

      <TraceDetail detail={detail} trace={trace} locale={locale} />
    </div>
  );
}
