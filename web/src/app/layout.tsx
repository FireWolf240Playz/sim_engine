import type { Metadata, Viewport } from "next";
import { IBM_Plex_Mono, Inter } from "next/font/google";
import "./globals.css";
import Providers from "./providers";
import { THEME_PREPAINT_SCRIPT } from "@/core/lib/themeStorage";

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

export const viewport: Viewport = {
  // Both schemes are supported, so the browser paints its own chrome
  // (scrollbars, form controls) to match whichever theme is active.
  colorScheme: "light dark",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  // `data-theme="light"` is the deterministic server default, and the
  // pre-paint script below replaces it with the persisted/OS preference
  // before the first frame is painted — so there is no white flash for a
  // dark-theme visitor. `suppressHydrationWarning` covers exactly that
  // one attribute: React's own tree still hydrates from
  // `getServerSnapshot()` (see core/state/ThemeContext), so nothing the
  // components render can disagree with the server HTML.
  return (
    <html
      lang="en"
      data-theme="light"
      suppressHydrationWarning
      className={`${inter.variable} ${plexMono.variable} h-full antialiased`}
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_PREPAINT_SCRIPT }} />
      </head>
      <body className="min-h-full flex flex-col font-sans">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
