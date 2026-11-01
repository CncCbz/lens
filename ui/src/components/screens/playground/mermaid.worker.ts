/// <reference lib="webworker" />

import type * as Merman from "@mermanjs/web-render";
import type {
  HostTextMeasureRequest,
  HostTextMeasureResult,
  HostTextMeasurer,
} from "@mermanjs/web-render";

import type { MermaidRequest, MermaidResponse } from "./mermaid-protocol";

type Runtime = { merman: typeof Merman; measure: HostTextMeasurer };

let runtime: Promise<Runtime> | null = null;

/**
 * Merman renders to SVG through WASM with no DOM, which is what lets the whole
 * layout run in this worker. A failed load is retried on the next call.
 */
function loadRuntime(): Promise<Runtime> {
  runtime ??= import("@mermanjs/web-render")
    .then(async (merman) => {
      await merman.initMerman();
      return { merman, measure: createCanvasTextMeasurer() };
    })
    .catch((error: unknown) => {
      runtime = null;
      throw error;
    });
  return runtime;
}

// Ideographs, kana, hangul and full-width forms break anywhere; other text
// breaks at spaces.
const BREAK_TOKENS =
  /[\u2E80-\u9FFF\uAC00-\uD7AF\uF900-\uFAFF\uFF00-\uFFEF]|\s+|[^\s\u2E80-\u9FFF\uAC00-\uD7AF\uF900-\uFAFF\uFF00-\uFFEF]+/gu;

/** Measures labels with OffscreenCanvas, which exists in a worker and on the page alike. */
function createCanvasTextMeasurer(): HostTextMeasurer {
  const context = new OffscreenCanvas(1, 1).getContext("2d");
  if (!context) {
    return () => undefined;
  }

  const use = (request: HostTextMeasureRequest) => {
    const size = Number.isFinite(request.font_size) ? request.font_size : 16;
    context.font = `${request.font_style || "normal"} ${request.font_weight ?? "normal"} ${size}px ${request.font_family || "sans-serif"}`;
    context.letterSpacing = `${request.letter_spacing || 0}px`;
    context.wordSpacing = `${request.word_spacing || 0}px`;
  };
  const width = (text: string) => Math.max(0, context.measureText(text).width);
  const fontBox = () => {
    const metrics = context.measureText("Mg");
    return {
      ascent: metrics.fontBoundingBoxAscent,
      height: metrics.fontBoundingBoxAscent + metrics.fontBoundingBoxDescent,
    };
  };
  const wrapLines = (text: string, maxWidth: number | null): string[] =>
    text.split("\n").flatMap((paragraph) => {
      if (maxWidth === null) {
        return [paragraph];
      }
      const lines: string[] = [];
      let line = "";
      for (const token of paragraph.match(BREAK_TOKENS) ?? []) {
        const candidate = line + token;
        if (
          line &&
          !/^\s+$/.test(token) &&
          width(candidate.trimEnd()) > maxWidth
        ) {
          lines.push(line.trimEnd());
          line = token.trimStart();
        } else {
          line = candidate;
        }
      }
      lines.push(line.trimEnd());
      return lines;
    });

  const wrapped = (request: HostTextMeasureRequest) => {
    const maxWidth =
      request.has_max_width && request.max_width ? request.max_width : null;
    const natural = Math.max(...request.text.split("\n").map(width));
    const lines =
      request.white_space === "nowrap"
        ? request.text.split("\n")
        : wrapLines(request.text, maxWidth);
    const wraps = maxWidth !== null && natural > maxWidth;
    return {
      width: wraps ? maxWidth : natural,
      height: lines.length * request.line_height,
      lineCount: lines.length,
      rawWidth: natural,
    };
  };

  return (request): HostTextMeasureResult | undefined => {
    use(request);
    if (!request.text) {
      if (
        request.operation === "bbox-x" ||
        request.operation === "title-bbox-x"
      ) {
        return { kind: "horizontal-extents", bbox_left: 0, bbox_right: 0 };
      }
      if (request.operation === "measure" || request.operation === "wrapped") {
        return { kind: "metrics", width: 0, height: 0, line_count: 1 };
      }
      return { kind: "length", length: 0 };
    }
    switch (request.operation) {
      case "measure":
      case "mermaid-calculate-text-dimensions":
        return {
          kind: "metrics",
          width: width(request.text),
          height: fontBox().height,
          line_count: 1,
        };
      case "wrapped": {
        if (request.wrap_mode !== "html-like") {
          return undefined;
        }
        const { width: w, height, lineCount } = wrapped(request);
        return { kind: "metrics", width: w, height, line_count: lineCount };
      }
      case "wrapped-with-raw-width": {
        if (request.wrap_mode !== "html-like") {
          return undefined;
        }
        const { width: w, height, lineCount, rawWidth } = wrapped(request);
        return {
          kind: "wrapped-with-raw-width",
          width: w,
          height,
          line_count: lineCount,
          raw_width: rawWidth,
        };
      }
      case "computed-length":
      case "canvas-measure-text-width":
      case "raw-bbox-width":
      case "tspan-bbox-width":
      case "simple-bbox-width":
      case "wrap-probe-bbox-width":
      case "bounding-client-rect-width":
        return { kind: "length", length: width(request.text) };
      case "raw-bbox-height":
      case "tspan-bbox-height":
      case "simple-bbox-height":
        return { kind: "length", length: fontBox().height };
      case "raw-bbox-y-offset":
        return { kind: "length", length: -fontBox().ascent };
      default:
        // Baseline-relative offsets and bbox extents fall back to Merman's own.
        return undefined;
    }
  };
}

// Drop shadows come from a theme variable for most families and from SVG filter
// references for sequence actors; the dashboard keeps static content flat.
const SCOPED_CSS = [
  '[filter*="drop-shadow"] { filter: none; }',
  'text[alignment-baseline="central"] { dominant-baseline: central; }',
  'text[alignment-baseline="middle"] { dominant-baseline: middle; }',
].join("\n");

const LAYOUT_WORK_LIMIT = 400_000_000;

self.onmessage = async ({ data }: MessageEvent<MermaidRequest>) => {
  let response: MermaidResponse;
  try {
    const { merman, measure } = await loadRuntime();
    const svg = merman.renderSvgWithTextMeasurer(data.source, measure, {
      presentation: {
        theme: {
          appearance: data.theme.appearance,
          font_family: data.theme.fontFamily,
          font_size: data.theme.fontSize,
          roles: data.theme.roles,
          series_palette: data.theme.seriesPalette,
        },
      },
      site_config: { themeVariables: { dropShadow: "none" } },
      svg: {
        diagram_id: data.diagramId,
        root_background_color: "transparent",
        scoped_css: SCOPED_CSS,
      },
      resources: {
        profile: "interactive",
        limits: { max_layout_work_units: LAYOUT_WORK_LIMIT },
      },
    });
    response = { id: data.id, svg };
  } catch (cause) {
    response = {
      id: data.id,
      error: cause instanceof Error ? cause.message : String(cause),
    };
  }
  self.postMessage(response);
};
