import { useState } from 'react';
import Modal from '../../components/Modal';
import { Icon } from '../../lib/icons';
import { apiErrorMessage } from '../../api/client';
import * as attendanceApi from '../../api/attendance';
import { gmailConnectUrl } from '../../api/gmail';

/**
 * The popup behind "Generate QR Codes": pick email or ZIP, then walk the email
 * flow. NEITHER OPTION FIRES ON OPEN — the ZIP used to download the moment the
 * button was pressed, which spent a download on anybody who only wanted to
 * email, so both are now explicit choices.
 *
 * ONE MODAL, FOUR STEPS, so the whole flow reopens cleanly on a re-trigger
 * rather than leaving stale counts on screen from a previous run:
 *
 *   choose      email or ZIP                              Cancel
 *   connect     Gmail is not linked yet                   Connect Gmail
 *   blocked     the server cannot connect anybody yet     Close
 *   review      every email variable, pre-filled          Back / Continue
 *   preview     the real email, and the real count        Back / Confirm & Send
 *   result      Sent / Failed / Skipped, with failures    Close

 * NOTHING IS SENT UNTIL `preview` IS CONFIRMED, and `details` is not skippable:
 * the SCA reviews every variable before each send, whether or not the event
 * already carries it.
 *
 * THE EDITED VALUES ARE NOT SAVED TO THE EVENT. They ride on the preview
 * request and again on the send, so a correction made for one mailing cannot
 * rewrite the catalogue that badges and reports read from. `values` is
 * therefore the single source for both, and the send reuses exactly what the
 * preview was built from; re-reading the event at send time would mean
 * approving one email and dispatching another.
 *
 * The count in `confirm` comes from qr_email_preview, the SAME function the
 * server uses to decide who actually gets emailed, so the number shown here
 * cannot drift from what send_qr_emails does next.
 */
