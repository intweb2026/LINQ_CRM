"""
Generator, run LOCALLY only.

Reads the source workbook and emits the single self-contained file production
runs. The payload is the workbook's own Invoice Number, Name, Delegate Number
and Paid/Free columns, gzipped and base64'd into the script, so production needs
no spreadsheet and no arguments.

    python build_prod_script.py <workbook.xlsx> <out.py>
"""
import base64
import gzip
import json
import sys
from pathlib import Path

import openpyxl

TEMPLATE_PATH = Path(__file__).with_name("_fix_template.py")


def cell(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    if s.lower() in ("nan", "none", "nat", "null"):
        return ""
    return " ".join(s.split())


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    wb = openpyxl.load_workbook(str(src), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header = [cell(h) for h in next(rows)]

    def col(*names):
        for n in names:
            if n in header:
                return header.index(n)
        raise SystemExit(f"column not found, tried {names}; header is {header}")

    # The Master Data export spells these "Delegate Count" and "Payable/Free",
    # while the Event Bookings Report spells them "Delegate Number" and
    # "Paid/Free". Both are accepted, and the pair actually used is printed, so
    # the mapping is never a silent guess. Values are normalised downstream;
    # "Payable" means "Paid" to the model.
    i_inv = col("Invoice Number")
    i_name = col("Name")
    i_num = col("Delegate Number", "Delegate Count")
    i_pf = col("Paid/Free", "Payable/Free")
    print(f"  Delegate Number  <- {header[i_num]!r}")
    print(f"  Payable / Free   <- {header[i_pf]!r}")

    payload = []
    for r in rows:
        def get(i):
            return cell(r[i]) if i < len(r) else ""
        inv, name = get(i_inv), get(i_name)
        if not inv or not name:
            continue
        payload.append([inv, name, get(i_num), get(i_pf)])
    wb.close()

    blob = base64.b64encode(
        gzip.compress(
            json.dumps(payload, separators=(",", ":")).encode("utf-8"), 9
        )
    ).decode("ascii")

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    wrapped = "\n".join(
        blob[i:i + 96] for i in range(0, len(blob), 96)
    )
    text = (
        template
        .replace("@@ROWS@@", str(len(payload)))
        .replace("@@SOURCE@@", src.name)
        .replace("__PAYLOAD__", '"""\n' + wrapped + '\n"""')
    )
    out.write_text(text, encoding="utf-8")
    print(f"{len(payload):,} rows embedded, {len(blob):,} base64 chars")
    print(f"wrote {out}  ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
