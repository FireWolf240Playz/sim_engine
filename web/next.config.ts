import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The in-app/dev browser may resolve the page origin to 127.0.0.1 while the
  // dev server canonicalizes localhost — allow both so HMR/dev resources are
  // not blocked cross-origin during development.
  allowedDevOrigins: ["localhost", "127.0.0.1"],
};

export default nextConfig;
