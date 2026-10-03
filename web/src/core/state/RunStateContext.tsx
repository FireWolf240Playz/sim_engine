"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useMutation } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";
import { DEMO_CONFIG } from "../lib/demo";
import type { SimulateResponse, SimulationConfig } from "../types";

/**
 * One shared run across the app: the active architecture, the selected
 * incident, the active run, and its result. The Simulator view renders it;
 * the Incidents view drives it ("run this incident" sets the playbook,
 * fires the run, navigates home). The engine call lives here exactly once
 * — no view owns the fetch.
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
  /** Fire a run; pass a playbook to switch+run in one step. */
  requestRun: (playbook?: string | null) => void;
  result: SimulateResponse | null;
  error: string | null;
  isPending: boolean;
}

const RunStateContext = createContext<RunStateValue | null>(null);

function friendlyError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 0) return `I can't reach the engine at ${api.base}.`;
    if (err.status === 404) return `The engine said no — ${err.message}`;
    return `The engine said no — ${err.message}`;
  }
  return "Something went wrong on my side — try RUN again.";
}

const DEMO_LABEL = "demo topology";

export function RunStateProvider({ children }: { children: ReactNode }) {
  const [playbook, setPlaybook] = useState<string | null>("db_failover");
  const [result, setResult] = useState<SimulateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [config, setConfig] = useState<SimulationConfig>(DEMO_CONFIG);
  const [architectureLabel, setArchitectureLabel] = useState<string>(DEMO_LABEL);

  const setArchitecture = useCallback((next: SimulationConfig, label: string) => {
    setConfig(next);
    setArchitectureLabel(label);
  }, []);

  const resetArchitecture = useCallback(() => {
    setConfig(DEMO_CONFIG);
    setArchitectureLabel(DEMO_LABEL);
  }, []);

  const run = useMutation({
    mutationFn: (pb: string | null) => api.simulate(config, pb),
    onSuccess: (data) => {
      setResult(data);
      setError(null);
    },
    onError: (err) => {
      setResult(null);
      setError(friendlyError(err));
    },
  });

  const requestRun = useCallback(
    (pb?: string | null) => {
      const chosen = pb === undefined ? playbook : pb;
      if (pb !== undefined) setPlaybook(pb);
      run.mutate(chosen);
    },
    [playbook, run],
  );

  const value = useMemo<RunStateValue>(
    () => ({
      config,
      architectureLabel,
      isDemoArchitecture: config === DEMO_CONFIG,
      setArchitecture,
      resetArchitecture,
      playbook,
      setPlaybook,
      requestRun,
      result,
      error,
      isPending: run.isPending,
    }),
    [config, architectureLabel, setArchitecture, resetArchitecture, playbook, requestRun, result, error, run.isPending],
  );

  return <RunStateContext.Provider value={value}>{children}</RunStateContext.Provider>;
}

export function useRunState(): RunStateValue {
  const ctx = useContext(RunStateContext);
  if (!ctx) throw new Error("useRunState must be used inside <RunStateProvider>");
  return ctx;
}
