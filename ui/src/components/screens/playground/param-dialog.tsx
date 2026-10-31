"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/lib/i18n";

export type PlaygroundReasoningEffort =
  "" | "none" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max";

export type PlaygroundParams = {
  system: string;
  temperature: number;
  topP: number;
  maxTokens: number;
  stream: boolean;
  reasoningEffort: PlaygroundReasoningEffort;
};

type ParamDialogProps = {
  open: boolean;
  params: PlaygroundParams;
  onOpenChange: (open: boolean) => void;
  onSave: (params: PlaygroundParams) => void;
};

export function ParamDialog({
  open,
  params,
  onOpenChange,
  onSave,
}: ParamDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <ParamForm
          key={open ? "open" : "closed"}
          params={params}
          onSave={onSave}
        />
      </DialogContent>
    </Dialog>
  );
}

type ParamFormProps = {
  params: PlaygroundParams;
  onSave: (params: PlaygroundParams) => void;
};

const REASONING_EFFORTS = [
  "none",
  "minimal",
  "low",
  "medium",
  "high",
  "xhigh",
  "max",
] as const;

function ParamForm({ params, onSave }: ParamFormProps) {
  const { locale } = useI18n();
  const copy =
    locale === "zh-CN"
      ? {
          title: "参数",
          systemPrompt: "系统提示",
          streaming: "流式输出",
          reasoning: "思考等级",
          defaultLevel: "默认",
          reset: "重置",
          save: "保存",
        }
      : {
          title: "Parameters",
          systemPrompt: "System prompt",
          streaming: "Streaming",
          reasoning: "Reasoning",
          defaultLevel: "Default",
          reset: "Reset",
          save: "Save",
        };
  const [draft, setDraft] = useState<PlaygroundParams>(params);

  return (
    <>
      <DialogHeader>
        <DialogTitle>{copy.title}</DialogTitle>
      </DialogHeader>

      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="playground-system">{copy.systemPrompt}</Label>
          <Textarea
            id="playground-system"
            value={draft.system}
            onChange={(event) =>
              setDraft((prev) => ({ ...prev, system: event.target.value }))
            }
            className="min-h-20"
          />
        </div>

        <div className="grid grid-cols-[7rem_minmax(0,1fr)_2.5rem] items-center gap-2">
          <Label htmlFor="playground-temperature" className="font-mono text-xs">
            temperature
          </Label>
          <input
            id="playground-temperature"
            type="range"
            min={0}
            max={2}
            step={0.1}
            value={draft.temperature}
            onChange={(event) =>
              setDraft((prev) => ({
                ...prev,
                temperature: Number(event.target.value),
              }))
            }
            className="w-full accent-primary"
          />
          <span className="text-right font-mono text-xs">
            {draft.temperature.toFixed(1)}
          </span>
        </div>

        <div className="grid grid-cols-[7rem_minmax(0,1fr)_2.5rem] items-center gap-2">
          <Label htmlFor="playground-top-p" className="font-mono text-xs">
            top_p
          </Label>
          <input
            id="playground-top-p"
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={draft.topP}
            onChange={(event) =>
              setDraft((prev) => ({
                ...prev,
                topP: Number(event.target.value),
              }))
            }
            className="w-full accent-primary"
          />
          <span className="text-right font-mono text-xs">
            {draft.topP.toFixed(2)}
          </span>
        </div>

        <div className="grid grid-cols-[7rem_minmax(0,1fr)_2.5rem] items-center gap-2">
          <Label htmlFor="playground-reasoning" className="text-xs">
            {copy.reasoning}
          </Label>
          <Select
            value={draft.reasoningEffort || "default"}
            onValueChange={(value) =>
              setDraft((prev) => ({
                ...prev,
                reasoningEffort:
                  value === "default"
                    ? ""
                    : (value as PlaygroundReasoningEffort),
              }))
            }
          >
            <SelectTrigger id="playground-reasoning" className="h-7 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="default">{copy.defaultLevel}</SelectItem>
              {REASONING_EFFORTS.map((effort) => (
                <SelectItem key={effort} value={effort}>
                  {effort}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <span />
        </div>

        <div className="grid grid-cols-[7rem_minmax(0,1fr)_2.5rem] items-center gap-2">
          <Label htmlFor="playground-max-tokens" className="font-mono text-xs">
            max_tokens
          </Label>
          <Input
            id="playground-max-tokens"
            type="number"
            min={1}
            value={draft.maxTokens}
            onChange={(event) =>
              setDraft((prev) => ({
                ...prev,
                maxTokens: Number(event.target.value) || 1,
              }))
            }
            className="h-7 font-mono text-xs"
          />
          <span />
        </div>

        <div className="grid grid-cols-[7rem_minmax(0,1fr)_2.5rem] items-center gap-2">
          <Label htmlFor="playground-stream" className="text-xs">
            {copy.streaming}
          </Label>
          <Switch
            id="playground-stream"
            checked={draft.stream}
            onCheckedChange={(checked) =>
              setDraft((prev) => ({ ...prev, stream: checked }))
            }
          />
          <span />
        </div>
      </div>

      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          onClick={() => setDraft({ ...params })}
        >
          {copy.reset}
        </Button>
        <Button type="button" onClick={() => onSave(draft)}>
          {copy.save}
        </Button>
      </DialogFooter>
    </>
  );
}
