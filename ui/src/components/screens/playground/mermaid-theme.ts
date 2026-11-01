/**
 * Merman's host theme: semantic roles plus a categorical palette, all opaque
 * hex so the whole thing can be posted to a worker.
 */
export type MermaidTheme = {
  appearance: "light" | "dark";
  fontFamily: string;
  fontSize: string;
  roles: Record<string, string>;
  seriesPalette: string[];
};

// Merman role → design token.
const ROLE_TOKENS: Record<string, string> = {
  surface: "--muted",
  "surface-alt": "--secondary",
  text: "--foreground",
  "subtle-text": "--muted-foreground",
  border: "--border",
  line: "--muted-foreground",
  "cluster-background": "--background",
  "cluster-border": "--border",
  "note-background": "--muted",
  "note-border": "--border",
};

const SERIES_TOKENS = [
  "--chart-1",
  "--chart-2",
  "--chart-3",
  "--chart-4",
  "--chart-5",
];

let probe: CanvasRenderingContext2D | null = null;

/**
 * Paint `layers` bottom-up on one pixel and read it back, so oklch and
 * translucent colours resolve to the RGB the page actually shows.
 */
function paint(layers: string[]): Uint8ClampedArray {
  probe ??= document.createElement("canvas").getContext("2d", {
    willReadFrequently: true,
  });
  if (!probe) {
    return new Uint8ClampedArray([255, 255, 255, 255]);
  }
  probe.clearRect(0, 0, 1, 1);
  for (const layer of layers) {
    probe.fillStyle = layer;
    probe.fillRect(0, 0, 1, 1);
  }
  return probe.getImageData(0, 0, 1, 1).data;
}

function paintHex(layers: string[]): string {
  const [r, g, b] = paint(layers);
  return `#${[r, g, b]
    .map((channel) => channel.toString(16).padStart(2, "0"))
    .join("")}`;
}

/** The colour the page shows behind `element`, translucent layers included. */
function backgroundBehind(element: Element): string {
  const layers: string[] = [];
  for (let node: Element | null = element; node; node = node.parentElement) {
    const color = getComputedStyle(node).backgroundColor;
    const alpha = paint([color])[3] / 255;
    if (alpha === 0) {
      continue;
    }
    layers.push(color);
    if (alpha === 1) {
      break;
    }
  }
  const page =
    getComputedStyle(document.documentElement)
      .getPropertyValue("--background")
      .trim() || "#ffffff";
  return paintHex(["#ffffff", page, ...layers.reverse()]);
}

export function resolveMermaidTheme(element: Element): MermaidTheme {
  const style = getComputedStyle(element);
  // Diagrams use body typography, not the surrounding code block's monospace.
  const body = getComputedStyle(element.ownerDocument.body);
  const canvas = backgroundBehind(element);
  const token = (name: string) =>
    paintHex([canvas, style.getPropertyValue(name).trim() || "transparent"]);
  const roles = Object.fromEntries(
    Object.entries(ROLE_TOKENS).map(([role, name]) => [role, token(name)]),
  );
  const [r, g, b] = [1, 3, 5].map((index) =>
    Number.parseInt(canvas.slice(index, index + 2), 16),
  );
  return {
    appearance: 0.2126 * r + 0.7152 * g + 0.0722 * b < 128 ? "dark" : "light",
    fontFamily: body.fontFamily,
    fontSize: body.fontSize,
    roles: { ...roles, canvas, "edge-label-background": canvas },
    seriesPalette: SERIES_TOKENS.map(token),
  };
}
