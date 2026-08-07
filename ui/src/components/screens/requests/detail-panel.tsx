"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { RequestLogDetail } from "@/lib/api";
import { cn } from "@/lib/utils";
import { ProtocolBadge, RequestOutcomeBadge } from "./components";
import {
  formatChannelCredentialLabel,
  formatErrorDisplay,
  formatMaybeMoney,
  formatMs,
  formatTps,
  getModelChain,
  getResolvedGroupName,
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

export function RequestLogDetailPanel({
  detail,
  locale,
}: {
  detail: RequestLogDetail;
  locale: "zh-CN" | "en-US";
}) {
  const [tab, setTab] = useState("headers");
  const errorDisplay = formatErrorDisplay(detail.error_message);
  const running =
    detail.lifecycle_status === "connecting" ||
    detail.lifecycle_status === "streaming";
  const modelName = detail.reasoning_effort
    ? `${getModelChain(detail)} ${detail.reasoning_effort}`
    : getModelChain(detail);

  return (
    <div className="grid min-h-0 gap-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border bg-muted/35 px-3 py-2.5">
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

      <Tabs
        value={tab}
        onValueChange={setTab}
        className="min-h-0 gap-0 overflow-hidden rounded-xl border"
      >
        <TabsList className="h-auto w-full justify-start gap-0 rounded-none border-b bg-transparent p-0">
          {(
            [
              ["headers", "请求头", "Headers"],
              ["request", "请求内容", "Request"],
              ["response", "响应内容", "Response"],
            ] as const
          ).map(([value, zh, en]) => (
            <TabsTrigger
              key={value}
              value={value}
              className={cn(
                "rounded-none border-b-2 border-transparent px-4 py-2.5 text-sm shadow-none",
                "data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none",
              )}
            >
              {titleForLocale(locale, zh, en)}
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="headers" className="mt-0 outline-none">
          {tab === "headers" ? (
            <div className="grid min-h-[50dvh] sm:min-h-[480px] xl:grid-cols-2">
              <JsonViewer
                key={`inbound-headers-${detail.id}`}
                className="min-h-[40dvh] sm:min-h-[420px]"
                title={titleForLocale(locale, "入站请求头", "Inbound headers")}
                content={detail.request_headers}
                emptyText={titleForLocale(
                  locale,
                  "无入站请求头",
                  "No inbound headers",
                )}
                locale={locale}
              />
              <JsonViewer
                key={`upstream-headers-${detail.id}`}
                className="min-h-[40dvh] border-t sm:min-h-[420px] xl:border-t-0 xl:border-l"
                title={titleForLocale(locale, "上游请求头", "Upstream headers")}
                content={detail.upstream_headers}
                emptyText={titleForLocale(
                  locale,
                  "无上游请求头",
                  "No upstream headers",
                )}
                locale={locale}
              />
            </div>
          ) : null}
        </TabsContent>
        <TabsContent value="request" className="mt-0 outline-none">
          {tab === "request" ? (
            <JsonViewer
              key={`request-${detail.id}`}
              className="min-h-[50dvh] sm:min-h-[480px]"
              title={titleForLocale(locale, "请求内容", "Request")}
              content={detail.request_content}
              emptyText={titleForLocale(
                locale,
                "无输入内容",
                "No request content",
              )}
              locale={locale}
            />
          ) : null}
        </TabsContent>
        <TabsContent value="response" className="mt-0 outline-none">
          {tab === "response" ? (
            <JsonViewer
              key={`response-${detail.id}`}
              className="min-h-[50dvh] sm:min-h-[480px]"
              title={titleForLocale(locale, "响应内容", "Response")}
              content={detail.response_content}
              emptyText={titleForLocale(
                locale,
                "无输出内容",
                "No response content",
              )}
              locale={locale}
            />
          ) : null}
        </TabsContent>
      </Tabs>
    </div>
  );
}
