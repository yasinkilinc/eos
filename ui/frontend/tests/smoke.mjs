#!/usr/bin/env node
/**
 * Browser smoke test for the eos-ui frontend.
 *
 * This is NOT part of `npm run build`: it drives a real browser against a
 * already-running server, so start one yourself first.
 *
 *   # 1. build the frontend into ui/static/
 *   cd ui/frontend && npm run build
 *
 *   # 2. start the API (serves ui/static at /)
 *   cd ../.. && uvicorn ui.server:app --host 127.0.0.1 --port 8000
 *
 *   # 3. run the smoke test
 *   cd ui/frontend && npm run smoke
 *   SMOKE_BASE_URL=http://127.0.0.1:5173 npm run smoke   # against the Vite dev server
 *
 * The workspace requires at least one registered EOS instance; with an empty
 * database the "instance list renders" step fails by design.
 *
 * Env:
 *   SMOKE_BASE_URL           default http://127.0.0.1:8000
 *   SMOKE_STEP_TIMEOUT_MS    per-selector wait, default 20000
 *   SMOKE_RUN_TIMEOUT_MS     hard watchdog for the whole run, default 180000
 *
 * Failure rules: any console error / uncaught page error, any network-level
 * failure on an /api/* request, and any /api/* response with a 5xx status. A
 * 404 from a Graphify endpoint is a legitimate "no artifact here" answer and is
 * not treated as a failure, and neither is the browser's unprompted
 * /favicon.ico probe, which the API server does not serve.
 *
 * Exits non-zero on any failed step.
 */
import puppeteer from "puppeteer";

const BASE_URL = (process.env.SMOKE_BASE_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");
const STEP_TIMEOUT_MS = Number(process.env.SMOKE_STEP_TIMEOUT_MS || 20000);
const RUN_TIMEOUT_MS = Number(process.env.SMOKE_RUN_TIMEOUT_MS || 180000);

const failures = [];
const consoleErrors = [];
const apiFailures = [];

let browserRef = null;

function isApiUrl(url) {
  try {
    return new URL(url).pathname.startsWith("/api/");
  } catch {
    return false;
  }
}

/** The browser requests /favicon.ico on its own; the API server does not serve one. */
function isFaviconUrl(url) {
  try {
    return new URL(url).pathname === "/favicon.ico";
  } catch {
    return false;
  }
}

async function step(name, fn) {
  try {
    await fn();
    console.log(`PASS  ${name}`);
    return true;
  } catch (err) {
    const message = err && err.message ? err.message : String(err);
    console.log(`FAIL  ${name}: ${message}`);
    failures.push(`${name}: ${message}`);
    return false;
  }
}

function skip(name, reason) {
  console.log(`FAIL  ${name}: skipped (${reason})`);
  failures.push(`${name}: skipped (${reason})`);
}

async function run(page) {
  await step("loads the app shell", async () => {
    const response = await page.goto(BASE_URL, {
      waitUntil: "domcontentloaded",
      timeout: STEP_TIMEOUT_MS,
    });
    if (!response) throw new Error(`no response from ${BASE_URL}`);
    if (!response.ok()) throw new Error(`${response.status()} from ${BASE_URL}`);
    await page.waitForSelector("#root", { timeout: STEP_TIMEOUT_MS });
  });

  const listed = await step("instance list renders", async () => {
    await page.waitForSelector("table.instances tr.row", { timeout: STEP_TIMEOUT_MS });
    const rows = await page.$$("table.instances tr.row");
    if (rows.length === 0) throw new Error("no instance rows");
  });

  if (!listed) {
    skip("opens the first instance", "instance list did not render");
    skip("toggles to the Graphify provider", "instance list did not render");
  } else {
    const opened = await step("opens the first instance", async () => {
      await page.click("table.instances tr.row");
      await page.waitForSelector(".graph-toolbar", { timeout: STEP_TIMEOUT_MS });
    });

    if (!opened) {
      skip("toggles to the Graphify provider", "instance graph did not open");
    } else {
      await step("toggles to the Graphify provider", async () => {
        await page.click('[data-testid="provider-graphify"]');
        await page.waitForSelector('[data-testid="graphify-summary"]', {
          timeout: STEP_TIMEOUT_MS,
        });
      });

      await step("returns to the instance list", async () => {
        await page.click('[data-testid="back"]');
        await page.waitForSelector("table.instances", { timeout: STEP_TIMEOUT_MS });
      });
    }
  }

  await step("workspace overview table renders", async () => {
    await page.waitForSelector('[data-testid="nav-workspace"]', { timeout: STEP_TIMEOUT_MS });
    await page.click('[data-testid="nav-workspace"]');
    await page.waitForSelector('[data-testid="workspace"]', { timeout: STEP_TIMEOUT_MS });
    await page.waitForSelector('[data-testid="workspace-projects"]', {
      timeout: STEP_TIMEOUT_MS,
    });
    const rows = await page.$$('[data-testid="workspace-project-row"]');
    if (rows.length === 0) throw new Error("workspace project table has no rows");
  });

  await step("no console errors", async () => {
    if (consoleErrors.length > 0) throw new Error(consoleErrors.join(" | "));
  });

  await step("no failed /api requests", async () => {
    if (apiFailures.length > 0) throw new Error(apiFailures.join(" | "));
  });
}

async function main() {
  console.log(`smoke: ${BASE_URL}`);
  const browser = await puppeteer.launch({
    headless: true,
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  browserRef = browser;
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(STEP_TIMEOUT_MS);

    page.on("console", (msg) => {
      if (msg.type() !== "error") return;
      const location = msg.location();
      if (location && isFaviconUrl(location.url)) return;
      consoleErrors.push(msg.text());
    });
    page.on("pageerror", (err) => {
      consoleErrors.push(`pageerror: ${err.message}`);
    });
    page.on("requestfailed", (req) => {
      if (!isApiUrl(req.url())) return;
      const failure = req.failure();
      apiFailures.push(`${req.method()} ${req.url()} ${failure ? failure.errorText : "failed"}`);
    });
    page.on("response", (res) => {
      if (isApiUrl(res.url()) && res.status() >= 500) {
        apiFailures.push(`${res.status()} ${res.url()}`);
      }
    });

    await run(page);
  } finally {
    browserRef = null;
    await browser.close().catch(() => {
      // Best-effort teardown; the exit code below still reports the real result.
    });
  }
}

// Hard watchdog so a hung page can never wedge CI. unref() keeps it from
// holding the event loop open once the run finishes normally.
const watchdog = setTimeout(() => {
  console.log(`FAIL  run timed out after ${RUN_TIMEOUT_MS}ms`);
  if (browserRef) {
    browserRef.close().catch(() => {});
  }
  process.exit(1);
}, RUN_TIMEOUT_MS);
if (typeof watchdog.unref === "function") watchdog.unref();

try {
  await main();
} catch (err) {
  const message = err && err.message ? err.message : String(err);
  console.log(`FAIL  smoke run aborted: ${message}`);
  failures.push(`smoke run aborted: ${message}`);
}

clearTimeout(watchdog);

if (failures.length > 0) {
  console.log(`\n${failures.length} failure(s):`);
  for (const failure of failures) console.log(`  - ${failure}`);
  process.exitCode = 1;
} else {
  console.log("\nall steps passed");
  process.exitCode = 0;
}
