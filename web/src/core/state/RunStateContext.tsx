"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ApiError, api } from "../api/client";
import { watchRun } from "../api/runStream";
import { failureEvent, withFailure, withoutFailure } from "../lib/clickToFail";
import { DEMO_CONFIG } from "../lib/demo";
import { completeRecord, startRecord, type FixRecord } from "../lib/fixHistory";
import {
  EMPTY_FEED,
  provisionalResult,
  reduceRun,
  type RunFeed,
} from "../lib/runStream";
import { applySuggestions } from "../lib/suggestions";
import { shareResult } from "../lib/runResult";
import type {
  ChaosEventCfg,
  MeasuredRun,
  SimulateResponse,
  SimulationConfig,
  Suggestion,
  Summary,
  TopologyNode,
  TrafficShape,
} from "../types";
import { withShape } from "../lib/trafficShape";

/**
 * How the next run executes (roadmap 1.1):
 * - "single"      — one seed, the original headline number;
 * - "confidence"  — 5 seeds, worst/typical/best confidence profile.
 */
export type RunMode = "single" | "confidence";

/** How many seeds a confidence run fires (the "5-seed" in the toggle). */
export const CONFIDENCE_SEEDS = 5;

/**
 * How often the stream's ticks and lines reach the page. A run's events can
 * arrive far faster than a screen redraws (60 ticks in a fraction of a
 * second), so they are folded at once and drawn at most this often.
 */
const FEED_FLUSH_MS = 100;

/**
 * One shared run across the app: the active architecture, the selected
 * incident, the active run, and its result. The Simulator view renders it;
 * the Incidents view drives it ("run this incident" sets the playbook,
 * fires the run, navigates home); Compare uses the same active config as
 * its baseline. The engine call lives here exactly once — no view owns
 * the fetch.
 *
 * 3.1c — a run is started with `POST /runs` and watched through its event
 * stream: the verdict lands when the run is measured (a fraction of a
 * second), the Fix-it plan when the engine has verified it (up to half a
 * minute on a large topology). `feed` holds the run's ticks, incidents and
 * feed lines for the live view.
 *
 * The active architecture defaults to the calibrated demo topology; the
 * Import page swaps it in (any uploaded YAML/JSON/Terraform config) and
 * every subsequent run — clean or under any incident — targets it until
 * it is reset.
 */
interface RunStateValue {
  /** The topology+traffic every run simulates (demo or imported). */
  config: SimulationConfig;
  /** One-line label for chips/headers: "demo topology" or the import label. */
  architectureLabel: string;
  /** true while the demo topology is active. */
  isDemoArchitecture: boolean;
  /** Swap in an imported architecture (label shown in the UI). */
  setArchitecture: (config: SimulationConfig, label: string) => void;
  /** Back to the calibrated demo topology. */
  resetArchitecture: () => void;
  /** null = clean run; otherwise a playbook key. */
  playbook: string | null;
  setPlaybook: (playbook: string | null) => void;
  /** single vs multi-seed confidence run; the next run respects it. */
  runMode: RunMode;
  setRunMode: (mode: RunMode) => void;
  /** Fire a run; pass a playbook to switch+run in one step. */
  requestRun: (playbook?: string | null) => void;
  /**
   * Roadmap 1.3 — "apply & re-run": patch the active config with the
   * suggested capacity changes and immediately run it. The patched config
   * becomes the new active architecture, so follow-up clean or incident
   * runs re-test the right-sized shape.
   */
  applyAndRerun: (suggestions: Suggestion[]) => void;
  /** Every apply on the current architecture: before, prediction, re-run. */
  fixHistory: FixRecord[];
  /**
   * The run on screen. While its plan is verified (`planPending`) it is the
   * measured summary with the stream's ticks, and carries no suggestions.
   */
  result: SimulateResponse | null;
  /**
   * 1.3g — the nodes of the config the current `result` was simulated
   * with (null before the first measured run, or after a reset). A node
   * whose size differs from these is "stale": its on-screen numbers
   * belong to the old size.
   */
  measuredNodes: TopologyNode[] | null;
  /**
   * 2.1c — the config and incident the current `result` was simulated with
   * (null when there is no measured run). An export names its run by these,
   * never by `config` / `playbook`: the picker and "Apply & re-run" move
   * those before anything new is measured.
   */
  measuredRun: MeasuredRun | null;
  error: string | null;
  /** A run has started and is not measured yet. */
  isPending: boolean;
  /** 3.1c — the run is measured; its Fix-it plan is still being verified. */
  planPending: boolean;
  /** 3.1c — why the measured run's plan could not be verified, if it could not. */
  planError: string | null;
  /** 3.1c — the latest run's events: ticks, incidents, feed lines. */
  feed: RunFeed;
  /**
   * 3.3 — failures the user injected by clicking nodes. Every run carries
   * them (on top of the incident) until they are removed, or the
   * architecture is replaced.
   */
  customChaos: ChaosEventCfg[];
  /** 3.3 — fail this node (a full outage for 15 s) and run at once. */
  injectFailure: (node: string) => void;
  /** 3.3 — remove this node's injected failure; the next run goes without it. */
  clearFailure: (node: string) => void;
  /**
   * 3.5b — the shape of the day for the next run. It changes the traffic of
   * the active config only: no run, and the change log, label and injected
   * failures stay.
   */
  setTrafficShape: (shape: TrafficShape) => void;
}

