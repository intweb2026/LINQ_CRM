/**
 * wire_probe.mjs — captures what the REAL frontend api/*.js modules put on the
 * wire, and asserts the invariants that shipped bugs violated.
 *
 * Run by accounts/tests_wire_probe.py, which shells `node` and then replays the
 * captured literals against Django. Kept as a .mjs beside the test rather than
 * in the frontend tree because it is test tooling, not shipped code.
 *
 * No dependencies: axios is stubbed, React imports are stripped, and the
 * modules are imported from a temp directory.
 *
 * ── WHY THIS FILE WAS REWRITTEN ─────────────────────────────────────────────
 * It was written against the Create React App tree and broke in four ways when
 * the frontend was replaced with the Vite one, all of them silent:
 *
 *   1. The axios stub matched `import axios from "axios";` with DOUBLE quotes.
 *      The new client.js uses single quotes, so axios was never stubbed and the
 *      probe died with ERR_MODULE_NOT_FOUND resolving the real package.
 *   2. It rewrote `process.env.REACT_APP_API_URL`, which no longer appears;
 *      client.js now reads `import.meta.env`, which is undefined under plain
 *      Node and throws on property access.
 *   3. It imported api/delegates.js and api/ticketCentral.js for `delegatesApi`
 *      / `ticketCentralApi`. Those files were CRA leftovers, unreachable from
 *      the app entry point, and are now deleted. The live module is
 *      api/bookings.js and it uses namespace exports, not an `*Api` object.
 *   4. Bulk update moved to a single generic helper in api/client.js, so
 *      per-module bulkUpdate functions no longer exist to probe.
 *
 * Because tests_wire_probe.py treats a non-zero exit as "probe unavailable" and
 * SKIPS, all four failures presented as a green suite that asserted nothing
 * about the shipped frontend. Anything below that stops matching the real
 * modules must fail loudly rather than skip.
 *
 * Output: a single JSON document on stdout.
 */
import { readFileSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const FE = resolve(process.argv[2] ?? "../frontend/src");
const dir = mkdtempSync(join(tmpdir(), "wire-"));
const captured = [];
globalThis.__CAP = captured;

const AXIOS_STUB = `
const __mk = () => ({
  get:    (url, cfg) => { globalThis.__CAP.push({ verb:"GET",  url, params: cfg?.params ?? null, body: null }); return Promise.resolve({ data: { count: 0, results: [] } }); },
  post:   (url, body) => { globalThis.__CAP.push({ verb:"POST", url, params: null, body: JSON.parse(JSON.stringify(body ?? null)) }); return Promise.resolve({ data: {} }); },
  put:    (url, body) => { globalThis.__CAP.push({ verb:"PUT",  url, params: null, body: JSON.parse(JSON.stringify(body ?? null)) }); return Promise.resolve({ data: {} }); },
  patch:  (url, body) => { globalThis.__CAP.push({ verb:"PATCH", url, params: null, body: JSON.parse(JSON.stringify(body ?? null)) }); return Promise.resolve({ data: {} }); },
  delete: (url) => { globalThis.__CAP.push({ verb:"DELETE", url, params: null, body: null }); return Promise.resolve({ data: {} }); },
  interceptors: { request: { use(){} }, response: { use(){} } },
});
const axios = { create: __mk };
`;

async function load(rel, extra = (s) => s) {
  const src = extra(readFileSync(join(FE, rel), "utf8"))
    // Quote-agnostic, so a formatter flipping quote style cannot silently
    // un-stub axios and turn a real assertion into a skipped test.
    .replace(/^import axios from ['"]axios['"];$/m, AXIOS_STUB)
    // NOTE: env access is NOT rewritten here. api/client.js reads config via
    // `process.env.REACT_APP_*`, which is real under Node, so this probe
    // exercises the shipped module rather than a rewritten copy of it. If that
    // ever changes to a browser-only mechanism (it was `import.meta.env` while
    // the build was Vite), this probe fails loudly on module load, which is the
    // correct signal.
    .replace(/^import .*from ['"]react['"];$/m, "");
  const p = join(dir, rel.replace(/[\\/]/g, "_").replace(/\.jsx?$/, ".mjs"));
  writeFileSync(p, src);
  return import("file://" + p.replace(/\\/g, "/"));
}

// api/client.js imports lib/liveData.js — the invalidation bus its response
// interceptor publishes writes to. Loaded FIRST so client.js's import of it can be
// redirected at the flat copy, the same way './client' and '../lib/constants' are
// below. That module is deliberately guarded for a non-browser global scope
// (no window, no BroadcastChannel, no localStorage under Node), which is what lets
// it be imported here at all.
const liveDataMod = await load("lib/liveData.js");
const liveDataPath = join(dir, "lib_liveData.mjs").replace(/\\/g, "/");

// api/*.js import './client' — resolve that to the stubbed copy we just wrote.
const clientMod = await load("api/client.js", (s) =>
  s.replace(/from ['"]\.\.\/lib\/liveData['"]/g, `from "file://${liveDataPath}"`));
const clientPath = join(dir, "api_client.mjs").replace(/\\/g, "/");
const patchClientImport = (s) =>
  s.replace(/from ['"]\.\/client['"]/g, `from "file://${clientPath}"`);

const { serializeParams, bulkUpdate, assertIdArray, apiErrorMessage } = clientMod;
const spec = await load("lib/filterSpec.js");
const { specToJson, partitionConds, toCriterion } = spec;
const bookings = await load("api/bookings.js", patchClientImport);
const tickets = await load("api/tickets.js", patchClientImport);
const webhooks = await load("api/webhooks.js", patchClientImport);

// The admin modules read ALL_MODULES from lib/constants, and api/users.js reads
// the matrix helpers from api/teams.js — the team is the role, so the user's
// delta is worked out against the team's grid. Everything is loaded flattened
// into one temp directory, so both relative imports need redirecting the same
// way './client' does. ORDER MATTERS: teams.js has to exist on disk before
// users.js is rewritten to point at it.
const constantsMod = await load("lib/constants.js");
const constantsPath = join(dir, "lib_constants.mjs").replace(/\\/g, "/");
const patchAdminImports = (s) =>
  patchClientImport(s).replace(/from ['"]\.\.\/lib\/constants['"]/g, `from "file://${constantsPath}"`);
const { ALL_MODULES } = constantsMod;
const teams = await load("api/teams.js", patchAdminImports);
const teamsPath = join(dir, "api_teams.mjs").replace(/\\/g, "/");
const users = await load("api/users.js", (s) =>
  patchAdminImports(s).replace(/from ['"]\.\/teams['"]/g, `from "file://${teamsPath}"`));
const roleFromTeam = await load("lib/roleFromTeam.js");
// api/companies.js was loaded here until the Companies tab was removed. The
// import outlived the file, so `node wire_probe.mjs` died with ENOENT before a
// single check ran — the whole point of the probe, unavailable, on a suite that
// otherwise looks fine. Anything added here must be a module the app still ships.
const delegateRows = await load("pages/bookings/DelegateTable.jsx", (s) =>
  // Only the pure row helpers are wanted; the JSX component below them cannot be
  // parsed by bare Node. Everything from the component's export onwards is cut.
  s.slice(0, s.indexOf("export default function DelegateTable"))
   // Multi-line aware: this file's constants import is spread over four lines,
   // and a line-at-a-time strip left the closing brace behind as a syntax error.
   .replace(/^import[\s\S]*?from\s+['"][^'"]*['"];?/gm, ""));

const results = { checks: [], literals: {} };
const check = (name, pass, detail = "") =>
  results.checks.push({ name, pass, detail: String(detail) });

// ── 0. The modules the probe depends on actually export what it needs ───────
// Asserted rather than assumed: a rename here previously turned every check
// below into a skipped test.
for (const [name, fn] of [
  ["client.serializeParams", serializeParams], ["client.bulkUpdate", bulkUpdate],
  ["client.assertIdArray", assertIdArray], ["filterSpec.specToJson", specToJson],
  ["filterSpec.partitionConds", partitionConds], ["bookings.bulkRemove", bookings.bulkRemove],
  ["bookings.count", bookings.count],
]) {
  check(`export exists: ${name}`, typeof fn === "function", typeof fn);
}
check("bookings.RESOURCE is 'delegates'", bookings.RESOURCE === "delegates", bookings.RESOURCE);

// ── 1. UI conditions -> filter_spec criteria ────────────────────────────────
// The schema shape mirrors what {resource}/filter_schema/ returns. Only fields
// and operators the schema lists may be sent, so this doubles as a check that
// partitionConds denies by default.
const SCHEMA = {
  fields: {
    ticket_tier: { type: "choice", operators: ["is", "is_not", "any_of", "none_of", "is_empty", "is_not_empty"], choices: ["Early Bird", "Standard"] },
    payment_status: { type: "choice", operators: ["is", "is_not", "any_of", "none_of", "is_empty", "is_not_empty"], choices: ["Paid", "Cancelled", "Pending"] },
    email: { type: "text", operators: ["is", "is_not", "contains", "not_contains", "starts_with", "ends_with", "any_of", "none_of", "is_empty", "is_not_empty"] },
  },
};
const COLS = [
  { key: "ticket_tier", serverField: "ticket_tier" },
  { key: "payment_status", serverField: "payment_status" },
  { key: "email", serverField: "email" },
  // No serverField: must stay client-side however it is filtered.
  { key: "owner" },
];

const partition = partitionConds([
  { key: "ticket_tier", op: "Is Empty", values: [] },
  { key: "payment_status", op: "Is Not", values: ["Paid", "Cancelled"] },
  { key: "owner", op: "Contains", values: ["alice"] },
  { key: "email", op: "Like", values: ["%@iq-hub.com"] },
], COLS, SCHEMA);

check("client-only column is not sent to the server",
  partition.criteria.every((c) => c.field !== "owner"),
  JSON.stringify(partition.criteria.map((c) => c.field)));
check("'Like' has no backend operator and stays client-side",
  partition.clientConds.some((c) => c.key === "email" && c.op === "Like"),
  JSON.stringify(partition.unsupported));
check("unsupported conditions are reported, never dropped silently",
  partition.unsupported.length === 2, JSON.stringify(partition.unsupported));
check("multi-value 'Is Not' maps to none_of with a values list",
  partition.criteria.some((c) => c.field === "payment_status" && c.op === "none_of"
    && Array.isArray(c.values) && c.values.length === 2),
  JSON.stringify(partition.criteria));
check("is_empty criterion carries no value key",
  partition.criteria.some((c) => c.op === "is_empty" && !("value" in c) && !("values" in c)),
  JSON.stringify(partition.criteria));

// A value outside the field's registered choices must not be sent — the backend
// answers 400 for the whole request, which reads as a permanently broken table.
const badChoice = toCriterion(
  { key: "payment_status", op: "Is", values: ["NotARealStatus"] },
  { key: "payment_status", serverField: "payment_status" }, SCHEMA);
check("value outside the schema's choices is refused locally", badChoice.ok === false, badChoice.reason || "");

// ── 2. The list query string ────────────────────────────────────────────────
const specJson = specToJson(partition.criteria);
check("specToJson returns raw JSON, not pre-encoded", !specJson.includes("%"), specJson.slice(0, 40));

const listQuery = serializeParams({
  page: 1, page_size: 50, ordering: "-_sort_request_date", filter_spec: specJson,
});
results.literals.delegates_list_query = listQuery;

const specOccurrences = (listQuery.match(/filter_spec=/g) || []).length;
check("filter_spec appears exactly once", specOccurrences === 1, specOccurrences);
check("filter_spec is single-encoded (no %25)", !listQuery.includes("%25"),
  listQuery.includes("%25") ? "found %25 — double encoded" : "ok");

const specValue = new URLSearchParams(listQuery).get("filter_spec");
let parsed = null;
try { parsed = JSON.parse(specValue); } catch { /* left null */ }
check("one decode yields parseable JSON", parsed !== null);
check("match mode is 'all' — the only one the backend accepts",
  parsed && parsed.match === "all", parsed ? parsed.match : "unparsed");

// MultipleChoiceFilter reads repeated bare keys via QueryDict.getlist(); axios'
// default `key[]=` form is silently ignored and the request comes back unfiltered.
const multi = serializeParams({ payment_status: ["Paid", "Cancelled"] });
check("array params serialise as repeated bare keys",
  multi === "payment_status=Paid&payment_status=Cancelled", multi);

// ── 3. bulk_update body shape ──────────────────────────────────────────────
captured.length = 0;
await bulkUpdate("delegates", { ids: [1, 2, 3], field: "delegate_payment_status", value: "Paid", commit: true, planHash: "hash" });
const commitBody = captured[captured.length - 1].body;
results.literals.delegates_bulk_update_body = commitBody;
check("bulkUpdate ids is a JSON array", Array.isArray(commitBody.ids), JSON.stringify(commitBody.ids));
check("bulkUpdate ids is not {}", JSON.stringify(commitBody.ids) !== "{}", JSON.stringify(commitBody.ids));
check("commit body carries plan_hash", commitBody.plan_hash === "hash", commitBody.plan_hash);

// KEY PRESENCE is the signal: omitting `value` means "no target chosen yet",
// sending it as null means "clear this field". Conflating them makes clearing
// a nullable override impossible.
captured.length = 0;
await bulkUpdate("delegates", { ids: [1], field: "delegate_payment_status", commit: false });
const previewBody = captured[captured.length - 1].body;
check("value-less preview omits the value key", !("value" in previewBody), Object.keys(previewBody).join(","));

captured.length = 0;
await bulkUpdate("delegates", { ids: [1], field: "delegate_payment_status", value: null, commit: false });
const clearBody = captured[captured.length - 1].body;
check("explicit clear SENDS value: null", "value" in clearBody && clearBody.value === null, JSON.stringify(clearBody));

// ── 4. A Set must THROW, never serialise to {} ──────────────────────────────
// EVERY surface that sends or iterates an ID collection. The Set -> {} bug
// shipped once; a new bulk endpoint added without the guard is how it returns.
for (const [name, call] of [
  ["client.bulkUpdate", () => bulkUpdate("delegates", { ids: new Set([1, 2]), field: "x", value: "y", commit: true })],
  ["bookings.bulkRemove", () => bookings.bulkRemove(new Set([1]))],
  ["bookings.bulkMarkPaid", () => bookings.bulkMarkPaid(new Set([1]))],
  ["tickets.bulkSubmit", () => tickets.bulkSubmit(new Set([1]))],
]) {
  let threw = false, msg = "";
  try { await call(); } catch (e) { threw = true; msg = e.message; }
  check(`${name} rejects a Set loudly`, threw, msg);
}

// ── 5. Counting must not walk every page ───────────────────────────────────
// Sidebar and AppShell only need a number; both used to pull ~35k rows for it.
captured.length = 0;
await bookings.count([{ field: "payment_status", op: "is", value: "Pending" }]);
const countReqs = captured.filter((c) => c.verb === "GET");
check("count() issues exactly one GET", countReqs.length === 1, countReqs.length);
check("count() asks for a single row", countReqs[0]?.params?.page_size === 1,
  JSON.stringify(countReqs[0]?.params));
results.literals.delegates_count_params = countReqs[0]?.params ?? null;

// ── 5b. Select all, and the batching every bulk surface now needs ───────────
// The header checkbox used to tick one page. On a filter matching 35,690
// tickets that selected 50, and every bulk action ran against those 50 and
// reported success — because 50 rows really were updated. Two things have to
// hold for the fix, and neither is visible from the browser:
//
//   1. The select-all request must narrow by the SAME terms as the list it
//      replaces, and must not be paged. A paged select-all IS the original bug.
//   2. Every id collection that leaves this frontend must be batched to what the
//      endpoint accepts. bulk_update and both bulk_delete actions cap at 1000
//      and answer 400 past it, which the user reads as the action being broken.
const { fetchAllIds, chunk, mapLimit, fetchPage } = clientMod;
for (const [name, fn] of [
  ["client.fetchAllIds", fetchAllIds], ["client.chunk", chunk], ["client.mapLimit", mapLimit],
]) {
  check(`export exists: ${name}`, typeof fn === "function", typeof fn);
}

captured.length = 0;
await fetchAllIds("delegates", { filterSpec: specJson, search: "ada" });
const idsReq = captured[captured.length - 1];
results.literals.select_all_request = { url: idsReq.url, params: idsReq.params };

check("select-all asks {resource}/ids/", idsReq.url === "delegates/ids/", idsReq.url);
check("select-all sends the filter_spec raw, not pre-encoded",
  !String(idsReq.params.filter_spec ?? "").includes("%"),
  String(idsReq.params.filter_spec ?? "").slice(0, 40));
// THE regression check. A select-all carrying page/page_size is a select-page
// wearing a different name, which is precisely what shipped.
check("select-all sends no page or page_size",
  !("page" in idsReq.params) && !("page_size" in idsReq.params),
  Object.keys(idsReq.params).join(","));

captured.length = 0;
await fetchPage("delegates", { page: 1, pageSize: 50, filterSpec: specJson, search: "ada" });
const pageReq = captured[captured.length - 1];
const narrowing = (p) => JSON.stringify({
  filter_spec: p.filter_spec ?? null, search: p.search ?? null,
});
// If these two ever disagree, select-all resolves rows the table is not showing
// and hands them to a mass update — worse than the bug it replaced, because it
// over-selects invisibly rather than under-selecting visibly.
check("select-all narrows by exactly the same terms as the page it replaces",
  narrowing(pageReq.params) === narrowing(idsReq.params),
  `${narrowing(pageReq.params)} vs ${narrowing(idsReq.params)}`);

const batched = chunk(Array.from({ length: 2500 }, (_, i) => i + 1), 1000);
check("chunk splits at the cap and keeps the remainder",
  batched.length === 3 && batched.map((b) => b.length).join(",") === "1000,1000,500",
  batched.map((b) => b.length).join(","));
check("chunk loses and duplicates nothing",
  new Set(batched.flat()).size === 2500, new Set(batched.flat()).size);

let inFlight = 0, peak = 0;
const ordered = await mapLimit(Array.from({ length: 20 }, (_, i) => i), 4, async (n) => {
  inFlight += 1; peak = Math.max(peak, inFlight);
  await new Promise((r) => setTimeout(r, 1));
  inFlight -= 1;
  return n * 2;
});
check("mapLimit never exceeds its limit in flight", peak <= 4, `peak ${peak}`);
check("mapLimit returns results in input order",
  ordered.every((v, i) => v === i * 2), JSON.stringify(ordered.slice(0, 5)));

captured.length = 0;
await bookings.bulkRemove(Array.from({ length: 2500 }, (_, i) => i + 1));
const deletePosts = captured.filter(
  (c) => c.verb === "POST" && c.url === "delegates/bulk_delete/");
check("bulkRemove batches at the endpoint's 1000-id cap",
  deletePosts.length === 3 && deletePosts.every((p) => p.body.ids.length <= 1000),
  deletePosts.map((p) => p.body.ids.length).join(","));
check("bulkRemove sends every id exactly once across its batches",
  new Set(deletePosts.flatMap((p) => p.body.ids)).size === 2500,
  new Set(deletePosts.flatMap((p) => p.body.ids)).size);

// ── 5c. The merged plan the mass-update modal renders ───────────────────────
// With a selection past 1000 the preview is several requests, and every number
// on screen is a fold of their plans. Getting that fold wrong misreports the
// blast radius of a write to tens of thousands of rows, so it is asserted here
// rather than trusted to read correctly.
const bulkHook = await load("hooks/useBulkUpdate.js", (s) =>
  s.replace(/from ['"]\.\.\/api\/client['"]/g, `from "file://${clientPath}"`)
    .replace(/^import \{ useToast \}.*$/m, "const useToast = () => () => {};"));
const { mergePlans } = bulkHook;
check("export exists: useBulkUpdate.mergePlans", typeof mergePlans === "function",
  typeof mergePlans);

const PLAN_A = {
  requested: 1000, permitted: 1000, no_op: 40, distribution: { Paid: 600, Pending: 400 },
  plan_hash: "aaa", side_effects: ["delegate_count set to 0"],
  collateral: { count: 5, sample: [{ id: 1, label: "x", parent: "INV-1" }], hidden_count: 2, overflow: 0 },
};
const PLAN_B = {
  requested: 300, permitted: 250, no_op: 10, distribution: { Paid: 100, Cancelled: 200 },
  plan_hash: "bbb", side_effects: ["delegate_count set to 0"],
  collateral: { count: 3, sample: [], hidden_count: 0, overflow: 1 },
};
const merged = mergePlans([PLAN_A, PLAN_B]);
results.literals.merged_plan = merged;

check("merged plan sums requested across batches", merged.requested === 1300, merged.requested);
check("merged plan sums permitted, so rows the caller cannot edit stay visible",
  merged.permitted === 1250, merged.permitted);
check("merged plan sums no_op", merged.no_op === 50, merged.no_op);
check("merged plan adds distribution buckets rather than overwriting them",
  merged.distribution.Paid === 700 && merged.distribution.Pending === 400
  && merged.distribution.Cancelled === 200, JSON.stringify(merged.distribution));
check("merged plan reports side effects once, not once per batch",
  merged.side_effects.length === 1, JSON.stringify(merged.side_effects));
// Collateral is per batch and counts rows in OTHER batches of the same
// selection, so the sum is an upper bound. The flag is what makes the modal say
// "up to" instead of presenting an inflated number as a count.
check("merged collateral is flagged as batched",
  merged.collateral.batched === true && merged.collateral.count === 8,
  JSON.stringify(merged.collateral));
check("multi-batch merge carries no single plan_hash",
  merged.plan_hash === null, JSON.stringify(merged.plan_hash));

const single = mergePlans([PLAN_A]);
check("a single batch keeps its plan_hash and is not flagged batched",
  single.plan_hash === "aaa" && single.collateral.batched === false,
  `${single.plan_hash} ${single.collateral.batched}`);

// A value-less preview has no no_op, and absent must stay absent — the modal
// tells "not asked yet" from "none of them", and a 0 would read as the latter.
const valueless = mergePlans([
  { requested: 10, permitted: 10, distribution: { Paid: 10 }, collateral: {} },
]);
check("merged plan omits no_op when the batches did",
  !("no_op" in valueless), Object.keys(valueless).join(","));


// ── 6. server.mapRow must read the RESOLVED field, not its raw twin ──────────
// DataTable in server mode receives rows straight off the wire, so every column
// keyed on a mapped name depends on `server.mapRow`. This shipped broken for a
// full round: Bookings rendered `payment_status` (the INVOICE-level value the
// serializer also exposes) instead of `effective_payment_status`, and it survived
// visual checking because a plausible status appeared in the cell.
//
// Discriminating BY CONSTRUCTION: each synthetic row sets the raw field and its
// resolved twin to DIFFERENT sentinels, so reading the wrong one cannot pass. No
// live row can do this — every delegate in the database has raw == resolved on all
// four resolved fields, which is exactly why a fixture is required here.
const RAW = "RAW_SENTINEL";

const mapCases = [
  ["bookings.fromApi payment_status -> effective", bookings.fromApi,
   { payment_status: RAW, effective_payment_status: "Paid" }, "payment_status", "Paid"],
  ["bookings.fromApi paid_or_free -> effective", bookings.fromApi,
   { paid_or_free: RAW, effective_paid_or_free: "Free" }, "paid_or_free", "Free"],
  ["bookings.fromApi payment_type -> effective", bookings.fromApi,
   { payment_type: RAW, effective_payment_type: "Stripe" }, "payment_type", "Stripe"],
  ["bookings.fromApi ticket_tier -> effective", bookings.fromApi,
   { ticket_tier: RAW, effective_ticket_tier: "EB" }, "ticket_tier", "EB"],
  ["bookings.fromApi payment_date -> effective", bookings.fromApi,
   { payment_date: "1999-01-01", effective_payment_date: "2026-03-04" }, "payment_date", "2026-03-04"],
  ["bookings.fromApi name <- full_name", bookings.fromApi,
   { full_name: "Ada L", first_name: RAW }, "name", "Ada L"],
  ["bookings.fromApi company_name <- company_display", bookings.fromApi,
   { company_display: "Acme Ltd", company_name_raw: RAW }, "company_name", "Acme Ltd"],
  ["bookings.fromApi owner <- sales_executive_name", bookings.fromApi,
   { sales_executive_name: "Rep One" }, "owner", "Rep One"],
  ["bookings.fromApi added_time <- created_at", bookings.fromApi,
   { created_at: "2026-01-02T03:04:05Z" }, "added_time", "2026-01-02T03:04:05Z"],
  // discount is stored as a FRACTION and displayed as a percent. The raw value
  // reached the cell before, which is how a zero discount read as "0.00".
  ["bookings.fromApi discount fraction -> percent", bookings.fromApi,
   { discount: "0.20" }, "discount", 20],
  ["bookings.fromApi zero discount is 0, not '0.00'", bookings.fromApi,
   { discount: "0.00" }, "discount", 0],
  // booking_code is the DELEGATE's column now, not the invoice's.
  ["bookings.fromApi booking_code is the delegate's own", bookings.fromApi,
   { booking_code: "Group Pass" }, "booking_code", "Group Pass"],
  ["webhooks.fromApi db_status <- db_insert_status", webhooks.fromApi,
   { db_insert_status: "inserted", db_status: RAW }, "db_status", "inserted"],
  ["webhooks.fromApi records = inserted + updated", webhooks.fromApi,
   { records_inserted: 3, records_updated: 4, records: 99 }, "records", 7],
  ["webhooks.fromApi duration_ms = seconds * 1000", webhooks.fromApi,
   { processing_duration: 1.5, duration_ms: 99999 }, "duration_ms", 1500],
  ["webhooks.fromApi retries <- retry_count", webhooks.fromApi,
   { retry_count: 6, retries: 99 }, "retries", 6],
  // tickets.fromApi is a pass-through now: `source_event` was a UI-only field with
  // no backend column, so it was always '' and the column reading it was always
  // blank. The assertion that matters is that a row's real fields survive the
  // mapper untouched — the table and the ticket form both read them by name.
  ["tickets.fromApi passes the row's own fields through", tickets.fromApi,
   { ticket_number: "T-1" }, "ticket_number", "T-1"],
  ["tickets.fromApi keeps added_user_text", tickets.fromApi,
   { added_user_text: "zoho_linq-corporate" }, "added_user_text", "zoho_linq-corporate"],
];

for (const [name, fn, row, key, want] of mapCases) {
  let got;
  try { got = fn(row)[key]; } catch (e) { got = `THREW ${e.message}`; }
  check(name, got === want, `got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
}

// Every server-mode caller must actually EXPORT a mapper. A page wired with
// `server={{ resource }}` and no mapRow is the original defect.
for (const [name, mod] of [["bookings", bookings], ["tickets", tickets],
                           ["webhooks", webhooks]]) {
  check(`${name}.fromApi is exported for server mode`, typeof mod.fromApi === "function", typeof mod.fromApi);
}

// ── 7. The Bookings modal's invoice write ───────────────────────────────────
// Two bugs, both invisible from the browser because the request SUCCEEDED:
//
//   a) Every invoice key was emitted with an `|| <default>` fallback, so a PATCH
//      built from the edit modal's meta ({invoice_number, event_code, event_name})
//      also carried payment_status:'Pending', booking_code:'', company_name:'',
//      request_date:null, discount:0 and source:'manual' — silently emptying the
//      invoice under a table that still looked right, because the delegate rows
//      resolve through their own overrides.
//   b) booking_code, delegate_number, delegate_payment_date and
//      delegate_paid_or_free were absent from the delegate payload, so those four
//      editors in the modal could not save at all.
//
// KEY ABSENCE is the assertion. A PATCH that names a column owns it, so "did not
// send it" is the only form "leave it alone" can take.
const feDelegate = (over = {}) => ({
  id: 501, name: "Ada Lovelace", company_name: "Acme Ltd", email: "ada@acme.test",
  phone_number: "", accounts_contact_email: "", payment_status: "Paid",
  booking_code: "Speaker", delegate_number: 2, request_date: "2026-01-02",
  invoice_date: "2026-01-03", paid_or_free: "Paid", payment_date: "2026-01-04",
  payment_type: "Stripe", ticket_tier: "EB", discount: 20, add_ons: "", reference: "",
  attendance: "Confirmed", ...over,
});
const EDIT_META = { invoice_number: "INV-9", event_code: "GSTU - VV", event_name: "GSTU 2026" };

captured.length = 0;
await bookings.saveInvoiceDelegates("INV-9", EDIT_META, [feDelegate()], 77);
const invBody = captured[captured.length - 1].body;
const invDel = invBody.delegates[0];
results.literals.invoice_patch_body = invBody;

check("invoice PATCH omits invoice fields the caller never set",
  !["company_name", "contact_name", "contact_email", "contact_phone", "source",
    "currency", "request_date", "invoice_date", "discount", "reference",
   ].some((k) => k in invBody),
  Object.keys(invBody).join(","));
check("invoice PATCH carries the delegates' agreed payment status, not 'Pending'",
  invBody.payment_status === "Paid", invBody.payment_status);
check("agreed person-level values clear the per-delegate override",
  invDel.delegate_payment_status === null && invDel.delegate_ticket_tier === null
  && invDel.delegate_payment_date === null,
  JSON.stringify({ s: invDel.delegate_payment_status, t: invDel.delegate_ticket_tier, d: invDel.delegate_payment_date }));
check("delegate payload carries booking_code", invDel.booking_code === "Speaker", invDel.booking_code);
check("delegate payload carries delegate_number", invDel.delegate_number === 2, invDel.delegate_number);
check("delegate payload carries the Attendance - IN? value",
  invDel.attendance === "Confirmed", invDel.attendance);
check("discount is sent as the stored fraction, not the percent shown",
  invDel.discount === 0.2, invDel.discount);
check("invoice PATCH does not send sales_executive — the server derives it",
  !("sales_executive" in invBody), Object.keys(invBody).join(","));

// Delegate Company is a REQUIRED column in both modals and reached nothing: the
// payload had no company_name_raw key at all, so every hand-entered booking
// stored a blank company under a form that refused to submit without one.
check("delegate payload carries the company as company_name_raw",
  invDel.company_name_raw === "Acme Ltd", JSON.stringify(invDel.company_name_raw));

// Whitespace is stripped at the boundary. A padded address passes the server's
// "@" test and stores " ada@acme.test ", which then does not match the unique
// (invoice, email) pair it is supposed to collide with.
captured.length = 0;
await bookings.saveInvoiceDelegates("INV-9", EDIT_META, [
  feDelegate({ name: "  Ada   Lovelace  ", email: "  ada@acme.test  ", company_name: " Acme Ltd " }),
], 77);
const trimmed = captured[captured.length - 1].body.delegates[0];
check("delegate email is trimmed before it is sent",
  trimmed.email === "ada@acme.test", JSON.stringify(trimmed.email));
check("a padded name still splits into first and last",
  trimmed.first_name === "Ada" && trimmed.last_name === "Lovelace",
  JSON.stringify([trimmed.first_name, trimmed.last_name]));
check("delegate company is trimmed before it is sent",
  trimmed.company_name_raw === "Acme Ltd", JSON.stringify(trimmed.company_name_raw));

// ── 7b. The modal must reject what the server rejects ───────────────────────
// THE BUG: the modals tested `!d.email.trim()` and nothing else, so "harrison"
// was posted and the invoice endpoint answered
//   400 {"delegates":["Delegate #1 has an invalid email."]}
// — which the modal replaced with "check the form and try again". Neither the
// row nor the field was named, so "Save booking" read as a dead button.
const { delegateProblem } = delegateRows;
check("export exists: DelegateTable.delegateProblem",
  typeof delegateProblem === "function", typeof delegateProblem);

const okRow = { name: "Ada Lovelace", company_name: "Acme Ltd", email: "ada@acme.test" };
const problemCases = [
  ["a complete row has no problem", [okRow], null],
  ["an email with no @ is caught in the form", [{ ...okRow, email: "harrison" }],
   "Delegate 1 has an invalid email address: harrison"],
  ["a domain with no dot is caught", [{ ...okRow, email: "ada@acme" }],
   "Delegate 1 has an invalid email address: ada@acme"],
  ["a padded address is not reported as invalid", [{ ...okRow, email: "  ada@acme.test " }], null],
  ["the offending ROW is named, not just the field",
   [okRow, { ...okRow, email: "nope" }], "Delegate 2 has an invalid email address: nope"],
  ["a missing email is still caught", [{ ...okRow, email: "  " }], "Delegate 1 needs an email address"],
  ["a missing name is still caught", [{ ...okRow, name: "" }], "Delegate 1 needs a name"],
  ["a missing company is still caught", [{ ...okRow, company_name: "" }], "Delegate 1 needs a company"],
  // The old guard read `d.name.trim()` directly, which THREW on a row whose
  // field was absent rather than empty — a TypeError in the click handler.
  ["an absent field does not throw", [{}], "Delegate 1 needs a name"],
];
for (const [name, rows, want] of problemCases) {
  let got;
  try { got = delegateProblem(rows); } catch (e) { got = `THREW ${e.message}`; }
  check(name, got === want, `got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
}

// ── 7c. The server's reason has to reach the user ───────────────────────────
const errCases = [
  ["a delegate error is passed through verbatim",
   { delegates: ["Delegate #1 has an invalid email."] }, "Delegate #1 has an invalid email."],
  ["a field error names its field",
   { invoice_number: ["This field may not be blank."] }, "Invoice number: This field may not be blank."],
  ["Django's model-speak uniqueness message is rewritten",
   { invoice_number: ["book event with this invoice number already exists."] },
   "Invoice number already exists."],
  ["detail outranks the fields beside it",
   { detail: "You do not have permission.", invoice_number: ["x"] }, "You do not have permission."],
  ["non_field_errors is not prefixed", { non_field_errors: ["Nope."] }, "Nope."],
  ["a bare list body is unwrapped", ["Nope."], "Nope."],
  ["a string body is used as-is", "Nope.", "Nope."],
  // The local copy in TicketFormModal rendered this shape as "[object Object]".
  ["a nested per-item error map is descended into",
   { delegates: { 0: { email: ["Enter a valid email address."] } } }, "Enter a valid email address."],
  ["an empty field list falls through to the next key",
   { invoice_number: [], delegates: ["Delegate #2 is missing a name."] },
   "Delegate #2 is missing a name."],
];
for (const [name, data, want] of errCases) {
  const got = apiErrorMessage({ response: { data } }, "FALLBACK");
  check(name, got === want, `got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
}
check("a bodyless failure falls back to the error's own message",
  apiErrorMessage(new Error("Network Error"), "FALLBACK") === "Network Error", "");
check("an unreadable body falls back",
  apiErrorMessage({ response: { data: {} } }, "FALLBACK") === "FALLBACK", "");

// Delegates that DISAGREE: the invoice must be left alone and each row must carry
// its own override, which is the only thing overrides exist for.
captured.length = 0;
await bookings.saveInvoiceDelegates("INV-9", EDIT_META, [
  feDelegate(),
  feDelegate({ id: 502, email: "b@acme.test", payment_status: "Cancelled" }),
], 77);
const mixedBody = captured[captured.length - 1].body;
check("differing person-level values are not written to the invoice",
  !("payment_status" in mixedBody), Object.keys(mixedBody).join(","));
check("differing person-level values stay as per-delegate overrides",
  mixedBody.delegates[0].delegate_payment_status === "Paid"
  && mixedBody.delegates[1].delegate_payment_status === "Cancelled",
  JSON.stringify(mixedBody.delegates.map((d) => d.delegate_payment_status)));

// ── 8. The delegate transfer request ────────────────────────────────────────
// The endpoint reads `target_event_code` and `invoice_number`
// (BookDelegateViewSet.transfer). The UI holds those values in camelCase, so the
// conversion happens in api/bookings.js — and a slip there is a 400 on a button
// that otherwise looks wired up, which is precisely the class of bug this probe
// exists for. The URL is asserted too: a detail action posted to the wrong path
// answers 404 and reads as "transfer is broken" rather than "the path is wrong".
captured.length = 0;
await bookings.transferDelegate(501, { targetEventCode: "XFR - BB", invoiceNumber: "DST-1" });
const xfer = captured[captured.length - 1];
results.literals.delegate_transfer_body = xfer.body;

check("transfer posts to the delegate's transfer action",
  xfer.verb === "POST" && xfer.url === "delegates/501/transfer/", `${xfer.verb} ${xfer.url}`);
check("transfer body uses the server's field names",
  xfer.body.target_event_code === "XFR - BB" && xfer.body.invoice_number === "DST-1",
  JSON.stringify(xfer.body));

// ── 9. Module wipes: the verb, and the path ─────────────────────────────────
// All five clear_all endpoints are DELETE. Ticket Central's was POST until the
// buttons were added, and a POST to a DELETE-only action answers 405 — on a control
// that is invisible to everyone except one account, so nobody else would ever hit
// it and report the breakage. Pinned here for exactly that reason.
for (const [name, mod, url] of [
  ["bookings", bookings, "invoices/clear_all/"],
  ["tickets", tickets, "tickets/clear_all/"],
]) {
  captured.length = 0;
  await mod.clearAll();
  const wipe = captured[captured.length - 1];
  check(`${name} clear-all is DELETE ${url}`,
    wipe.verb === "DELETE" && wipe.url === url, `${wipe.verb} ${wipe.url}`);
}

// ── 10. Team permissions: the grid every member inherits ────────────────────
// THE BUG THIS COVERS, which outlived the model it was found in. The UI holds a
// grid as {view, create, update, delete} and it used to be spread into the
// request as-is, while the endpoint reads can_view / can_create / can_update /
// can_delete and DEFAULTS EVERY MISSING KEY TO FALSE. So saving a permission
// set returned 200, toasted success, and stored a fully denied grid.
//
// A same-shaped payload cannot be spot-checked by eye — `view: true` and
// `can_view: true` look equally correct in a request log — so the names are
// pinned here and the captured body is replayed at the real endpoint by
// tests_wire_probe.py. The grid now belongs to a TEAM, and the stakes went up
// with it: one wrong save is every member of that team, not one person.
const fullGrid = {};
ALL_MODULES.forEach((m) => { fullGrid[m] = { view: true, create: true, update: true, delete: true }; });

captured.length = 0;
await teams.savePermissions(7, fullGrid, { isAllAccess: false });
const teamPut = captured.find((c) => c.verb === "PUT");
const permRows = teamPut?.body?.permissions ?? [];
results.literals.team_permissions_body = teamPut?.body ?? null;

check("team permissions PUT goes to the team's permissions action",
  teamPut?.url === "teams/7/permissions/", `${teamPut?.verb} ${teamPut?.url}`);
check("team permissions use the backend's can_* field names",
  permRows.length > 0 && permRows.every((p) => ["can_view", "can_create", "can_update", "can_delete"]
    .every((k) => typeof p[k] === "boolean")),
  JSON.stringify(permRows[0]));
check("team permissions do NOT send the UI's bare view/create/update/delete keys",
  permRows.every((p) => !("view" in p) && !("create" in p) && !("update" in p) && !("delete" in p)),
  JSON.stringify(permRows[0]));
check("every registered module is sent, so none is left at its old value",
  permRows.length === ALL_MODULES.length
  && ALL_MODULES.every((m) => permRows.some((p) => p.module === m)),
  `${permRows.length} rows for ${ALL_MODULES.length} modules`);
check("a ticked box arrives as true, not dropped",
  permRows.every((p) => p.can_view && p.can_create && p.can_update && p.can_delete),
  JSON.stringify(permRows.find((p) => !p.can_view) ?? "all true"));
check("is_all_access travels with the grid",
  teamPut?.body?.is_all_access === false, JSON.stringify(teamPut?.body?.is_all_access));

// An unticked grid must send explicit falses rather than omitting the keys —
// revoking a permission is a real operation and has to travel.
captured.length = 0;
const emptyGrid = {};
ALL_MODULES.forEach((m) => { emptyGrid[m] = { view: false, create: false, update: false, delete: false }; });
await teams.savePermissions(7, emptyGrid);
const revoked = captured.find((c) => c.verb === "PUT").body.permissions;
check("revoking sends explicit false, it does not omit the key",
  revoked.every((p) => p.can_view === false && p.can_delete === false),
  JSON.stringify(revoked[0]));

// ── 11. User permissions: the DELTA, and its third state ────────────────────
// The form works in effective terms and api/users.js derives the delta against
// the team. null is the default and it means INHERIT: a cell that agrees with
// the team must send null, not the value they happen to share, or the person is
// frozen at today's answer and the next widening of their team passes them by.
//
// bool(null) is false on the server, so a null that leaked through as `false`
// would read as a REVOKE. That is the failure this section exists to catch: it
// looks like agreement and behaves like a denial.
const teamMatrix = {};
ALL_MODULES.forEach((m) => { teamMatrix[m] = { view: true, create: false, update: false, delete: false }; });

const desired = {};
ALL_MODULES.forEach((m) => { desired[m] = { ...teamMatrix[m] }; });
desired.reports = { view: true, create: true, update: false, delete: false };   // grant
desired.bookings = { view: false, create: false, update: false, delete: false }; // revoke

captured.length = 0;
await users.savePermissions(5, desired, teamMatrix);
const userPut = captured[captured.length - 1];
const deltas = userPut.body.permissions;
results.literals.user_permissions_body = userPut.body;
const byModule = Object.fromEntries(deltas.map((d) => [d.module, d]));

check("user permissions PUT goes to the user's permissions action",
  userPut.verb === "PUT" && userPut.url === "users/5/permissions/",
  `${userPut.verb} ${userPut.url}`);
check("a cell matching the team is sent as null, so it keeps inheriting",
  byModule.events.can_view === null && byModule.events.can_create === null,
  JSON.stringify(byModule.events));
check("an extra grant is sent as true",
  byModule.reports.can_create === true, JSON.stringify(byModule.reports));
check("a revoke is sent as false, not as an omission",
  byModule.bookings.can_view === false, JSON.stringify(byModule.bookings));
check("an agreeing cell inside an overridden module still inherits",
  byModule.reports.can_view === null, JSON.stringify(byModule.reports));
check("every module is present, so a cleared exception is actually cleared",
  deltas.length === ALL_MODULES.length, `${deltas.length} of ${ALL_MODULES.length}`);
check("no user permission cell is undefined — null is the inherit signal",
  deltas.every((d) => ["can_view", "can_create", "can_update", "can_delete"]
    .every((k) => d[k] === null || typeof d[k] === "boolean")),
  JSON.stringify(deltas[0]));

// ── 11b. Users: the create/edit body ───────────────────────────────────────
// `is_lead` is the frontend's name and `is_team_lead` is the column; sending the
// former means the checkbox is accepted and discarded. `team_id` is the field
// that now grants access — a create that drops it makes an account that can see
// nothing, which reads as "the new user is broken", not "the form is".
captured.length = 0;
await users.create({
  username: "ada", email: "ada@iq-hub.com", first_name: "Ada", last_name: "Lovelace",
  role: "sales", status: "active", team_id: 3, is_lead: true,
  password: "hunter2hunter2",
});
const userPost = captured[captured.length - 1];
results.literals.user_create_body = userPost.body;
check("new user posts to users/", userPost.verb === "POST" && userPost.url === "users/",
  `${userPost.verb} ${userPost.url}`);
check("user create sends is_team_lead, not the UI's is_lead",
  userPost.body.is_team_lead === true && !("is_lead" in userPost.body), JSON.stringify(userPost.body));
check("user create carries the team that grants access",
  userPost.body.team_id === 3, JSON.stringify(userPost.body.team_id));
check("user create carries the password when one was typed",
  userPost.body.password === "hunter2hunter2", String(!!userPost.body.password));
// There is no permission set to pick any more; sending one would be writing to a
// column that no longer exists, and DRF ignores unknown keys silently.
check("user create does NOT send a permission set — access comes from the team",
  !("custom_role_id" in userPost.body), Object.keys(userPost.body).join(","));

// A blank password box means "leave it alone", never "set it to empty".
captured.length = 0;
await users.update(5, { first_name: "Ada", password: "" });
const pwPatch = captured[captured.length - 1].body;
check("an untouched password box is not sent",
  !("password" in pwPatch), Object.keys(pwPatch).join(","));

// A PATCH that names a column owns it, so an edit must send only what changed.
captured.length = 0;
await users.update(5, { status: "inactive" });
const userPatch = captured[captured.length - 1];
check("user edit patches only the keys it was given",
  userPatch.url === "users/5/" && Object.keys(userPatch.body).join(",") === "status",
  `${userPatch.url} ${JSON.stringify(userPatch.body)}`);

// null is meaningful — it unassigns — so it must survive the "only send what was
// given" filter rather than being treated as absent.
captured.length = 0;
await users.update(5, { team_id: null });
const unassign = captured[captured.length - 1].body;
check("unassigning a team sends null, not nothing",
  unassign.team_id === null, JSON.stringify(unassign));

// The endpoint flips the status; an empty body is the whole request. It used to
// be rejected with 400 for not naming a status, on a button called "Deactivate".
captured.length = 0;
await users.toggleStatus(5);
const toggle = captured[captured.length - 1];
results.literals.user_toggle_body = toggle.body;
check("toggle-status patches the user's toggle action with an empty body",
  toggle.verb === "PATCH" && toggle.url === "users/5/toggle-status/"
  && Object.keys(toggle.body).length === 0,
  `${toggle.verb} ${toggle.url} ${JSON.stringify(toggle.body)}`);

captured.length = 0;
await users.resetPassword(5, "hunter2hunter2");
const reset = captured[captured.length - 1];
check("reset-password sends both halves the endpoint compares",
  reset.url === "users/5/reset-password/"
  && reset.body.password === "hunter2hunter2" && reset.body.confirm_password === "hunter2hunter2",
  `${reset.url} ${JSON.stringify(Object.keys(reset.body))}`);

// ── 12. Teams: create and edit ─────────────────────────────────────────────
captured.length = 0;
await teams.create({ name: "Market Research", color: "#009CBC", description: "MR team" });
const teamPost = captured[captured.length - 1];
results.literals.team_create_body = teamPost.body;
check("new team posts to teams/", teamPost.verb === "POST" && teamPost.url === "teams/",
  `${teamPost.verb} ${teamPost.url}`);
check("team create carries name, colour and description",
  teamPost.body.name === "Market Research" && teamPost.body.color === "#009CBC"
  && teamPost.body.description === "MR team", JSON.stringify(teamPost.body));
// slug is derived server-side and is unique; sending a client guess is how two
// teams with the same name collide.
check("team create does not send a client-guessed slug",
  !("slug" in teamPost.body), Object.keys(teamPost.body).join(","));

captured.length = 0;
await teams.update(3, { name: "Market Research EU" });
const teamPatch = captured[captured.length - 1];
check("team edit patches only the keys it was given",
  teamPatch.verb === "PATCH" && teamPatch.url === "teams/3/"
  && Object.keys(teamPatch.body).join(",") === "name",
  `${teamPatch.verb} ${teamPatch.url} ${JSON.stringify(teamPatch.body)}`);

// ── 13. Team name -> role, the copy that has to match the server's ─────────
// The Add user form fills Role in the moment a Team is picked, because the
// server derives the same value on save and a form that did not show it left
// the user looking at one role while a different one was stored. That preview is
// only worth anything if it agrees with User.save(). Both are keyword chains
// where ORDER decides the answer, so "Telesales" and "Speaker Sales Ops" are the
// interesting names, not the obvious ones.
//
// The names and this side's answers are handed to tests_wire_probe.py, which
// puts the SAME names through accounts.models.role_from_team_name and fails on
// any disagreement. Neither copy is trusted; they are checked against each other.
const { roleFromTeamName, TEAM_NAME_ROLE_KEYWORDS } = roleFromTeam;
check("export exists: roleFromTeam.roleFromTeamName",
  typeof roleFromTeamName === "function", typeof roleFromTeamName);

const TEAM_NAMES = [
  "Sales", "Sales Team", "Market Research", "MARKET RESEARCH ", "  market research  ",
  "Data Mining", "DMD", "SpEx", "spex crew", "Operations", "Ops", "Operation",
  "Speaker Sales", "Speaker Sales Ops", "Telemarketing", "Tele Marketing",
  "Telesales", "Tele", "Admin", "Admin Team", "admin support",
  "Finance", "Random Team", "", "   ",
];
results.literals.team_name_role_map = Object.fromEntries(
  TEAM_NAMES.map((n) => [n, roleFromTeamName(n)]),
);
results.literals.team_name_role_keywords = TEAM_NAME_ROLE_KEYWORDS;

check("a name with no keyword implies nothing, rather than defaulting to sales",
  roleFromTeamName("Finance") === null, JSON.stringify(roleFromTeamName("Finance")));
check("'tele' is tested before 'sales', so Telesales is telemarketing",
  roleFromTeamName("Telesales") === "telemarketing", roleFromTeamName("Telesales"));
check("'ops' is tested before 'speaker sales'",
  roleFromTeamName("Speaker Sales Ops") === "operations", roleFromTeamName("Speaker Sales Ops"));
check("matching ignores case and surrounding space",
  roleFromTeamName("  MARKET RESEARCH  ") === "market_research",
  roleFromTeamName("  MARKET RESEARCH  "));
check("an admin-named team implies the admin role",
  roleFromTeamName("Admin Team") === "admin", roleFromTeamName("Admin Team"));

results.pass = results.checks.every((c) => c.pass);
process.stdout.write(JSON.stringify(results, null, 2));
