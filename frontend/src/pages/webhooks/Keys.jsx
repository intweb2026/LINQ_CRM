import { useState } from 'react';
import Modal from '../../components/Modal';
import { EmptyState } from '../../components/UI';
import { Icon } from '../../lib/icons';
import { nf, rel } from '../../lib/helpers';
import * as webhooksApi from '../../api/webhooks';
import * as eventsApi from '../../api/events';
import * as usersApi from '../../api/users';
import { http } from '../../api/client';
import { useFetch } from '../../hooks/useFetch';
import { useLiveData } from '../../hooks/useLiveData';
import { useToast } from '../../context/ToastContext';

/**
 * Destinations a key can be scoped to, mirroring WebhookApiKey.Target.
 *
 * A destination only exists once there is an endpoint behind it, which is a
 * backend change either way, so this list is code and not data. What is NOT
 * hardcoded here is the path: that arrives per key as `ingest_path`, resolved
 * server-side from webhooks/urls.py, which is why a scoped key now copies its
 * own URL instead of the booking one.
 *
 * ALL is the empty target and must stay the default. Every key issued before
 * the column existed holds it, and it means the key posts to every endpoint.
 */
// The one target that is a browser page rather than an ingest endpoint.
const FORM_TARGET = 'paper_review_form';

const TARGETS = [
  { value: 'ALL', label: 'ALL, every endpoint' },
  { value: 'bookings', label: 'Bookings' },
  { value: 'tickets', label: 'Tickets' },
  { value: 'paper_review', label: 'Paper reviews' },
  // NOT an ingest endpoint. This one is a person's public paper review form
  // link, so it carries a reviewer and its "copy URL" button copies the page
  // the reviewer opens rather than an API path. See
  // backend/paper_review/public_form.py.
  { value: FORM_TARGET, label: 'Paper review form (MRE link)' },
];

const targetLabel = (t) => (TARGETS.find((x) => x.value === (t || 'ALL')) || {}).label || t;

/**
 * The full ingest URL for a key, everything an external team needs in one
 * string, with no header setup.
 *
 * The origin comes from the axios baseURL rather than a literal, because this
 * app is served from more than one host (dev proxy, staging, production) and a
 * hardcoded hostname would hand the tester a URL pointing at somebody else's
 * CRM. baseURL is `/api/` by default, which is relative and carries no origin;
 * only an absolute REACT_APP_API_BASE_URL does, and that one already ends in
 * /api, which has to come off before the path below is appended.
 */
function originOf() {
  const base = http.defaults.baseURL || '';
  return /^https?:\/\//i.test(base)
    ? base.replace(/\/+$/, '').replace(/\/api$/i, '')
    : window.location.origin;
}

function ingestUrl(k) {
  return originOf() + k.ingest_path + '?X-CRM-API-KEY=' + encodeURIComponent(k.key);
}

/**
 * The link an MRE opens, for a form key.
 *
 * This is a FRONT-END route (App.jsx), not an API path, which is why it is
 * written here rather than served as ingest_path: the backend owns the endpoint
 * the page posts to, the router owns the page. `crm_key` is the parameter name
 * api/paperReviewForm.js forwards.
 */
function formUrl(k) {
  return originOf() + '/paper-review/submit?crm_key=' + encodeURIComponent(k.key);
}

const isForm = (k) => k.target === FORM_TARGET;

