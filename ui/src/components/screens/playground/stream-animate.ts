/**
 * Streaming animation modelled on streamdown's approach: the source is split
 * into top-level blocks, and each block renders on its own so only the block
 * being appended re-renders. Within a block the words that arrived since the
 * previous paint are wrapped in fade-in spans; everything before that already
 * appeared and is left alone.
 */

/** Soft budget for how far ahead of wall-clock a cascade may run. */
const MAX_BACKLOG_MS = 320;
/** Floor for per-word stagger under compression. */
const MIN_STAGGER_STEP_MS = 4;
const STAGGER_MS = 12;
const DURATION_MS = 250;

/**
 * Shared wall-clock cursor that serializes stagger delays across blocks and
 * across streaming ticks, so a fast stream compresses instead of queueing an
 * unbounded opacity:0 backlog.
 */
type Timeline = {
  beginPass: (now: number) => void;
  commitPass: () => void;
  take: (units: number) => { baseDelay: number; step: number };
};

export function createTimeline(
  now: () => number = () => performance.now(),
): Timeline {
  let committed = 0;
  let current = 0;
  return {
    beginPass(at) {
      current = Math.min(Math.max(committed, at), at + MAX_BACKLOG_MS);
    },
    commitPass() {
      committed = current;
    },
    take(units) {
      if (units <= 0) {
        return { baseDelay: 0, step: STAGGER_MS };
      }
      const at = now();
      const start = Math.max(current, at);
      const budgetEnd = at + MAX_BACKLOG_MS;
      const idealLast = start + (units - 1) * STAGGER_MS;
      let step = STAGGER_MS;
      if (idealLast > budgetEnd && units > 1) {
        step =
          start < budgetEnd
            ? Math.max(MIN_STAGGER_STEP_MS, (budgetEnd - start) / (units - 1))
            : MIN_STAGGER_STEP_MS;
      }
      const baseDelay = Math.max(0, Math.round(start - at));
      current = start + units * step;
      return { baseDelay, step };
    },
  };
}

// CJK has no inter-word spaces, so each character is its own unit; otherwise a
// whole Chinese paragraph would fade in as one blob.
const CJK =
  /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\u3000-\u303F\uFF00-\uFFEF]/u;
const WHITESPACE_ONLY = /^\s+$/;

// Layout-sensitive subtrees stay static; code, diagrams and math would reflow
// if their text were wrapped in spans.
const SKIP_TAGS = new Set(["pre", "svg", "math", "annotation"]);

/** Split into animateable units, gluing trailing whitespace to the word. */
export function splitUnits(text: string): string[] {
  const parts: string[] = [];
  let current = "";
  let inWhitespace = false;
  let prevCjk = false;

  for (const char of text) {
    const isSpace = /\s/.test(char);
    const isCjk = !isSpace && CJK.test(char);
    const startsUnit = !isSpace && current !== "" && (isCjk || prevCjk);
    if (startsUnit) {
      parts.push(current);
      current = "";
      inWhitespace = false;
    } else if (isSpace !== inWhitespace && current) {
      if (isSpace) {
        current += char;
        inWhitespace = true;
        continue;
      }
      parts.push(current);
      current = "";
    }
    current += char;
    inWhitespace = isSpace;
    prevCjk = isCjk;
  }
  if (current) {
    parts.push(current);
  }
  return parts;
}

const ANIMATED = "animate-in fade-in fill-mode-backwards";

/**
 * Wrap the text past `from` (a character offset into the container's text) in
 * fade-in spans, with delays taken from the shared timeline.
 */
export function animateTail(
  container: HTMLElement,
  from: number,
  timeline: Timeline,
): void {
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const targets: Array<{ node: Text; start: number }> = [];
  let offset = 0;
  let node = walker.nextNode() as Text | null;
  while (node) {
    if (offset + node.data.length > from) {
      targets.push({ node, start: offset });
    }
    offset += node.data.length;
    node = walker.nextNode() as Text | null;
  }

  // Count first so the timeline reserves exactly one slot per new unit.
  let units = 0;
  const plans = targets.map(({ node: textNode, start }) => {
    const cut = Math.max(0, from - start);
    const parts = splitUnits(textNode.data.slice(cut));
    units += parts.filter((part) => !WHITESPACE_ONLY.test(part)).length;
    return { textNode, cut, parts };
  });
  if (units === 0) {
    return;
  }

  timeline.beginPass(performance.now());
  const { baseDelay, step } = timeline.take(units);
  let index = 0;

  for (const { textNode, cut, parts } of plans) {
    if (isSkipped(textNode)) {
      continue;
    }
    const fragment = document.createDocumentFragment();
    if (cut > 0) {
      fragment.appendChild(
        document.createTextNode(textNode.data.slice(0, cut)),
      );
    }
    for (const part of parts) {
      if (!part) {
        continue;
      }
      if (WHITESPACE_ONLY.test(part)) {
        fragment.appendChild(document.createTextNode(part));
        continue;
      }
      const span = document.createElement("span");
      span.className = ANIMATED;
      span.style.animationDuration = `${DURATION_MS}ms`;
      span.style.animationDelay = `${Math.round(baseDelay + index * step)}ms`;
      span.textContent = part;
      fragment.appendChild(span);
      index += 1;
    }
    textNode.replaceWith(fragment);
  }

  timeline.commitPass();
}

function isSkipped(node: Text): boolean {
  let parent = node.parentElement;
  while (parent) {
    if (SKIP_TAGS.has(parent.tagName.toLowerCase())) {
      return true;
    }
    parent = parent.parentElement;
  }
  return false;
}
