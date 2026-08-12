/**
 * Serves the PRODUCTION BUILD (frontend/build) on :3000 and proxies the API to
 * Django on :8000. This is what `npm start` runs after `react-scripts build`.
 *
 * Why a script rather than the `serve` package:
 *
 *   src/api/client.js uses a RELATIVE baseURL ('/api/'). That is deliberate —
 *   it means the same bundle works when Django serves it (one origin) and when
 *   it is served here. But a plain static server answers /api/... with its own
 *   404, so every request in the app fails. `serve` dropped origin-proxying in
 *   v11 and CRA's package.json "proxy" field only applies to `react-scripts
 *   start` (the dev server), which is not what we run. Hence: static files with
 *   SPA fallback, plus a real proxy for /api and /media.
 *
 * Keeping requests same-origin also keeps them preflight-free, so the
 * `Authorization: Token …` header the client attaches needs no CORS round trip.
 *
 * Usage:
 *   node scripts/serve-build.mjs                 # :3000 -> Django :8000
 *   PORT=4000 API_TARGET=http://127.0.0.1:8001 node scripts/serve-build.mjs
 */
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BUILD_DIR = path.resolve(HERE, '..', 'build');
const PORT = Number(process.env.PORT || 3000);
const API_TARGET = new URL(process.env.API_TARGET || 'http://127.0.0.1:8000');
const PROXY_PREFIXES = ['/api/', '/api', '/media/', '/media', '/admin/', '/static/admin/'];

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.txt': 'text/plain; charset=utf-8',
  '.xml': 'application/xml; charset=utf-8',
};

if (!fs.existsSync(path.join(BUILD_DIR, 'index.html'))) {
  console.error(
    `No build found at ${BUILD_DIR}\n` +
    'Run `npm run build` first, or `npm start` which builds and then serves.',
  );
  process.exit(1);
}

function shouldProxy(pathname) {
  return PROXY_PREFIXES.some((p) => pathname === p || pathname.startsWith(p));
}

function proxy(req, res, pathname, search) {
  const upstream = http.request(
    {
      protocol: API_TARGET.protocol,
      hostname: API_TARGET.hostname,
      port: API_TARGET.port || 80,
      method: req.method,
      path: pathname + search,
      // Django's ALLOWED_HOSTS is localhost,127.0.0.1 — forwarding the upstream
      // host keeps it satisfied regardless of how the browser addressed us.
      headers: { ...req.headers, host: API_TARGET.host },
    },
    (upRes) => {
      res.writeHead(upRes.statusCode || 502, upRes.headers);
      upRes.pipe(res);
    },
  );
  upstream.on('error', (err) => {
    // A dead backend must not look like an application bug. 502 + a named cause
    // is what client.js's retry/error path expects to surface.
    if (!res.headersSent) {
      res.writeHead(502, { 'Content-Type': 'application/json; charset=utf-8' });
    }
    res.end(JSON.stringify({
      detail: `Cannot reach the API at ${API_TARGET.origin} (${err.code || err.message}). ` +
              'Is Django running?  python backend/manage.py runserver',
    }));
  });
  // Stream rather than buffer: the import wizard POSTs multi-megabyte payloads.
  req.pipe(upstream);
}

function serveFile(res, filePath, { immutable = false } = {}) {
  const ext = path.extname(filePath).toLowerCase();
  const stream = fs.createReadStream(filePath);
  stream.on('open', () => {
    res.writeHead(200, {
      'Content-Type': MIME[ext] || 'application/octet-stream',
      // build/static/* filenames carry a content hash, so they are safe to pin.
      // index.html must never be cached or a deploy serves stale asset names.
      'Cache-Control': immutable ? 'public, max-age=31536000, immutable' : 'no-store',
    });
  });
  stream.on('error', () => {
    if (!res.headersSent) res.writeHead(500);
    res.end('Read error');
  });
  stream.pipe(res);
}

function handle(req, res) {
  // RAW (still percent-encoded) path vs DECODED path are not interchangeable and
  // must not be mixed up:
  //   - the proxy needs RAW. http.request() throws ERR_UNESCAPED_CHARACTERS on a
  //     decoded path, and re-encoding it would corrupt any literal % or ?/# in a
  //     path segment. Real URLs hit this: /api/event-performance/BFSI%20SUMMIT/.
  //   - the filesystem lookup needs DECODED, because build/ holds real bytes.
  let rawPath;
  let decodedPath;
  let search = '';
  try {
    const url = new URL(req.url, 'http://localhost');
    rawPath = url.pathname;
    search = url.search;
    decodedPath = decodeURIComponent(url.pathname);
  } catch {
    // A malformed escape (a lone '%') only breaks decoding. It is still a valid
    // request to proxy, so fall back rather than 400 the whole thing.
    decodedPath = rawPath;
    if (!rawPath) {
      res.writeHead(400, { 'Content-Type': 'text/plain; charset=utf-8' }).end('Bad request URL');
      return;
    }
  }

  if (shouldProxy(decodedPath)) {
    proxy(req, res, rawPath, search);
    return;
  }

  // Resolve inside BUILD_DIR only. path.join on a "../.." pathname would escape
  // the build directory and happily serve source or dotfiles.
  const candidate = path.resolve(BUILD_DIR, '.' + path.posix.normalize(decodedPath));
  const inside = candidate === BUILD_DIR || candidate.startsWith(BUILD_DIR + path.sep);

  if (inside && fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
    serveFile(res, candidate, { immutable: decodedPath.startsWith('/static/') });
    return;
  }

  // A missing /static/ asset is a build problem; answering it with the SPA
  // shell turns that into a confusing "unexpected token '<'" in the console.
  if (decodedPath.startsWith('/static/')) {
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end(`Not found in build/: ${decodedPath}`);
    return;
  }

  // Everything else is a client-side route (/bookings, /reports/overview, …) —
  // same catch-all Django applies in production via config/urls.py.
  serveFile(res, path.join(BUILD_DIR, 'index.html'));
}

/**
 * One malformed request must never take the server down. An uncaught throw in a
 * Node request handler is a process-level exception, so without this the whole
 * app goes dark for every open tab — a 500 on one request is the correct blast
 * radius. (Learned the hard way: an encoded space in an event-performance URL
 * killed the server mid-QA.)
 */
const server = http.createServer((req, res) => {
  try {
    handle(req, res);
  } catch (err) {
    console.error(`[serve-build] ${req.method} ${req.url} — ${err.stack || err.message}`);
    if (!res.headersSent) res.writeHead(500, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('Internal server error — see the serve-build console.');
  }
});

// Client disconnects mid-response surface as socket errors on req/res. They are
// normal browser behaviour (navigating away cancels in-flight requests), not
// faults, and must not reach the uncaughtException path.
server.on('clientError', (err, socket) => {
  if (socket.writable) socket.end('HTTP/1.1 400 Bad Request\r\n\r\n');
});
process.on('uncaughtException', (err) => {
  if (['ECONNRESET', 'EPIPE', 'ERR_STREAM_WRITE_AFTER_END'].includes(err.code)) return;
  console.error('[serve-build] uncaught:', err.stack || err.message);
});

server.listen(PORT, () => {
  console.log(`\n  Serving production build  ${BUILD_DIR}`);
  console.log(`  Local                     http://localhost:${PORT}`);
  console.log(`  API + /media proxied to   ${API_TARGET.origin}\n`);
  console.log('  Rebuild after a source change:  npm run build   (or re-run npm start)\n');
});
