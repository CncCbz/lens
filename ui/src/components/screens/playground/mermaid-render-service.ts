import type { MermaidRequest, MermaidResponse } from "./mermaid-protocol";
import type { MermaidTheme } from "./mermaid-theme";

// A render that runs longer is stopped by terminating its worker; mermaid
// layout cannot be interrupted otherwise.
const DEADLINE_MS = 10_000;

type Waiter = {
  resolve: (svg: string) => void;
  reject: (error: Error) => void;
};

type Job = {
  source: string;
  theme: MermaidTheme;
  diagramId: string;
  waiters: Waiter[];
};

/**
 * Renders Mermaid diagrams in a dedicated worker so layout never blocks the
 * window. One render runs at a time; a newer request for a diagram still queued
 * replaces it, and every caller of that diagram receives the newer SVG.
 */
class MermaidRenderService {
  private worker: Worker | null = null;
  private queue: Job[] = [];
  private running: {
    id: number;
    job: Job;
    timer: ReturnType<typeof setTimeout>;
  } | null = null;
  private nextId = 0;

  render(
    source: string,
    { theme, diagramId }: { theme: MermaidTheme; diagramId: string },
  ): Promise<string> {
    return new Promise((resolve, reject) => {
      const waiter = { resolve, reject };
      const queued = this.queue.find((job) => job.diagramId === diagramId);
      if (queued) {
        queued.source = source;
        queued.theme = theme;
        queued.waiters.push(waiter);
        return;
      }
      this.queue.push({ source, theme, diagramId, waiters: [waiter] });
      this.next();
    });
  }

  private next() {
    if (this.running) {
      return;
    }
    const job = this.queue.shift();
    if (!job) {
      return;
    }
    const worker = (this.worker ??= this.spawn());
    const id = ++this.nextId;
    const timer = setTimeout(() => {
      this.restart(
        new Error(`Diagram took longer than ${DEADLINE_MS / 1000} s`),
      );
    }, DEADLINE_MS);
    this.running = { id, job, timer };
    const request: MermaidRequest = {
      id,
      source: job.source,
      theme: job.theme,
      diagramId: job.diagramId,
    };
    worker.postMessage(request);
  }

  private spawn(): Worker {
    const worker = new Worker(new URL("./mermaid.worker.ts", import.meta.url), {
      type: "module",
    });
    worker.onmessage = ({ data }: MessageEvent<MermaidResponse>) => {
      if (this.worker !== worker || this.running?.id !== data.id) {
        return;
      }
      const { job, timer } = this.running;
      clearTimeout(timer);
      this.running = null;
      for (const waiter of job.waiters) {
        if ("svg" in data) {
          waiter.resolve(data.svg);
        } else {
          waiter.reject(new Error(data.error));
        }
      }
      this.next();
    };
    worker.onerror = () => {
      if (this.worker !== worker) {
        return;
      }
      this.restart(new Error("Diagram renderer stopped unexpectedly"));
    };
    return worker;
  }

  /** Fails the render in progress, replaces its worker, and carries on. */
  private restart(error: Error) {
    this.worker?.terminate();
    this.worker = null;
    if (this.running) {
      clearTimeout(this.running.timer);
      for (const waiter of this.running.job.waiters) {
        waiter.reject(error);
      }
      this.running = null;
    }
    this.next();
  }
}

export const mermaidRenderService = new MermaidRenderService();