export default function QrEmailModal({ ev, downloading, onDownload, onClose }) {
  const [step, setStep] = useState('choose');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [preview, setPreview] = useState(null);
  const [result, setResult] = useState(null);
  // The email the first real recipient will receive, rendered by the server
  // from the same template the send uses.
  const [sample, setSample] = useState(null);
  const [fields, setFields] = useState([]);
  const [values, setValues] = useState({});
  // {field key -> why it is unusable}, as the server last reported it.
  const [problems, setProblems] = useState({});

  async function startPreview() {
    setBusy(true);
    setError('');
    try {
      const data = await attendanceApi.qrEmailPreview(ev);
      setPreview(data);
      // Three-way, not two. Offering "Connect Gmail" when the server has no
      // usable Google configuration puts a button in front of somebody whose
      // only possible outcome is an error about environment variables.
      if (!data.gmail_connected) {
        setStep(data.gmail_can_connect ? 'connect' : 'blocked');
        return;
      }
      // ALWAYS the form, never skipped. Pre-filled from the event so a
      // complete event is a glance and a Continue, not retyping.
      setFields(data.fields || []);
      setValues(Object.fromEntries(
        (data.fields || []).map((f) => [f.key, f.value || ''])));
      setStep('review');
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not check who would receive an email.'));
    } finally {
      setBusy(false);
    }
  }

  async function connectGmail() {
    setBusy(true);
    setError('');
    try {
      const { authorize_url: url } = await gmailConnectUrl();
      window.location.href = url;
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not start the Gmail connection.'));
      setBusy(false);
    }
  }

  // The page owns the download and toasts its own failure, so the modal just
  // closes behind it rather than reporting the same error twice.
  async function download() {
    setError('');
    await onDownload();
    onClose();
  }

  async function loadSample() {
    setBusy(true);
    setError('');
    try {
      setSample(await attendanceApi.qrEmailSample(ev, values));
      setStep('preview');
    } catch (e) {
      // The server is the authority on what is unusable, so its answer drives
      // the marking rather than a second rule in the browser.
      setProblems(Object.fromEntries(
        (e?.response?.data?.missing_fields || []).map((f) => [f.key, f.reason])));
      setError(apiErrorMessage(e, 'Could not build the email preview.'));
    } finally {
      setBusy(false);
    }
  }

  async function send() {
    setBusy(true);
    setError('');
    try {
      // The same values the preview rendered, not the event row.
      const summary = await attendanceApi.sendQrEmails(ev, values);
      setResult(summary);
      setStep('result');
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not send the QR emails.'));
    } finally {
      setBusy(false);
    }
  }

  if (step === 'choose') {
    return (
      <Modal size="sm" title="QR codes" onClose={onClose}
        footer={<button className="btn btn-s" onClick={onClose}>Cancel</button>}
      >
        <p>Every confirmed person on this event has a QR code. Choose what to do with them.</p>
        <div className="qe-choices">
          <button className="qe-choice" onClick={startPreview} disabled={busy || downloading}>
            <Icon name="mail" size={18} />
            <span>
              <b>{busy ? 'Checking…' : 'Send QR Codes by Email'}</b>
              Emails each confirmed attendee their own QR code from your Gmail
              account. You see the recipient count and confirm before anything
              is sent.
            </span>
          </button>
          <button className="qe-choice" onClick={download} disabled={busy || downloading}>
            <Icon name="download" size={18} />
            <span>
              <b>{downloading ? 'Building ZIP…' : 'Download ZIP'}</b>
              Downloads one PDF per confirmed person, named
              “Firstname Lastname.pdf”, in a single ZIP. Nothing is emailed.
            </span>
          </button>
        </div>
        {error ? <p className="qe-err">{error}</p> : null}
      </Modal>
    );
  }

  if (step === 'connect') {
    return (
      <Modal size="sm" title="Connect Gmail first" onClose={onClose}
        footer={<>
          <button className="btn btn-s" onClick={onClose}>Cancel</button>
          <button className="btn btn-p" onClick={connectGmail} disabled={busy}>
            <Icon name="mail" size={15} />
            {busy ? 'Opening…' : 'Connect Gmail'}
          </button>
        </>}
      >
        <p>
          QR emails are sent from your own Gmail account, so it has to be
          connected once before the first send. You will be sent to Google to
          approve it, then brought back here.
        </p>
        {error ? <p className="qe-err">{error}</p> : null}
      </Modal>
    );
  }

  // No Connect button on purpose: nothing here is fixable by pressing it.
  //
  // `gmail_config_error` is sent only to admins, so the one person who CAN fix
  // this reads what is wrong here rather than in a server log, while everyone
  // else is spared a message about environment variables.
  if (step === 'blocked') {
    return (
      <Modal size="sm" title="Email is not set up yet" onClose={onClose}
        footer={<button className="btn btn-p" onClick={onClose}>Close</button>}
      >
        <p>
          QR codes cannot be emailed yet, because sending has not finished
          being set up on the server.
          {preview?.gmail_config_error ? '' : ' Please ask an administrator to complete it.'}
        </p>
        {preview?.gmail_config_error ? (
          <div className="qe-fail">
            <div className="fs-t">Server configuration, visible to admins</div>
            <p>{preview.gmail_config_error}</p>
          </div>
        ) : null}
        <p className="qe-note">
          Downloading the badges as a ZIP works in the meantime.
        </p>
      </Modal>
    );
  }

  // Every variable, every time. All are mandatory, so Continue stays disabled
  // until each is filled and valid, and problems are marked on the field
  // rather than left for the SCA to hunt after a failed Continue.
  //
  // THE SERVER IS THE AUTHORITY on what counts as a problem: `problems` below
  // is whatever it last answered, and the browser only clears a mark when that
  // field is retyped. A second validity rule here would be a second thing to
  // keep in step with attendance/qr_email.py.
  if (step === 'review') {
    const problem = (f) =>
      (!(values[f.key] || '').trim() && 'Required') || problems[f.key] || '';
    const outstanding = fields.filter(problem);
    const groups = [...new Set(fields.map((f) => f.group))];
    return (
      <Modal size="mdw" title="Review the email details"
        sub="These apply to every email in this batch"
        onClose={onClose}
        footer={<>
          <button className="btn btn-s" onClick={() => setStep('choose')} disabled={busy}>
            Back
          </button>
          <button className="btn btn-p" onClick={loadSample}
            disabled={busy || outstanding.length > 0}>
            {busy ? 'Building preview…' : 'Continue to preview'}
          </button>
        </>}
      >
        <p className="qe-lede">
          Pre-filled from the event record. Anything you change applies to this
          send only and is <b>not</b> written back to the event.
        </p>

        {outstanding.length ? (
          <p className="qe-warn">
            <Icon name="warn" size={14} />
            {outstanding.length} field{outstanding.length > 1 ? 's' : ''} need
            {outstanding.length > 1 ? '' : 's'} attention before you can preview.
          </p>
        ) : null}

        {groups.map((group) => (
          <section className="qe-group" key={group}>
            <h4>{group}</h4>
            <div className="qe-grid">
              {fields.filter((f) => f.group === group).map((f) => {
                const bad = problem(f);
                // The two the CRM cannot supply. Flagged so the SCA knows a
                // blank here is expected rather than a lookup that failed.
                const manual = f.key === 'event_url' || f.key === 'linkedin_url';
                return (
                  <label key={f.key}
                    className={`qe-field${bad ? ' bad' : ''}${manual ? ' manual' : ''}`}>
                    <span>
                      {f.label}
                      {manual ? <i>Enter manually</i> : null}
                    </span>
                    {/* `in` is the CRM's own input class (components.css),
                        so these match every other field in the app and follow
                        its themes without a second set of rules here. */}
                    <input
                      className="in" type={manual ? 'url' : 'text'}
                      value={values[f.key] || ''} autoComplete="off"
                      placeholder={manual ? 'https://…' : f.help}
                      aria-invalid={bad ? 'true' : undefined}
                      onChange={(e) => {
                        const v = e.target.value;
                        setValues((prev) => ({ ...prev, [f.key]: v }));
                        setProblems(({ [f.key]: _drop, ...rest }) => rest);
                      }}
                    />
                    <small className={bad ? 'bad' : undefined}>{bad || f.help}</small>
                  </label>
                );
              })}
            </div>
          </section>
        ))}

        {/* Named so nobody looks for it in the form above and assumes it was
            forgotten. It is the one merge field that is per recipient. */}
        <p className="qe-note">
          <Icon name="qr" size={13} />
          First name and the QR code come from each attendee's own record, so
          every recipient still gets their own.
        </p>
        {error ? <p className="qe-err">{error}</p> : null}
      </Modal>
    );
  }

  // The last stop before anything is sent. The body below is rendered by the
  // server from compose_email, the same function the send calls, so this is
  // the email itself rather than a mockup of it. It sits in a sandboxed
  // iframe: an email body carries its own markup and styles, and dropping that
  // into the app's DOM would let it inherit, and disturb, the CRM's own CSS.
  if (step === 'preview') {
    const skippedTotal = preview.skipped_no_email + preview.skipped_invalid_email
      + preview.skipped_already_sent;
    return (
      <Modal size="mdw" title="Preview and send" onClose={onClose} bodyFill
        footer={<>
          <button className="btn btn-s" disabled={busy}
            onClick={() => setStep('review')}>
            Back
          </button>
          <button className="btn btn-p" onClick={send}
            disabled={busy || !preview.eligible_count}>
            {busy ? 'Sending…' : `Confirm & send to ${preview.eligible_count}`}
          </button>
        </>}
      >
        <p>
          This is the email <b>{sample?.name}</b> ({sample?.to}) will receive,
          with their own QR code. <b>{preview.eligible_count}</b> confirmed
          attendees will each get their own.
        </p>
        {skippedTotal ? (
          <p className="qe-note">
            {skippedTotal} will be skipped
            {preview.skipped_no_email ? ` · ${preview.skipped_no_email} no email on file` : ''}
            {preview.skipped_invalid_email ? ` · ${preview.skipped_invalid_email} invalid email` : ''}
            {preview.skipped_already_sent ? ` · ${preview.skipped_already_sent} already sent` : ''}.
          </p>
        ) : null}
        <div className="qe-subject"><b>Subject:</b> {sample?.subject}</div>
        <iframe className="qe-frame fs-fill" title="Email preview" sandbox=""
          srcDoc={sample?.html || ''} />
        {error ? <p className="qe-err">{error}</p> : null}
      </Modal>
    );
  }

  // step === 'result'
  return (
    <Modal size="sm" title="QR emails" onClose={onClose}
      footer={<button className="btn btn-p" onClick={onClose}>Close</button>}
    >
      {/* A whole-run failure leads, above the counts. Without it the reason
          was repeated once per recipient in the failure list below, in
          Google's own wording, which is how a 403 filled this dialog with the
          same 700-character paragraph twice. */}
      {result.fatal_error ? <p className="qe-err">{result.fatal_error}</p> : null}
      <p>
        Sent: <b>{result.sent}</b> · Failed: <b>{result.failed}</b> · Skipped: <b>{result.skipped}</b>
      </p>
      {result.fatal_error ? (
        <p className="qe-note">
          Sending stopped there, so nobody after that point was attempted. They
          are all still eligible; retry once this is fixed and nobody receives a
          duplicate.
        </p>
      ) : null}
      {result.failures?.length ? (
        <div className="qe-fail">
          <div className="fs-t">Failures</div>
          <ul>
            {result.failures.map((f, i) => (
              <li key={i}>{f.name} ({f.email}) — {f.error}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </Modal>
  );
}