const RunStateContext = createContext<RunStateValue | null>(null);

function friendlyError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.timedOut) return `${err.message} — it may still be running.`;
    if (err.status === 0) return `I can't reach the engine at ${api.base}.`;
    return `The engine said no — ${err.message}`;
  }
  return "Something went wrong on my side — try RUN again.";
}

/** A sentence from the engine or the stream, as the page shows it. */
function sentence(message: string): string {
  return message ? message[0].toUpperCase() + message.slice(1) : message;
}

const DEMO_LABEL = "demo topology";

/**
 * One run's input. `configOverride` lets "apply & re-run" (roadmap 1.3)
 * start a run with the patched config in the same tick — the `config`
 * state would be one render stale.
 */
type RunPayload = {
  pb: string | null;
  configOverride?: SimulationConfig;
  /** This run is the re-run of an apply: its measurement completes the log. */
  fixStep?: boolean;
  /** 3.3: the injected failures, when they changed in this same tick. */
  chaosOverride?: ChaosEventCfg[];
};

export function RunStateProvider({ children }: { children: ReactNode }) {
  const [playbook, setPlaybook] = useState<string | null>("db_failover");
  const [result, setResult] = useState<SimulateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [config, setConfig] = useState<SimulationConfig>(DEMO_CONFIG);
  const [architectureLabel, setArchitectureLabel] = useState<string>(DEMO_LABEL);
  const [isDemoArchitecture, setIsDemoArchitecture] = useState(true);
  const [runMode, setRunMode] = useState<RunMode>("single");
  const [fixHistory, setFixHistory] = useState<FixRecord[]>([]);
  const [measuredConfig, setMeasuredConfig] = useState<SimulationConfig | null>(null);
  const [measuredPlaybook, setMeasuredPlaybook] = useState<string | null>(null);
  const [isPending, setIsPending] = useState(false);
  const [planPending, setPlanPending] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const [feed, setFeed] = useState<RunFeed>(EMPTY_FEED);
  const [customChaos, setCustomChaos] = useState<ChaosEventCfg[]>([]);
  const [measuredChaos, setMeasuredChaos] = useState<ChaosEventCfg[]>([]);

  /**
   * Monotonic run counter; only the newest run may write state. Runs are
   * not cancelled server-side when a newer one starts, so without it a slow
   * earlier run could land last and overwrite the newer result — the
   * classic "switch incident, hit Run twice, read the wrong numbers" race.
   */
  const runSeq = useRef(0);
  /** Stops watching the current run's stream. */
  const stopStream = useRef<(() => void) | null>(null);
  /** The current run's feed, folded at once; `feed` is its drawn copy. */
  const feedRef = useRef<RunFeed>(EMPTY_FEED);
  const flushTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flushFeed = useCallback(() => {
    if (flushTimer.current !== null) {
      clearTimeout(flushTimer.current);
      flushTimer.current = null;
    }
    setFeed(feedRef.current);
  }, []);

  const scheduleFlush = useCallback(() => {
    if (flushTimer.current === null) flushTimer.current = setTimeout(flushFeed, FEED_FLUSH_MS);
  }, [flushFeed]);

  // Leaving the app stops the stream and the pending draw.
  useEffect(
    () => () => {
      stopStream.current?.();
      if (flushTimer.current !== null) clearTimeout(flushTimer.current);
    },
    [],
  );

  // A new architecture starts a new change log.
  const setArchitecture = useCallback((next: SimulationConfig, label: string) => {
    setConfig(next);
    setArchitectureLabel(label);
    setIsDemoArchitecture(false);
    setFixHistory([]);
    setMeasuredConfig(null);
    setMeasuredPlaybook(null);
    // Injected failures name nodes of the old architecture.
    setCustomChaos([]);
  }, []);

  const resetArchitecture = useCallback(() => {
    setConfig(DEMO_CONFIG);
    setArchitectureLabel(DEMO_LABEL);
    setIsDemoArchitecture(true);
    setFixHistory([]);
    setMeasuredConfig(null);
    setMeasuredPlaybook(null);
    setCustomChaos([]);
  }, []);

  const startRun = useCallback(
    async ({ pb, configOverride, fixStep = false, chaosOverride }: RunPayload) => {
      const token = (runSeq.current += 1);
      const current = () => token === runSeq.current;
      stopStream.current?.();
      stopStream.current = null;

      const effective = configOverride ?? config;
      const injected = chaosOverride ?? customChaos;
      const nSeeds = runMode === "confidence" ? CONFIDENCE_SEEDS : 1;
      setIsPending(true);
      setPlanPending(false);
      setPlanError(null);
      let measured = false;

      // Nothing was measured. An apply's re-run keeps the last result: it is
      // the same incident, and the resized nodes read NOT MEASURED (1.3g).
      // Any other failed run clears it, since the old numbers may be
      // another incident's.
      const failBeforeMeasured = (message: string) => {
        if (!fixStep) setResult(null);
        setError(message);
        setIsPending(false);
        if (fixStep) setFixHistory((h) => completeRecord(h, null));
      };

      const onMeasured = (summary: Summary, shown: SimulateResponse) => {
        measured = true;
        setResult((prev) => shareResult(prev, shown));
        setMeasuredConfig(effective);
        setMeasuredPlaybook(pb);
        setMeasuredChaos(injected);
        setError(null);
        setIsPending(false);
        // Score, band and findings are final at this point; only the plan is not.
        if (fixStep) setFixHistory((h) => completeRecord(h, summary));
      };

      const onResult = (data: SimulateResponse) => {
        if (!measured) onMeasured(data.summary, data);
        setResult((prev) => shareResult(prev, data));
        setPlanPending(false);
      };

      // The run ended without a result. After the measurement that is the
      // plan's failure, not the run's: the verdict on screen stands.
      const giveUp = (message: string) => {
        if (!current()) return;
        if (measured) {
          setPlanPending(false);
          setPlanError(message);
        } else {
          failBeforeMeasured(sentence(message));
        }
      };

      let runId: string;
      try {
        const started = await api.startRun({
          config: effective,
          playbook: pb,
          ...(nSeeds > 1 ? { n_seeds: nSeeds } : {}),
          ...(injected.length ? { custom_chaos: injected } : {}),
        });
        runId = started.run_id;
      } catch (err) {
        if (current()) failBeforeMeasured(friendlyError(err));
        return;
      }
      if (!current()) return;

      // The stream died, but the run may well have finished without us:
      // ask once before saying it failed.
      const recover = async (message: string) => {
        try {
          const status = await api.runStatus(runId);
          if (!current()) return;
          if (status.status === "done" && status.result) {
            onResult(status.result);
            return;
          }
        } catch {
          // fall through: the stream's own message says what happened
        }
        giveUp(message);
      };

      stopStream.current = watchRun(api.runEventsUrl(runId), {
        onEvent: (event) => {
          if (!current()) return;
          feedRef.current = reduceRun(feedRef.current, event);
          switch (event.type) {
            case "measured": {
              flushFeed();
              const shown = provisionalResult(feedRef.current);
              if (shown) onMeasured(event.data.summary, shown);
              setPlanPending(true);
              break;
            }
            case "result":
              flushFeed();
              onResult(event.data);
              break;
            case "error":
              flushFeed();
              giveUp(event.data.detail);
              break;
            default:
              scheduleFlush();
          }
        },
        onFail: (message) => {
          if (!current()) return;
          flushFeed();
          void recover(message);
        },
      });
    },
    [config, runMode, customChaos, flushFeed, scheduleFlush],
  );

  /** 3.3 — "inject failure here": add the node's outage and run with it now. */
  const injectFailure = useCallback(
    (node: string) => {
      const next = withFailure(customChaos, failureEvent(node, config));
      setCustomChaos(next);
      void startRun({ pb: playbook, chaosOverride: next });
    },
    [customChaos, config, playbook, startRun],
  );

  const clearFailure = useCallback((node: string) => {
    setCustomChaos((list) => withoutFailure(list, node));
  }, []);

  const setTrafficShape = useCallback((shape: TrafficShape) => {
    setConfig((current) => ({ ...current, traffic: withShape(current.traffic, shape) }));
  }, []);

  const requestRun = useCallback(
    (pb?: string | null) => {
      const chosen = pb === undefined ? playbook : pb;
      if (pb !== undefined) setPlaybook(pb);
      void startRun({ pb: chosen });
    },
    [playbook, startRun],
  );

  /**
   * Roadmap 1.3 — "apply & re-run": immutably patch the active config with
   * the run's suggestions and fire a run with the patched config. The
   * patched config becomes the active architecture (label gets a
   * "right-sized" mark), so follow-up runs re-test the fixed shape; the
   * run-token guard discards stale in-flight results exactly as before.
   *
   * The patched shape is no longer the imported/demo baseline, so it is
   * marked as a custom architecture: the header chip then offers a reset
   * back to the original topology instead of the static "seed 42" note.
   */
  const applyAndRerun = useCallback(
    (suggestions: Suggestion[]) => {
      const next = applySuggestions(config, suggestions);
      setConfig(next);
      if (!architectureLabel.includes("right-sized")) {
        setArchitectureLabel(`${architectureLabel} · right-sized`);
      }
      setIsDemoArchitecture(false);
      if (result) setFixHistory((h) => startRecord(h, result.summary, suggestions, playbook));
      void startRun({ pb: playbook, configOverride: next, fixStep: true });
    },
    [config, architectureLabel, playbook, startRun, result],
  );

  const measuredRun = useMemo<MeasuredRun | null>(
    () =>
      measuredConfig
        ? {
            config: measuredConfig,
            playbook: measuredPlaybook,
            ...(measuredChaos.length ? { customChaos: measuredChaos } : {}),
          }
        : null,
    [measuredConfig, measuredPlaybook, measuredChaos],
  );

  const value = useMemo<RunStateValue>(
    () => ({
      config,
      architectureLabel,
      isDemoArchitecture,
      setArchitecture,
      resetArchitecture,
      playbook,
      setPlaybook,
      runMode,
      setRunMode,
      requestRun,
      applyAndRerun,
      fixHistory,
      result,
      measuredNodes: measuredConfig ? measuredConfig.topology.nodes : null,
      measuredRun,
      error,
      isPending,
      planPending,
      planError,
      feed,
      customChaos,
      injectFailure,
      clearFailure,
      setTrafficShape,
    }),
    [
      config,
      architectureLabel,
      isDemoArchitecture,
      setArchitecture,
      resetArchitecture,
      playbook,
      runMode,
      requestRun,
      applyAndRerun,
      fixHistory,
      result,
      measuredConfig,
      measuredRun,
      error,
      isPending,
      planPending,
      planError,
      feed,
      customChaos,
      injectFailure,
      clearFailure,
      setTrafficShape,
    ],
  );

  return <RunStateContext.Provider value={value}>{children}</RunStateContext.Provider>;
}

export function useRunState(): RunStateValue {
  const ctx = useContext(RunStateContext);
  if (!ctx) throw new Error("useRunState must be used inside <RunStateProvider>");
  return ctx;
}
