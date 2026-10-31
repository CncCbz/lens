"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronRight,
  Lock,
  Plus,
  RefreshCcw,
  Send,
  SlidersHorizontal,
  Square,
  Trash2,
} from "lucide-react";

import { DashboardHeaderActions } from "@/components/shell/dashboard-header-actions";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverAnchor,
  PopoverContent,
} from "@/components/ui/popover";
import { Textarea } from "@/components/ui/textarea";
import {
  ApiError,
  apiRequest,
  playgroundChat,
  readSseJsonStream,
  type ModelGroup,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import {
  buildPlaygroundCatalog,
  firstUsableModel,
  type PlaygroundModel,
} from "./playground/catalog";
import { MessageList } from "./playground/message-list";
import { ParamDialog, type PlaygroundParams } from "./playground/param-dialog";
import {
  createSession,
  loadSessions,
  saveActiveId,
  saveSessions,
} from "./playground/sessions";
import { TargetPicker } from "./playground/target-picker";
import type { PlaygroundMessage, PlaygroundSession } from "./playground/types";

const DEFAULT_PARAMS: PlaygroundParams = {
  system: "",
  temperature: 0.7,
  topP: 1,
  maxTokens: 2048,
  stream: true,
  reasoningEffort: "",
};

type ChatCompletionDelta = {
  choices?: {
    delta?: {
      content?: string | null;
      reasoning_content?: string | null;
      reasoning?: string | null;
    };
  }[];
};

type ChatCompletionResponse = {
  choices?: {
    message?: {
      content?: string | null;
      reasoning_content?: string | null;
      reasoning?: string | null;
    };
  }[];
};

type SessionState = {
  sessions: PlaygroundSession[];
  activeId: string;
};

const ZH = {
  empty: "暂无消息",
  clear: "清空对话",
  loading: "加载中",
  loadFailed: "模型组加载失败",
  noTarget: "没有可用于 OpenAI Chat 的模型组",
  channel: "渠道",
  group: "组合",
  model: "模型",
  locked: "锁定",
  params: "参数",
  send: "发送",
  stop: "停止",
  hint: "Enter 发送",
  placeholder: "输入消息",
  streamOn: "流式",
  streamOff: "非流式",
  reasoning: "思考",
  newSession: "新建对话",
  untitled: "新对话",
  deleteSession: "删除对话",
};

const EN = {
  empty: "No messages",
  clear: "Clear conversation",
  loading: "Loading",
  loadFailed: "Failed to load model groups",
  noTarget: "No model group supports OpenAI Chat",
  channel: "Channel",
  group: "Group",
  model: "Model",
  locked: "Locked",
  params: "Params",
  send: "Send",
  stop: "Stop",
  hint: "Enter to send",
  placeholder: "Message",
  streamOn: "stream",
  streamOff: "no stream",
  reasoning: "reasoning",
  newSession: "New chat",
  untitled: "New chat",
  deleteSession: "Delete chat",
};

function requestMessages(messages: PlaygroundMessage[], system: string) {
  const items: { role: string; content: string }[] = [];
  const trimmed = system.trim();
  if (trimmed) {
    items.push({ role: "system", content: trimmed });
  }
  for (const message of messages) {
    if (!message.content) {
      continue;
    }
    items.push({ role: message.role, content: message.content });
  }
  return items;
}

function initialSessionState(): SessionState {
  const stored = loadSessions();
  if (stored.sessions.length > 0) {
    return { sessions: stored.sessions, activeId: stored.activeId };
  }
  const session = createSession();
  return { sessions: [session], activeId: session.id };
}

export function PlaygroundScreen() {
  const { locale } = useI18n();
  const copy = locale === "zh-CN" ? ZH : EN;
  const queryClient = useQueryClient();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const runnersRef = useRef<Map<string, AbortController>>(new Map());

  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [paramOpen, setParamOpen] = useState(false);
  const [params, setParams] = useState<PlaygroundParams>(DEFAULT_PARAMS);
  const [input, setInput] = useState("");
  const [sessionState, setSessionState] =
    useState<SessionState>(initialSessionState);
  const [streamingIds, setStreamingIds] = useState<string[]>([]);

  const activeSession =
    sessionState.sessions.find(
      (session) => session.id === sessionState.activeId,
    ) ?? sessionState.sessions[0];
  const messages = activeSession.messages;
  // Every session keeps its own runner, so switching chats never cancels one.
  const isStreaming = streamingIds.includes(activeSession.id);

  const {
    data: groups,
    error: groupsError,
    isError: groupsIsError,
    isLoading,
  } = useQuery({
    queryKey: ["model-groups"],
    queryFn: () => apiRequest<ModelGroup[]>("/admin/model-groups"),
    staleTime: 2 * 60_000,
    refetchOnMount: "always",
  });

  const catalog = useMemo(() => buildPlaygroundCatalog(groups ?? []), [groups]);

  const selection = useMemo(
    () => catalog.find((model) => model.key === selectedKey) ?? null,
    [catalog, selectedKey],
  );

  useEffect(() => {
    if (catalog.length === 0) {
      return;
    }
    if (selectedKey && catalog.some((model) => model.key === selectedKey)) {
      return;
    }
    const fallback = firstUsableModel(catalog);
    if (fallback) {
      setSelectedKey(fallback.key);
    }
  }, [catalog, selectedKey]);

  useEffect(() => {
    if (streamingIds.length > 0) {
      return;
    }
    saveSessions(sessionState.sessions);
  }, [sessionState.sessions, streamingIds]);

  useEffect(() => {
    saveActiveId(sessionState.activeId);
  }, [sessionState.activeId]);

  useEffect(() => {
    const node = scrollRef.current;
    if (node) {
      node.scrollTop = node.scrollHeight;
    }
  }, [messages]);

  function patchMessages(
    sessionId: string,
    updater: (previous: PlaygroundMessage[]) => PlaygroundMessage[],
  ) {
    setSessionState((previous) => ({
      ...previous,
      sessions: previous.sessions.map((session) =>
        session.id === sessionId
          ? {
              ...session,
              messages: updater(session.messages),
              updatedAt: Date.now(),
            }
          : session,
      ),
    }));
  }

  function appendAssistant(
    sessionId: string,
    history: PlaygroundMessage[],
    assistant: PlaygroundMessage,
  ) {
    setSessionState((previous) => ({
      ...previous,
      sessions: previous.sessions.map((session) => {
        if (session.id !== sessionId) {
          return session;
        }
        const next = [...history, assistant];
        const firstUser = next.find((message) => message.role === "user");
        return {
          ...session,
          messages: next,
          title:
            session.title ||
            (firstUser ? firstUser.content.slice(0, 24) : session.title),
          updatedAt: Date.now(),
        };
      }),
    }));
  }

  async function runCompletion(
    history: PlaygroundMessage[],
    target: PlaygroundModel,
    sessionId: string,
  ) {
    const controller = new AbortController();
    runnersRef.current.set(sessionId, controller);
    const assistantId = crypto.randomUUID();
    const startedAt = performance.now();
    let firstTokenAt: number | null = null;
    let reasoningStartedAt: number | null = null;
    let reasoningEndedAt: number | null = null;

    appendAssistant(sessionId, history, {
      id: assistantId,
      role: "assistant",
      content: "",
      createdAt: Date.now(),
      pending: true,
      modelName: target.modelName,
      channelName: target.channelName,
      groupName: target.groupName,
    });
    setStreamingIds((previous) =>
      previous.includes(sessionId) ? previous : [...previous, sessionId],
    );

    try {
      const requestBody: Record<string, unknown> = {
        model: target.groupName,
        messages: requestMessages(history, params.system),
        stream: params.stream,
        temperature: params.temperature,
        top_p: params.topP,
        max_tokens: params.maxTokens,
      };
      if (params.reasoningEffort) {
        requestBody.reasoning_effort = params.reasoningEffort;
      }

      const response = await playgroundChat(
        {
          target: {
            channel_id: target.channelId,
            model_name: target.modelName,
            credential_id: target.credentialId,
          },
          request: requestBody,
        },
        controller.signal,
      );
      const requestId = response.headers.get("x-lens-request-id") ?? undefined;

      if (params.stream) {
        await readSseJsonStream<ChatCompletionDelta>(response, (chunk) => {
          const delta = chunk.choices?.[0]?.delta;
          const reasoning = delta?.reasoning_content ?? delta?.reasoning;
          if (reasoning) {
            if (reasoningStartedAt === null) {
              reasoningStartedAt = performance.now();
            }
            patchMessages(sessionId, (previous) =>
              previous.map((message) =>
                message.id === assistantId
                  ? {
                      ...message,
                      reasoning: (message.reasoning ?? "") + reasoning,
                      reasoningActive: true,
                    }
                  : message,
              ),
            );
            return;
          }

          const content = delta?.content;
          if (!content) {
            return;
          }
          if (firstTokenAt === null) {
            firstTokenAt = performance.now();
          }
          if (reasoningStartedAt !== null && reasoningEndedAt === null) {
            reasoningEndedAt = performance.now();
          }
          patchMessages(sessionId, (previous) =>
            previous.map((message) =>
              message.id === assistantId
                ? {
                    ...message,
                    content: message.content + content,
                    reasoningActive: false,
                  }
                : message,
            ),
          );
        });
      } else {
        const payload = (await response.json()) as ChatCompletionResponse;
        const responseMessage = payload.choices?.[0]?.message;
        const content = responseMessage?.content ?? "";
        const reasoning =
          responseMessage?.reasoning_content ??
          responseMessage?.reasoning ??
          "";
        patchMessages(sessionId, (previous) =>
          previous.map((message) =>
            message.id === assistantId
              ? { ...message, content, reasoning: reasoning || undefined }
              : message,
          ),
        );
      }

      const finishedAt = performance.now();
      patchMessages(sessionId, (previous) =>
        previous.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                pending: false,
                requestId,
                reasoningActive: false,
                reasoningMs:
                  reasoningStartedAt !== null && reasoningEndedAt !== null
                    ? reasoningEndedAt - reasoningStartedAt
                    : undefined,
                totalMs: finishedAt - startedAt,
                firstTokenMs:
                  firstTokenAt === null ? undefined : firstTokenAt - startedAt,
              }
            : message,
        ),
      );
    } catch (error) {
      const aborted = controller.signal.aborted;
      const finishedAt = performance.now();
      patchMessages(sessionId, (previous) =>
        previous.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                pending: false,
                stopped: aborted,
                reasoningActive: false,
                totalMs: finishedAt - startedAt,
                error: aborted
                  ? undefined
                  : error instanceof ApiError
                    ? error.message
                    : String(error),
              }
            : message,
        ),
      );
    } finally {
      if (runnersRef.current.get(sessionId) === controller) {
        runnersRef.current.delete(sessionId);
      }
      setStreamingIds((previous) => previous.filter((id) => id !== sessionId));
    }
  }

  function sendMessage() {
    const content = input.trim();
    if (!selection || !content || isStreaming) {
      return;
    }
    const userMessage: PlaygroundMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content,
      createdAt: Date.now(),
    };
    const history = [...messages, userMessage];
    setInput("");
    void runCompletion(history, selection, activeSession.id);
  }

  function stopStreaming() {
    runnersRef.current.get(activeSession.id)?.abort();
  }

  function regenerate(messageId: string) {
    if (isStreaming) {
      return;
    }
    const index = messages.findIndex((message) => message.id === messageId);
    if (index < 0) {
      return;
    }
    const previous = messages[index];
    const target =
      catalog.find(
        (model) =>
          model.modelName === previous.modelName &&
          model.channelName === previous.channelName &&
          model.groupName === previous.groupName,
      ) ?? selection;
    if (!target) {
      return;
    }
    void runCompletion(messages.slice(0, index), target, activeSession.id);
  }

  function deleteMessage(messageId: string) {
    patchMessages(activeSession.id, (previous) =>
      previous.filter((message) => message.id !== messageId),
    );
  }

  function clearMessages() {
    runnersRef.current.get(activeSession.id)?.abort();
    patchMessages(activeSession.id, () => []);
  }

  function createNewSession() {
    const session = createSession();
    setSessionState((previous) => ({
      sessions: [session, ...previous.sessions],
      activeId: session.id,
    }));
  }

  function selectSession(sessionId: string) {
    setSessionState((previous) => ({ ...previous, activeId: sessionId }));
  }

  function deleteSession(sessionId: string) {
    runnersRef.current.get(sessionId)?.abort();
    runnersRef.current.delete(sessionId);
    setStreamingIds((previous) => previous.filter((id) => id !== sessionId));
    setSessionState((previous) => {
      const remaining = previous.sessions.filter(
        (session) => session.id !== sessionId,
      );
      if (remaining.length === 0) {
        const session = createSession();
        return { sessions: [session], activeId: session.id };
      }
      return {
        sessions: remaining,
        activeId:
          previous.activeId === sessionId ? remaining[0].id : previous.activeId,
      };
    });
  }

  const canSend = Boolean(selection?.enabled) && input.trim().length > 0;

  return (
    <>
      <DashboardHeaderActions>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={copy.clear}
          disabled={messages.length === 0}
          onClick={clearMessages}
        >
          <Trash2 />
        </Button>
      </DashboardHeaderActions>

      <section className="flex h-[calc(100dvh-9.5rem)] min-h-[32rem] overflow-hidden rounded-xl border bg-background">
        <aside className="hidden w-56 shrink-0 flex-col border-r bg-muted/20 md:flex">
          <div className="p-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="w-full justify-start gap-1.5"
              onClick={createNewSession}
            >
              <Plus data-icon="inline-start" />
              {copy.newSession}
            </Button>
          </div>
          <div className="hide-scrollbar min-h-0 flex-1 overflow-y-auto px-2 pb-2">
            {sessionState.sessions.map((session) => (
              <div
                key={session.id}
                className={cn(
                  "group/session flex items-center gap-1 rounded-md px-2 py-1.5",
                  session.id === activeSession.id
                    ? "bg-muted"
                    : "hover:bg-muted/60",
                )}
              >
                <button
                  type="button"
                  onClick={() => selectSession(session.id)}
                  className="flex min-w-0 flex-1 items-center gap-1.5 truncate text-left text-xs"
                >
                  {streamingIds.includes(session.id) ? (
                    <span className="size-1.5 shrink-0 animate-pulse rounded-full bg-primary" />
                  ) : null}
                  <span className="truncate">
                    {session.title || copy.untitled}
                  </span>
                </button>
                <Button
                  type="button"
                  size="icon-xs"
                  variant="ghost"
                  aria-label={copy.deleteSession}
                  className="opacity-0 transition-opacity group-hover/session:opacity-100 focus-visible:opacity-100"
                  onClick={() => deleteSession(session.id)}
                >
                  <Trash2 />
                </Button>
              </div>
            ))}
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <div
            ref={scrollRef}
            className="hide-scrollbar min-h-0 flex-1 overflow-y-auto px-4 py-2 sm:px-6"
          >
            {groupsIsError ? (
              <div className="flex h-full items-center justify-center text-sm text-destructive">
                {copy.loadFailed}:{" "}
                {groupsError instanceof Error
                  ? groupsError.message
                  : String(groupsError)}
              </div>
            ) : isLoading ? (
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                {copy.loading}
              </div>
            ) : messages.length === 0 ? (
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                {catalog.length === 0 ? copy.noTarget : copy.empty}
              </div>
            ) : (
              <MessageList
                messages={messages}
                onRegenerate={regenerate}
                onDelete={deleteMessage}
              />
            )}
          </div>

          <div className="shrink-0 border-t px-3 py-3 sm:px-6 sm:py-4">
            <div className="rounded-xl border bg-card p-2 shadow-xs">
              <Popover open={pickerOpen} onOpenChange={setPickerOpen}>
                <PopoverAnchor asChild>
                  <div className="flex min-w-0 items-center gap-1 border-b px-1 pb-2">
                    <TargetChip
                      label={copy.channel}
                      value={selection?.channelName ?? "-"}
                      onClick={() => setPickerOpen(true)}
                    />
                    <ChevronRight className="size-3 shrink-0 text-muted-foreground" />
                    <TargetChip
                      label={copy.group}
                      value={selection?.groupName ?? "-"}
                      onClick={() => setPickerOpen(true)}
                    />
                    <ChevronRight className="size-3 shrink-0 text-muted-foreground" />
                    <TargetChip
                      label={copy.model}
                      value={selection?.modelName ?? "-"}
                      mono
                      onClick={() => setPickerOpen(true)}
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      aria-label="Refresh"
                      onClick={() =>
                        void queryClient.invalidateQueries({
                          queryKey: ["model-groups"],
                        })
                      }
                    >
                      <RefreshCcw />
                    </Button>
                    <span className="flex-1" />
                    <span className="hidden items-center gap-1 text-[11px] text-muted-foreground sm:flex">
                      <Lock className="size-3" />
                      {copy.locked}
                    </span>
                  </div>
                </PopoverAnchor>
                <PopoverContent
                  align="start"
                  side="top"
                  sideOffset={8}
                  className="w-[min(92vw,44rem)] p-0"
                >
                  <TargetPicker
                    models={catalog}
                    selectedKey={selectedKey}
                    onCancel={() => setPickerOpen(false)}
                    onConfirm={(model) => {
                      setSelectedKey(model.key);
                      setPickerOpen(false);
                    }}
                  />
                </PopoverContent>
              </Popover>

              <Textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (
                    event.key === "Enter" &&
                    !event.shiftKey &&
                    !event.nativeEvent.isComposing
                  ) {
                    event.preventDefault();
                    sendMessage();
                  }
                }}
                placeholder={copy.placeholder}
                className="min-h-14 resize-none border-0 bg-transparent px-2 py-2 shadow-none focus-visible:ring-0"
              />

              <div className="flex min-w-0 items-center gap-2 border-t px-1 pt-2">
                <Button
                  type="button"
                  variant="outline"
                  size="xs"
                  onClick={() => setParamOpen(true)}
                >
                  <SlidersHorizontal data-icon="inline-start" />
                  {copy.params}
                </Button>
                <span className="hidden truncate font-mono text-[11px] text-muted-foreground sm:inline">
                  temp {params.temperature.toFixed(1)} · top_p{" "}
                  {params.topP.toFixed(2)} · {params.maxTokens} ·{" "}
                  {params.stream ? copy.streamOn : copy.streamOff}
                  {params.reasoningEffort
                    ? ` · ${copy.reasoning} ${params.reasoningEffort}`
                    : ""}
                </span>
                <span className="flex-1" />
                <span className="hidden text-[11px] text-muted-foreground sm:inline">
                  {copy.hint}
                </span>
                {isStreaming ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={stopStreaming}
                  >
                    <Square data-icon="inline-start" />
                    {copy.stop}
                  </Button>
                ) : (
                  <Button
                    type="button"
                    size="sm"
                    disabled={!canSend}
                    onClick={sendMessage}
                  >
                    <Send data-icon="inline-start" />
                    {copy.send}
                  </Button>
                )}
              </div>
            </div>
          </div>
        </div>
      </section>

      <ParamDialog
        open={paramOpen}
        params={params}
        onOpenChange={setParamOpen}
        onSave={(next) => {
          setParams(next);
          setParamOpen(false);
        }}
      />
    </>
  );
}

type TargetChipProps = {
  label: string;
  value: string;
  mono?: boolean;
  onClick: () => void;
};

function TargetChip({ label, value, mono, onClick }: TargetChipProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex h-7 min-w-0 items-center gap-1.5 rounded-full border bg-muted/40 px-2.5 text-xs transition-colors hover:bg-muted"
    >
      <span className="shrink-0 text-[10px] text-muted-foreground">
        {label}
      </span>
      <span className={cn("truncate", mono && "font-mono")}>{value}</span>
      <ChevronDown className="size-3 shrink-0 text-muted-foreground" />
    </button>
  );
}
