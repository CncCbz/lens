"use client";

import { Bot, Copy, RefreshCcw, Trash2, User } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { Markdown } from "./markdown";
import { ReasoningBlock } from "./reasoning-block";
import type { PlaygroundMessage } from "./types";

type MessageListProps = {
  messages: PlaygroundMessage[];
  onRegenerate: (messageId: string) => void;
  onDelete: (messageId: string) => void;
};

type MessageCopy = {
  copy: string;
  regenerate: string;
  delete: string;
  firstToken: string;
  totalTime: string;
  stopped: string;
};

function formatDuration(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function formatTime(timestamp: number, locale: string): string {
  return new Date(timestamp).toLocaleString(locale, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const AVATAR_CLASS =
  "grid size-[30px] shrink-0 place-items-center rounded-full bg-muted text-muted-foreground";

// Actions stay visible on the newest answer, fade in with hover everywhere else.
const ACTIONS_CLASS = {
  shown: "opacity-100",
  hidden:
    "opacity-0 transition-opacity focus-within:opacity-100 group-hover/message:opacity-100",
} as const;

export function MessageList({
  messages,
  onRegenerate,
  onDelete,
}: MessageListProps) {
  const { locale } = useI18n();
  const copy: MessageCopy =
    locale === "zh-CN"
      ? {
          copy: "复制",
          regenerate: "重新生成",
          delete: "删除",
          firstToken: "首字",
          totalTime: "总耗时",
          stopped: "已停止",
        }
      : {
          copy: "Copy",
          regenerate: "Regenerate",
          delete: "Delete",
          firstToken: "TTFT",
          totalTime: "Total",
          stopped: "Stopped",
        };

  const lastAssistantId = messages.findLast(
    (message) => message.role === "assistant",
  )?.id;

  return (
    <div className="flex flex-col gap-1">
      {messages.map((message) => {
        const time = formatTime(message.createdAt, locale);

        if (message.role === "user") {
          return (
            <div
              key={message.id}
              className="group/message flex w-full justify-end pt-2.5"
            >
              <div className="flex min-w-0 max-w-[calc(100%-2.5rem)] items-start justify-end gap-2.5">
                <div className="flex min-w-0 flex-col items-end">
                  <div className="max-w-full rounded-[10px] bg-muted px-4 py-2.5 text-sm leading-[1.65] break-words whitespace-pre-wrap">
                    {message.content}
                  </div>
                  <div className="mt-1 flex min-h-6 items-center gap-2 text-[11px] text-muted-foreground">
                    <span>{time}</span>
                    <Button
                      type="button"
                      size="icon-xs"
                      variant="ghost"
                      aria-label={copy.delete}
                      className="opacity-0 transition-opacity group-hover/message:opacity-100 focus-visible:opacity-100"
                      onClick={() => onDelete(message.id)}
                    >
                      <Trash2 />
                    </Button>
                  </div>
                </div>
                <div className={cn(AVATAR_CLASS, "mt-0.5")}>
                  <User className="size-4" />
                </div>
              </div>
            </div>
          );
        }

        const showActions = message.id === lastAssistantId || message.pending;

        return (
          <div
            key={message.id}
            className="group/message flex w-full items-start gap-2.5 pt-2.5"
          >
            <div className={AVATAR_CLASS}>
              <Bot className="size-4" />
            </div>
            <div className="flex min-w-0 flex-1 flex-col">
              <div className="flex w-full min-w-0 items-center gap-1.5">
                <span className="truncate text-sm leading-5 font-semibold">
                  {message.modelName}
                </span>
                <span className="flex min-w-0 items-center gap-1 text-xs leading-5 text-muted-foreground">
                  <span className="truncate">{message.channelName}</span>
                </span>
              </div>

              <div className="mt-2 min-w-0 max-w-full">
                {message.reasoning ? (
                  <ReasoningBlock
                    text={message.reasoning}
                    streaming={Boolean(message.reasoningActive)}
                    durationMs={message.reasoningMs}
                  />
                ) : null}
                <Markdown text={message.content} />
                {message.pending ? (
                  <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-foreground align-middle" />
                ) : null}
                {message.error ? (
                  <div className="mt-1 text-xs text-destructive">
                    {message.error}
                  </div>
                ) : null}
              </div>

              <div
                className={cn(
                  "mt-1 flex min-h-6 flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted-foreground",
                  showActions ? ACTIONS_CLASS.shown : ACTIONS_CLASS.hidden,
                )}
              >
                {message.firstTokenMs !== undefined ? (
                  <span className="font-mono">
                    {copy.firstToken} {formatDuration(message.firstTokenMs)}
                  </span>
                ) : null}
                {message.totalMs !== undefined ? (
                  <span className="font-mono">
                    {copy.totalTime} {formatDuration(message.totalMs)}
                  </span>
                ) : null}
                {message.stopped ? <span>{copy.stopped}</span> : null}
                {message.requestId ? (
                  <span className="font-mono">
                    #{message.requestId.slice(0, 6)}
                  </span>
                ) : null}
                <span>{time}</span>
                <span className="flex items-center">
                  <Button
                    type="button"
                    size="icon-xs"
                    variant="ghost"
                    aria-label={copy.copy}
                    onClick={() => void copyMessage(message)}
                  >
                    <Copy />
                  </Button>
                  <Button
                    type="button"
                    size="icon-xs"
                    variant="ghost"
                    aria-label={copy.regenerate}
                    disabled={message.pending}
                    onClick={() => onRegenerate(message.id)}
                  >
                    <RefreshCcw />
                  </Button>
                  <Button
                    type="button"
                    size="icon-xs"
                    variant="ghost"
                    aria-label={copy.delete}
                    disabled={message.pending}
                    onClick={() => onDelete(message.id)}
                  >
                    <Trash2 />
                  </Button>
                </span>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

async function copyMessage(message: PlaygroundMessage) {
  try {
    await navigator.clipboard.writeText(message.content);
    toast.success("Copied");
  } catch (error) {
    toast.error(String(error));
  }
}
