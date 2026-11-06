import { logger, schedules } from "@trigger.dev/sdk";

// Food deals agent, v2 (Talabat only). Runs unattended every day at 13:00 Dubai time.
// Flow: Firecrawl scrape of each watchlist branch page -> read the page's embedded menu data
// (__NEXT_DATA__) -> compute discount from price vs oldPrice per item -> Telegram alert ONLY if a
// branch has a whole-menu discount. No LLM. Never throws: every step is caught and logged.

// minPct: alert only at this discount or higher (default MIN_PCT = 40% with 1 point of rounding slack)
type Branch = { restaurant: string; branch: string; url: string; minPct?: number };

const WATCHLIST: Branch[] = [
  { restaurant: "Aseer Time", branch: "Al Barsha 1", url: "https://www.talabat.com/uae/restaurant/46986/aseer-time-al-barsha-1?aid=1174" },
  { restaurant: "Aseer Time", branch: "Umm Suqeim 1", url: "https://www.talabat.com/uae/restaurant/693986/aseer-time-umm-suqeim-1?aid=1357" },
  { restaurant: "Aseer Time", branch: "Jumeirah", url: "https://www.talabat.com/uae/restaurant/50573/aseer-time-jumeriah-umm-suqeim-2?aid=1304" },
  { restaurant: "SMK Holy Smokd", branch: "Al Barsha South", url: "https://www.talabat.com/uae/restaurant/704847/smk-al-barsha-south?aid=1210" },
  { restaurant: "Pizza Di Rocco", branch: "Motor City", url: "https://www.talabat.com/uae/restaurant/714287/pizza-di-rocco-dubai-motor-city?aid=1176" },
  { restaurant: "CRST New York Pizza", branch: "Business Bay", url: "https://www.talabat.com/uae/restaurant/799121/crst-new-york-pizza-dh-kitchen-business-bay?aid=1176", minPct: 49 },
  { restaurant: "STIM Burgers & Pizza", branch: "Business Bay", url: "https://www.talabat.com/uae/restaurant/800671/stim-burgers-pizza-dh-kitchen-business-bay?aid=1176", minPct: 49 },
];

// Alert rule: "whole menu" = at least MIN_SHARE of the priced items are discounted by MIN_PCT or more.
// Items shown as "price on selection" (price 0) have no visible price and are ignored in the ratio.
const MIN_PCT = 39; // 40% off, with 1 point of rounding tolerance
const MIN_SHARE = 0.6;
const MIN_PRICED_ITEMS = 10; // guards against tiny or half-loaded menus

const FIRECRAWL_SCRAPE = "https://api.firecrawl.dev/v2/scrape";
const TELEGRAM_LIMIT = 4000;

type BranchResult = {
  branch: Branch;
  pricedItems: number;
  discountedItems: number;
  bigDiscountItems: number;
  medianPct: number;
  wholeMenu: boolean;
  error?: string;
};

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

class OutOfCreditsError extends Error {}

