import type { MermaidTheme } from "./mermaid-theme";

export type MermaidRequest = {
  id: number;
  source: string;
  theme: MermaidTheme;
  diagramId: string;
};

export type MermaidResponse =
  { id: number; svg: string } | { id: number; error: string };
