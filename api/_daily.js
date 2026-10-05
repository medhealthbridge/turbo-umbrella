// The daily report: pull products and sales from Gumroad, summarise them, and
// write promo drafts plus one template idea. Nothing here posts anywhere.
//
// The Gumroad API (v2) is used read-only: GET /products and GET /sales. It is
// not used to create products — see docs/DAILY.md for why and the manual step.
//
// Claude writes the drafts when ANTHROPIC_API_KEY is set. Without a key, or if
// the call fails for any reason, a plain template fills in instead, so the job
// always produces a report.

const GUMROAD = () => (process.env.GUMROAD_API_BASE || "https://api.gumroad.com/v2").replace(/\/$/, "");
const MAX_SALES_PAGES = 20;
const DAY = 24 * 3600 * 1000;

export const PLATFORMS = ["pinterest", "facebook", "tiktok"];

// ------------------------------------------------------------------ Gumroad

async function gumroad(path, params = {}) {
  const token = (process.env.GUMROAD_ACCESS_TOKEN || "").trim();
  if (!token) throw new Error("GUMROAD_ACCESS_TOKEN is not set.");
  const url = new URL(GUMROAD() + path);
  for (const [k, v] of Object.entries(params)) if (v) url.searchParams.set(k, v);
  const resp = await fetch(url, {
    headers: { authorization: `Bearer ${token}`, accept: "application/json" },
    signal: AbortSignal.timeout(20000),
  });
  const text = await resp.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* handled below */ }
  if (!resp.ok || !data || data.success === false) {
    const why = (data && data.message) || resp.statusText || "unexpected response";
    const hint = resp.status === 401 || resp.status === 403
      ? " — the access token is invalid or lacks the view_sales scope." : "";
    throw new Error(`Gumroad ${path} failed (${resp.status}): ${why}${hint}`);
  }
  return data;
}

export async function fetchProducts() {
  const data = await gumroad("/products");
  return Array.isArray(data.products) ? data.products : [];
}

/** Every sale since `afterDate` (YYYY-MM-DD), following the cursor. */
export async function fetchSales(afterDate) {
  const sales = [];
  let pageKey = "";
  for (let page = 0; page < MAX_SALES_PAGES; page++) {
    const data = await gumroad("/sales", { after: afterDate, page_key: pageKey });
    sales.push(...(Array.isArray(data.sales) ? data.sales : []));
    pageKey = data.next_page_key || "";
    if (!pageKey) return { sales, truncated: false };
  }
  return { sales, truncated: true };
}

// ------------------------------------------------------------------ summary

const cents = (n) => (Number.isFinite(Number(n)) ? Number(n) : 0);

export function money(amountCents, currency = "usd") {
  const unit = String(currency || "usd").toUpperCase();
  return `${(cents(amountCents) / 100).toFixed(2)} ${unit}`;
}

/** The calendar day in the shop's time zone, as YYYY-MM-DD. */
export function reportDay(now, tz) {
  try {
    return new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
  } catch {
    return now.toISOString().slice(0, 10);
  }
}

/** Only http(s) links survive into the report, which the admin page links to. */
export function safeUrl(value) {
  try {
    const u = new URL(String(value));
    return u.protocol === "https:" || u.protocol === "http:" ? u.href : "";
  } catch {
    return "";
  }
}

function windowTotals(sales, since) {
  let count = 0, revenue = 0;
  for (const s of sales) {
    if (Date.parse(s.created_at) >= since) { count += 1; revenue += cents(s.price); }
  }
  return { sales: count, revenue_cents: revenue };
}

/**
 * Totals for the last 24 hours and 7 days, and the best and worst product.
 * Fully refunded sales are left out. Best and worst are judged on the last
 * 7 days; with no sales in that window the best falls back to all-time sales.
 */
