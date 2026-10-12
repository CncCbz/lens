"use client";

import type { Dispatch, SetStateAction } from "react";
import { Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type {
  CredentialBalance,
  ProtocolKind,
  Site,
  SiteRuntimeSummary,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { Switch } from "@/components/ui/switch";
import { ChannelFiltersPanel } from "./filters";
import { SiteHealthPreview } from "./health-preview";
import {
  isSiteEnabled,
  protocolBadgeClassName,
  protocolLabel,
  SiteFavicon,
  siteProtocols,
  type ChannelSort,
  type ChannelStatusFilter,
  type Locale,
  type SiteRow,
} from "./shared";

function siteCredentialCount(site: SiteRow) {
  return site.credentials.filter((item) => item.enabled).length;
}

export function ChannelsOverview({
  locale,
  visibleSites,
  isLoading,
  sitesIsError,
  siteRuntimeById,
  timeZone,
  search,
  statusFilter,
  protocolFilter,
  sortBy,
  activeFilterCount,
  busyId,
  onSearchChange,
  onStatusChange,
  onProtocolChange,
  onSortChange,
  onReset,
  onOpenEdit,
  onToggleSiteEnabled,
  setDeleteTarget,
  balancesByCredentialId,
}: {
  locale: Locale;
  visibleSites: SiteRow[];
  isLoading: boolean;
  sitesIsError: boolean;
  siteRuntimeById: Map<string, SiteRuntimeSummary>;
  timeZone?: string;
  search: string;
  statusFilter: ChannelStatusFilter;
  protocolFilter: "all" | ProtocolKind;
  sortBy: ChannelSort;
  activeFilterCount: number;
  busyId: string | null;
  onSearchChange: Dispatch<SetStateAction<string>>;
  onStatusChange: Dispatch<SetStateAction<ChannelStatusFilter>>;
  onProtocolChange: Dispatch<SetStateAction<"all" | ProtocolKind>>;
  onSortChange: Dispatch<SetStateAction<ChannelSort>>;
  onReset: () => void;
  onOpenEdit: (site: Site) => void;
  onToggleSiteEnabled: (site: Site, enabled: boolean) => void;
  setDeleteTarget: Dispatch<SetStateAction<Site | null>>;
  balancesByCredentialId: Map<string, CredentialBalance>;
}) {
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
      <Card className="overflow-hidden py-0 min-h-0 xl:min-h-[calc(100dvh-7.5rem)]">
        <CardContent className="max-h-[calc(100dvh-7.5rem)] overflow-y-auto px-3 py-3">
          {isLoading || sitesIsError ? null : visibleSites.length ? (
            <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-3">
              {visibleSites.map((site) => {
                const runtimeSummary = siteRuntimeById.get(site.id);
                const requestCount = runtimeSummary?.recent_request_count ?? 0;
                const credentialCount = siteCredentialCount(site);
                const protocols = siteProtocols(site);
                const endpointUrl =
                  site.base_urls.find((item) => item.enabled)?.url ||
                  site.base_urls[0]?.url ||
                  "";
                return (
                  <div
                    key={site.id}
                    role="button"
                    tabIndex={0}
                    className="flex h-full min-h-0 flex-col gap-2.5 overflow-hidden rounded-2xl border border-border/80 bg-gradient-to-b from-background to-muted/[0.18] p-3.5 shadow-sm transition-shadow hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 cursor-pointer"
                    onClick={() => onOpenEdit(site)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        onOpenEdit(site);
                      }
                    }}
                  >
                    <div className="flex min-w-0 items-start gap-2.5">
                      <SiteFavicon name={site.name} />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <h3 className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">
                            {site.name}
                          </h3>
                          <div
                            className="shrink-0"
                            onClick={(event) => event.stopPropagation()}
                            onKeyDown={(event) => event.stopPropagation()}
                          >
                            <Switch
                              checked={isSiteEnabled(site)}
                              disabled={busyId === site.id}
                              onCheckedChange={(checked) =>
                                void onToggleSiteEnabled(site, checked)
                              }
                            />
                          </div>
                        </div>
                        {endpointUrl ? (
                          <a
                            href={endpointUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="mt-0.5 block truncate text-xs text-muted-foreground hover:text-foreground hover:underline"
                            onClick={(event) => event.stopPropagation()}
                            onKeyDown={(event) => event.stopPropagation()}
                          >
                            {site.endpoint_summary}
                          </a>
                        ) : (
                          <p className="mt-0.5 truncate text-xs text-muted-foreground">
                            {locale === "zh-CN"
                              ? "未配置请求地址"
                              : "No endpoint configured"}
                          </p>
                        )}
                      </div>
                    </div>

                    <div className="flex h-5 min-w-0 items-center gap-1 overflow-hidden">
                      {protocols.length ? (
                        protocols.map((p) => (
                          <Badge
                            key={p}
                            variant="outline"
                            className={cn(
                              "shrink-0 px-1.5 py-0 text-[10px]",
                              protocolBadgeClassName(p),
                            )}
                          >
                            {protocolLabel(p, locale)}
                          </Badge>
                        ))
                      ) : (
                        <span className="text-[11px] text-muted-foreground">
                          {locale === "zh-CN" ? "无协议" : "No protocols"}
                        </span>
                      )}
                    </div>

                    <div className="min-h-7">
                      <SiteHealthPreview
                        site={site}
                        summary={runtimeSummary}
                        locale={locale}
                        timeZone={timeZone}
                      />
                    </div>

                    {(() => {
                      const readyBalances = site.credentials
                        .filter((item) => item.enabled)
                        .map((cred) => ({
                          credential: cred,
                          balance: balancesByCredentialId.get(cred.id),
                        }))
                        .filter(
                          (
                            item,
                          ): item is {
                            credential: (typeof site.credentials)[number];
                            balance: CredentialBalance;
                          } => item.balance?.status === "ok",
                        );
                      if (!readyBalances.length) return null;
                      const shown = readyBalances.slice(0, 2);
                      const remaining = readyBalances.length - shown.length;
                      const tooltip = readyBalances
                        .map(
                          (item) =>
                            `${item.credential.name.trim() || (locale === "zh-CN" ? "密钥" : "Key")}: ${item.balance.balance} ${item.balance.currency}`,
                        )
                        .join("\n");

                      return (
                        <div
                          className="flex min-w-0 flex-wrap items-center gap-1.5"
                          title={tooltip}
                        >
                          {shown.map(({ credential, balance }) => {
                            const keyName =
                              credential.name.trim() ||
                              (locale === "zh-CN" ? "密钥" : "Key");
                            return (
                              <div
                                key={credential.id}
                                className="inline-flex max-w-full min-w-0 items-center gap-1 rounded-md border border-border/70 bg-background/80 px-1.5 py-0.5 text-[11px] shadow-2xs"
                                title={`${keyName}: ${balance.balance} ${balance.currency}`}
                              >
                                <span className="max-w-[76px] truncate text-muted-foreground">
                                  {keyName}
                                </span>
                                <span className="font-mono font-medium text-foreground">
                                  {balance.balance}
                                </span>
                                <span className="text-[10px] text-muted-foreground">
                                  {balance.currency}
                                </span>
                              </div>
                            );
                          })}
                          {remaining > 0 ? (
                            <span
                              className="rounded-md border border-border/60 bg-muted/40 px-1.5 py-0.5 text-[10px] font-mono text-muted-foreground"
                              title={tooltip}
                            >
                              +{remaining}
                            </span>
                          ) : null}
                        </div>
                      );
                    })()}

                    <div
                      className="mt-auto flex items-center justify-between gap-2 border-t border-border/60 pt-2"
                      onClick={(event) => event.stopPropagation()}
                      onKeyDown={(event) => event.stopPropagation()}
                    >
                      <span className="truncate text-[11px] text-muted-foreground">
                        {locale === "zh-CN"
                          ? `${site.model_count} 模型 · ${credentialCount} 密钥 · ${requestCount} 请求`
                          : `${site.model_count} models · ${credentialCount} keys · ${requestCount} req`}
                      </span>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        className="size-7 shrink-0 text-destructive hover:text-destructive"
                        aria-label={
                          locale === "zh-CN" ? "删除渠道" : "Delete channel"
                        }
                        onClick={() => setDeleteTarget(site)}
                      >
                        <Trash2 />
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="rounded-xl border border-dashed px-6 py-12 text-center text-sm text-muted-foreground">
              {search.trim()
                ? locale === "zh-CN"
                  ? "没有匹配的渠道。"
                  : "No matching channels."
                : locale === "zh-CN"
                  ? "当前还没有渠道。"
                  : "No channels yet."}
            </div>
          )}
        </CardContent>
      </Card>

      <aside className="order-1 xl:order-2">
        <ChannelFiltersPanel
          locale={locale}
          search={search}
          statusFilter={statusFilter}
          protocolFilter={protocolFilter}
          sortBy={sortBy}
          activeFilterCount={activeFilterCount}
          onSearchChange={onSearchChange}
          onStatusChange={onStatusChange}
          onProtocolChange={onProtocolChange}
          onSortChange={onSortChange}
          onReset={onReset}
        />
      </aside>
    </div>
  );
}
