"use client";

import { useEffect, useRef, useState } from "react";
import type { RefObject } from "react";

import { renderSvgInShadowHost } from "./svg-host";

type DiagramProps = {
  source: string;
  /** Returns the SVG markup to show; runs on a debounce while streaming. */
  render: (source: string) => Promise<string>;
  className?: string;
  /** Lets the caller read the tokens in effect where the diagram sits. */
  hostRef?: RefObject<HTMLDivElement | null>;
};

const DEBOUNCE_MS = 300;

/**
 * Shared shell for the diagram previews: debounced render, error surface, and
 * an SVG parked in a shadow root so vendor styles stay scoped.
 */
export function Diagram({ source, render, className, hostRef }: DiagramProps) {
  const localRef = useRef<HTMLDivElement | null>(null);
  const targetRef = hostRef ?? localRef;
  const [error, setError] = useState<string | null>(null);
  const [drawn, setDrawn] = useState("");

  useEffect(() => {
    const host = targetRef.current;
    if (!host) {
      return;
    }
    let cancelled = false;

    const timer = window.setTimeout(() => {
      void render(source)
        .then((svg) => {
          if (cancelled || !targetRef.current) {
            return;
          }
          renderSvgInShadowHost(svg, targetRef.current);
          setError(null);
          setDrawn(source);
        })
        .catch((cause: unknown) => {
          if (!cancelled) {
            setError(cause instanceof Error ? cause.message : String(cause));
            setDrawn(source);
          }
        });
    }, DEBOUNCE_MS);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [render, source, targetRef]);

  if (error) {
    return (
      <div className="my-[1em] rounded-lg border-[0.5px] border-destructive/40 bg-destructive/5 px-3 py-2 font-mono text-xs whitespace-pre-wrap text-destructive">
        {error}
      </div>
    );
  }

  return (
    <div className={className}>
      <div ref={targetRef} />
      {drawn === source ? null : (
        <span className="mt-1 block text-[11px] text-muted-foreground">
          渲染中…
        </span>
      )}
    </div>
  );
}
