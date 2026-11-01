"use client";

import DOMPurify from "dompurify";

// Vendor SVGs carry their own colours; a Shadow DOM keeps those rules from
// leaking into the message and vice versa.
const HOST_STYLES = `
  :host {
    display: block;
    width: 100%;
    padding: 1em;
    overflow: auto;
    background-color: var(--background);
    border: 0.5px solid var(--border);
    border-radius: 8px;
    white-space: normal;
  }
  svg { display: block; max-width: 100%; height: auto; }
`;

const SANITIZE_OPTIONS = {
  ADD_TAGS: ["animate", "foreignObject", "use"],
  ADD_ATTR: ["from", "to"],
  HTML_INTEGRATION_POINTS: { foreignobject: true },
};

/**
 * Render an SVG string into a shadow root on `host`. Throws when the markup is
 * not a usable SVG, so callers can surface the error instead of a blank box.
 */
export function renderSvgInShadowHost(svg: string, host: HTMLElement): void {
  const sanitized = DOMPurify.sanitize(svg, SANITIZE_OPTIONS);
  const root = host.shadowRoot ?? host.attachShadow({ mode: "open" });
  root.innerHTML = "";

  const style = document.createElement("style");
  style.textContent = HOST_STYLES;
  root.appendChild(style);

  if (!sanitized.trim()) {
    return;
  }

  const doc = new DOMParser().parseFromString(sanitized, "image/svg+xml");
  let element: Element = doc.documentElement;

  // Mermaid and friends emit HTML-flavoured SVG; the XML parser rejects it, so
  // fall back to the lenient HTML parser before giving up.
  if (
    doc.querySelector("parsererror") ||
    element.namespaceURI !== "http://www.w3.org/2000/svg"
  ) {
    const container = document.createElement("div");
    container.innerHTML = sanitized;
    const fromHtml = container.querySelector("svg");
    if (!fromHtml) {
      throw new Error("Invalid SVG content");
    }
    fromHtml.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    element = fromHtml;
  }

  if (!(element instanceof SVGSVGElement)) {
    throw new Error("Invalid SVG content");
  }

  // Vendor SVGs pin a pixel size that ignores the column. Scaling them up would
  // blow a small diagram across the whole message, so cap at the intrinsic size
  // and let only oversized ones shrink.
  const width = Number(element.getAttribute("width"));
  const viewBox = element
    .getAttribute("viewBox")
    ?.trim()
    .split(/[\s,]+/);
  const intrinsicWidth = width || Number(viewBox?.[2]) || 0;
  element.removeAttribute("height");
  element.style.removeProperty("height");
  element.style.width = intrinsicWidth > 0 ? `${intrinsicWidth}px` : "100%";
  element.style.maxWidth = "100%";
  element.style.height = "auto";
  root.appendChild(element);
}
