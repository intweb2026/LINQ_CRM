import { useState } from 'react';
import Modal from '../../components/Modal';
import { EmptyState } from '../../components/UI';
import { Icon } from '../../lib/icons';
import { nf, rel } from '../../lib/helpers';
import * as webhooksApi from '../../api/webhooks';
import * as eventsApi from '../../api/events';
import { http } from '../../api/client';
import { useFetch } from '../../hooks/useFetch';
import { useLiveData } from '../../hooks/useLiveData';
import { useToast } from '../../context/ToastContext';

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
function ingestUrl(key) {
  const base = http.defaults.baseURL || '';
  const origin = /^https?:\/\//i.test(base)
    ? base.replace(/\/+$/, '').replace(/\/api$/i, '')
    : window.location.origin;
  return origin + '/api/webhooks/ingest/?X-CRM-API-KEY=' + encodeURIComponent(key);
}

export default function WebhookKeys() {
  const toast = useToast();
  const { data: keys, refetchQuiet: reloadKeys } = useFetch(webhooksApi.listKeys, [], { initialData: [] });
  const { data: events } = useFetch(eventsApi.list, [], { initialData: [] });
  const WH_KEYS = keys || [];
  const EVENTS = events || [];
  const [genOpen, setGenOpen] = useState(false);
  // The delivery counter on each key moves every time the website posts a booking,
  // so this list is out of date without anyone touching the CRM at all.
  const { refreshNow: refresh } = useLiveData(reloadKeys, { resources: ['webhooks'] });

  async function generate(e) {
    e.preventDefault();
    const name = e.target.elements.name.value.trim();
    if (!name) { toast('Name is required', 'er'); return; }
    await webhooksApi.generateKey(name, e.target.elements.event.value);
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
            <table className="dt">
              <thead><tr><th>Name</th><th>Event</th><th>Key</th><th className="num">Calls</th><th>Last used</th><th>Status</th><th /></tr></thead>
              <tbody>
                {WH_KEYS.map((k) => (
                  <tr key={k.id}>
                    <td className="st">{k.name}</td>
                    <td className="mono">{k.event_code}</td>
                    <td className="mono">{k.key || '—'}</td>
                    <td className="num">{nf(k.calls)}</td>
                    <td>{k.last_used ? rel(k.last_used) : '—'}</td>
                    <td><span className={'bg bg-' + (k.active ? 'green' : 'neutral')}><i />{k.active ? 'active' : 'disabled'}</span></td>
                    <td>
                      <div style={{ display: 'flex', gap: 2 }}>
                        <button className="btn btn-g btn-sm btn-ic" title="Copy" onClick={() => { if (!k.key) { toast('No key available yet', 'wn'); return; } navigator.clipboard?.writeText(k.key); toast('Key copied to clipboard', 'ok'); }}><Icon name="copy" size={13} /></button>
                        <button className="btn btn-g btn-sm btn-ic" title="Copy test URL" onClick={() => { if (!k.key) { toast('No key available yet', 'wn'); return; } navigator.clipboard?.writeText(ingestUrl(k.key)); toast('Ingest URL copied, it contains the key', 'wn'); }}><Icon name="link" size={13} /></button>
                        <button className="btn btn-g btn-sm btn-ic" title="Regenerate" onClick={() => regenerate(k.id, k.name)}><Icon name="refresh" size={13} /></button>
                        <button className="btn btn-g btn-sm btn-ic" title="Toggle" onClick={() => toggle(k.id, k.name, k.active)}><Icon name={k.active ? 'pause' : 'play'} size={13} /></button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ fontSize: 11.5, color: 'var(--text-4)', marginTop: 8, lineHeight: 1.5 }}>
            The test URL carries the key in plain text. It is recorded in server access logs and in the
            browser history of anyone who opens it, so share it privately, and regenerate the key once
            the test is finished.
          </div>
        </div>
      ) : (
        <EmptyState icon="key" title="No API Keys Found" body="Generate a key to let the event website submit bookings into this CRM."
          action={<button className="btn btn-s btn-sm" onClick={() => setGenOpen(true)}><Icon name="plus" size={13} />Generate key</button>} />
      )}
      {genOpen ? (
        <Modal size="sm" title="Generate API key" sub="Scope it to one event or leave it unscoped." onClose={() => setGenOpen(false)}
          footer={<><button className="btn btn-s" onClick={() => setGenOpen(false)}>Cancel</button><button className="btn btn-p" type="submit" form="genKeyForm"><Icon name="key" size={15} />Generate</button></>}>
          <form id="genKeyForm" onSubmit={generate}>
            <div className="fd" style={{ marginBottom: 12 }}><label className="fd-l">Name<span className="req">*</span></label><input className="in" name="name" placeholder="e.g. website-prod" /></div>
            <div className="fd"><label className="fd-l">Event</label><select className="in" name="event"><option>ALL</option>{EVENTS.map((e) => <option key={e.id}>{e.event_code}</option>)}</select></div>
          </form>
        </Modal>
      ) : null}
    </>
  );
}
