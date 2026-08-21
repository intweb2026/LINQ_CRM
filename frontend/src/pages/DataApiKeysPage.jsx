import { useState } from 'react';
import Modal from '../components/Modal';
import { EmptyState, PageHead } from '../components/UI';
import { Icon } from '../lib/icons';
import { nf, rel } from '../lib/helpers';
import * as keysApi from '../api/dataApiKeys';
import { useFetch } from '../hooks/useFetch';
import { useSession } from '../context/SessionContext';
import { useConfirm } from '../context/ConfirmContext';
import { useToast } from '../context/ToastContext';
import NoAccessPage from './NoAccessPage';
import { HP_USERNAME } from '../lib/constants';

/**
 * Data API keys — the credentials external consumers (Google Sheets, Apps
 * Script) use to read from /api/data/. This page replaces
 * `manage.py create_data_api_key`, which still works and is still the only way
 * to mint an UNSCOPED key: the form below requires at least one scope, because
 * an empty scope list means unrestricted on the backend, and that must not be
 * what an unfilled checkbox group produces.
 *
 * ONE ACCOUNT, NOT A ROLE. This page is reachable only by HP, and the same is
 * true of /api/data/keys/ (accounts.permissions.IsHPAccount). No permission
 * grant opens it: a key minted here reads the whole export API, is shown in the
 * clear exactly once, and the list alone tells you what every live credential
 * can reach — so merely VIEWING is as sensitive as creating, and the audience is
 * a named owner rather than anything a role can hold.
 *
 * The username check below and the rail's `hpOnly` entry read the same constant
 * as each other, and it mirrors the server's. The 403 fallback is kept anyway
 * and is not redundant: the server is the authority, and a client-side identity
 * test is only ever a way to spare people a page they cannot use.
 */
