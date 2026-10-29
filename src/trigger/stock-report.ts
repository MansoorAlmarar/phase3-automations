import { logger, task } from "@trigger.dev/sdk";
import { python } from "@trigger.dev/python";
import { readFile } from "node:fs/promises";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, basename } from "node:path";

// Cloud stock report. Trigger it with { ticker: "ARLO", note: "holding 864 @ 12.97, 56.8 @ 12.55" }.
// The note is optional: positions in the HOLDINGS_JSON env var are used when it is empty; "watching" forces watching;
// "peers: A, B, C" sets verified peers.
// Flow: validate -> python/build_report.py (data, charts, Halal, AI notes, .docx) -> Telegram (docx + interactive chart + problems).
// Never throws: every step is caught and logged. Spec: STOIC references/stock-report-cloud-spec.md.
// Trigger over HTTP with an idempotency key (ticker + date) so a double tap does not send two reports.

type Payload = {
  ticker: string;
  note?: string;
  noHalal?: boolean; // skip the Osooli check (saves ~2 Firecrawl credits)
  noAi?: boolean; // skip the AI notes
  noWeb?: boolean; // AI notes without the 2 Firecrawl web searches
};

const TELEGRAM_LIMIT = 4000;
const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function sendText(text: string): Promise<boolean> {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  const chat = process.env.TELEGRAM_CHAT_ID;
  if (!token || !chat) {
    logger.error("telegram: missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID");
    return false;
  }
  const body = text.length > TELEGRAM_LIMIT ? text.slice(0, TELEGRAM_LIMIT - 20) + "\n...(trimmed)" : text;
  for (let attempt = 1; attempt <= 2; attempt++) {
    try {
      const res = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: chat, text: body, disable_web_page_preview: true }),
        signal: AbortSignal.timeout(20_000),
      });
      if (res.ok) {
        logger.info("telegram: message sent", { chars: body.length, attempt });
        return true;
      }
      logger.warn("telegram: message non-OK", { status: res.status, attempt }); // never log the URL (token)
    } catch (e) {
      logger.warn("telegram: message failed", { error: errMsg(e).replace(token, "***"), attempt });
    }
    await sleep(3000);
  }
  return false;
}

async function sendFile(path: string, caption: string, mime: string, kind: "document" | "photo" = "document"): Promise<boolean> {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  const chat = process.env.TELEGRAM_CHAT_ID;
  if (!token || !chat) return false;
  let bytes: Buffer;
  try {
    bytes = await readFile(path);
  } catch (e) {
    logger.error("telegram: cannot read file", { path: basename(path), error: errMsg(e) });
    return false;
  }
  for (let attempt = 1; attempt <= 2; attempt++) {
    try {
      const form = new FormData();
      form.append("chat_id", chat);
      form.append("caption", caption.slice(0, 1000));
      form.append(kind, new Blob([new Uint8Array(bytes)], { type: mime }), basename(path));
      const res = await fetch(`https://api.telegram.org/bot${token}/${kind === "photo" ? "sendPhoto" : "sendDocument"}`, {
        method: "POST",
        body: form,
        signal: AbortSignal.timeout(60_000),
      });
      if (res.ok) {
        logger.info("telegram: file sent", { file: basename(path), kb: Math.round(bytes.length / 1024), attempt });
        return true;
      }
      logger.warn("telegram: file non-OK", { status: res.status, attempt });
    } catch (e) {
      logger.warn("telegram: file failed", { error: errMsg(e).replace(token, "***"), attempt });
    }
    await sleep(3000);
  }
  return false;
}

function validate(p: Payload): { ticker: string; note: string } | string {
  const ticker = String(p?.ticker ?? "").trim().toUpperCase();
  if (!/^[A-Z]{1,6}$/.test(ticker)) return `bad ticker "${String(p?.ticker ?? "").slice(0, 12)}"`;
  const note = String(p?.note ?? "").trim().slice(0, 300);
  return { ticker, note };
}

