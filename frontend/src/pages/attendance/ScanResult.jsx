import { Icon } from '../../lib/icons';
import { RESULTS } from '../../api/attendance';

/**
 * One card per outcome, readable in direct sunlight and with colour deficiency.
 *
 * COLOUR IS NEVER THE ONLY SIGNAL. Every card carries a distinct ICON and a
 * one-word VERDICT as well as its tone, because a door is worked outdoors on a
 * phone by whoever is on shift, and a green rectangle and an amber one are the
 * same rectangle in bright sun or to a red-green colour-deficient reader.
 *
 * already_checked_in IS AMBER, NOT RED. The person in front of the desk is
 * registered and has already been let in once; the only new information is that
 * they were scanned twice. Painting it red trains staff to turn away legitimate
 * attendees, which is a worse failure than a double scan.
 *
 * THE SERVER'S OWN SENTENCE IS PREFERRED for the body wherever it sent one. It
 * knows things this component cannot — which event a wrongly-presented badge
 * actually belongs to, and whether this operator is even entitled to be told.
 * The fallbacks below are for the case where it sent nothing.
 */

const CARDS = {
  [RESULTS.CHECKED_IN]: {
    tone: 'ok', icon: 'check', verdict: 'Checked in',
    body: 'Let them through.',
  },
  [RESULTS.ALREADY]: {
    tone: 'warn', icon: 'clock', verdict: 'Already in',
    body: 'This badge has been scanned before. Let them through.',
  },
  [RESULTS.WRONG_EVENT]: {
    tone: 'warn', icon: 'flag', verdict: 'Wrong event',
    body: 'This badge is for a different event.',
  },
  [RESULTS.INVALID_QR]: {
    tone: 'bad', icon: 'x', verdict: 'Not recognised',
    body: 'This badge does not match anybody on the confirmed roster.',
  },
  [RESULTS.INVALID_EVENT]: {
    tone: 'bad', icon: 'x', verdict: 'No event',
    body: 'Pick an event at the top of the page first.',
  },
  [RESULTS.FORBIDDEN_EVENT]: {
    tone: 'bad', icon: 'lock', verdict: 'Not your event',
    body: 'You are not assigned to this event. Ask an administrator.',
  },
  [RESULTS.UNREACHABLE]: {
    tone: 'bad', icon: 'globe', verdict: 'No connection',
    body: 'Could not reach the server, so nothing was recorded. Try again.',
  },
};

const FALLBACK = {
  tone: 'bad', icon: 'warn', verdict: 'Could not check in',
  body: 'Try again, or use Manual check-in.',
};

const time = (value) => {
  if (!value) return '';
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? '' : d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
};

export default function ScanResult({ outcome, onDismiss }) {
  if (!outcome) return null;
  const card = CARDS[outcome.result] || FALLBACK;
  const record = outcome.record;

  return (
    <div className={`att-result att-${card.tone}`} role="status" aria-live="polite">
      <div className="att-result-head">
        <Icon name={card.icon} size={26} />
        <strong>{card.verdict}</strong>
        {onDismiss ? (
          <button className="att-result-x" onClick={onDismiss} aria-label="Dismiss">
            <Icon name="x" size={16} />
          </button>
        ) : null}
      </div>
      {record ? (
        <div className="att-result-who">
          <b>{record.attendee_name}</b>
          <span>{[record.company_name, record.attendee_type].filter(Boolean).join('  ·  ')}</span>
          {record.checked_in_at ? <span>Arrived {time(record.checked_in_at)}</span> : null}
        </div>
      ) : null}
      <p className="att-result-body">{outcome.detail || card.body}</p>
    </div>
  );
}