export default function DataApiKeysPage() {
  const { user } = useSession();
  const toast = useToast();
  const confirm = useConfirm();
  // `immediate: isHP` — the guard below is a render-time return, which happens
  // AFTER the hooks have run, so without this every non-HP visitor still fires a
  // list call that can only ever answer 403. Skipping it is the difference
  // between a bounce and a bounce plus a logged permission failure.
  const isHP = user?.username === HP_USERNAME;
  const { data: keys, error, refetchQuiet: reload } = useFetch(
    keysApi.list, [isHP], { initialData: [], immediate: isHP },
  );
  const [createOpen, setCreateOpen] = useState(false);
  const [scopes, setScopes] = useState([]);
  const [saving, setSaving] = useState(false);
  // Held only until the admin dismisses it. Never persisted, never refetchable.
  const [minted, setMinted] = useState(null);

  if (!isHP || error?.response?.status === 403) {
    return (
      <NoAccessPage
        module="Data API Keys"
        reason="This page is restricted to a single account. It cannot be granted under Roles."
      />
    );
  }

  const ROWS = keys || [];

  function toggleScope(s) {
    setScopes((prev) => (prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]));
  }

  function openCreate() {
    setScopes([]);
    setCreateOpen(true);
  }

  async function submit(e) {
    e.preventDefault();
    const name = e.target.elements.name.value.trim();
    if (!name) { toast('Name is required', 'er'); return; }
    if (!scopes.length) { toast('Pick at least one scope', 'er'); return; }
    const expires = e.target.elements.expires_at.value;
    setSaving(true);
    try {
      const res = await keysApi.create({
        name,
        scopes,
        // datetime-local carries no timezone, so it is converted here rather
        // than posted raw. An empty string would be a 400, so send null.
        expires_at: expires ? new Date(expires).toISOString() : null,
      });
      setCreateOpen(false);
      setMinted(res);
    } catch (err) {
      const d = err?.response?.data;
      toast(d?.scopes?.[0] || d?.name?.[0] || d?.detail || 'Could not create the key', 'er');
    } finally {
      setSaving(false);
    }
  }

  function dismissMinted() {
    setMinted(null);
    reload();
  }

  async function doRevoke(row) {
    const ok = await confirm({
      title: 'Revoke ' + row.name + '?',
      sub: 'Every consumer using this key stops reading immediately.',
      body: (
        <p className="hint">
          This cannot be undone. The key stays in the table with its usage history;
          restoring access means minting a new key and updating the consumer.
        </p>
      ),
      ok: 'Revoke key',
      danger: true,
    });
    if (!ok) return;
    try {
      await keysApi.revoke(row.id);
      toast(row.name + ' revoked', 'ok');
      reload();
    } catch {
      toast('Could not revoke the key', 'er');
    }
  }

  // An expired key is still is_active in the database; the authenticator
  // rejects it on expiry. Showing it as plain "active" would misreport why a
  // consumer suddenly stopped reading.
  function statusOf(row) {
    if (!row.is_active) return ['neutral', 'revoked'];
    if (row.is_expired) return ['amber', 'expired'];
    return ['green', 'active'];
  }

  const createBtn = (
    <button className="btn btn-p btn-sm" onClick={openCreate}>
      <Icon name="plus" size={13} />Create key
    </button>
  );

  return (
    <>
      <PageHead actions={createBtn} />
      {ROWS.length ? (
        <div className="tw">
          <div className="tsc">
            <table className="dt dt-form">
              <thead>
                <tr>
                  <th>Name</th><th>Key</th><th>Scopes</th><th>Status</th>
                  <th>Created</th><th>Created by</th><th>Last used</th>
                  <th className="num">Calls</th><th />
                </tr>
              </thead>
              <tbody>
                {ROWS.map((k) => {
                  const [tone, label] = statusOf(k);
                  return (
                    <tr key={k.id}>
                      <td className="st">{k.name}</td>
                      <td className="mono">{k.key_preview || '—'}</td>
                      <td>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
                          {(k.scopes || []).length
                            ? k.scopes.map((s) => <span key={s} className="bg bg-slate"><i />{s}</span>)
                            : <span className="bg bg-amber"><i />all resources</span>}
                        </div>
                      </td>
                      <td><span className={'bg bg-' + tone}><i />{label}</span></td>
                      <td>{k.created_at ? rel(k.created_at) : '—'}</td>
                      <td>{k.created_by || '—'}</td>
                      <td>{k.last_used_at ? rel(k.last_used_at) : 'never'}</td>
                      <td className="num">{nf(k.usage_count)}</td>
                      <td>
                        {k.is_active ? (
                          <button className="btn btn-g btn-sm btn-ic" title="Revoke"
                            onClick={() => doRevoke(k)}><Icon name="lock" size={13} /></button>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <EmptyState icon="key" title="No Data API keys" action={createBtn}
          body="A key lets an external consumer, such as a Google Sheet, read bookings, delegates, events or tickets from this CRM." />
      )}

      {createOpen ? (
        <Modal size="sm" title="Create Data API key" sub="Scope it to the resources the consumer actually needs."
          onClose={() => setCreateOpen(false)}
          footer={<>
            <button className="btn btn-s" onClick={() => setCreateOpen(false)}>Cancel</button>
            <button className="btn btn-p" type="submit" form="dapiKeyForm" disabled={saving}>
              <Icon name="key" size={15} />{saving ? 'Creating...' : 'Create key'}
            </button>
          </>}>
          <form id="dapiKeyForm" onSubmit={submit}>
            <div className="fd" style={{ marginBottom: 12 }}>
              <label className="fd-l">Name<span className="req">*</span></label>
              <input className="in" name="name" maxLength={150} placeholder="e.g. Google Sheets Sync" />
            </div>
            <div className="fd" style={{ marginBottom: 12 }}>
              <label className="fd-l">Scopes<span className="req">*</span></label>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 4 }}>
                {keysApi.SCOPES.map((s) => (
                  <label key={s} style={{ display: 'flex', alignItems: 'center', gap: 7, cursor: 'pointer' }}>
                    <input type="checkbox" checked={scopes.includes(s)} onChange={() => toggleScope(s)} />
                    <span>{s}</span>
                  </label>
                ))}
              </div>
            </div>
            <div className="fd">
              <label className="fd-l">Expires</label>
              <input className="in" type="datetime-local" name="expires_at" />
              <p className="hint" style={{ marginTop: 4 }}>Leave empty for a key that never expires.</p>
            </div>
          </form>
        </Modal>
      ) : null}

      {minted ? (
        <Modal size="sm" title="Key created" sub={minted.name} onClose={dismissMinted}
          footer={<>
            <button className="btn btn-g" onClick={() => {
              navigator.clipboard?.writeText(minted.raw_key);
              toast('Key copied to clipboard', 'ok');
            }}><Icon name="copy" size={15} />Copy key</button>
            <button className="btn btn-p" onClick={dismissMinted}>Done</button>
          </>}>
          <div className="vr wn" style={{ marginBottom: 12 }}>
            <Icon name="warn" size={15} />
            <span>Copy this key now. It will not be shown again.</span>
          </div>
          <div className="fd" style={{ marginBottom: 12 }}>
            <label className="fd-l">API key</label>
            <input className="in mono" readOnly value={minted.raw_key}
              onFocus={(e) => e.target.select()} />
          </div>
          <p className="hint">
            Scoped to {(minted.scopes || []).join(', ')}. The consumer sends it as the
            <b> X-DATA-API-KEY</b> header.
          </p>
        </Modal>
      ) : null}
    </>
  );
}