export const stockReportCloud = task({
  id: "stock-report-cloud",
  machine: "small-2x", // pandas + matplotlib need more than the default memory
  maxDuration: 420,
  retry: { maxAttempts: 1 }, // a retry would re-bill the scrapes and AI calls
  onFailure: async ({ payload, error }: { payload: Payload; error: unknown }) => {
    logger.error("task failed hard", { error: errMsg(error) });
    const t = String(payload?.ticker ?? "?").slice(0, 8).toUpperCase();
    await sendText(`Stock report task FAILED for ${t}: ${errMsg(error).slice(0, 200)}`);
  },
  run: async (payload: Payload, { ctx }) => {
    const started = Date.now();
    const v = validate(payload);
    if (typeof v === "string") {
      logger.error("validate: rejected", { reason: v });
      await sendText(`Stock report not started: ${v}. Send a ticker of 1 to 6 letters.`);
      return { ok: false, error: v };
    }
    const { ticker, note } = v;
    logger.info("run: start", { ticker, hasNote: note.length > 0, noHalal: !!payload.noHalal, noAi: !!payload.noAi, noWeb: !!payload.noWeb });

    const warnings: string[] = [];
    let result: any = null;
    try {
      const outDir = await mkdtemp(join(tmpdir(), `report-${ticker}-`));
      const args = [ticker, "--out", outDir];
      if (note) args.push(`--note=${note}`); // one token: a note that starts with "-" would otherwise be read as a flag
      if (payload.noHalal) args.push("--no-halal");
      if (payload.noAi) args.push("--no-ai");
      if (payload.noWeb) args.push("--no-web");
      const res = await python.runScript("./python/build_report.py", args);
      for (const line of (res.stderr ?? "").split("\n")) {
        if (line.trim() && !line.includes("crumb")) logger.info("python", { line: line.slice(0, 300) });
      }
      const line = (res.stdout ?? "").split("\n").find((l) => l.startsWith("RESULT_JSON "));
      if (!line) throw new Error("no RESULT_JSON from python (stdout: " + (res.stdout ?? "").slice(0, 200) + ")");
      result = JSON.parse(line.slice("RESULT_JSON ".length));
    } catch (e) {
      logger.error("python step failed", { error: errMsg(e).slice(0, 500) });
      await sendText(`Stock report for ${ticker} failed: ${errMsg(e).slice(0, 200)}`);
      return { ok: false, ticker, error: errMsg(e).slice(0, 200) };
    }

    logger.info("python: done", {
      ok: result.ok, error: result.error, seconds: result.seconds, steps: result.steps,
    });

    if (!result.ok) {
      const why = result.error === "no price data" ? "no price data found (check the ticker)" : String(result.error ?? "unknown error");
      await sendText(`Stock report for ${ticker} failed: ${why}`);
      return { ok: false, ticker, error: why };
    }

    // Problems that did not stop the report: tell Mansoor, in one message.
    const steps = result.steps ?? {};
    if (result.halal?.login_expired) warnings.push("Osooli login expired. Halal was NOT checked. Re-login needed (tell STOIC).");
    else if (result.halal?.status === "unchecked" && !payload.noHalal) warnings.push(`Halal not checked: ${String(result.halal?.notes ?? "").slice(0, 160)}`);
    if (steps.ai && steps.ai.ok === false && !payload.noAi) warnings.push(`AI notes unavailable: ${String(steps.ai.error ?? "").slice(0, 120)}`);
    if (steps.snapshot?.errors?.length) warnings.push(`Data gaps: ${steps.snapshot.errors.slice(0, 3).join("; ").slice(0, 200)}`);
    if (steps.charts?.errors?.length) warnings.push(`Chart problems: ${steps.charts.errors.slice(0, 2).join("; ").slice(0, 160)}`);

    const docxOk = await sendFile(
      result.docx,
      String(result.caption ?? `${ticker} report`),
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    );
    // No chart photos in Telegram: the interactive HTML (opened in the Documents app) replaces them. Charts stay in the Word report.
    let htmlOk = false;
    if (result.html)
      htmlOk = await sendFile(
        result.html,
        `${ticker} interactive chart. Phone: tap Share, then Save to Documents (Readdle app), and open it there. PC: open in Chrome or Edge.`,
        "text/html"
      );
    if (!docxOk) warnings.push("Could not send the Word report to Telegram.");
    if (result.html && !htmlOk) warnings.push("Could not send the interactive chart.");
    if (warnings.length) await sendText(`${ticker} report notes:\n- ${warnings.join("\n- ")}`);

    const summary = {
      ok: docxOk,
      ticker,
      docxSent: docxOk,
      htmlSent: htmlOk,
      halal: result.halal?.status,
      ai: steps.ai?.model ?? null,
      aiDropped: steps.ai?.dropped?.length ?? 0,
      warnings: warnings.length,
      pythonSeconds: result.seconds,
      seconds: Math.round((Date.now() - started) / 1000),
      runId: ctx.run.id,
    };
    logger.info("run: done", summary);
    return summary;
  },
});
