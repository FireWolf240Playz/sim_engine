"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useMutation } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";
import { DEMO_CONFIG } from "../lib/demo";
import { completeRecord, startRecord, type FixRecord } from "../lib/fixHistory";
import { applySuggestions } from "../lib/suggestions";
import type { SimulateResponse, SimulationConfig, Suggestion } from "../types";

/**
 * How the next run executes (roadmap 1.1):
 * - "single"      — one seed, the original headline number;
 * - "confidence"  — 5 seeds, worst/typical/best confidence profile.
 */
export type RunMode = "single" | "confidence";

/** How many seeds a confidence run fires (the "5-seed" in the toggle). */
export const CONFIDENCE_SEEDS = 5;

/**
 * One shared run across the app: the active architecture, the selected
 * incident, the active run, and its result. The Simulator view renders it;
 * the Incidents view drives it ("run this incident" sets the playbook,
 * fires the run, navigates home); Compare uses the same active config as
 * its baseline. The engine call lives here exactly once — no view owns
 * the fetch.
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
  result: SimulateResponse | null;
  error: string | null;
  isPending: boolean;
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

const DEMO_LABEL = "demo topology";

/**
 * The settled outcome of one run, tagged with the sequence number it was
 * started under.
 *
 * Runs are not cancelled when a newer one starts (the engine call is
 * already in flight server-side), so without the tag a slow earlier run
 * could resolve last and overwrite the newer result — the classic
 * "switch incident, hit Run twice, read the wrong numbers" race. Both
 * success and failure are returned rather than thrown, so one guard in
 * `onSettled` covers both paths.
 */
type RunOutcome =
  | { token: number; data: SimulateResponse }
  | { token: number; failure: string };

export function RunStateProvider({ children }: { children: ReactNode }) {
  const [playbook, setPlaybook] = useState<string | null>("db_failover");
  const [result, setResult] = useState<SimulateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [config, setConfig] = useState<SimulationConfig>(DEMO_CONFIG);
  const [architectureLabel, setArchitectureLabel] = useState<string>(DEMO_LABEL);
  const [isDemoArchitecture, setIsDemoArchitecture] = useState(true);
  const [runMode, setRunMode] = useState<RunMode>("single");
  const [fixHistory, setFixHistory] = useState<FixRecord[]>([]);

  /** Monotonic run counter; only the newest token may write state. */
  const runSeq = useRef(0);

  // A new architecture starts a new change log.
  const setArchitecture = useCallback((next: SimulationConfig, label: string) => {
    setConfig(next);
    setArchitectureLabel(label);
    setIsDemoArchitecture(false);
    setFixHistory([]);
  }, []);

  const resetArchitecture = useCallback(() => {
    setConfig(DEMO_CONFIG);
    setArchitectureLabel(DEMO_LABEL);
    setIsDemoArchitecture(true);
    setFixHistory([]);
  }, []);

  /**
   * The run payload. `configOverride` lets "apply & re-run" (roadmap 1.3)
   * start a run with the patched config in the same tick — reading the
   * `config` state inside `mutationFn` would be one render stale, so the
   * override travels with the mutation instead.
   */
  type RunPayload = {
    pb: string | null;
    configOverride?: SimulationConfig;
    /** This run is the re-run of an apply: its result completes the log. */
    fixStep?: boolean;
  };

  const run = useMutation<RunOutcome, never, RunPayload>({
    mutationFn: async ({ pb, configOverride }) => {
      const token = (runSeq.current += 1);
      const effective = configOverride ?? config;
      try {
        const data = await api.simulate(
          effective,
          pb,
          runMode === "confidence" ? CONFIDENCE_SEEDS : 1,
        );
        return { token, data };
      } catch (err) {
        return { token, failure: friendlyError(err) };
      }
    },
    onSuccess: (outcome, payload) => {
      // A newer run has been started since this one — discard it.
      if (outcome.token !== runSeq.current) return;
      if ("failure" in outcome) {
        setResult(null);
        setError(outcome.failure);
        if (payload.fixStep) setFixHistory((h) => completeRecord(h, null));
        return;
      }
      setResult(outcome.data);
      setError(null);
      if (payload.fixStep) setFixHistory((h) => completeRecord(h, outcome.data.summary));
    },
  });

  const { mutate } = run;

  const requestRun = useCallback(
    (pb?: string | null) => {
      const chosen = pb === undefined ? playbook : pb;
      if (pb !== undefined) setPlaybook(pb);
      mutate({ pb: chosen });
    },
    // `mutate` is referentially stable in React Query v5; depending on the
    // whole mutation object would rebuild this callback (and the context
    // value below) on every render.
    [playbook, mutate],
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
      mutate({ pb: playbook, configOverride: next, fixStep: true });
    },
    [config, architectureLabel, playbook, mutate, result],
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
      error,
      isPending: run.isPending,
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
      error,
      run.isPending,
    ],
  );

  return <RunStateContext.Provider value={value}>{children}</RunStateContext.Provider>;
}

export function useRunState(): RunStateValue {
  const ctx = useContext(RunStateContext);
  if (!ctx) throw new Error("useRunState must be used inside <RunStateProvider>");
  return ctx;
}
