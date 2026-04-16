/**
 * k6 load test for URL Shortener
 *
 * Usage:
 *   k6 run loadtest/script.js
 *
 * Two scenarios:
 *   1. baseline  — ramp to 500 req/s, target p99 < 50ms
 *   2. with_cache — same load after cache is warm, target p99 < 15ms
 *
 * Set BASE_URL env var to override the target host:
 *   k6 run -e BASE_URL=http://localhost:8000 loadtest/script.js
 */

import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Trend } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";

// Custom metrics for resume-worthy numbers
const shortCodes = new Counter("short_codes_created");
const redirectLatency = new Trend("redirect_latency_ms", true);

// Pre-seeded short codes for the redirect phase
const seedCodes = [];

export const options = {
  scenarios: {
    // Phase 1: Create URLs — populates seedCodes, warms the DB
    seed_urls: {
      executor: "constant-arrival-rate",
      rate: 50,
      timeUnit: "1s",
      duration: "20s",
      preAllocatedVUs: 20,
      maxVUs: 50,
      tags: { phase: "seed" },
      exec: "seedUrl",
    },

    // Phase 2: Baseline — 500 req/s, cold or partially warm cache
    baseline: {
      executor: "constant-arrival-rate",
      rate: 500,
      timeUnit: "1s",
      duration: "60s",
      preAllocatedVUs: 100,
      maxVUs: 300,
      startTime: "25s",   // starts after seeding
      tags: { phase: "baseline" },
      exec: "redirectUrl",
    },

    // Phase 3: Cache warm — same rate, cache should be fully warm
    with_cache: {
      executor: "constant-arrival-rate",
      rate: 500,
      timeUnit: "1s",
      duration: "60s",
      preAllocatedVUs: 100,
      maxVUs: 300,
      startTime: "95s",   // starts after baseline
      tags: { phase: "with_cache" },
      exec: "redirectUrl",
    },
  },

  thresholds: {
    // Baseline: p99 under 50ms
    "http_req_duration{phase:baseline}": ["p(99)<50"],
    // With cache: p99 under 15ms
    "http_req_duration{phase:with_cache}": ["p(99)<15"],
    // Overall error rate under 1%
    "http_req_failed": ["rate<0.01"],
  },
};

/** Create a short URL and stash the code for redirect tests. */
export function seedUrl() {
  const payload = JSON.stringify({
    url: `https://example.com/page/${Math.floor(Math.random() * 100000)}`,
  });

  const res = http.post(`${BASE_URL}/api/v1/shorten`, payload, {
    headers: { "Content-Type": "application/json" },
  });

  check(res, {
    "shorten status 201": (r) => r.status === 201,
    "has short_code":     (r) => r.json("short_code") !== undefined,
  });

  if (res.status === 201) {
    seedCodes.push(res.json("short_code"));
    shortCodes.add(1);
  }

  sleep(0.01);
}

/** Redirect to a previously created short URL. */
export function redirectUrl() {
  if (seedCodes.length === 0) {
    sleep(0.1);
    return;
  }

  const code = seedCodes[Math.floor(Math.random() * seedCodes.length)];
  const start = Date.now();

  const res = http.get(`${BASE_URL}/${code}`, {
    redirects: 0, // Don't follow — we measure the shortener latency only
  });

  redirectLatency.add(Date.now() - start);

  check(res, {
    "redirect 302": (r) => r.status === 302,
    "has Location":  (r) => r.headers["Location"] !== undefined,
  });
}
