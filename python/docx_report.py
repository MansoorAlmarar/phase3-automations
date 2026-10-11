"""Build the Word report. Every number comes from the snapshot/plan. Text from the AI notes is optional.

Sections follow references/stock-report-cloud-spec.md (STOIC repo), section 7.
No buy/sell calls anywhere: the report applies Mansoor's rules, he decides.
"""
import os
from datetime import date, timedelta

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

HALAL_RECHECK_DAYS = 7
GREEN, RED, AMBER, GREY, BLUE = "DCFCE7", "FEE2E2", "FEF3C7", "F3F4F6", "DBEAFE"
INK = RGBColor(0x1F, 0x29, 0x37)
MUTED = RGBColor(0x6B, 0x72, 0x80)


# ---------- formatting helpers ----------
def money(x, d=2):
    if x is None:
        return "n/a"
    return f"-${abs(x):,.{d}f}" if x < 0 else f"${x:,.{d}f}"


def pct(x, d=1, sign=True):
    return "n/a" if x is None else (f"{x:+.{d}f}%" if sign else f"{x:.{d}f}%")


def big(x):
    if x is None:
        return "n/a"
    for unit, div in [("T", 1e12), ("B", 1e9), ("M", 1e6)]:
        if abs(x) >= div:
            return f"${x / div:,.2f}{unit}"
    return f"${x:,.0f}"


def fdate(iso):
    """2025-11-20 -> 20 Nov 2025"""
    try:
        d = date.fromisoformat(str(iso)[:10])
        return f"{d.day} {d.strftime('%b %Y')}"
    except (ValueError, TypeError):
        return str(iso or "n/a")


def shade(cell, hex_fill):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tcPr.append(shd)


def para(doc_or_cell, text="", bold=False, size=None, color=None, italic=False, after=4, align=None):
    p = doc_or_cell.add_paragraph()
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.space_before = Pt(0)
    if align:
        p.alignment = align
    if text:
        r = p.add_run(text)
        r.bold, r.italic = bold, italic
        if size:
            r.font.size = Pt(size)
        if color:
            r.font.color.rgb = color
    return p


def add_link(paragraph, text, url):
    """Clickable short link (python-docx has no built-in hyperlink)."""
    rid = paragraph.part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                                   is_external=True)
    h = OxmlElement("w:hyperlink")
    h.set(qn("r:id"), rid)
    r = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    c = OxmlElement("w:color")
    c.set(qn("w:val"), "2563EB")
    u = OxmlElement("w:u")
    u.set(qn("w:val"), "single")
    sz = OxmlElement("w:sz")
    sz.set(qn("w:val"), "18")
    for el in (c, u, sz):
        rpr.append(el)
    r.append(rpr)
    t = OxmlElement("w:t")
    t.text = text
    r.append(t)
    h.append(r)
    paragraph._p.append(h)


def heading(doc, text):
    p = para(doc, text, bold=True, size=13, color=INK, after=3)
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.keep_with_next = True
    return p


