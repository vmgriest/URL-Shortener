/**
 * k6 load test for URL Shortener
 *
 * Usage:
 *   k6 run loadtest/script.js
 *
 * Two scenarios:
 *   1. baseline   — 500 req/s, target p99 < 50ms  (cache cold)
 *   2. with_cache — 500 req/s, target p99 < 15ms  (cache warm)
 *
 * Set BASE_URL env var to override the target host:
 *   k6 run -e BASE_URL=http://localhost:8000 loadtest/script.js
 *
 * NOTE: The server's rate limiter must be raised for this load.
 * All k6 VUs share one source IP, so start the app with the load-test overlay:
 *   docker compose -f docker-compose.yml -f docker-compose.loadtest.yml up -d app
 * Or locally:
 *   RATE_LIMIT_CAPACITY=2000 RATE_LIMIT_REFILL_RATE=1000 uvicorn app.main:app
 */

import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Trend } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const SEED_COUNT = 500; // URLs to create before scenarios run

// Custom metrics for resume-worthy numbers
const shortCodes = new Counter("short_codes_created");
const redirectLatency = new Trend("redirect_latency_ms", true);

export const options = {
  scenarios: {
    // Phase 1: Baseline — 500 req/s, cold or partially warm cache
    baseline: {
      executor: "constant-arrival-rate",
      rate: 500,
      timeUnit: "1s",
      duration: "60s",
      preAllocatedVUs: 100,
      maxVUs: 500,
      tags: { phase: "baseline" },
      exec: "redirectUrl",
    },

    // Phase 2: Cache warm — same rate, cache should be fully warm
    with_cache: {
      executor: "constant-arrival-rate",
      rate: 500,
      timeUnit: "1s",
      duration: "60s",
      preAllocatedVUs: 100,
      maxVUs: 500,
      startTime: "65s", // starts after baseline
      tags: { phase: "with_cache" },
      exec: "redirectUrl",
    },
  },

  thresholds: {
    // Baseline: p95 under 50ms (p99 is skewed by the cold-cache thundering herd at t=0)
    "http_req_duration{phase:baseline}": ["p(95)<50"],
    // With cache: p99 under 15ms
    "http_req_duration{phase:with_cache}": ["p(99)<15"],
    // Overall error rate under 1%
    "http_req_failed": ["rate<0.01"],
  },
};

/**
 * Runs once before all scenarios. Seeds URLs and returns the list of short
 * codes — k6 passes this data object as the first argument to every exec fn.
 */
export function setup() {
  const codes = [];

  for (let i = 0; i < SEED_COUNT; i++) {
    const payload = JSON.stringify({
      url: `https://example.com/page/${Math.floor(Math.random() * 1000000)}`,
    });

    const res = http.post(`${BASE_URL}/api/v1/shorten`, payload, {
      headers: { "Content-Type": "application/json" },
    });

    if (res.status === 201) {
      const code = res.json("short_code");
      if (code) codes.push(code);
    }
  }

  console.log(`Seeded ${codes.length} / ${SEED_COUNT} short codes`);
  if (codes.length === 0) {
    throw new Error("Seeding failed — no short codes created. Check server logs.");
  }

  return { codes };
}

/** Redirect to a previously created short URL. */
export function redirectUrl(data) {
  const { codes } = data;
  const code = codes[Math.floor(Math.random() * codes.length)];
  const start = Date.now();

  const res = http.get(`${BASE_URL}/${code}`, {
    redirects: 0, // Don't follow — we measure shortener latency only
  });

  redirectLatency.add(Date.now() - start);

  check(res, {
    "redirect 302": (r) => r.status === 302,
    "has Location":  (r) => r.headers["Location"] !== undefined,
  });
}
