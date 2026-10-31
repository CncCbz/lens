export type PlaygroundMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: number;
  reasoning?: string;
  reasoningMs?: number;
  reasoningActive?: boolean;
  modelName?: string;
  channelName?: string;
  groupName?: string;
  firstTokenMs?: number;
  totalMs?: number;
  requestId?: string;
  error?: string;
  pending?: boolean;
  stopped?: boolean;
};

export type PlaygroundSession = {
  id: string;
  title: string;
  messages: PlaygroundMessage[];
  createdAt: number;
  updatedAt: number;
};