def kv_table(doc, rows, widths=(1.7, 5.3)):
    t = doc.add_table(rows=0, cols=2)
    t.style = "Table Grid"
    for k, v in rows:
        cells = t.add_row().cells
        cells[0].width, cells[1].width = Inches(widths[0]), Inches(widths[1])
        shade(cells[0], GREY)
        cells[0].paragraphs[0].add_run(k).bold = True
        cells[1].paragraphs[0].add_run(str(v))
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def grid_table(doc, header, rows, widths=None):
    t = doc.add_table(rows=1, cols=len(header))
    t.style = "Table Grid"
    for i, h in enumerate(header):
        c = t.rows[0].cells[i]
        shade(c, GREY)
        c.paragraphs[0].add_run(h).bold = True
    for r in rows:
        cells = t.add_row().cells
        for i, v in enumerate(r):
            cells[i].paragraphs[0].add_run(str(v))
    if widths:
        for row in t.rows:
            for i, w in enumerate(widths):
                row.cells[i].width = Inches(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def banner(doc, text, fill, bold=True):
    t = doc.add_table(rows=1, cols=1)
    t.style = "Table Grid"
    c = t.rows[0].cells[0]
    shade(c, fill)
    r = c.paragraphs[0].add_run(text)
    r.bold = bold
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


# ---------- content builders ----------
def rule_flags(snap, plan, halal):
    """Risks computed from Mansoor's rules and the numbers. Used when the AI notes are missing, and always shown."""
    flags = []
    t = snap["technicals"]
    rg = t.get("range") or {}
    nx = (snap.get("earnings") or {}).get("next") or {}
    if halal.get("status") != "compliant":
        flags.append(("high", "Halal not confirmed", "Do not use this plan until the Halal status is checked."))
    if rg.get("broken"):
        flags.append(("high", "Floor broken", f"{rg['broken']['closes_below']} daily closes under the floor, "
                                              f"last on {fdate(rg['broken']['last_close_below'])}."))
    if plan.get("loss_pct") is not None and abs(plan["loss_pct"]) > plan["goal_pct"]:
        flags.append(("high", "Stop further than the goal", f"Stop loses {abs(plan['loss_pct']):.1f}% to gain {plan['goal_pct']}%."))
    if plan.get("stop") is None:
        flags.append(("medium", "No stop level found", "No floor or support below the entry. Set one manually."))
    if nx.get("days_away") is not None and nx["days_away"] <= 45:
        flags.append(("medium", "Earnings inside the window", f"{fdate(nx['date'])} ({nx['days_away']} days). Earnings move price hard either way."))
    if plan.get("in_way"):
        flags.append(("medium", "Resistance before the goal", "At " + ", ".join(money(p) for p in plan["in_way"]) + "."))
    adj = (((snap.get("earnings") or {}).get("eps_growth") or {}).get("quarter") or {}).get("now")
    gaap = (snap.get("gaap_eps") or {}).get("value")
    if adj and gaap is not None and adj > 0 and gaap < adj * 0.5:
        flags.append(("medium", "Earnings quality", f"Adjusted EPS {money(adj)} vs GAAP {money(gaap)} last quarter. Check what the adjustments are."))
    rsi = t.get("rsi14")
    if rsi is not None and rsi >= 70:
        flags.append(("medium", "RSI overbought", f"RSI 14 is {rsi}."))
    if (snap.get("technicals") or {}).get("sma200") and plan.get("price") and plan["price"] < t["sma200"]:
        flags.append(("low", "Below the 200-day average", f"Price {money(plan['price'])} vs SMA 200 {money(t['sma200'])}."))
    return flags


def auto_headline(snap, plan):
    t = snap["technicals"]
    rg = t.get("range") or {}
    bits = [f"{snap['ticker']} at {money(plan['price'])}"]
    if rg.get("broken"):
        bits.append("floor broken")
    elif rg.get("floor"):
        bits.append(f"{rg.get('now', '')} ({money(rg['floor'][0])}-{money(rg['floor'][1])})")
    if t.get("rsi14") is not None:
        bits.append(f"RSI {t['rsi14']}")
    if plan.get("reward_risk"):
        bits.append(f"reward : risk {plan['reward_risk']} : 1")
    return ", ".join(bits) + "."


def eps_rows(snap, m, fn):
    """EPS growth rows. Adjusted EPS from Finnhub's reported quarters; GAAP from Yahoo for comparison."""
    g = (snap.get("earnings") or {}).get("eps_growth") or {}
    rows = []

    def one(label, d, note_key):
        if not d:
            return
        gp = d.get("growth_pct")
        txt = f"{money(d['now'])} vs {money(d['year_ago'])}" + (f" ({pct(gp)})" if gp is not None else " (not meaningful: prior EPS was zero or negative)")
        rows.append((label, txt, fn.get(note_key, "")))

    one("EPS (adjusted), latest quarter vs year ago", g.get("quarter"), "eps_growth")
    one("EPS (adjusted), last 4 quarters vs previous 4", g.get("ttm"), "eps_growth_ttm")
    ge = snap.get("gaap_eps") or {}
    if ge.get("value") is not None:
        rows.append(("EPS (GAAP, diluted), latest quarter", money(ge["value"]), "Adjusted EPS excludes some costs. A big gap is worth a look."))
    if not rows:
        rows.append(("EPS growth", pct(m.get("epsGrowthTTMYoy")), "No EPS history returned by the data sources."))
    return rows


def add_halal(doc, halal):
    status = halal.get("status", "unchecked")
    label = {"compliant": "HALAL (compliant)", "not_compliant": "NOT HALAL", "doubtful": "DOUBTFUL: decide manually",
             "unchecked": "NOT CHECKED: verify manually"}.get(status, status)
    fill = {"compliant": GREEN, "not_compliant": RED, "doubtful": AMBER}.get(status, AMBER)
    meta = []
    if halal.get("purification_pct") is not None:
        meta.append(f"purification {halal['purification_pct']}%")
    if halal.get("data_date"):
        meta.append(f"data {halal['data_date']}")
    if halal.get("checked"):
        meta.append(f"checked {halal['checked']}")
        recheck = (date.fromisoformat(halal["checked"]) + timedelta(days=HALAL_RECHECK_DAYS)).isoformat()
        meta.append(f"re-check by {recheck}")
    banner(doc, label + ("  |  " + " | ".join(meta) if meta else ""), fill)
    v = halal.get("verdicts") or {}
    if v:
        grid_table(doc, ["Screener", "Verdict"], [(k, val) for k, val in v.items()], widths=(2, 2))
    for r in halal.get("reasons") or []:
        para(doc, f"{r.get('screener')}: {r.get('reason')}", size=10, after=2)
    lk = halal.get("last_known")
    if lk and (status in ("unchecked", "doubtful") or not v):
        para(doc, f"Last known status: {lk.get('status')} on {lk.get('date')}. {lk.get('note', '')}", size=10, italic=True)
    if halal.get("notes"):
        para(doc, halal["notes"], size=10)


def add_summary(doc, snap, plan, halal, flags):
    """Status now, flags, and things to consider. Numbers only: no buy or sell calls."""
    t = snap["technicals"]
    rg = t.get("range") or {}
    heading(doc, "Summary")
    status = []
    if plan["position"] == "holding":
        status.append(f"Holding at an average {money(plan['entry'])}. Price {money(plan['price'])} ({pct(plan['pnl_pct'])})"
                      + (f", {money(plan['pnl_abs'])} on {plan['shares']:g} shares." if plan.get("pnl_abs") is not None else "."))
        if plan.get("stop"):
            status.append(f"Goal {money(plan['target'])} is {pct(plan['to_goal_pct'])} away. Computed stop {money(plan['stop'])} is "
                          f"{pct(plan['to_stop_pct'])} away (reward : risk from your entry {plan['reward_risk']} : 1).")
        if plan.get("plan_note"):
            status.append(f"Your plan: {plan['plan_note']}")
    else:
        status.append(f"Not in the trade. Price {money(plan['price'])}, {rg.get('now', 'no floor found')}.")
    status.append("Halal: " + {"compliant": "compliant", "not_compliant": "NOT compliant", "doubtful": "doubtful, decide manually"}.get(
        halal.get("status"), "not checked in this run") + ".")
    para(doc, "Status now", bold=True, after=1)
    for x in status:
        para(doc, "- " + x, after=1)

    para(doc, "Flags", bold=True, after=1).paragraph_format.space_before = Pt(6)
    if flags:
        for lvl, ti, d in flags:
            para(doc, f"- {lvl.upper()}: {ti}. {d}", after=1)
    else:
        para(doc, "- No rule-based flags.", after=1)

    ec = plan.get("entry_check")
    para(doc, "To consider: entry price", bold=True, after=1).paragraph_format.space_before = Pt(6)
    if ec:
        lead = "If you were entering today at" if plan["position"] == "watching" else "For a new entry today at"
        para(doc, f"- {lead} {money(ec['price'])}: stop {money(ec['stop'])} ({pct(ec['loss_pct'])}, {plan['stop_buffer_pct']}% below "
                  f"{ec['stop_ref_label']}), reward : risk {ec['reward_risk']} : 1.", after=1)
        d1 = ec["pct_above_1to1"]
        para(doc, f"- Entry price where reward : risk is 1 : 1 with that stop: {money(ec['entry_for_1to1'])}. "
                  + (f"Price now is {d1:.1f}% above it." if d1 > 0 else f"Price now is {abs(d1):.1f}% below it, so reward : risk is better than 1 : 1."),
             after=1)
        if ec.get("floor_top"):
            d2 = ec["pct_above_floor_top"]
            if d2 > 0 and ec.get("reward_risk_at_floor_top"):
                para(doc, f"- At the top of the floor ({money(ec['floor_top'])}, price is {d2:.1f}% above it) "
                          f"reward : risk would be {ec['reward_risk_at_floor_top']} : 1.", after=1)
            else:
                para(doc, f"- Price is inside the floor zone (top {money(ec['floor_top'])}).", after=1)
        para(doc, "- Closer to the floor means a smaller stop and better reward : risk, but the floor can also break. "
                  "Past bounces don't guarantee the next one.", after=1, italic=True, color=MUTED)
    else:
        para(doc, "- No floor or support below price, so no entry check.", after=1)


def build_docx(snap, plan, halal, notes, charts, path, note_text="", note_warning=None):
    notes = notes or {}
    if notes.get("_error"):
        notes = {}
    t, p, q = snap["technicals"], snap.get("profile") or {}, snap.get("quote") or {}
    m = snap.get("metrics") or {}
    rg = t.get("range") or {}
    nx = (snap.get("earnings") or {}).get("next") or {}

    doc = Document()
    sec = doc.sections[0]
    sec.left_margin = sec.right_margin = Inches(0.7)
    sec.top_margin = sec.bottom_margin = Inches(0.6)
    st = doc.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(10.5)

    # 1. Header
    para(doc, f"{snap['ticker']}  |  {p.get('companyName') or ''}", bold=True, size=20, color=INK, after=0)
    chg = q.get("change_pct")
    stale = t.get("as_of") and t["as_of"] != snap["date"]
    para(doc, f"{money(plan['price'])} ({pct(chg, 2)} {'last session' if stale else 'today'})  |  {p.get('sector') or ''}  |  {fdate(snap['date'])}",
         size=10.5, color=MUTED, after=2)
    if stale:
        para(doc, f"Market closed. Prices are from the last close ({fdate(t['as_of'])}).", size=10, color=MUTED, after=2)
    if plan["position"] == "holding":
        sh = f"{plan['shares']:g} shares, " if plan.get("shares") else ""
        lots = " + ".join(f"{s:g} @ {money(pr)}" for s, pr in plan["lots"])
        pos = f"Holding {sh}average entry {money(plan['entry'])}" + (f" ({lots})" if len(plan["lots"]) > 1 else "")
    else:
        pos = f"Watching (plan uses price now, {money(plan['entry'])})"
    para(doc, f"Position: {pos}", size=10.5, after=2)
    if note_text:
        para(doc, f"Note: {note_text}", size=9.5, color=MUTED, after=2)
    if note_warning:
        para(doc, note_warning, size=10, color=RGBColor(0xB4, 0x53, 0x09), after=2)

    # 2. Headline
    heading(doc, "Headline")
    para(doc, notes.get("headline") or auto_headline(snap, plan), size=11.5, bold=True)

    # 3. Halal
    heading(doc, "Halal")
    add_halal(doc, halal)

    # 4. Floor and bounces
    heading(doc, "Floor and bounces (1 year)")
    if rg.get("floor"):
        para(doc, f"Floor {money(rg['floor'][0])} to {money(rg['floor'][1])}, {rg.get('touches')} touches. "
                  f"Price: {rg.get('now')}.", after=3)
        if rg.get("bounces"):
            grid_table(doc, ["Floor touch", "Low", "Peak date", "Peak", "Gain", "Days"],
                       [(fdate(b["low_date"]), money(b["low"]), fdate(b["peak_date"]), money(b["peak"]), pct(b["gain_pct"]), b["days"])
                        for b in rg["bounces"]],
                       widths=(1.3, 0.9, 1.3, 0.9, 0.9, 0.7))
        if rg.get("broken"):
            para(doc, f"Floor broken: {rg['broken']['closes_below']} closes under it, "
                      f"{fdate(rg['broken']['first_close_below'])} to {fdate(rg['broken']['last_close_below'])}, "
                      f"lowest {money(rg['broken']['lowest_close'])}.", color=RGBColor(0xB9, 0x1C, 0x1C))
        else:
            para(doc, f"{rg.get('verdict')}: {rg.get('good_bounces')} bounces of 10%+"
                      + (f", average {pct(rg.get('avg_bounce_pct'))} in about {rg.get('avg_days_to_peak')} days" if rg.get("avg_bounce_pct") else "")
                      + f". 1-year trend {pct(rg.get('trend_1y_pct'), 0)}. Past bounces don't guarantee the next one.", size=10)
    else:
        para(doc, "No repeating floor found in the last year.")

    # 5. Plan numbers
    heading(doc, "Plan numbers (your rules)")
    pb = plan.get("past_bounces") or {}
    rows = [
        ("Entry", f"{money(plan['entry'])} ({'your position' if plan['position'] == 'holding' else 'price now'})"),
        ("Profit goal", f"+{plan['goal_pct']}% = {money(plan['target'])}"
         + (f"  (resistance at {', '.join(money(x) for x in plan['in_way'])} on the way)" if plan.get("in_way") else "")),
        ("Stop", (f"{money(plan['stop'])} ({pct(plan['loss_pct'])}), {plan['stop_buffer_pct']}% below {plan['stop_ref_label']} {money(plan['stop_ref'])}"
                  if plan.get("stop") else "No support below entry. Set one manually.")),
        ("Reward : risk", f"{plan['reward_risk']} : 1" if plan.get("reward_risk") else "n/a"),
    ]
    if pb.get("done"):
        rows.append(("Past bounces", f"{pb['hit']} of {pb['done']} reached +{plan['goal_pct']}% "
                                      f"(best {pct(pb['best'])}, worst {pct(pb['worst'])})"))
    if plan["position"] == "holding":
        hold = [("Position", (f"{plan['shares']:g} shares, cost {money(plan['cost'])}, value now {money(plan['value'])}"
                              if plan.get("shares") else "Shares not given")),
                ("Profit / loss now", (f"{money(plan['pnl_abs'])} ({pct(plan['pnl_pct'])})" if plan.get("pnl_abs") is not None
                                       else pct(plan["pnl_pct"]))),
                ("Distance to goal", f"{pct(plan['to_goal_pct'])} (to {money(plan['target'])})"),
                ("Distance to stop", (f"{pct(plan['to_stop_pct'])} (to {money(plan['stop'])})"
                                      + (f", about {money(abs(plan['risk_abs']))} at risk" if plan.get("risk_abs") else "")
                                      if plan.get("stop") else "n/a"))]
        if plan.get("plan_note"):
            hold.append(("Your plan", plan["plan_note"]))
        rows = hold + rows
    rows.append(("Breakeven rule", f"At about +3% ({money(plan['breakeven_trigger'])}) the stop moves to your entry {money(plan['entry'])} (your rule 2)."))
    kv_table(doc, rows)
    grid_table(doc, ["Price up", "Price", "Safer stop", "Tighter stop"],
               [(f"+{r['level']}%", money(r["price"]), money(r["safer_stop"]), money(r["tighter_stop"])) for r in plan["ladder"]],
               widths=(1.2, 1.5, 1.6, 1.6))
    para(doc, "Profit-lock ladder: when price reaches a level, you choose where to move the stop.", size=9.5, italic=True, color=MUTED)

    # 6. Earnings
    heading(doc, "Next earnings")
    if nx.get("date"):
        flag = "  Inside your holding window." if nx["days_away"] <= 45 else "  Likely after a short trade ends."
        para(doc, f"{fdate(nx['date'])} ({nx['days_away']} days away).{flag}", bold=nx["days_away"] <= 45)
    else:
        para(doc, "No upcoming date found.")

    # 7. Risks
    heading(doc, "Top risks")
    flags = rule_flags(snap, plan, halal)
    risks = [{"level": l, "title": ti, "detail": d} for l, ti, d in flags]  # rule flags always shown
    have = {r["title"].lower() for r in risks}
    risks += [r for r in (notes.get("risks") or []) if r.get("title", "").lower() not in have]
    order = {"high": 0, "medium": 1, "low": 2}
    for r in sorted(risks, key=lambda r: order.get(r.get("level"), 1))[:6]:
        fill = {"high": RED, "medium": AMBER, "low": GREY}.get(r.get("level"), AMBER)
        banner(doc, f"{str(r.get('level', '')).upper()}: {r.get('title')}. {r.get('detail', '')}", fill, bold=False)
    if not risks:
        para(doc, "No rule-based flags.")

    # 8. Change conditions
    heading(doc, "What changes the picture")
    cc = list(notes.get("change_conditions") or [])
    if rg.get("floor") and not rg.get("broken"):
        lvl = rg["floor"][0] * (1 - plan["stop_buffer_pct"] / 100)
        cc.insert(0, f"2 daily closes below {money(lvl)} (floor bottom {money(rg['floor'][0])} minus "
                     f"{plan['stop_buffer_pct']}%): the floor pattern is broken. One close below can be a shakeout.")
    cc.append("Halal status changes on re-check.")
    for c in cc:
        para(doc, "- " + c, after=1)

    # 9. Fundamentals
    heading(doc, "Fundamentals")
    fn = notes.get("fundamentals_notes") or {}
    frows = [
        ("Revenue growth (latest quarter, YoY)", pct(m.get("revenueGrowthQuarterlyYoy")), fn.get("revenue_growth", "")),
        *eps_rows(snap, m, fn),
        ("Gross margin (TTM)", pct(m.get("grossMarginTTM"), sign=False), fn.get("gross_margin", "")),
        ("Net margin (TTM)", pct(m.get("netProfitMarginTTM"), sign=False), fn.get("net_margin", "")),
        ("P/E (TTM)", f"{m['peTTM']:.1f}x" if m.get("peTTM") else "n/a (no profit)", fn.get("pe", "")),
        ("Debt / equity", f"{m['totalDebt/totalEquityQuarterly']:.2f}" if m.get("totalDebt/totalEquityQuarterly") is not None else "n/a", fn.get("debt_equity", "")),
        ("Market cap", big(p.get("marketCap")), ""),
    ]
    grid_table(doc, ["Metric", "Value", "Note"], frows, widths=(2.8, 1.3, 2.9))
    l4 = (snap.get("earnings") or {}).get("last_4") or []
    if l4:
        para(doc, "EPS surprise, last quarters: " + ", ".join(f"{(x.get('period') or '')[:7]} {pct(x.get('surprise_pct'))}" for x in l4), size=10)
    if notes.get("quality_flag"):
        banner(doc, "Earnings quality: " + notes["quality_flag"], AMBER, bold=False)
    if notes.get("fundamentals_verdict"):
        para(doc, notes["fundamentals_verdict"], size=10.5)

    # 10. Peers
    heading(doc, "Peers")
    peers = snap.get("peers") or []
    para(doc, snap.get("peers_status") or "", size=9.5, italic=True, color=MUTED)
    if peers:
        me = {"ticker": snap["ticker"], "market_cap_m": (p.get("marketCap") or 0) / 1e6 or None,
              "revenue_growth_yoy": m.get("revenueGrowthTTMYoy"), "gross_margin": m.get("grossMarginTTM"),
              "net_margin": m.get("netProfitMarginTTM"), "pe_ttm": m.get("peTTM")}
        grid_table(doc, ["Company", "Mkt cap", "Rev growth", "Gross margin", "Net margin", "P/E"],
                   [(r["ticker"], big(r["market_cap_m"] * 1e6) if r.get("market_cap_m") else "n/a",
                     pct(r.get("revenue_growth_yoy")), pct(r.get("gross_margin"), sign=False),
                     pct(r.get("net_margin"), sign=False), f"{r['pe_ttm']:.1f}x" if r.get("pe_ttm") else "n/a")
                    for r in [me] + peers], widths=(1.2, 1.2, 1.2, 1.3, 1.2, 0.9))
    if notes.get("peers_note"):
        para(doc, notes["peers_note"])

    # 11. News
    heading(doc, "News (last 30 days)")
    items = notes.get("news") or [{"date": n["date"], "headline": n["headline"], "source": n["source"], "url": n["url"]}
                                  for n in (snap.get("news") or [])[:3]]
    icon = {"positive": "[+]", "negative": "[-]", "neutral": "[ ]"}
    for n in items[:3]:
        np_ = para(doc, f"{icon.get(n.get('take'), '[ ]')} {n.get('headline')}  ({n.get('source')}, {fdate(n.get('date'))})  ",
                   size=10, after=1)
        if n.get("url"):
            add_link(np_, "open", n["url"])
        if n.get("why"):
            para(doc, n["why"], size=9.5, italic=True, color=MUTED, after=3)
    if not items:
        para(doc, "No news found.")
    if notes.get("context"):
        para(doc, "Context from the web (earnings, guidance, recent events)", bold=True, after=2).paragraph_format.space_before = Pt(6)
        for c in notes["context"]:
            cp = para(doc, f"- {c['point']}  ", size=10, after=2)
            add_link(cp, "source", c["url"])

    # 12. Charts
    heading(doc, "Charts (year to date)")
    para(doc, "Pictures only (Word cannot zoom). For zoom, SMA tick boxes and levels, open the interactive chart file "
              f"sent with this report: {snap['ticker']}-chart.html.", size=9.5, italic=True, color=MUTED, after=6)
    for key in ("sma_short", "sma_long", "rsi"):
        pth = (charts or {}).get(key)
        if pth and os.path.exists(pth):
            doc.add_picture(pth, width=Inches(6.9))
            pic = doc.paragraphs[-1]
            pic.alignment = WD_ALIGN_PARAGRAPH.CENTER
            pic.paragraph_format.space_before = Pt(14)
            pic.paragraph_format.space_after = Pt(22)
            pic.paragraph_format.keep_together = True
        else:
            para(doc, f"Chart unavailable: {key}", italic=True, color=MUTED)

    # 13. Summary
    add_summary(doc, snap, plan, halal, flags)

    # 14. Footer
    para(doc, "", after=2)
    foot = ["Research only, not advice. You decide.",
            f"Data: Finnhub, FMP, Yahoo Finance (prices as of {t.get('as_of')}).",
            "Halal: Osooli (Yaaqen, Zoya, Musaffa)." if halal.get("status") != "unchecked" else "Halal: not checked in this run.",
            (f"AI notes: {notes['model']} (free model). Numbers in the notes are checked against the data"
             + (f"; {len(notes.get('_dropped') or [])} unverifiable item(s) removed." if notes.get("_dropped") else ".")
             if notes.get("model") else "AI notes: none in this report.")]
    if snap.get("errors"):
        foot.append("Data gaps: " + "; ".join(snap["errors"])[:300])
    for f in foot:
        para(doc, f, size=8.5, color=MUTED, after=0)

    doc.save(path)
    return path
