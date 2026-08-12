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

// api/*.js import './client' — resolve that to the stubbed copy we just wrote.
const clientMod = await load("api/client.js");
const clientPath = join(dir, "api_client.mjs").replace(/\\/g, "/");
const patchClientImport = (s) =>
  s.replace(/from ['"]\.\/client['"]/g, `from "file://${clientPath}"`);

const { serializeParams, bulkUpdate, assertIdArray } = clientMod;
const spec = await load("lib/filterSpec.js");
const { specToJson, partitionConds, toCriterion } = spec;
const bookings = await load("api/bookings.js", patchClientImport);
const tickets = await load("api/tickets.js", patchClientImport);
const webhooks = await load("api/webhooks.js", patchClientImport);
const companies = await load("api/companies.js", patchClientImport);

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
  ["webhooks.fromApi db_status <- db_insert_status", webhooks.fromApi,
   { db_insert_status: "inserted", db_status: RAW }, "db_status", "inserted"],
  ["webhooks.fromApi records = inserted + updated", webhooks.fromApi,
   { records_inserted: 3, records_updated: 4, records: 99 }, "records", 7],
  ["webhooks.fromApi duration_ms = seconds * 1000", webhooks.fromApi,
   { processing_duration: 1.5, duration_ms: 99999 }, "duration_ms", 1500],
  ["webhooks.fromApi retries <- retry_count", webhooks.fromApi,
   { retry_count: 6, retries: 99 }, "retries", 6],
  ["companies.fromApi delegate_count", companies.fromApi,
   { delegate_count: 4242 }, "delegate_count", 4242],
  ["tickets.fromApi source_event defaults to empty", tickets.fromApi,
   { ticket_number: "T-1" }, "source_event", ""],
];

for (const [name, fn, row, key, want] of mapCases) {
  let got;
  try { got = fn(row)[key]; } catch (e) { got = `THREW ${e.message}`; }
  check(name, got === want, `got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
}

// Every server-mode caller must actually EXPORT a mapper. A page wired with
// `server={{ resource }}` and no mapRow is the original defect.
for (const [name, mod] of [["bookings", bookings], ["tickets", tickets],
                           ["webhooks", webhooks], ["companies", companies]]) {
  check(`${name}.fromApi is exported for server mode`, typeof mod.fromApi === "function", typeof mod.fromApi);
}

results.pass = results.checks.every((c) => c.pass);
process.stdout.write(JSON.stringify(results, null, 2));
