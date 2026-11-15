/**
 * 3.1c — watching a run's event stream (`GET /runs/{id}/events`).
 *
 * The browser's `EventSource` reconnects by itself when a connection drops,
 * sending `Last-Event-ID`, and the engine resumes after that event; that is
 * not a failure. A stream closed for good before the run ended is, and so is
 * a stream that says nothing at all, not even its ping, for `SILENCE_MS`.
 */
import { RUN_EVENT_NAMES, parseRunEvent, type RunEvent } from "../lib/runStream";

/** The part of `EventSource` this module uses (a test passes a fake). */
export interface StreamSource {
  addEventListener(type: string, listener: (event: { data: string }) => void): void;
  close(): void;
  onerror: (() => void) | null;
  /** 0 connecting / reconnecting, 1 open, 2 closed for good. */
  readyState: number;
}

/** The engine pings every 15 s; three missed pings is a dead stream. */
export const SILENCE_MS = 45_000;

const CLOSED = 2;

export interface RunWatcher {
  onEvent: (event: RunEvent) => void;
  /** The stream died before the run ended. Called at most once. */
  onFail: (message: string) => void;
}

/**
 * Open the stream at `url` and hand every event on, in order, until the run
 * ends (`result` or `error`, after which the stream is closed so the browser
 * does not reconnect to a finished run). Returns a function that stops
 * watching; after it, nothing more is handed on.
 */
export function watchRun(
  url: string,
  { onEvent, onFail }: RunWatcher,
  open: (url: string) => StreamSource = (target) =>
    new EventSource(target) as unknown as StreamSource,
): () => void {
  const source = open(url);
  let over = false;
  let silence: ReturnType<typeof setTimeout> | undefined;

  const end = () => {
    over = true;
    if (silence !== undefined) clearTimeout(silence);
    source.close();
  };
  const fail = (message: string) => {
    if (over) return;
    end();
    onFail(message);
  };
  const heard = () => {
    if (silence !== undefined) clearTimeout(silence);
    silence = setTimeout(() => fail("the engine stopped answering"), SILENCE_MS);
  };

  for (const name of RUN_EVENT_NAMES) {
    source.addEventListener(name, (message) => {
      if (over) return;
      heard();
      const event = parseRunEvent(name, message.data);
      if (!event) return;
      if (event.type === "result" || event.type === "error") end();
      onEvent(event);
    });
  }
  source.addEventListener("ping", () => {
    if (!over) heard();
  });
  source.onerror = () => {
    if (over || source.readyState !== CLOSED) return;
    fail("the stream from the engine closed before the run finished");
  };
  heard();

  return () => {
    if (!over) end();
  };
}
