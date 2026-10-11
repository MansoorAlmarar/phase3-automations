import { logger, task } from "@trigger.dev/sdk";
import { python } from "@trigger.dev/python";

// Phase 0 of the cloud stock report: proves Python (price fetch, chart, .docx) runs inside Trigger.dev.
// Manual trigger only. Never throws: failures are returned in the output and logged.
export const pythonHelloTest = task({
  id: "python-hello-test",
  maxDuration: 180,
  retry: { maxAttempts: 1 },
  run: async (payload: { ticker?: string }) => {
    const ticker = (payload?.ticker ?? "ARLO").toUpperCase().replace(/[^A-Z]/g, "").slice(0, 6) || "ARLO";
    logger.info("python test: start", { ticker });
    try {
      const res = await python.runScript("./python/hello_test.py", [ticker]);
      const line = res.stdout.split("\n").find((l) => l.startsWith("RESULT_JSON "));
      if (res.stderr?.trim()) logger.warn("python stderr", { stderr: res.stderr.slice(0, 500) });
      if (!line) {
        logger.error("python test: no result line", { stdout: res.stdout.slice(0, 500) });
        return { ok: false, error: "no RESULT_JSON line in output" };
      }
      const parsed = JSON.parse(line.slice("RESULT_JSON ".length));
      logger.info("python test: done", parsed);
      return { ok: parsed.all_ok === true, ...parsed };
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      logger.error("python test: failed", { error: msg.slice(0, 500) });
      return { ok: false, error: msg.slice(0, 500) };
    }
  },
});
