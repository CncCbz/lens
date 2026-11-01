"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw, Settings2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  listModelVerifications,
  type ModelVerificationResult,
} from "@/lib/api";
import { titleForLocale, useI18n } from "@/lib/i18n";
import { DecisionBadge } from "./decision-badge";
import { ConfidenceBadge, type Thresholds } from "./confidence";

const PAGE_SIZE = 20;

type DecisionFilter = "all" | "scored" | "partial" | "unscorable";
type TriggerFilter = "all" | "manual" | "scheduled";

export function VerificationHistoryCard({
  siteId,
  modelName,
  thresholds,
  onOpenSettings,
  onSelect,
}: {
  siteId: string;
  modelName: string;
  thresholds: Thresholds;
  onOpenSettings: () => void;
  onSelect: (row: ModelVerificationResult) => void;
}) {
  const { locale } = useI18n();
  const [page, setPage] = useState(0);
  const [modelQuery, setModelQuery] = useState("");
  const [decision, setDecision] = useState<DecisionFilter>("all");
  const [trigger, setTrigger] = useState<TriggerFilter>("all");

  // A model filter narrows the list to one model, so the free-text box only
  // matters when the page is showing more than that.
  const effectiveModel = modelName || modelQuery.trim();

  const historyQuery = useQuery({
    queryKey: [
      "model-verifications",
      "page",
      siteId,
      effectiveModel,
      decision,
      trigger,
      page,
    ],
    queryFn: () =>
      listModelVerifications({
        site_id: siteId || undefined,
        model_name: effectiveModel || undefined,
        decision: decision === "all" ? undefined : decision,
        trigger: trigger === "all" ? undefined : trigger,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
  });

  const total = historyQuery.data?.total ?? 0;
  const rows = historyQuery.data?.items ?? [];
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  // A shrinking result set can leave the offset past the end. Clamping here
  // keeps the pager honest without a state update inside an effect; the next
  // fetch uses the clamped page.
  const currentPage = Math.min(page, pageCount - 1);

  function resetPage() {
    setPage(0);
  }

  return (
    <Card>
      <CardContent className="flex flex-col gap-3 py-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-medium">
            {titleForLocale(locale, "历史记录", "History")}
          </h2>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={onOpenSettings}>
              <Settings2 className="mr-1.5 size-3.5" />
              {titleForLocale(locale, "定时任务参数", "Scheduled run settings")}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void historyQuery.refetch()}
            >
              <RefreshCw className="size-3.5" />
            </Button>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Input
            value={modelQuery}
            onChange={(event) => {
              setModelQuery(event.target.value);
              resetPage();
            }}
            placeholder={titleForLocale(locale, "筛选模型", "Filter model")}
            className="h-8 w-44"
          />
          <Select
            value={decision}
            onValueChange={(value) => {
              setDecision(value as DecisionFilter);
              resetPage();
            }}
          >
            <SelectTrigger className="h-8 w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">
                {titleForLocale(locale, "全部结论", "All verdicts")}
              </SelectItem>
              <SelectItem value="scored">
                {titleForLocale(locale, "已评分", "Scored")}
              </SelectItem>
              <SelectItem value="partial">
                {titleForLocale(locale, "部分", "Partial")}
              </SelectItem>
              <SelectItem value="unscorable">
                {titleForLocale(locale, "无法评分", "Unscorable")}
              </SelectItem>
            </SelectContent>
          </Select>
          <Select
            value={trigger}
            onValueChange={(value) => {
              setTrigger(value as TriggerFilter);
              resetPage();
            }}
          >
            <SelectTrigger className="h-8 w-28">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">
                {titleForLocale(locale, "全部来源", "All sources")}
              </SelectItem>
              <SelectItem value="manual">
                {titleForLocale(locale, "手动", "Manual")}
              </SelectItem>
              <SelectItem value="scheduled">
                {titleForLocale(locale, "定时", "Scheduled")}
              </SelectItem>
            </SelectContent>
          </Select>
          <span className="ml-auto text-[11px] text-muted-foreground tabular-nums">
            {total}
          </span>
        </div>

        {/* A fixed height keeps the page from jumping as rows stream in; the
            table scrolls inside it instead. The table wrapper's own overflow is
            disabled so this div is the only scroll container, which is what
            makes the sticky header stick. */}
        <div className="max-h-[26rem] overflow-auto rounded-lg border [&>[data-slot=table-container]]:overflow-visible">
          <Table>
            {/* The background must sit on the cells too: <thead> alone leaves
                the header transparent, so scrolled rows show through it. */}
            <TableHeader className="sticky top-0 z-10 bg-card [&_th]:bg-card">
              <TableRow>
                <TableHead>{titleForLocale(locale, "模型", "Model")}</TableHead>
                <TableHead>
                  {titleForLocale(locale, "结论", "Verdict")}
                </TableHead>
                <TableHead>
                  {titleForLocale(locale, "识别为", "Identified as")}
                </TableHead>
                <TableHead>
                  {titleForLocale(locale, "触发", "Trigger")}
                </TableHead>
                <TableHead>
                  {titleForLocale(locale, "置信度", "Confidence")}
                </TableHead>
                <TableHead>{titleForLocale(locale, "时间", "Time")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow
                  key={row.id}
                  className="cursor-pointer"
                  onClick={() => onSelect(row)}
                >
                  <TableCell className="font-mono text-xs">
                    {row.model_name}
                  </TableCell>
                  <TableCell>
                    <DecisionBadge decision={row.decision} locale={locale} />
                  </TableCell>
                  <TableCell className="text-xs">
                    {row.prediction_name || "—"}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {row.trigger === "scheduled"
                      ? titleForLocale(locale, "定时", "Scheduled")
                      : titleForLocale(locale, "手动", "Manual")}
                  </TableCell>
                  <TableCell>
                    <ConfidenceBadge
                      verdict={{
                        probability: row.probability ?? null,
                        decision: row.decision,
                        prediction: row.prediction,
                        prediction_name: row.prediction_name,
                        model_name: row.model_name,
                        matches_expected: row.matches_expected ?? null,
                      }}
                      thresholds={thresholds}
                      locale={locale}
                    />
                    {row.probability == null ? (
                      <span className="text-xs text-muted-foreground">—</span>
                    ) : null}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {row.created_at
                      ? row.created_at.slice(0, 19).replace("T", " ")
                      : "—"}
                  </TableCell>
                </TableRow>
              ))}
              {rows.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={6}
                    className="py-8 text-center text-xs text-muted-foreground"
                  >
                    {historyQuery.isLoading
                      ? titleForLocale(locale, "加载中", "Loading")
                      : titleForLocale(
                          locale,
                          "没有匹配的记录",
                          "No matching records",
                        )}
                  </TableCell>
                </TableRow>
              ) : null}
            </TableBody>
          </Table>
        </div>

        <div className="flex items-center justify-between">
          <span className="text-[11px] text-muted-foreground tabular-nums">
            {currentPage + 1} / {pageCount}
          </span>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={currentPage === 0 || historyQuery.isFetching}
              onClick={() => setPage(currentPage - 1)}
            >
              {titleForLocale(locale, "上一页", "Previous")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={currentPage >= pageCount - 1 || historyQuery.isFetching}
              onClick={() => setPage(currentPage + 1)}
            >
              {titleForLocale(locale, "下一页", "Next")}
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
