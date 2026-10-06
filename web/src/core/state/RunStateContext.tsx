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
import type { SimulateResponse, SimulationConfig } from "../types";

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

  /** Monotonic run counter; only the newest token may write state. */
  const runSeq = useRef(0);

  const setArchitecture = useCallback((next: SimulationConfig, label: string) => {
    setConfig(next);
    setArchitectureLabel(label);
    setIsDemoArchitecture(false);
  }, []);

  const resetArchitecture = useCallback(() => {
    setConfig(DEMO_CONFIG);
    setArchitectureLabel(DEMO_LABEL);
    setIsDemoArchitecture(true);
  }, []);

  const run = useMutation<RunOutcome, never, string | null>({
    mutationFn: async (pb) => {
      const token = (runSeq.current += 1);
      try {
        const data = await api.simulate(
          config,
          pb,
          runMode === "confidence" ? CONFIDENCE_SEEDS : 1,
        );
        return { token, data };
      } catch (err) {
        return { token, failure: friendlyError(err) };
      }
    },
    onSuccess: (outcome) => {
      // A newer run has been started since this one — discard it.
      if (outcome.token !== runSeq.current) return;
      if ("failure" in outcome) {
        setResult(null);
        setError(outcome.failure);
        return;
      }
      setResult(outcome.data);
      setError(null);
    },
  });

  const { mutate } = run;

  const requestRun = useCallback(
    (pb?: string | null) => {
      const chosen = pb === undefined ? playbook : pb;
      if (pb !== undefined) setPlaybook(pb);
      mutate(chosen);
    },
    // `mutate` is referentially stable in React Query v5; depending on the
    // whole mutation object would rebuild this callback (and the context
    // value below) on every render.
    [playbook, mutate],
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