export default function WebhookKeys() {
  const toast = useToast();
  const { data: keys, refetchQuiet: reloadKeys } = useFetch(webhooksApi.listKeys, [], { initialData: [] });
  const { data: events } = useFetch(eventsApi.list, [], { initialData: [] });
  const { data: users } = useFetch(usersApi.list, [], { initialData: [] });
  const WH_KEYS = keys || [];
  const EVENTS = events || [];
  // Market research only. A form link stamps its reviewer on every review
  // submitted through it, so offering the whole user list would invite links
  // attributed to people who do not review papers.
  const MRES = (users || []).filter((u) => u.role === 'market_research');
  const [genOpen, setGenOpen] = useState(false);
  const [genTarget, setGenTarget] = useState('ALL');
  // The delivery counter on each key moves every time the website posts a booking,
  // so this list is out of date without anyone touching the CRM at all.
  const { refreshNow: refresh } = useLiveData(reloadKeys, { resources: ['webhooks'] });

  async function generate(e) {
    e.preventDefault();
    const name = e.target.elements.name.value.trim();
    if (!name) { toast('Name is required', 'er'); return; }
    const target = e.target.elements.target.value;
    const mre = target === FORM_TARGET ? e.target.elements.mre.value : '';
    if (target === FORM_TARGET && !mre) {
      toast('Pick the reviewer this form belongs to', 'er'); return;
    }
    await webhooksApi.generateKey(
      name, e.target.elements.event.value, target, mre,
    );
    setGenOpen(false); refresh();
    toast('Key generated for ' + name, 'ok');
  }
  async function toggle(id, name, active) {
    await webhooksApi.toggleKey(id);
    refresh();
    toast(name + ' is now ' + (active ? 'disabled' : 'active'), 'ok');
  }
  async function regenerate(id, name) {
    await webhooksApi.regenerateKey(id);
    refresh();
    toast(name + ' key regenerated — update the website config', 'wn');
  }

  return (
    <>
      <div className="tb"><div className="tb-sp" /><button className="btn btn-p btn-sm" onClick={() => setGenOpen(true)}><Icon name="plus" size={13} />Generate key</button></div>
      {WH_KEYS.length ? (
        <div className="tw">
          <div className="tsc">
            <table className="dt dt-form">
              <thead><tr><th>Name</th><th>Destination</th><th>Reviewer</th><th>Event</th><th>Key</th><th className="num">Calls</th><th>Last used</th><th>Status</th><th /></tr></thead>
              <tbody>
                {WH_KEYS.map((k) => (
                  <tr key={k.id}>
                    <td className="st">{k.name}</td>
                    <td>{targetLabel(k.target)}</td>
                    <td>{k.mre_name || '—'}</td>
                    <td className="mono">{isForm(k) ? '—' : k.event_code}</td>
                    <td className="mono">{k.key || '—'}</td>
                    <td className="num">{nf(k.calls)}</td>
                    <td>{k.last_used ? rel(k.last_used) : '—'}</td>
                    <td><span className={'bg bg-' + (k.active ? 'green' : 'neutral')}><i />{k.active ? 'active' : 'disabled'}</span></td>
                    <td>
                      <div style={{ display: 'flex', gap: 2 }}>
                        <button className="btn btn-g btn-sm btn-ic" title="Copy" onClick={() => { if (!k.key) { toast('No key available yet', 'wn'); return; } navigator.clipboard?.writeText(k.key); toast('Key copied to clipboard', 'ok'); }}><Icon name="copy" size={13} /></button>
                        <button className="btn btn-g btn-sm btn-ic" title={isForm(k) ? 'Copy form link' : 'Copy test URL'} onClick={() => { if (!k.key) { toast('No key available yet', 'wn'); return; } navigator.clipboard?.writeText(isForm(k) ? formUrl(k) : ingestUrl(k)); toast(isForm(k) ? 'Form link copied, anyone holding it can submit' : 'Ingest URL copied, it contains the key', 'wn'); }}><Icon name="link" size={13} /></button>
                        <button className="btn btn-g btn-sm btn-ic" title="Regenerate" onClick={() => regenerate(k.id, k.name)}><Icon name="refresh" size={13} /></button>
                        <button className="btn btn-g btn-sm btn-ic" title="Toggle" onClick={() => toggle(k.id, k.name, k.active)}><Icon name={k.active ? 'pause' : 'play'} size={13} /></button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <EmptyState icon="key" title="No API Keys Found" body="Generate a key to let the event website submit bookings into this CRM."
          action={<button className="btn btn-s btn-sm" onClick={() => setGenOpen(true)}><Icon name="plus" size={13} />Generate key</button>} />
      )}
      {genOpen ? (
        <Modal size="sm" title="Generate API key" sub="Pick what the key posts, then scope it to one event or leave it unscoped." onClose={() => setGenOpen(false)}
          footer={<><button className="btn btn-s" onClick={() => setGenOpen(false)}>Cancel</button><button className="btn btn-p" type="submit" form="genKeyForm"><Icon name="key" size={15} />Generate</button></>}>
          <form id="genKeyForm" onSubmit={generate}>
            <div className="fd" style={{ marginBottom: 12 }}><label className="fd-l">Name<span className="req">*</span></label><input className="in" name="name" placeholder="e.g. website-prod" /></div>
            <div className="fd" style={{ marginBottom: 12 }}><label className="fd-l">Destination</label><select className="in" name="target" value={genTarget} onChange={(e) => setGenTarget(e.target.value)}>{TARGETS.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}</select></div>
            {genTarget === FORM_TARGET ? (
              <>
                <div className="fd" style={{ marginBottom: 12 }}><label className="fd-l">Reviewer<span className="req">*</span></label>
                  <select className="in" name="mre" defaultValue="">
                    <option value="">— Select —</option>
                    {MRES.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                  </select>
                </div>
                {/* The form's events come from the reviewer's own assignments, so
                    a per-key event scope would be a second, disagreeing answer to
                    the same question. Hidden rather than dropped: generate()
                    reads this field on every destination. */}
                <input type="hidden" name="event" value="ALL" />
                <p style={{ fontSize: 11.5, color: 'var(--text-3)', margin: 0 }}>
                  The form offers the events this reviewer is assigned to. Copy the
                  link from the row's link button once the key exists.
                </p>
              </>
            ) : (
              <div className="fd"><label className="fd-l">Event</label><select className="in" name="event"><option>ALL</option>{EVENTS.map((e) => <option key={e.id}>{e.event_code}</option>)}</select></div>
            )}
          </form>
        </Modal>
      ) : null}
    </>
  );
}
