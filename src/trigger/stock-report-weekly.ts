import { logger, schedules, idempotencyKeys } from "@trigger.dev/sdk";
import { stockReportCloud } from "./stock-report";

// Weekly stock report for the positions Mansoor holds. Monday 17:00 Dubai.
// Everything else is on demand: Telegram `/report TICKER`.
// Keep this list short: each full report costs about 6 Firecrawl credits (see the spec, section 12b).
const WEEKLY_TICKERS = ["ARLO"];

export const stockReportWeekly = schedules.task({
  id: "stock-report-weekly",
  cron: { pattern: "0 17 * * 1", timezone: "Asia/Dubai" },
  retry: { maxAttempts: 1 },
  run: async (payload) => {
    const day = payload.timestamp.toISOString().slice(0, 10);
    const started: string[] = [];
    for (const ticker of WEEKLY_TICKERS) {
      try {
        const key = await idempotencyKeys.create(`weekly-${ticker}-${day}`);
        const handle = await stockReportCloud.trigger({ ticker }, { idempotencyKey: key });
        logger.info("weekly: report started", { ticker, runId: handle.id });
        started.push(ticker);
      } catch (e) {
        logger.error("weekly: could not start report", { ticker, error: e instanceof Error ? e.message : String(e) });
      }
    }
    return { day, started };
  },
});
