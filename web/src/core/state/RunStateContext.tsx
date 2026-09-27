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
import type { SimulateResponse } from "../types";

/**
 * One shared run across the app: the selected incident, the active run,
 * and its result. The Simulator view renders it; the Incidents view drives
 * it ("run this incident" sets the playbook, fires the run, navigates home).
 * The engine call lives here exactly once — no view owns the fetch.
 */
interface RunStateValue {
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

export function RunStateProvider({ children }: { children: ReactNode }) {
  const [playbook, setPlaybook] = useState<string | null>("db_failover");
  const [result, setResult] = useState<SimulateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = useMutation({
    mutationFn: (pb: string | null) => api.simulate(DEMO_CONFIG, pb),
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
      playbook,
      setPlaybook,
      requestRun,
      result,
      error,
      isPending: run.isPending,
    }),
    [playbook, requestRun, result, error, run.isPending],
  );

  return <RunStateContext.Provider value={value}>{children}</RunStateContext.Provider>;
}

export function useRunState(): RunStateValue {
  const ctx = useContext(RunStateContext);
  if (!ctx) throw new Error("useRunState must be used inside <RunStateProvider>");
  return ctx;
}
