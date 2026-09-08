import { useMemo, useRef, useState } from 'react';
import Modal from '../../components/Modal';
import { Seg } from '../../components/UI';
import { Icon } from '../../lib/icons';
import { toExcel, printElement, fileName, sheetTitle } from '../../lib/exportSheet';
import { TO_PRINT } from '../../api/preEventDocs';

/**
 * The badge run, and the freeze.
 *
 * THIS MODAL IS WHERE THE WORKBOOK'S ONE REAL BUG GETS FIXED. In the sheet, the
 * badge list and the log of what had been printed were two unconnected things:
 * you exported the list, and then somebody had to remember to paste it into
 * Database_Sent. Skip that and the change lists keep re-flagging people whose
 * badges are already correct and keep listing people who never left, with
 * nothing on screen accounting for it.
 *
 * Here, exporting IS freezing. One action, so the record cannot fall behind the
 * badge table. The undo in the run history is what makes that safe: a freeze
 * made by mistake is one click to remove, which is a better trade than a record
 * that silently rots.
 *
 * `mode` is what the desk is about to do, and it changes only the wording and
 * the filename, never the rows. A full run and a top-up run are the same
 * operation against the log.
 *
 * THERE IS NO BLANK-NAME WARNING ANY MORE. It existed for TBA rows, which the
 * workbook kept with the name cleared; Name Badges now drops them outright, so
 * every row reaching this modal has a name on it.
 */
export default function BadgeRunModal({
  mode, rows, eventLabel, onClose, onConfirm, busy,
}) {
  const [format, setFormat] = useState('excel');
  const sheetRef = useRef(null);

  const isTopUp = mode === 'additional';
  const what = isTopUp ? 'additional badges' : 'name badges';

  // The columns a badge printer is given, headed as the delivered files head
  // them. Name first even though the list is sorted by company, because that is
  // the field the badge is actually for.
  //
  // A TOP-UP RUN CARRIES REMARKS AND A FULL RUN DOES NOT. This export used to
  // be Name and Company for both, which quietly made the freeze a worse file
  // than the tab's own Excel button: the printer was handed a list of
  // corrections with nothing saying which card was new and which replaced one
  // already on the table. "WSE 26 - Additional Name Badge.xlsx", the file that
  // really goes out two days before an event, has that third column.
  const cols = useMemo(() => [
    ['name', 'Full Name'], ['company', 'Company Name'],
    ...(isTopUp ? [['remark', 'Remarks']] : []),
  ], [isTopUp]);

  function run() {
    const name = fileName(eventLabel, what);
    if (format === 'excel') {
      toExcel(rows, cols, name, 'Name badges', {
        title: sheetTitle(eventLabel, isTopUp ? 'Additional Name Badges' : 'Name Badges'),
        section: isTopUp ? TO_PRINT : undefined,
      });
    } else printElement(sheetRef.current, name);
    // Logged AFTER the file exists, so a failed export never records a run that
    // did not happen.
    onConfirm(rows);
  }

  return (
    <Modal
      size="mdw"
      onClose={onClose}
      title={isTopUp ? 'Freeze and print additional badges' : 'Freeze and print name badges'}
      sub={`${rows.length} ${rows.length === 1 ? 'badge' : 'badges'} for ${eventLabel}`}
      bodyFill
      footer={
        <>
          <button className="btn btn-s" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn btn-p" onClick={run} disabled={busy || !rows.length}>
            <Icon name={format === 'excel' ? 'download' : 'sheet'} size={15} />
            {busy ? 'Freezing…' : format === 'excel' ? 'Export and freeze' : 'Print and freeze'}
          </button>
        </>
      }
    >
      <div className="ped-run">
        <div className="ped-run-opts">
          <div className="fs-t">Format</div>
          <Seg
            value={format}
            onChange={setFormat}
            options={[
              { value: 'excel', label: 'Excel' },
              { value: 'print', label: 'PDF / print' },
            ]}
          />
          <p className="ped-note">
            {format === 'excel'
              ? 'Downloads a .xlsx file with one row per badge.'
              : 'Opens the print dialog. Choose Save as PDF there for a PDF.'}
          </p>
        </div>

        {/* The consequence, stated before the button rather than after. */}
        <div className="ped-run-warn">
          <Icon name="info" size={15} />
          <div>
            <b>This freezes the list.</b> Every badge below is recorded as sent
            today, and <b>Additional Name Badges</b> compares against it from now
            on: change a name or company in Bookings after this and it appears
            there as a correction. Until you freeze, that report stays empty.
            A freeze made by mistake can be undone from the run history.
          </div>
        </div>

        {/* What is about to be printed, exactly as it will print. This element
            is also the print target, so the paper and the preview cannot
            disagree. */}
        <div className="fs-t">Badges</div>
        <div className="ped-sheet fs-fill" ref={sheetRef}>
          <div className="ped-sheet-h">
            <h3>{eventLabel}</h3>
            <p>{what} · {rows.length} · {new Date().toLocaleDateString()}</p>
          </div>
          {/* Driven off the same `cols` the workbook is, so the preview, the
              paper and the .xlsx cannot end up with different columns. */}
          <table className="ped-tbl">
            <thead>
              <tr>{cols.map(([, header]) => <th key={header}>{header}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.delegate_id}>
                  {cols.map(([key]) => <td key={key}>{r[key]}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Modal>
  );
}
