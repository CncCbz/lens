import type { PlaygroundMessage, PlaygroundSession } from "./types";

const STORAGE_KEY = "lens_playground_sessions_v1";
const ACTIVE_KEY = "lens_playground_active_session_v1";

export type StoredSessions = {
  sessions: PlaygroundSession[];
  activeId: string;
};

export function createSession(): PlaygroundSession {
  const now = Date.now();
  return {
    id: crypto.randomUUID(),
    title: "",
    messages: [],
    createdAt: now,
    updatedAt: now,
  };
}

function isMessage(value: unknown): value is PlaygroundMessage {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const record = value as Record<string, unknown>;
  return (
    typeof record.id === "string" &&
    (record.role === "user" || record.role === "assistant") &&
    typeof record.content === "string"
  );
}

function isSession(value: unknown): value is PlaygroundSession {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const record = value as Record<string, unknown>;
  return (
    typeof record.id === "string" &&
    typeof record.title === "string" &&
    Array.isArray(record.messages) &&
    record.messages.every(isMessage)
  );
}

export function loadSessions(): StoredSessions {
  const empty = { sessions: [] as PlaygroundSession[], activeId: "" };
  if (typeof window === "undefined") {
    return empty;
  }
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) {
    return empty;
  }
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) {
      return empty;
    }
    const sessions = parsed.filter(isSession);
    if (sessions.length === 0) {
      return empty;
    }
    const storedActive = window.localStorage.getItem(ACTIVE_KEY) ?? "";
    const activeId = sessions.some((session) => session.id === storedActive)
      ? storedActive
      : sessions[0].id;
    return { sessions, activeId };
  } catch (error) {
    if (!(error instanceof SyntaxError)) {
      throw error;
    }
    window.localStorage.removeItem(STORAGE_KEY);
    return empty;
  }
}

export function saveSessions(sessions: PlaygroundSession[]): void {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
}

export function saveActiveId(activeId: string): void {
  if (typeof window === "undefined" || !activeId) {
    return;
  }
  window.localStorage.setItem(ACTIVE_KEY, activeId);
}
