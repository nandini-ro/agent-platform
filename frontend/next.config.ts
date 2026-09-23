import path from "node:path";

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emit a self-contained server bundle (.next/standalone) so the production
  // image can drop node_modules and the source tree entirely.
  output: "standalone",

  // Pin the workspace root. A stray package-lock.json in the home directory
  // otherwise makes Turbopack infer a root above this project.
  turbopack: { root: path.resolve(__dirname) },

  // Next 16 blocks dev-server resources requested from an origin other than
  // the one it was opened on, so visiting 127.0.0.1:3000 would load the HTML
  // but never the client bundle - the page hydrates into nothing. Allow both
  // spellings of localhost in development.
  allowedDevOrigins: ["127.0.0.1", "localhost"],
};

export default nextConfig;
