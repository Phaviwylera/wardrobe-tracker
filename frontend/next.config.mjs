/** @type {import('next').NextConfig} */
// The browser talks to /api/* on this origin; Next forwards it to the FastAPI service.
// On Render, API_HOSTPORT is the backend's private-network host:port (set in render.yaml).
const backend = process.env.API_HOSTPORT ? `http://${process.env.API_HOSTPORT}` : (process.env.API_URL || "http://localhost:8000");

const nextConfig = {
  reactStrictMode: true,
  images: { unoptimized: true },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};
export default nextConfig;
