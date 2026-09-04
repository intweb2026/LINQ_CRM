#!/usr/bin/env python
"""
set_verdicts_2026_09.py
───────────────────────
Writes the Performance Matrix verdicts from the Weekly Event Data status list
supplied on 2026-09-05. The list is embedded below, so this needs no Google
access and no spreadsheet; only the database the Django settings point at.

    cd backend
    python scripts/set_verdicts_2026_09.py            dry run, prints the plan
    python scripts/set_verdicts_2026_09.py --apply    writes the verdicts

Codes are matched to Event.event_code case insensitively; a code with no event
is listed and skipped, never guessed. Only events whose verdict differs are
written, through the same update path the matrix uses, so nothing else on the
row moves. Safe to run twice; the second run reports nothing to change.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Bootstraps Django the way manage.py does, so this runs as a plain script from
# the backend folder with the same .env the server reads.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()

from performance_matrix.management.commands.sync_verdicts_from_sheet import (  # noqa: E402
    apply_changes, plan_changes,
)

# Event code, status. Verbatim from the list; the status text is normalised by
# the same alias map the sheet command uses.
DATA = """
HFE - RS	Going Ahead
WSU - MP	Going Ahead
HIU - VV	Going Ahead
BNZ - RS	Going Ahead
BIU/GS - PM	Going Ahead
SCE - PM	Going Ahead
WLZ - MP	Going Ahead
BGU - AD	Going Ahead
BIUK - PM	Going Ahead
BIZ - PM	Going Ahead
FCM - JS	Postponed
AFS - JS	Going Ahead
THZ - LN	Postponed
CPU - VV	Going Ahead
WLU - MP	Going Ahead
FOU - AD	Going Ahead
SGB - PM	Postponed
DDU - PT	Going Ahead
CCE - VV	Going Ahead
REU - RS	Going Ahead
DOU - JS	Going Ahead
EGU - VV	Going Ahead
BTA - RS	Postponed
DAU - AD	Going Ahead
TIU - PT	Going Ahead
CRU - PT	Postponed
ROU - IP	Postponed
FPU - PT	Postponed
PSU/GS - MP	Going Ahead
SCSG - PM	Going Ahead
BIE - PM	Going Ahead
MMU/GS - JS	Going Ahead
SPE - VV	Going Ahead
HAU - IP	Going Ahead
RGU - AD	Going Ahead
SFU - AD	Going Ahead
VFU - AD	Going Ahead
DSM - VV	Going Ahead
MRU - PT	Going Ahead
CLF - JS	Going Ahead
OBU - PT	Going Ahead
SGC - PM	Going Ahead
SDU - RS	Going Ahead
WLKU - MP	Going Ahead
HIE - VV	Going Ahead
EFU - VV	Postponed
WLE - MP	Going Ahead
HDU - VV	Going Ahead
WMA - JS	Postponed
FLE - PM	Going Ahead
FZU - AD	Going Ahead
WMM - JS	Going Ahead
OIM - JS	Going Ahead
VXU - PM	Going Ahead
DLC - VV	Going Ahead
SGU - PM	Going Ahead
PCU - PM	Postponed
EAU - RS	Going Ahead
OMU - IP	Going Ahead
BGE - AD	Going Ahead
EOU - JS	Going Ahead
FPSU - JS	Postponed
CCC - VV	Going Ahead
ORU - JS	Going Ahead
OSC - JS	Going Ahead
RMU - MP	Postponed
OWE - PM	Going Ahead
OCU - PT	Postponed
STE - IP	Going Ahead
PSE - MP	Going Ahead
PRM - JS	Postponed
SCU - PM	Going Ahead
SPU - VV	Going Ahead
THU - IP	Going Ahead
WIU - MP	Going Ahead
ODU - PT	Going Ahead
PSZ - MP	Going Ahead
SSLU - JS	Postponed
SGE - PM	Going Ahead
MRE - PT	Going Ahead
RSU - JS	Postponed
BLU - AD	Going Ahead
LFU - RS	Going Ahead
WTTU - MP	Going Ahead
ACU - RS	Going Ahead
DRN - JS	Postponed
HFU - RS	Postponed
AVU - RS	Going Ahead
ADA - PT	Postponed
VPU - VV	Postponed
DDE - PT	Going Ahead
WLKE - MP	Going Ahead
FLU - PM	Postponed
PPTX - JS	Going Ahead
WSE - MP	Going Ahead
REF - JS	Postponed
CFS - JS	Going Ahead
DLE - VV	Going Ahead
DIU - JS	Postponed
SGZ - PM	Going Ahead
DOC - JS	Postponed
GGU - RS	Postponed
WDRM - MP	Postponed
DLG - VV	Going Ahead
PRG - JS	Going Ahead
LMA - RS	Postponed
CCM - VV	Postponed
BNC - RS	Going Ahead
STU - IP	Postponed
OIU - JS	Going Ahead
TIE - PT	Going Ahead
MTU - MP	Postponed
RFU - RS	Postponed
SME - RS	Postponed
WTTE - MP	Going Ahead
SIU - RS	Going Ahead
FCU - JS	Going Ahead
SDE - RS	Postponed
PAU - AD	Going Ahead
REE - RS	Postponed
NRU - PM	Postponed
TQU - VV	Going Ahead
PSU - MP	Going Ahead
TLU - RS	Standby
AIU - AD	Going Ahead
BDU - AD	Standby
HRU - VV	Cancelled
CCU - VV	Standby
AVE - RS	Standby
WDU - JS	Standby
CCZ - VV	Standby
BIF - PM	Standby
BIC - PM	Standby
LESU - PM	Standby
CRCU - VV	Standby
CTU - JS	Standby
DLU - VV	Standby
BISG - PM	Standby
GSTU - VV	Standby
SAFU - JS	Standby
WTTZ - MP	Standby
THE - IP	Standby
LDZ - VV	Standby
Feb2027_HIU-VV	Standby
Feb2027_DGRU-VV	Standby
Feb2027_HFE-RS	Standby
Feb2027_BIZ-PM	Standby
Feb2027_BIU-PM	Standby
Feb2027_FAFU-AD	Standby
Feb2027_BRE-PT	Standby
Feb2027_BGU-AD	Standby
Feb2027_HAZ-IP	Standby
Feb2027_WSU-MP	Standby
Feb2027_BMSE-RS	Standby
Feb2027_SFIL-AD	Standby
Feb2027_AFS-JS	Standby
Feb2027_CNZ-RS	Standby
Feb2027_GTPU-JS	Standby
Feb2027_DDU-PT	Standby
Feb2027_DPRU-MP	Standby
Feb2027_PIU-PT	Standby
Feb2027_SCE-DV	Standby
Feb2027_MFE-JS	Standby
Feb2027_EAU-RS	Standby
Feb2027_TIU-PT	Standby
Feb2027_OWUK-PM	Standby
Feb2027_CONE-VV	Standby
Feb2027_SAFE-JS	Standby
Feb2027_FOU-AD	Standby
Feb2027_CMU-PT	Standby
Feb2027_BIUK-PM	Standby
Feb2027_WLU-MP	Standby
Feb2027_SUE-PM	Standby
Feb2027_WMPG-JS	Standby
Feb2027_HAE-IP	Standby
Feb2027_TXU-PM	Standby
Mar2027_BIY-PM	Standby
Mar2027_ADCA-PT	Standby
Mar2027_REU-RS	Standby
Mar2027_HAU-IP	Standby
Mar2027_MSE-RS	Standby
Mar2027_MMU-JS	Standby
Mar2027_FZU-AD	Standby
Mar2027_BDE-AD	Standby
Mar2027_EGU-VV	Standby
Mar2027_NRU-PM	Standby
Mar2027_PSC-MP	Standby
Mar2027_LAE-JS	Standby
Mar2027_CTM-JS	Standby
Mar2027_FDCU-RS	Standby
Mar2027_AIE-AD	Standby
Mar2027_OIM-JS	Standby
Mar2027_WMPU-JS	Standby
Mar2027_WLZ-MP	Standby
Mar2027_CPU-VV	Standby
Mar2027_PSZ-MP	Standby
Mar2027_OBE-PT	Standby
Mar2027_DOU-JS	Standby
Mar2027_BIBC-PM	Standby
Mar2027_FLE-DV	Standby
Mar2027_CCM-VV	Standby
Mar2027_MSU-IP	Standby
Mar2027_VPU-VV	Standby
Mar2027_PRM-JS	Standby
Mar2027_FLTX-DV	Standby
Mar2027_MRU-PT	Standby
Mar2027_WPU-MP	Standby
Mar2027_WSUK-MP	Standby
Apr2027_PPC-JS	Standby
Apr2027_SPU-VV	Standby
Apr2027_ODU-PT	Standby
Apr2027_DSM-VV	Standby
Apr2027_SGC-PM	Standby
Apr2027_RGU-AD	Standby
Apr2027_RPU-PT	Standby
Apr2027_PSE-MP	Standby
Apr2027_DLC-VV	Standby
Apr2027_BINY-PM	Standby
Apr2027_EOU-JS	Standby
Apr2027_THC-IP	Standby
Apr2027_PSPA-MP	Standby
Apr2027_WLKE-MP	Standby
Apr2027_OIC-JS	Standby
Apr2027_DIU-JS	Standby
Apr2027_OSC-JS	Standby
Apr2027_SDU-RS	Standby
Apr2027_TNU-JS	Standby
Apr2027_ORC-JS	Standby
Apr2027_ASRU-RS	Standby
Apr2027_CCC-VV	Standby
Apr2027_RAU-RS	Standby
Apr2027_BAPE-DV	Standby
Apr2027_CLF-JS	Standby
"""


def rows():
    """The embedded list as [code, status] rows, blank lines dropped."""
    out = []
    for line in DATA.strip().splitlines():
        if not line.strip():
            continue
        code, _, status = line.partition("\t")
        out.append([code.strip(), status.strip()])
    return out


def main(argv):
    apply = "--apply" in argv
    plan = plan_changes(rows(), 0, 1)

    print(f"Rows in the list          : {len(rows())}")
    print(f"Verdicts to change        : {len(plan['changes'])}")
    print(f"Already correct           : {len(plan['unchanged'])}")
    print(f"Unknown status, skipped   : {len(plan['unknown'])}")
    print(f"Codes with no event       : {len(plan['unmatched'])}")
    for event, verdict in plan["changes"]:
        print(f"  {event.event_code:<24} {event.verdict or 'Standby':<20} -> {verdict}")
    for code, raw in plan["unknown"]:
        print(f"  UNKNOWN  {code:<24} {raw!r}")
    for code in plan["unmatched"]:
        print(f"  NO EVENT {code}")

    if not apply:
        print("Dry run. Re run with --apply to write these verdicts.")
        return 0
    print(f"Updated {apply_changes(plan['changes'])} verdicts.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
