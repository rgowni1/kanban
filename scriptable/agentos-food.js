// Variables used by Scriptable.
// These must be at the very top of the file. Do not edit.
// icon-color: orange; icon-glyph: utensils;

// AgentOS — food log widget.
//
// An in-day nudge, not a report. The dashboard already reviews the week after
// the fact; the point of a home-screen widget is to reach you *before* lunch is
// ordered, so every number here is framed as budget remaining rather than as a
// count spent. "3 non-profile meals" is a fact you can do nothing with;
// "2 left, 4 days to go" is a decision.
//
// Setup
//   1. Save this file into Scriptable's iCloud folder (Scriptable/agentos-food.js).
//   2. Run it once inside the app — it will prompt for the kanban login and
//      store it in the iOS keychain. Nothing is written to this file.
//   3. Home screen → add a Scriptable widget → pick this script.
//
// The widget reads the same Supabase rows the dashboard does, so it is only as
// fresh as the last sync (the GitHub Action, daily). It never writes.

const SUPABASE_URL = "https://icxwnryqyffvmxscwfcv.supabase.co";
const SUPABASE_PUBLISHABLE_KEY = "sb_publishable_kw2DYRs7qCPz74v-tzR12w_TDreloum";

// --- Targets. Tune these; everything below is derived from them. ------------
const TARGETS = {
  // Non-profile = under 20% of calories from protein on a >=200 kcal meal.
  // Recent weeks have run 3-6, so 4 is a stretch rather than a formality.
  nonProfilePerWeek: 4,
  // Share of the week's meals eaten at home (Notion's Source = Home).
  cookedShare: 0.5,
  // Last meal of the day, as a 24h clock time.
  lastMealBy: "20:00",
  // Desserts/pastries per week.
  dcpPerWeek: 3,
};

// An eating day runs 04:00 -> 03:59, matching sync_journal_to_supabase.py and
// the dashboard drill-down. A midnight snack closes the night before.
const EATING_DAY_START_MIN = 4 * 60;

const KEYCHAIN_EMAIL = "agentos.food.email";
const KEYCHAIN_PASSWORD = "agentos.food.password";

// --- Credentials ------------------------------------------------------------

async function credentials() {
  if (Keychain.contains(KEYCHAIN_EMAIL) && Keychain.contains(KEYCHAIN_PASSWORD)) {
    return { email: Keychain.get(KEYCHAIN_EMAIL), password: Keychain.get(KEYCHAIN_PASSWORD) };
  }
  if (config.runsInWidget) throw new Error("Run the script in Scriptable once to sign in.");
  const prompt = new Alert();
  prompt.title = "Sign in to AgentOS";
  prompt.message = "Stored in the iOS keychain on this device only.";
  prompt.addTextField("email");
  prompt.addSecureTextField("password");
  prompt.addAction("Save");
  prompt.addCancelAction("Cancel");
  if ((await prompt.presentAlert()) === -1) throw new Error("Cancelled.");
  const email = prompt.textFieldValue(0).trim();
  const password = prompt.textFieldValue(1);
  Keychain.set(KEYCHAIN_EMAIL, email);
  Keychain.set(KEYCHAIN_PASSWORD, password);
  return { email, password };
}

async function accessToken() {
  const { email, password } = await credentials();
  const request = new Request(`${SUPABASE_URL}/auth/v1/token?grant_type=password`);
  request.method = "POST";
  request.headers = { apikey: SUPABASE_PUBLISHABLE_KEY, "Content-Type": "application/json" };
  request.body = JSON.stringify({ email, password });
  const response = await request.loadJSON();
  if (!response.access_token) {
    // A rotated password would otherwise fail silently every refresh.
    Keychain.remove(KEYCHAIN_EMAIL);
    Keychain.remove(KEYCHAIN_PASSWORD);
    throw new Error(response.error_description || response.msg || "Sign-in failed.");
  }
  return response.access_token;
}

// --- Data -------------------------------------------------------------------

function weekStart(date = new Date()) {
  const day = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  // getDay() is 0 for Sunday; the journal week starts on Monday.
  day.setDate(day.getDate() - ((day.getDay() + 6) % 7));
  return day;
}