export function summarize(products, sales, now = new Date()) {
  const t = now.getTime();
  const kept = sales.filter((s) => !s.refunded && Number.isFinite(Date.parse(s.created_at)));
  const currency = String((kept[0] && kept[0].currency) || "usd").toLowerCase();

  const perProduct = new Map();
  for (const p of products) {
    perProduct.set(String(p.id), {
      id: String(p.id), name: String(p.name || "Untitled"), url: safeUrl(p.short_url),
      price_cents: cents(p.price), published: p.published !== false,
      all_time_sales: Number(p.sales_count) || 0, sales_7d: 0, revenue_7d_cents: 0,
    });
  }
  for (const s of kept) {
    if (Date.parse(s.created_at) < t - 7 * DAY) continue;
    const key = String(s.product_id);
    if (!perProduct.has(key)) {
      perProduct.set(key, {
        id: key, name: String(s.product_name || "Unknown product"), url: "", price_cents: 0,
        published: true, all_time_sales: 0, sales_7d: 0, revenue_7d_cents: 0,
      });
    }
    const row = perProduct.get(key);
    row.sales_7d += 1;
    row.revenue_7d_cents += cents(s.price);
  }

  const rows = [...perProduct.values()].filter((p) => p.published);
  const byStrength = (a, b) =>
    b.revenue_7d_cents - a.revenue_7d_cents || b.sales_7d - a.sales_7d || b.all_time_sales - a.all_time_sales;
  const ranked = [...rows].sort(byStrength);
  const sold7d = ranked.some((p) => p.sales_7d > 0);

  return {
    currency,
    last_24h: windowTotals(kept, t - DAY),
    last_7d: windowTotals(kept, t - 7 * DAY),
    products_total: products.length,
    best: ranked[0] || null,
    best_basis: ranked[0] ? (sold7d ? "last 7 days" : "all-time sales (nothing sold in the last 7 days)") : null,
    // Needs at least two products to be meaningful.
    worst: ranked.length > 1 ? ranked[ranked.length - 1] : null,
    products: ranked,
  };
}

// ------------------------------------------------------------------- drafts

const SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["drafts", "template_idea"],
  properties: {
    drafts: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["platform", "caption", "hashtags", "image_idea"],
        properties: {
          platform: { type: "string", enum: PLATFORMS },
          caption: { type: "string" },
          hashtags: { type: "array", items: { type: "string" } },
          image_idea: { type: "string" },
        },
      },
    },
    template_idea: {
      type: "object",
      additionalProperties: false,
      required: ["title", "why", "outline"],
      properties: { title: { type: "string" }, why: { type: "string" }, outline: { type: "string" } },
    },
  },
};

const tag = (text) => "#" + String(text).replace(/[^a-z0-9]+/gi, "").toLowerCase();

export function fallbackContent(summary) {
  const best = summary.best;
  const name = best ? best.name : "my printable coloring book";
  const link = best && best.url ? ` ${best.url}` : "";
  const base = ["coloringbook", "toddleractivities", "printablesforkids", "screenfree", "preschoolathome"];
  const drafts = [
    {
      platform: "pinterest",
      caption: `${name}: big, simple pages made for ages 3-5. Print at home and keep little hands busy.${link}`,
      hashtags: [...base, "homeschoolpreschool"].map(tag),
      image_idea: "Tall 2:3 pin: a finished, brightly coloured page next to the blank line-art version, with the title in bold letters.",
    },
    {
      platform: "facebook",
      caption: `Rainy afternoon, no screens? ${name} gives your 3-5 year old pages they can actually finish. Instant download, print as many times as you like.${link}`,
      hashtags: base.slice(0, 3).map(tag),
      image_idea: "A child's hand colouring a page on a kitchen table, crayons scattered, soft natural light.",
    },
    {
      platform: "tiktok",
      caption: `POV: you found a quiet-time activity that works. ${name}. Link in bio.`,
      hashtags: ["coloringbook", "toddlermom", "preschoolactivities", "fyp"].map(tag),
      image_idea: "15-second time-lapse: blank page to fully coloured, ending on the cover with the price.",
    },
  ];
  const theme = best ? best.name : "coloring book";
  return {
    drafts,
    template_idea: {
      title: `Companion pack for "${theme}"`,
      why: "Buyers of a successful title are the likeliest to buy a matching follow-up. This is a default idea written without Claude, based on your top product.",
      outline: "12 new pages in the same style, a themed certificate page, and a one-page parent guide with activity ideas.",
    },
    source: "template",
  };
}

