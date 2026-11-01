/** @type {import('next').NextConfig} */
// Production: `STATIC_EXPORT=1 next build` writes plain HTML/JS to out/, which the FastAPI service
// serves on the same origin as /api (one free Render service).
// Development: `next dev` proxies /api/* to the FastAPI server.
const exportMode = process.env.STATIC_EXPORT === "1";
const backend = process.env.API_URL || "http://localhost:8000";

const nextConfig = {
  reactStrictMode: true,
  trailingSlash: true,
  images: { unoptimized: true },
  ...(exportMode
    ? { output: "export" }
    : { async rewrites() { return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }]; } }),
};
export default nextConfig;
