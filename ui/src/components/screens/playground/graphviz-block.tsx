"use client";

import { useCallback } from "react";

import { Diagram } from "./diagram";

type GraphvizBlockProps = {
  code: string;
};

type VizInstance = Awaited<ReturnType<typeof import("@viz-js/viz").instance>>;

let vizPromise: Promise<VizInstance> | null = null;

function loadViz(): Promise<VizInstance> {
  vizPromise ??= import("@viz-js/viz").then((module) => module.instance());
  return vizPromise;
}

// Viz ignores node/edge defaults unless they are inside the graph body, so they
// are injected right after the opening brace.
function withDefaults(source: string, dark: boolean): string {
  const text = dark ? "#d4d4d4" : "#24292e";
  const border = dark ? "#3f3f46" : "#d4d4d8";
  const font = 'fontname="Helvetica,Arial,sans-serif"';
  const defaults = [
    `graph [bgcolor="transparent" color="${border}" fontcolor="${text}" ${font}];`,
    `node [color="${border}" fontcolor="${text}" ${font}];`,
    `edge [color="${border}" fontcolor="${text}" ${font}];`,
  ].join(" ");
  return source.replace("{", `{ ${defaults}`);
}

export function GraphvizBlock({ code }: GraphvizBlockProps) {
  const render = useCallback(async (source: string) => {
    const viz = await loadViz();
    return viz.renderString(withDefaults(source, isDark()), { format: "svg" });
  }, []);

  return <Diagram source={code} render={render} className="my-[1em]" />;
}

function isDark(): boolean {
  return document.documentElement.classList.contains("dark");
}
