"use client";

import { useMemo, type Dispatch, type SetStateAction } from "react";
import { ShieldCheck, Trash2 } from "lucide-react";
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
import {
  toneTextClass,
  useLatestVerdicts,
  useVerificationThresholds,
  verdictKey,
  verdictTone,
} from "@/components/screens/verification/confidence";

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
  const verdicts = useLatestVerdicts();
  const thresholds = useVerificationThresholds();

  // Roll the per-model verdicts up to one line per channel so a bad model
  // is visible without opening anything.
  const verdictSummary = useMemo(() => {
    const summary = new Map<
      string,
      { total: number; risky: number; questionable: number; trusted: number }
    >();
    for (const site of visibleSites) {
      const counts = {
        total: 0,
        risky: 0,
        questionable: 0,
        trusted: 0,
      };
      for (const combo of site.protocols) {
        for (const model of combo.models) {
          const verdict = verdicts.get(
            verdictKey(combo.id, model.credential_id, model.model_name),
          );
          if (!verdict) continue;
          counts.total += 1;
          const tone = verdictTone(verdict.probability, thresholds);
          if (tone === "risky") counts.risky += 1;
          else if (tone === "questionable") counts.questionable += 1;
          else if (tone === "trusted") counts.trusted += 1;
        }
      }
      summary.set(site.id, counts);
    }
    return summary;
  }, [thresholds, verdicts, visibleSites]);
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
      <Card className="overflow-hidden py-0 min-h-0 xl:min-h-[calc(100dvh-7.5rem)]">
        <CardContent className="max-h-[calc(100dvh-7.5rem)] overflow-y-auto px-3 py-3">
          {isLoading || sitesIsError ? null : visibleSites.length ? (
            <div className="columns-1 gap-3 sm:columns-2 2xl:columns-3">
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
                    className={cn(
                      "mb-3 flex min-h-0 break-inside-avoid flex-col gap-2.5 overflow-hidden rounded-2xl border border-border/80 bg-gradient-to-b from-background to-muted/[0.18] p-3.5 shadow-sm transition-all hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 cursor-pointer",
                      !isSiteEnabled(site) && "opacity-60",
                    )}
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

                      return (
                        <div
                          className={cn(
                            "grid gap-1.5",
                            readyBalances.length === 1
                              ? "grid-cols-1"
                              : "grid-cols-2",
                          )}
                        >
                          {readyBalances.map(({ credential, balance }) => {
                            const keyName =
                              credential.name.trim() ||
                              (locale === "zh-CN" ? "默认密钥" : "Key");
                            return (
                              <div
                                key={credential.id}
                                className="flex min-w-0 flex-col gap-0.5 rounded-xl border border-border/70 bg-muted/35 px-2.5 py-1.5 shadow-2xs transition-colors hover:border-border hover:bg-muted/50"
                                title={`${keyName}: ${balance.balance} ${balance.currency}`}
                              >
                                <span className="truncate text-[11px] font-medium text-muted-foreground">
                                  {keyName}
                                </span>
                                <div className="flex min-w-0 items-baseline gap-1">
                                  <span className="truncate font-mono text-sm font-bold tracking-tight text-foreground">
                                    {balance.balance}
                                  </span>
                                  <span className="text-[10px] font-semibold uppercase text-muted-foreground">
                                    {balance.currency}
                                  </span>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      );
                    })()}

                    <div className="flex min-h-6 items-center justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <SiteHealthPreview
                          site={site}
                          summary={runtimeSummary}
                          locale={locale}
                          timeZone={timeZone}
                        />
                      </div>
                      <span className="shrink-0 text-xs font-medium text-muted-foreground">
                        {locale === "zh-CN"
                          ? `${requestCount} 请求`
                          : `${requestCount} req`}
                      </span>
                    </div>

                    <div
                      className="mt-auto flex items-center justify-between gap-2 border-t border-border/60 pt-2"
                      onClick={(event) => event.stopPropagation()}
                      onKeyDown={(event) => event.stopPropagation()}
                    >
                      <span className="flex min-w-0 items-center gap-2 truncate text-[11px] text-muted-foreground">
                        <span className="truncate">
                          {locale === "zh-CN"
                            ? `${site.model_count} 模型 · ${credentialCount} 密钥`
                            : `${site.model_count} models · ${credentialCount} keys`}
                        </span>
                        {(() => {
                          const counts = verdictSummary.get(site.id);
                          if (!counts || counts.total === 0) return null;
                          const tone =
                            counts.risky > 0
                              ? "risky"
                              : counts.questionable > 0
                                ? "questionable"
                                : "trusted";
                          return (
                            <span
                              className={cn(
                                "inline-flex shrink-0 items-center gap-1",
                                toneTextClass(tone),
                              )}
                              title={
                                locale === "zh-CN"
                                  ? `已验真 ${counts.total} 个：可信 ${counts.trusted}，可疑 ${counts.questionable}，低置信 ${counts.risky}`
                                  : `Verified ${counts.total}: ${counts.trusted} trusted, ${counts.questionable} questionable, ${counts.risky} low`
                              }
                            >
                              <ShieldCheck className="size-3" />
                              {counts.total}
                            </span>
                          );
                        })()}
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
