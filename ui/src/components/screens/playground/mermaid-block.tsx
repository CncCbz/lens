"use client";

import { useCallback, useId, useRef } from "react";

import { Diagram } from "./diagram";
import { mermaidRenderService } from "./mermaid-render-service";
import { resolveMermaidTheme } from "./mermaid-theme";

type MermaidBlockProps = {
  code: string;
};

export function MermaidBlock({ code }: MermaidBlockProps) {
  const diagramId = useId();
  const hostRef = useRef<HTMLDivElement | null>(null);

  const render = useCallback(
    (source: string) => {
      // Colours come from the tokens in effect where the diagram sits.
      const theme = resolveMermaidTheme(hostRef.current ?? document.body);
      return mermaidRenderService.render(source, { theme, diagramId });
    },
    [diagramId],
  );

  return (
    <Diagram
      source={code}
      render={render}
      className="my-[1em]"
      hostRef={hostRef}
    />
  );
}