async function fetchPage(url: string, apiKey: string): Promise<string> {
  let lastError = "unknown error";
  for (let attempt = 1; attempt <= 2; attempt++) {
    const t0 = Date.now();
    try {
      const res = await fetch(FIRECRAWL_SCRAPE, {
        method: "POST",
        headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
        body: JSON.stringify({ url, formats: ["rawHtml"], onlyMainContent: false, maxAge: 0 }),
        signal: AbortSignal.timeout(90_000),
      });
      if (res.status === 402) throw new OutOfCreditsError("Firecrawl out of credits (HTTP 402)");
      if (res.status === 429 || res.status >= 500) {
        lastError = `HTTP ${res.status}`;
        logger.warn("scrape: retryable status", { url, attempt, status: res.status });
        await sleep(5000);
        continue;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${(await res.text()).slice(0, 200)}`);
      const json: any = await res.json();
      const html: string | undefined = json?.data?.rawHtml;
      if (!html) throw new Error("no rawHtml in response");
      logger.info("scrape: ok", { url, kb: Math.round(html.length / 1024), ms: Date.now() - t0 });
      return html;
    } catch (e) {
      if (e instanceof OutOfCreditsError) throw e;
      lastError = errMsg(e);
      logger.warn("scrape: attempt failed", { url, attempt, error: lastError, ms: Date.now() - t0 });
      await sleep(3000);
    }
  }
  throw new Error(lastError);
}

function analyse(branch: Branch, html: string): BranchResult {
  const m = html.match(/<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/);
  if (!m) throw new Error("page format changed: no __NEXT_DATA__");
  const data = JSON.parse(m[1]);
  const rawItems: any[] | undefined = data?.props?.pageProps?.initialMenuState?.menuData?.items;
  if (!Array.isArray(rawItems) || rawItems.length === 0) throw new Error("page format changed: no menu items");

  const byId = new Map<number, any>();
  for (const it of rawItems) byId.set(it.id, it);

  let priced = 0;
  const pcts: number[] = [];
  const big: { name: string; price: number; old: number; pct: number }[] = [];
  for (const it of byId.values()) {
    const price = Number(it.price) || 0;
    const old = Number(it.oldPrice) || 0;
    if (price > 0 || old > 0) priced++;
    if (price > 0 && old > price) {
      const pct = (1 - price / old) * 100;
      pcts.push(pct);
      if (pct >= (branch.minPct ?? MIN_PCT)) big.push({ name: String(it.name), price, old, pct });
    }
  }
  const bigPcts = big.map((b) => b.pct).sort((a, b) => a - b);
  const median = bigPcts.length ? bigPcts[Math.floor(bigPcts.length / 2)] : 0;
  const wholeMenu = priced >= MIN_PRICED_ITEMS && big.length / priced >= MIN_SHARE;

  return {
    branch,
    pricedItems: priced,
    discountedItems: pcts.length,
    bigDiscountItems: big.length,
    medianPct: Math.round(median),
    wholeMenu,
  };
}

function formatAlert(dateStr: string, hits: BranchResult[], failed: BranchResult[]): string {
  const lines: string[] = [`Talabat deal, ${dateStr}`, ""];
  for (const r of hits) lines.push(`${r.branch.restaurant} (${r.branch.branch}): ${r.medianPct}% off full menu`);
  if (failed.length) {
    lines.push("", `Could not check: ${failed.map((f) => `${f.branch.restaurant} ${f.branch.branch}`).join(", ")}`);
  }
  const text = lines.join("\n");
  return text.length > TELEGRAM_LIMIT ? text.slice(0, TELEGRAM_LIMIT - 20) + "\n...(trimmed)" : text;
}

async function sendTelegram(text: string): Promise<boolean> {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  const chat = process.env.TELEGRAM_CHAT_ID;
  if (!token || !chat) {
    logger.error("telegram: missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID");
    return false;
  }
  for (let attempt = 1; attempt <= 2; attempt++) {
    try {
      const res = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: chat, text, disable_web_page_preview: true }),
        signal: AbortSignal.timeout(20_000),
      });
      if (res.ok) {
        logger.info("telegram: sent", { chars: text.length, attempt });
        return true;
      }
      logger.warn("telegram: non-OK", { status: res.status, attempt }); // never log the URL (token)
    } catch (e) {
      logger.warn("telegram: attempt failed", { error: errMsg(e).replace(token, "***"), attempt });
    }
    await sleep(3000);
  }
  return false;
}

export const foodDealsDaily = schedules.task({
  id: "food-deals-daily",
  cron: { pattern: "0 13 * * 0,3-6", timezone: "Asia/Dubai" },
  maxDuration: 300,
  retry: { maxAttempts: 1 }, // a task-level retry would re-run (and re-bill) every scrape
  onFailure: async ({ error }) => {
    // Last resort for hard failures (e.g. maxDuration). The run body itself never throws.
    logger.error("task failed hard", { error: errMsg(error) });
    await sendTelegram(`Food deals task FAILED: ${errMsg(error).slice(0, 200)}`);
  },
  run: async (payload) => {
    const started = Date.now();
    const dateStr = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Dubai", dateStyle: "medium" }).format(
      payload.timestamp ?? new Date()
    );
    logger.info("run: start", { date: dateStr, branches: WATCHLIST.map((b) => `${b.restaurant} ${b.branch}`) });

    const results: BranchResult[] = [];
    const apiKey = process.env.FIRECRAWL_API_KEY;
    let outOfCredits = false;

    if (!apiKey) {
      logger.error("config: missing FIRECRAWL_API_KEY");
    } else {
      for (const branch of WATCHLIST) {
        const label = `${branch.restaurant} ${branch.branch}`;
        const blank = { branch, pricedItems: 0, discountedItems: 0, bigDiscountItems: 0, medianPct: 0, wholeMenu: false };
        if (outOfCredits) {
          results.push({ ...blank, error: "skipped, out of credits" });
          continue;
        }
        try {
          const html = await fetchPage(branch.url, apiKey);
          const r = analyse(branch, html);
          logger.info("analyse: done", {
            branch: label,
            pricedItems: r.pricedItems,
            discountedItems: r.discountedItems,
            bigDiscountItems: r.bigDiscountItems,
            medianPct: r.medianPct,
            wholeMenu: r.wholeMenu,
          });
          results.push(r);
        } catch (e) {
          if (e instanceof OutOfCreditsError) outOfCredits = true;
          logger.error("branch failed", { branch: label, error: errMsg(e) });
          results.push({ ...blank, error: errMsg(e) });
        }
        await sleep(1500);
      }
    }

    const hits = results.filter((r) => r.wholeMenu);
    const failed = results.filter((r) => r.error);
    let alert: string | null = null;
    let sent = false;

    try {
      if (hits.length > 0) {
        alert = formatAlert(dateStr, hits, failed);
      } else if (!apiKey || failed.length > 0) {
        // Quiet on no-deal days, but never quiet about breakage.
        const why = !apiKey
          ? "FIRECRAWL_API_KEY missing in Trigger.dev env vars"
          : outOfCredits
            ? "Firecrawl is out of credits"
            : failed.map((f) => `${f.branch.restaurant} ${f.branch.branch}: ${(f.error ?? "").slice(0, 80)}`).join("; ");
        alert = `Food deals check had problems (${dateStr}): ${why}`;
      }
      if (alert) sent = await sendTelegram(alert);
      else logger.info("alert: none today (no whole-menu deals, no errors)");
    } catch (e) {
      logger.error("alert step failed", { error: errMsg(e) });
    }

    const summary = {
      alertNeeded: alert !== null,
      telegramSent: sent,
      wholeMenuDeals: hits.map((h) => `${h.branch.restaurant} ${h.branch.branch}`),
      failedBranches: failed.map((f) => `${f.branch.restaurant} ${f.branch.branch}`),
      credits: results.filter((r) => !r.error).length, // 1 credit per successful scrape
      seconds: Math.round((Date.now() - started) / 1000),
    };
    logger.info("run: done", summary);
    return summary;
  },
});