async function fetchMeals(token) {
  // Fetch from the start of the week minus a day, so a Monday-early-hours meal
  // that belongs to Sunday's eating day is still available to be excluded.
  const from = weekStart();
  from.setDate(from.getDate() - 1);
  const query = new URLSearchParams({
    select: "name,eaten_at,eaten_at_override,meal_type,source,calories,protein_g,is_non_profile,is_dcp,is_cooked",
    eaten_at: `gte.${from.toISOString()}`,
    order: "eaten_at.asc",
  });
  const request = new Request(`${SUPABASE_URL}/rest/v1/food_log_entries?${query}`);
  request.headers = {
    apikey: SUPABASE_PUBLISHABLE_KEY,
    Authorization: `Bearer ${token}`,
  };
  return await request.loadJSON();
}

function eatingSlot(meal) {
  const iso = meal.eaten_at_override || meal.eaten_at;
  if (!iso) return null;
  const at = new Date(iso);
  if (isNaN(at)) return null;
  let minutes = at.getHours() * 60 + at.getMinutes();
  const day = new Date(at.getFullYear(), at.getMonth(), at.getDate());
  if (minutes < EATING_DAY_START_MIN) {
    day.setDate(day.getDate() - 1);
    minutes += 24 * 60;
  }
  return { day, minutes };
}

function summarise(meals) {
  const start = weekStart();
  const rows = [];
  for (const meal of meals) {
    const slot = eatingSlot(meal);
    if (slot && slot.day >= start) rows.push({ ...meal, ...slot });
  }
  const lastByDay = new Map();
  for (const row of rows) {
    const key = row.day.getTime();
    if (!lastByDay.has(key) || row.minutes > lastByDay.get(key)) lastByDay.set(key, row.minutes);
  }
  const latest = rows.length ? rows[rows.length - 1] : null;
  return {
    total: rows.length,
    nonProfile: rows.filter(r => r.is_non_profile).length,
    dcp: rows.filter(r => r.is_dcp).length,
    cooked: rows.filter(r => r.is_cooked).length,
    protein: rows.reduce((sum, r) => sum + (Number(r.protein_g) || 0), 0),
    calories: rows.reduce((sum, r) => sum + (Number(r.calories) || 0), 0),
    daysLogged: lastByDay.size,
    lastMealMins: latest ? latest.minutes : null,
    lastMealName: latest ? latest.name : null,
    // Monday is day 1. Elapsed days drive the pace check: two non-profile meals
    // is fine on a Sunday and a problem on a Tuesday.
    dayOfWeek: Math.min(7, Math.floor((Date.now() - start.getTime()) / 86400000) + 1),
  };
}

// --- Formatting -------------------------------------------------------------

const clock = minutes => {
  if (minutes == null) return "—";
  const norm = ((Math.round(minutes) % 1440) + 1440) % 1440;
  const hour = Math.floor(norm / 60);
  return `${hour % 12 || 12}:${String(norm % 60).padStart(2, "0")} ${hour >= 12 ? "PM" : "AM"}`;
};
const toMinutes = hhmm => {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
};

const COLORS = {
  good: new Color("#3f9e5a"),
  warn: new Color("#d9a441"),
  bad: new Color("#d95926"),
  text: Color.dynamic(new Color("#1b1b1b"), new Color("#f2f0ec")),
  muted: Color.dynamic(new Color("#6d6a64"), new Color("#9b978f")),
  panel: Color.dynamic(new Color("#f6f4f0"), new Color("#1c1b19")),
};

// The one line at the top. Ranked by urgency, so the widget leads with whatever
// is actually off rather than with a fixed metric that is usually fine.
function headline(s) {
  const budget = TARGETS.nonProfilePerWeek;
  const pace = budget * (s.dayOfWeek / 7);
  if (!s.total) return { text: "Nothing logged this week", tone: COLORS.muted };
  if (s.nonProfile > budget) {
    return { text: `${s.nonProfile} non-profile — over budget`, tone: COLORS.bad };
  }
  if (s.nonProfile === budget) {
    return { text: `${s.nonProfile} non-profile — budget spent`, tone: COLORS.bad };
  }
  if (s.nonProfile > pace) {
    return { text: `${s.nonProfile} non-profile by day ${s.dayOfWeek} — ahead of pace`, tone: COLORS.warn };
  }
  if (s.dcp > TARGETS.dcpPerWeek) {
    return { text: `${s.dcp} desserts this week`, tone: COLORS.warn };
  }
  const share = s.total ? s.cooked / s.total : 0;
  if (s.dayOfWeek >= 3 && share < TARGETS.cookedShare) {
    return { text: `Only ${Math.round(share * 100)}% of meals cooked at home`, tone: COLORS.warn };
  }
  const left = 8 - s.dayOfWeek;
  return { text: `${budget - s.nonProfile} non-profile left, ${left} day${left === 1 ? "" : "s"} to go`, tone: COLORS.good };
}

