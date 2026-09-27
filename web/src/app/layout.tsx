import type { Metadata } from "next";
import { IBM_Plex_Mono, Inter } from "next/font/google";
import "./globals.css";
import Providers from "./providers";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-plex-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Eleven — pre-deployment cloud resilience simulator",
  description:
    "Size a topology, inject an incident, and see exactly where it breaks — before you deploy.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  // data-theme="light" is a deterministic default: the DOM must match the
  // server HTML at hydration time for every user, so the persisted/OS theme
  // is applied by ThemeProvider right after hydration (one frame, no
  // pre-paint script that would desynchronize the DOM from the server HTML).
  return (
    <html
      lang="en"
      data-theme="light"
      className={`${inter.variable} ${plexMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col font-sans">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
