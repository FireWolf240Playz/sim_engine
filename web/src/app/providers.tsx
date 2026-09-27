"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Shell } from "@/components/Shell";
import { RunStateProvider } from "@/core/state/RunStateContext";
import { ThemeProvider } from "@/core/state/ThemeContext";

/**
 * Providers: theme (light/dark), React Query (the API is stateless —
 * no persistence layer), the shared run state (playbook + active run +
 * result, one owner), and the app shell (sidebar + engine status) that
 * wraps every route.
 */
export default function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60_000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  return (
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <RunStateProvider>
          <Shell>{children}</Shell>
        </RunStateProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