function meter(stack, filled, total, tone) {
  const bar = stack.addStack();
  bar.spacing = 2;
  for (let i = 0; i < Math.max(total, filled); i++) {
    const pip = bar.addStack();
    pip.size = new Size(9, 4);
    pip.cornerRadius = 2;
    pip.backgroundColor = i < filled ? tone : new Color(tone.hex, 0.18);
  }
}

function row(widget, label, value, tone, meterArgs) {
  const line = widget.addStack();
  line.centerAlignContent();
  const name = line.addText(label);
  name.font = Font.systemFont(11);
  name.textColor = COLORS.muted;
  line.addSpacer();
  if (meterArgs) {
    meter(line, meterArgs[0], meterArgs[1], tone);
    line.addSpacer(8);
  }
  const amount = line.addText(value);
  amount.font = Font.semiboldRoundedSystemFont(12);
  amount.textColor = tone;
}

function build(s) {
  const widget = new ListWidget();
  widget.backgroundColor = COLORS.panel;
  widget.setPadding(12, 14, 12, 14);

  const head = widget.addStack();
  const kicker = head.addText("THIS WEEK");
  kicker.font = Font.semiboldSystemFont(9);
  kicker.textColor = COLORS.muted;
  head.addSpacer();
  const when = head.addText(`day ${s.dayOfWeek}/7 · ${s.daysLogged} logged`);
  when.font = Font.systemFont(9);
  when.textColor = COLORS.muted;

  widget.addSpacer(6);
  const lead = headline(s);
  const leadText = widget.addText(lead.text);
  leadText.font = Font.semiboldRoundedSystemFont(15);
  leadText.textColor = lead.tone;
  leadText.minimumScaleFactor = 0.7;
  leadText.lineLimit = 2;

  if (config.widgetFamily === "small") {
    widget.addSpacer(6);
    const sub = widget.addText(`${s.cooked}/${s.total} cooked · ${clock(s.lastMealMins)} last`);
    sub.font = Font.systemFont(10);
    sub.textColor = COLORS.muted;
    return widget;
  }

  widget.addSpacer(9);

  const budget = TARGETS.nonProfilePerWeek;
  row(widget, "🍔 Non-profile", `${s.nonProfile}/${budget}`,
    s.nonProfile > budget ? COLORS.bad : s.nonProfile > budget * (s.dayOfWeek / 7) ? COLORS.warn : COLORS.good,
    [Math.min(s.nonProfile, budget), budget]);

  widget.addSpacer(5);
  const share = s.total ? s.cooked / s.total : 0;
  row(widget, "🏠 Cooked at home", `${s.cooked}/${s.total} · ${Math.round(share * 100)}%`,
    share >= TARGETS.cookedShare ? COLORS.good : COLORS.warn);

  widget.addSpacer(5);
  const lateBy = toMinutes(TARGETS.lastMealBy);
  row(widget, "🌙 Last meal", clock(s.lastMealMins),
    s.lastMealMins == null ? COLORS.muted : s.lastMealMins > lateBy ? COLORS.warn : COLORS.good);

  if (config.widgetFamily === "large") {
    widget.addSpacer(5);
    row(widget, "🍪 Desserts", `${s.dcp}/${TARGETS.dcpPerWeek}`,
      s.dcp > TARGETS.dcpPerWeek ? COLORS.warn : COLORS.good);
  }

  widget.addSpacer(8);
  const perDay = s.daysLogged || 1;
  const foot = widget.addText(
    `${Math.round(s.protein / perDay)}g protein · ${Math.round(s.calories / perDay).toLocaleString()} kcal per logged day`
  );
  foot.font = Font.systemFont(9.5);
  foot.textColor = COLORS.muted;

  return widget;
}

function errorWidget(message) {
  const widget = new ListWidget();
  widget.backgroundColor = COLORS.panel;
  widget.setPadding(12, 14, 12, 14);
  const title = widget.addText("AgentOS · Food");
  title.font = Font.semiboldSystemFont(12);
  title.textColor = COLORS.text;
  widget.addSpacer(5);
  const body = widget.addText(message);
  body.font = Font.systemFont(10);
  body.textColor = COLORS.muted;
  body.lineLimit = 4;
  return widget;
}

// --- Entry point ------------------------------------------------------------

let widget;
try {
  const token = await accessToken();
  widget = build(summarise(await fetchMeals(token)));
} catch (error) {
  widget = errorWidget(String(error.message || error));
}

if (config.runsInWidget) Script.setWidget(widget);
else await widget.presentMedium();
Script.complete();
