/** @type {import('next').NextConfig} */
const isDev = process.env.NODE_ENV === 'development';

const nextConfig = {
  output: 'export',
  images: { unoptimized: true },
  // Dev only: the static export is served by FastAPI on the same origin, so it needs no proxy.
  ...(isDev
    ? { async rewrites() { return [{ source: '/api/:path*', destination: 'http://localhost:8000/api/:path*' }]; } }
    : {}),
};

export default nextConfig;
