/** @type {import('next').NextConfig} */
// DESKTOP_EXPORT=1 produces a static `out/` the backend serves directly (single
// origin) for the Tauri desktop build. Otherwise the Docker server build is used.
const desktop = process.env.DESKTOP_EXPORT === "1";
const nextConfig = {
  output: desktop ? "export" : "standalone",
  reactStrictMode: true,
  // Static export can't use the Next image optimizer.
  ...(desktop ? { images: { unoptimized: true } } : {}),
};
export default nextConfig;