function promptFor(summary) {
  const best = summary.best;
  const top = summary.products.slice(0, 5).map((p) => ({
    name: p.name, price: money(p.price_cents, summary.currency),
    sales_7d: p.sales_7d, all_time_sales: p.all_time_sales,
  }));
  return [
    "You write marketing drafts for a small Gumroad shop that sells Canva-made printable templates.",
    "The first product is a kids' coloring book for ages 3-5. The buyer is a parent or preschool teacher.",
    "",
    `Top product (${summary.best_basis}): ${JSON.stringify(best && { name: best.name, price: money(best.price_cents, summary.currency), url: best.url })}`,
    `Products ranked by performance: ${JSON.stringify(top)}`,
    "",
    "Return exactly three drafts, one each for pinterest, facebook and tiktok, all promoting the top product.",
    "Each has a ready-to-post caption in a warm, plain voice (no hype, no invented reviews, no invented discounts),",
    "3 to 6 hashtags without the # sign, and a short image idea that can be made in Canva.",
    "Pinterest: searchable keywords early. Facebook: conversational, aimed at parents. TikTok: a short hook for a 10-20 second video.",
    "Then suggest ONE new Canva template idea based on what is selling: a title, why it should sell, and a short outline.",
  ].join("\n");
}

async function claudeContent(summary) {
  const { default: Anthropic } = await import("@anthropic-ai/sdk");
  const client = new Anthropic({ timeout: 45000, maxRetries: 1 });
  const msg = await client.beta.messages.create({
    model: process.env.ANTHROPIC_MODEL || "claude-opus-5-5",
    max_tokens: 4000,
    betas: ["server-side-fallback-2026-07-01"],
    fallbacks: "default",
    output_config: { effort: "medium", format: { type: "json_schema", schema: SCHEMA } },
    messages: [{ role: "user", content: promptFor(summary) }],
  });
  if (msg.stop_reason === "refusal" || msg.stop_reason === "max_tokens") {
    throw new Error(`Claude stopped early (${msg.stop_reason}).`);
  }
  const block = (msg.content || []).find((b) => b.type === "text");
  const parsed = JSON.parse(block ? block.text : "");
  const drafts = PLATFORMS.map((platform) => {
    const d = (parsed.drafts || []).find((x) => x.platform === platform);
    if (!d || !d.caption) throw new Error(`Claude returned no ${platform} draft.`);
    return {
      platform,
      caption: String(d.caption),
      hashtags: (d.hashtags || []).map((h) => tag(h)).filter((h) => h.length > 1).slice(0, 8),
      image_idea: String(d.image_idea || ""),
    };
  });
  const idea = parsed.template_idea || {};
  if (!idea.title) throw new Error("Claude returned no template idea.");
  return {
    drafts,
    template_idea: { title: String(idea.title), why: String(idea.why || ""), outline: String(idea.outline || "") },
    source: "claude",
    model: msg.model || process.env.ANTHROPIC_MODEL || "claude-opus-5-5",
  };
}

export async function writeContent(summary) {
  if (!(process.env.ANTHROPIC_API_KEY || "").trim()) {
    return { ...fallbackContent(summary), note: "ANTHROPIC_API_KEY is not set, so these drafts come from a plain template." };
  }
  try {
    return await claudeContent(summary);
  } catch (err) {
    return { ...fallbackContent(summary), note: `Claude was unavailable (${String(err.message || err).slice(0, 200)}); template drafts used.` };
  }
}

// ------------------------------------------------------------------- report

/** Run the whole job. Returns the report; throws only if Gumroad fails. */
export async function buildReport(now = new Date()) {
  const tz = process.env.REPORT_TZ || "Asia/Manila";
  const after = new Date(now.getTime() - 8 * DAY).toISOString().slice(0, 10);
  const [products, fetched] = await Promise.all([fetchProducts(), fetchSales(after)]);
  const summary = summarize(products, fetched.sales, now);
  const content = await writeContent(summary);
  return {
    day: reportDay(now, tz),
    timezone: tz,
    generated_at: now.toISOString(),
    summary,
    drafts: content.drafts,
    template_idea: content.template_idea,
    drafts_source: content.source,
    model: content.model || null,
    notes: [
      content.note,
      fetched.truncated ? `Sales list was cut at ${MAX_SALES_PAGES} pages; totals may be understated.` : null,
      "Drafts only. Nothing has been posted anywhere.",
    ].filter(Boolean),
  };
}
