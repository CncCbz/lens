"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Play } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  getModelVerificationRun,
  startModelVerification,
  type ModelVerificationResult,
  type ModelVerificationRun,
} from "@/lib/api";
import { titleForLocale, useI18n } from "@/lib/i18n";

import {
  useVerificationCatalog,
  type VerificationTarget,
} from "./verification/catalog";
import { useVerificationThresholds } from "./verification/confidence";
import { VerificationHistoryCard } from "./verification/history-card";
import { VerificationResultDialog } from "./verification/result-panel";
import { VerificationScheduleDialog } from "./verification/schedule-card";

// A run outlives the screen: the backend keeps it, so leaving the page and
// coming back only needs the run id to pick the spinner and verdict back up.
const RUN_ID_STORAGE_KEY = "lens.verification.runId";
const POLL_INTERVAL_MS = 2000;

function readRunId(): string | null {
  try {
    return window.localStorage.getItem(RUN_ID_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeRunId(runId: string): void {
  try {
    if (runId) {
      window.localStorage.setItem(RUN_ID_STORAGE_KEY, runId);
    } else {
      window.localStorage.removeItem(RUN_ID_STORAGE_KEY);
    }
  } catch {
    // Storage is unavailable; the run still works while the page stays open.
  }
}

export function VerificationScreen() {
  const { locale } = useI18n();
  const queryClient = useQueryClient();
  const { targets, isLoading, error } = useVerificationCatalog();

  const [siteId, setSiteId] = useState("");
  const [targetKey, setTargetKey] = useState("");
  const [count, setCount] = useState("3");
  const [effort, setEffort] = useState("none");
  const [strict, setStrict] = useState(false);
  // Only a history row the user opened; the latest run comes from the cache.
  const [picked, setPicked] = useState<ModelVerificationResult | null>(null);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const thresholds = useVerificationThresholds();
  const [runId, setRunId] = useState<string>(() => readRunId() ?? "");

  const sites = useMemo(() => {
    const seen = new Map<string, string>();
    for (const target of targets) {
      if (!seen.has(target.siteId)) seen.set(target.siteId, target.siteName);
    }
    return Array.from(seen, ([id, name]) => ({ id, name }));
  }, [targets]);

  const visibleTargets = useMemo(
    () =>
      targets.filter(
        (target) => target.verifiable && (!siteId || target.siteId === siteId),
      ),
    [siteId, targets],
  );

  const selected = useMemo(
    () => targets.find((target) => target.key === targetKey) ?? null,
    [targetKey, targets],
  );

  // The canonical group name is the claim under test (falling back to the
  // channel model name), so a prefixed upstream model name still matches the
  // reference bank.
  const expected = selected?.groupName || selected?.modelName || "";

  // Levels belong to the group, so a stale choice from another model must not
  // survive the switch; the selection is kept only while it stays valid.
  const activeEffort = useMemo(() => {
    if (!selected) return "none";
    const levels = selected.efforts.map((option) => option.level);
    return levels.includes(effort) ? effort : selected.defaultEffort;
  }, [effort, selected]);

  const startMutation = useMutation({
    mutationFn: async (target: VerificationTarget) =>
      startModelVerification({
        target: {
          site_id: target.siteId,
          protocol_config_id: target.comboId,
          credential_id: target.credentialId,
          model_name: target.modelName,
        },
        count: Number(count),
        strict,
        reasoning_effort: activeEffort,
        expected_model: expected.trim(),
      }),
    onSuccess: (run) => {
      setPicked(null);
      setRunId(run.run_id);
      writeRunId(run.run_id);
    },
    onError: (cause: Error) => toast.error(cause.message),
  });

  // Polling stops on its own once the run settles, so this stays cheap while
  // the page is open and resumes from the stored id after a remount.
  const runQuery = useQuery<ModelVerificationRun>({
    queryKey: ["model-verification-run", runId],
    queryFn: () => getModelVerificationRun(runId),
    enabled: Boolean(runId),
    refetchInterval: (query) =>
      query.state.data?.status === "running" ? POLL_INTERVAL_MS : false,
    refetchOnWindowFocus: true,
  });

  const runStatus = runId ? (runQuery.data?.status ?? "running") : "idle";
  const isRunning = runStatus === "running";
  const result = picked ?? runQuery.data?.result ?? null;

  // Settling once is enough: clear the stored id so a reload does not replay
  // the outcome, and refresh the history the finished run appended to.
  const settled = runStatus === "succeeded" || runStatus === "failed";
  useEffect(() => {
    if (!settled) return;
    writeRunId("");
    if (runStatus === "succeeded") {
      // The history page keys off ["model-verifications", ...], so this
      // refetches whichever page is on screen.
      void queryClient.invalidateQueries({ queryKey: ["model-verifications"] });
    } else {
      toast.error(runQuery.data?.error || "Verification failed");
    }
  }, [queryClient, runQuery.data?.error, runStatus, settled]);

  const handleRun = useCallback(() => {
    if (!selected) {
      toast.error(titleForLocale(locale, "先选择模型", "Pick a model first"));
      return;
    }
    setPicked(null);
    startMutation.mutate(selected);
  }, [locale, selected, startMutation]);

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardContent className="flex flex-col gap-4 py-4">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs text-muted-foreground">
                {titleForLocale(locale, "渠道", "Site")}
              </Label>
              <Select
                value={siteId || "__all__"}
                onValueChange={(value) => {
                  setSiteId(value === "__all__" ? "" : value);
                  setTargetKey("");
                }}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">
                    {titleForLocale(locale, "全部渠道", "All sites")}
                  </SelectItem>
                  {sites.map((site) => (
                    <SelectItem key={site.id} value={site.id}>
                      {site.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex flex-col gap-1.5 md:col-span-2">
              <Label className="text-xs text-muted-foreground">
                {titleForLocale(locale, "模型", "Model")}
              </Label>
              <Select value={targetKey} onValueChange={setTargetKey}>
                <SelectTrigger>
                  <SelectValue
                    placeholder={
                      isLoading
                        ? titleForLocale(locale, "加载中", "Loading")
                        : titleForLocale(
                            locale,
                            "选择模型组中已启用的模型",
                            "Pick an enabled group member",
                          )
                    }
                  />
                </SelectTrigger>
                <SelectContent>
                  {visibleTargets.map((target) => (
                    <SelectItem key={target.key} value={target.key}>
                      {target.groupName && target.groupName !== target.modelName
                        ? `${target.groupName} / ${target.modelName}`
                        : target.modelName}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex items-end">
              <Button
                className="w-full"
                onClick={handleRun}
                disabled={!selected || isRunning}
              >
                {isRunning ? (
                  <Loader2 className="mr-1.5 size-4 animate-spin" />
                ) : (
                  <Play className="mr-1.5 size-4" />
                )}
                {isRunning
                  ? titleForLocale(locale, "验真中", "Verifying")
                  : titleForLocale(locale, "开始验真", "Verify")}
              </Button>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs text-muted-foreground">
                {titleForLocale(locale, "挑战数", "Challenges")}
              </Label>
              <Select value={count} onValueChange={setCount}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="1">1</SelectItem>
                  <SelectItem value="2">2</SelectItem>
                  <SelectItem value="3">3</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label className="text-xs text-muted-foreground">
                {titleForLocale(locale, "思考档位", "Reasoning effort")}
              </Label>
              <Select
                value={activeEffort}
                onValueChange={setEffort}
                disabled={!selected || selected.efforts.length === 0}
              >
                <SelectTrigger>
                  <SelectValue
                    placeholder={titleForLocale(
                      locale,
                      "该模型不支持",
                      "Not supported",
                    )}
                  />
                </SelectTrigger>
                <SelectContent>
                  {selected && selected.efforts.length === 0 ? (
                    <SelectItem value="__none__">
                      {titleForLocale(locale, "不支持", "Not supported")}
                    </SelectItem>
                  ) : null}
                  {(selected?.efforts ?? []).map((option) => (
                    <SelectItem key={option.level} value={option.level}>
                      {option.level}
                      {option.wire && option.wire !== option.level
                        ? ` → ${option.wire}`
                        : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label className="text-xs text-muted-foreground">
                {titleForLocale(locale, "期望模型", "Expected model")}
              </Label>
              <Input
                value={expected}
                readOnly
                placeholder={titleForLocale(
                  locale,
                  "选择模型后自动填入",
                  "Filled from selection",
                )}
                className="bg-muted/40"
              />
            </div>

            <div className="flex items-end gap-2 pb-2">
              <Switch
                checked={strict}
                onCheckedChange={setStrict}
                id="strict"
              />
              <Label htmlFor="strict" className="text-xs">
                {titleForLocale(locale, "严格模式", "Strict")}
              </Label>
            </div>
          </div>

          {error ? (
            <p className="text-xs text-destructive">
              {(error as Error).message}
            </p>
          ) : null}
          {!isLoading && visibleTargets.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              {titleForLocale(
                locale,
                "没有可验真的模型：模型组里已启用的文本协议成员才会出现。",
                "No verifiable models: only enabled text-protocol group members show up.",
              )}
            </p>
          ) : null}
        </CardContent>
      </Card>

      <VerificationResultDialog
        result={result}
        onOpenChange={(open) => {
          if (!open) setPicked(null);
        }}
      />
      <VerificationScheduleDialog
        open={scheduleOpen}
        onOpenChange={setScheduleOpen}
      />

      <VerificationHistoryCard
        siteId={siteId}
        modelName={selected?.modelName ?? ""}
        thresholds={thresholds}
        onOpenSettings={() => setScheduleOpen(true)}
        onSelect={setPicked}
      />
    </div>
  );
}
