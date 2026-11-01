"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Save } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { AppDialogContent, Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { apiRequest, type SettingItem } from "@/lib/api";
import { titleForLocale, useI18n } from "@/lib/i18n";

const BATCH_SIZE = "verification_batch_size";
const CHALLENGE_COUNT = "verification_challenge_count";
const REASONING_EFFORT = "verification_reasoning_effort";
const TRUST_THRESHOLD = "verification_trust_threshold";
const WARN_THRESHOLD = "verification_warn_threshold";

const EFFORTS = ["none", "minimal", "low", "medium", "high", "xhigh", "max"];

function valueOf(items: SettingItem[], key: string, fallback: string): string {
  return items.find((item) => item.key === key)?.value ?? fallback;
}

/**
 * The scheduled sweep covers every enabled model once per cycle; these three
 * settings decide how much of it each run takes.
 */
export function VerificationScheduleDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { locale } = useI18n();
  const queryClient = useQueryClient();
  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: () => apiRequest<SettingItem[]>("/admin/settings"),
    staleTime: 5 * 60_000,
  });

  const [batch, setBatch] = useState("5");
  const [count, setCount] = useState("3");
  const [effort, setEffort] = useState("none");
  const [trust, setTrust] = useState("80");
  const [warn, setWarn] = useState("50");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const items = settingsQuery.data;
    if (!items) return;
    setBatch(valueOf(items, BATCH_SIZE, "5"));
    setCount(valueOf(items, CHALLENGE_COUNT, "3"));
    setEffort(valueOf(items, REASONING_EFFORT, "none"));
    setTrust(valueOf(items, TRUST_THRESHOLD, "80"));
    setWarn(valueOf(items, WARN_THRESHOLD, "50"));
  }, [settingsQuery.data]);

  async function save() {
    setSaving(true);
    try {
      await apiRequest<SettingItem[]>("/admin/settings", {
        method: "PUT",
        body: JSON.stringify({
          items: [
            { key: BATCH_SIZE, value: batch },
            { key: CHALLENGE_COUNT, value: count },
            { key: REASONING_EFFORT, value: effort },
            { key: TRUST_THRESHOLD, value: trust },
            { key: WARN_THRESHOLD, value: warn },
          ],
        }),
      });
      await queryClient.invalidateQueries({ queryKey: ["settings"] });
      toast.success(titleForLocale(locale, "已保存", "Saved"));
      onOpenChange(false);
    } catch (cause) {
      toast.error((cause as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open ? (
        <AppDialogContent
          className="max-w-lg"
          title={titleForLocale(
            locale,
            "定时任务参数",
            "Scheduled run settings",
          )}
          description={titleForLocale(
            locale,
            "「定时任务」页的「渠道模型验真」按最久未验真优先，每轮处理下列数量。",
            "The verification cron job takes the least recently checked models first.",
          )}
          footer={
            <>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={saving}
              >
                {titleForLocale(locale, "取消", "Cancel")}
              </Button>
              <Button type="button" onClick={save} disabled={saving}>
                <Save className="mr-1.5 size-3.5" />
                {titleForLocale(locale, "保存", "Save")}
              </Button>
            </>
          }
        >
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs text-muted-foreground">
                {titleForLocale(locale, "每轮模型数", "Models per run")}
              </Label>
              <Input
                value={batch}
                onChange={(event) => setBatch(event.target.value)}
                inputMode="numeric"
              />
            </div>
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
                value={effort || "__default__"}
                onValueChange={(value) =>
                  setEffort(value === "__default__" ? "" : value)
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__default__">
                    {titleForLocale(locale, "默认", "Default")}
                  </SelectItem>
                  {EFFORTS.map((value) => (
                    <SelectItem key={value} value={value}>
                      {value}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs text-muted-foreground">
                {titleForLocale(locale, "可信阈值", "Trusted at")}
              </Label>
              <Input
                value={trust}
                onChange={(event) => setTrust(event.target.value)}
                inputMode="numeric"
                placeholder="80"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs text-muted-foreground">
                {titleForLocale(locale, "警告阈值", "Warn below")}
              </Label>
              <Input
                value={warn}
                onChange={(event) => setWarn(event.target.value)}
                inputMode="numeric"
                placeholder="50"
              />
            </div>
          </div>
        </AppDialogContent>
      ) : null}
    </Dialog>
  );
}
