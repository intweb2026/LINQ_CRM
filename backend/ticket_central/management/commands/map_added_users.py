"""
map_added_users
───────────────
Stamp `added_user_text` on every ticket from the purpose it was raised under.

WHY THIS EXISTS
Ticket Central scopes its list by author (scoping.scope_tickets): a non-exempt
role sees created_by = me OR added_user_text = my username / my display name.
The 37,001 migrated rows satisfy neither — created_by is the HP admin on all of
them, and added_user_text holds Zoho login names ("zoho_linq-corporate" on
33,295 rows, "josh.serrano1", "mark.paramo2", …) that match no current
username. So an MR user opens the module and sees nothing they raised before
the cutover.

Purpose IS the ownership key here: each MR person owns a fixed set of purpose
codes, and the table below is that ownership list as supplied by the business.

The DISPLAY NAME is what gets written, deliberately — it is the form
TicketCreateSerializer writes for new tickets (serializers.py:168-169), so
migrated and new rows end up under one convention rather than the two
scope_tickets currently has to match against.

    python manage.py map_added_users --dry-run   # preview, writes nothing
    python manage.py map_added_users

Purposes with no owner in the table are left untouched and reported. An owner
name that resolves to no user aborts the run: writing it as free text would
look like a successful mapping while handing those rows to nobody.
"""
import logging
import re

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ticket_central.models import Ticket
from ticket_central.utils import display_name, normalize_purpose

logger = logging.getLogger(__name__)
User = get_user_model()

# purpose <TAB> owner, pasted from the business's ownership sheet; keep that
# shape so a revision is a paste, not a re-typing.
#
# Keys go through normalize_purpose() — upper-cased, whitespace collapsed, the
# same treatment the column itself gets — so the stray trailing space on the
# second "SGE " row and the lower-case tails on "TIEf", "ODU b" and
# "Pharma Generic" all land on the value actually stored.
_OWNERSHIP = """
SCU\tPaxton Medina
SCE\tPaxton Medina
BAPE\tPaxton Medina
FLE\tPaxton Medina
FLU\tPaxton Medina
FLC\tPaxton Medina
BIY\tPaxton Medina
BIU\tPaxton Medina
BIF\tPaxton Medina
BIBY\tPaxton Medina
OWUK\tPaxton Medina
TXU\tPaxton Medina
LESU\tPaxton Medina
NRU\tPaxton Medina
SUE\tPaxton Medina
CMU\tPercy Tovar
ODU\tPercy Tovar
RPU\tPercy Tovar
ADCA\tPercy Tovar
RPE\tPercy Tovar
PSE\tMark Paramo
PSPA\tMark Paramo
WLKE\tMark Paramo
WSUK\tMark Paramo
WTTE\tMark Paramo
BDE\tArchie Diaz
AIE\tArchie Diaz
SFIL\tArchie Diaz
FOU\tArchie Diaz
BGU\tArchie Diaz
FAFU\tArchie Diaz
RAU\tRay Santos
ASRU\tRay Santos
SDU\tRay Santos
DOU\tJosh Serrano
TNU\tJosh Serrano
TNE\tJosh Serrano
DIU\tJosh Serrano
CTU\tJosh Serrano
GTPU\tJosh Serrano
SPE\tVick Varela
CCE\tVick Varela
SPU\tVick Varela
HIE\tVick Varela
GSTU\tVick Varela
HIC\tVick Varela
CPU\tVick Varela
DLU\tVick Varela
VPC\tVick Varela
CCC\tVick Varela
BIBC\tPaxton Medina
BIUK\tPaxton Medina
BIZ\tPaxton Medina
MSU\tIan Pineda
HAU\tIan Pineda
HAE\tIan Pineda
HAZ\tIan Pineda
MRU\tPercy Tovar
OBE\tPercy Tovar
TIU\tPercy Tovar
PIU\tPercy Tovar
DDU\tPercy Tovar
BRE\tPercy Tovar
PSZ\tMark Paramo
WLZ\tMark Paramo
PSC\tMark Paramo
WLU\tMark Paramo
DPRU\tMark Paramo
WSU\tMark Paramo
FZU\tArchie Diaz
PRM\tJosh Serrano
WMPU\tJosh Serrano
OIM\tJosh Serrano
CTM\tJosh Serrano
LAE\tJosh Serrano
MMU\tJosh Serrano
WMPG\tJosh Serrano
SAFE\tJosh Serrano
MFE\tJosh Serrano
AFS\tJosh Serrano
MSE\tRay Santos
EAU\tRay Santos
FDCU\tRay Santos
REU\tRay Santos
CNZ\tRay Santos
BMSE\tRay Santos
HFE\tRay Santos
SCSG\tPaxton Medina
PCU\tPaxton Medina
FLTX\tPaxton Medina
FLIL\tPaxton Medina
FLUK\tPaxton Medina
BINY\tPaxton Medina
BIE\tPaxton Medina
DDE\tPercy Tovar
WPU\tMark Paramo
REE\tRay Santos
OIU\tJosh Serrano
ORC\tJosh Serrano
PPC\tJosh Serrano
OSC\tJosh Serrano
EOU\tJosh Serrano
ALF\tJosh Serrano
WDU\tJosh Serrano
PPTX\tJosh Serrano
VPU\tVick Varela
HDU\tVick Varela
HDE\tVick Varela
CONE\tVick Varela
WTTZ\tMark Paramo
REF\tJosh Serrano
CRCU\tVick Varela
HDZ\tVick Varela
SDE\tRay Santos
BIC\tPaxton Medina
PIE\tPercy Tovar
GLU\tArchie Diaz
RGU\tArchie Diaz
BDU\tArchie Diaz
DYU\tArchie Diaz
AIU\tArchie Diaz
CLF\tJosh Serrano
WMPC\tJosh Serrano
PRG\tJosh Serrano
FCM\tJosh Serrano
ORU\tJosh Serrano
LDZ\tVick Varela
GSTE\tVick Varela
DLG\tVick Varela
DLC\tVick Varela
WMM\tJosh Serrano
EOC\tJosh Serrano
HIU\tVick Varela
SGU\tPaxton Medina
FWAU\tArchie Diaz
DLE\tVick Varela
BIG\tPaxton Medina
OWE\tPaxton Medina
MRE\tPercy Tovar
GPTU\tVick Varela
SAFU\tJosh Serrano
FCU\tJosh Serrano
CCU\tVick Varela
DSM\tVick Varela
DSU\tVick Varela
SGZ\tPaxton Medina
SGE\tPaxton Medina
PSU\tMark Paramo
WLE\tMark Paramo
WIU\tMark Paramo
WTTU\tMark Paramo
SFE\tArchie Diaz
LAU\tJosh Serrano
GCU\tPaxton Medina
VXU\tPaxton Medina
BISG\tPaxton Medina
BIT\tPaxton Medina
OBMA\tPercy Tovar
CLU\tPercy Tovar
WWSG\tMark Paramo
WSZ\tMark Paramo
MTU\tMark Paramo
FWAE\tArchie Diaz
BIS\tPaxton Medina
VXE\tPaxton Medina
DCU\tPaxton Medina
FOE\tArchie Diaz
BIPL\tPaxton Medina
ECU\tPercy Tovar
WSE\tMark Paramo
WGU\tArchie Diaz
CPSU\tJosh Serrano
SPC\tVick Varela
DGRU\tVick Varela
BIP\tPaxton Medina
BII\tPaxton Medina
OPU\tPaxton Medina
BRU\tPercy Tovar
TIE\tPercy Tovar
ADA\tPercy Tovar
MFU\tJosh Serrano
DRN\tJosh Serrano
CCM\tVick Varela
CRCE\tVick Varela
WLSU\tMark Paramo
EGU\tVick Varela
SBBU\tPercy Tovar
ADE\tPercy Tovar
RMC\tMark Paramo
TQU\tVick Varela
OIC\tJosh Serrano
STU\tIan Pineda
LMA\tRay Santos
SIU\tRay Santos
CME\tPercy Tovar
GIG\tJosh Serrano
LISG\tJosh Serrano
MOU\tJosh Serrano
DOC\tJosh Serrano
ATPU\tPercy Tovar
CLE\tPercy Tovar
PHSU\tPercy Tovar
OWU\tPaxton Medina
CEU\tPercy Tovar
WDRM\tMark Paramo
DGRC\tVick Varela
HZE\tVick Varela
LFU\tRay Santos
VFM\tArchie Diaz
VFA\tArchie Diaz
VFU\tArchie Diaz
RGE\tArchie Diaz
PAE\tArchie Diaz
FZE\tArchie Diaz
SFA\tArchie Diaz
SFU\tArchie Diaz
DAU\tArchie Diaz
DAE\tArchie Diaz
SWFZ\tArchie Diaz
SWFE\tArchie Diaz
AQE\tArchie Diaz
BGE\tArchie Diaz
BLE\tArchie Diaz
BLU\tArchie Diaz
AIM\tArchie Diaz
FAU\tArchie Diaz
PFE\tArchie Diaz
PFU\tArchie Diaz
AFU\tArchie Diaz
AQU\tArchie Diaz
SWFU\tArchie Diaz
FAE\tArchie Diaz
LHU\tArchie Diaz
AIZ\tArchie Diaz
SFM\tArchie Diaz
BNZ\tRay Santos
BTA\tRay Santos
ACU\tRay Santos
VTUK\tPercy Tovar
OCU\tPercy Tovar
Pharma Generic\tPercy Tovar
FPU\tPercy Tovar
OCE\tPercy Tovar
OCZ\tPercy Tovar
POU\tPercy Tovar
OBU\tPercy Tovar
ODE\tPercy Tovar
VVU\tPercy Tovar
VVE\tPercy Tovar
CRU\tPercy Tovar
GSUK\tPercy Tovar
GSU\tPercy Tovar
RMU\tMark Paramo
WIE\tMark Paramo
WLKC\tMark Paramo
HLE\tVick Varela
HLU\tVick Varela
HLC\tVick Varela
SLU\tVick Varela
EGE\tVick Varela
MPU\tVick Varela
MPE\tVick Varela
EFU\tVick Varela
EFE\tVick Varela
CDU\tVick Varela
HZU\tVick Varela
PCE\tPaxton Medina
SGC\tPaxton Medina
SGB\tPaxton Medina
TLU\tRay Santos
LMU\tRay Santos
WLKU\tMark Paramo
WMA\tJosh Serrano
PRZ\tJosh Serrano
FPSU\tJosh Serrano
RSU\tJosh Serrano
DOM\tJosh Serrano
DEU\tJosh Serrano
CSU\tJosh Serrano
WMC\tJosh Serrano
PRB\tJosh Serrano
MMC\tJosh Serrano
SSLU\tJosh Serrano
DIM\tJosh Serrano
CFS\tJosh Serrano
REFC\tJosh Serrano
GTPE\tJosh Serrano
GTPM\tJosh Serrano
BNC\tRay Santos
GTPU3\tJosh Serrano
CCZ\tVick Varela
TIU00\tPercy Tovar
RFU\tRay Santos
THZ\tIan Pineda
THU\tIan Pineda
VFE\tArchie Diaz
DAZ\tArchie Diaz
SGE \tPaxton Medina
NRSG\tPaxton Medina
NRC\tPaxton Medina
NRE\tPaxton Medina
HRU\tVick Varela
EPU\tRay Santos
RME\tMark Paramo
MME\tJosh Serrano
ADSG\tPercy Tovar
VVU20-Feb-2026\tVick Varela
SME\tRay Santos
BSFU\tArchie Diaz
ODC\tPercy Tovar
STE\tIan Pineda
BIB\tPaxton Medina
WDRE\tMark Paramo
TIEf\tPercy Tovar
OMU\tIan Pineda
PAU\tArchie Diaz
ADU\tArchie Diaz
THE\tIan Pineda
ROU\tIan Pineda
ODU b\tPercy Tovar
WIZ\tMark Paramo
MRSG\tPercy Tovar
LLOU\tJosh Serrano
FLNU\tJosh Serrano
BSFE\tArchie Diaz
CRE\tPercy Tovar
FPE\tPercy Tovar
SBU\tJosh Serrano
THM\tIan Pineda
HFU\tRay Santos
ROE\tIan Pineda
CPE\tVick Varela
Pharma General\tPercy Tovar
DAA\tArchie Diaz
AIA\tArchie Diaz
FZA\tArchie Diaz
RGZ\tArchie Diaz
BDZ\tArchie Diaz
RSC\tJosh Serrano
HLZ\tVick Varela
PPG\tJosh Serrano
LLMU\tJosh Serrano
FSRU\tJosh Serrano
AFE\tArchie Diaz
AFM\tArchie Diaz
HZD\tVick Varela
WSC\tJosh Serrano
MPZ\tVick Varela
GSTM\tVick Varela
WCU\tMark Paramo
GSTA\tVick Varela
WRU\tMark Paramo
OMZ\tIan Pineda
SLC\tVick Varela
SLE\tVick Varela
SNU\tVick Varela
CYU\tPercy Tovar
DLZ\tVick Varela
MTE\tMark Paramo
GSE\tRay Santos
BNM\tRay Santos
GGE\tRay Santos
GGU\tRay Santos
PRU\tPercy Tovar
OAU\tVick Varela
WRE\tMark Paramo
SRU\tIan Pineda
SRM\tIan Pineda
SRZ\tPercy Tovar
SRE\tPercy Tovar
LHA\tArchie Diaz
DAM\tArchie Diaz
ROSG\tIan Pineda
POE\tPercy Tovar
BPE\tIan Pineda
BPU\tIan Pineda
AIF\tArchie Diaz
STM\tIan Pineda
STZ\tIan Pineda
STSG\tIan Pineda
LRU\tVick Varela
BOU\tPercy Tovar
BOE\tPercy Tovar
AMU\tPercy Tovar
THB\tIan Pineda
PAB\tArchie Diaz
AOT\tVick Varela
COT\tVick Varela
HMU\tRay Santos
MDE\tIan Pineda
MDU\tIan Pineda
MDSG\tIan Pineda
DSA\tVick Varela
WAU\tMark Paramo
WWP\tMark Paramo
FWE\tArchie Diaz
SFZ\tArchie Diaz
HAM\tIan Pineda
CAE\tArchie Diaz
CAU\tArchie Diaz
DRTX\tVick Varela
BTJP\tRay Santos
BTE\tRay Santos
AVSG\tRay Santos
AVE\tRay Santos
LME\tRay Santos
APU\tRay Santos
LMJP\tRay Santos
PCSG\tPercy Tovar
AVU\tRay Santos
BIM\tPaxton Medina
LHE\tArchie Diaz
OSU\tJosh Serrano
PAM\tArchie Diaz
HSU\tJosh Serrano
FFU\tVick Varela
BAU\tRay Santos
ETU\tRay Santos
EGC\tVick Varela
EAE\tRay Santos
"""

# Tab is the sheet's separator; 2+ spaces is what a paste through a terminal
# usually degrades it to. Both, so either paste parses.
_SPLIT = re.compile(r"\t|\s{2,}")


def parse_ownership(raw=_OWNERSHIP):
    """{normalised purpose: owner name}. Raises if two owners claim one purpose."""
    owners = {}
    for line in raw.strip().splitlines():
        if not line.strip():
            continue
        parts = _SPLIT.split(line.strip(), 1)
        if len(parts) != 2:
            raise CommandError(f"Unparseable ownership line: {line!r}")
        purpose, owner = normalize_purpose(parts[0]), parts[1].strip()
        prior = owners.get(purpose)
        if prior and prior != owner:
            raise CommandError(
                f"Purpose {purpose} is claimed by both {prior} and {owner}"
            )
        owners[purpose] = owner
    return owners


def resolve_owners(names):
    """
    ({owner name: text to store}, [names matching no user]).

    Matched on first + last name, which is the form the sheet uses. What gets
    STORED is display_name(user) — the same string the serializer writes for a
    ticket raised in the CRM — so one convention covers migrated and new rows.
    """
    resolved, missing = {}, []
    for name in sorted(names):
        first, _, last = name.partition(" ")
        user = User.objects.filter(
            first_name__iexact=first, last_name__iexact=last,
        ).first()
        if user:
            resolved[name] = display_name(user)
        else:
            missing.append(name)
    return resolved, missing


class Command(BaseCommand):
    help = "Set tickets.added_user_text from each ticket's purpose."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would change but write nothing.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        owners = parse_ownership()

        resolved, missing = resolve_owners(set(owners.values()))
        if missing:
            raise CommandError(
                "No user matches: " + ", ".join(missing)
                + " — fix the name or create the user, then re-run."
            )

        # Grouped by owner so each write is one UPDATE … WHERE purpose IN (…),
        # eight statements rather than one per purpose.
        by_owner = {}
        for purpose, name in owners.items():
            by_owner.setdefault(resolved[name], []).append(purpose)

        total = 0
        with transaction.atomic():
            for stored_name, purposes in sorted(by_owner.items()):
                rows = Ticket.objects.filter(purpose__in=purposes).exclude(
                    added_user_text=stored_name
                )
                n = rows.count() if dry_run else rows.update(
                    added_user_text=stored_name
                )
                total += n
                self.stdout.write(
                    f"  {stored_name:<16}{n:>7} tickets  ({len(purposes)} purposes)"
                )
            if dry_run:
                transaction.set_rollback(True)

        # order_by() BEFORE distinct(): Ticket.Meta.ordering is
        # (-created_at, -id), and those columns join the SELECT list, so
        # .distinct() on the default queryset de-duplicates (purpose,
        # created_at, id) and returns one row per ticket — it printed the same
        # purpose thirteen times before this was cleared.
        unmapped = sorted(
            Ticket.objects.exclude(purpose__in=owners)
            .order_by().values_list("purpose", flat=True).distinct()
        )
        if unmapped:
            self.stdout.write(self.style.WARNING(
                f"Unmapped purposes, left untouched ({len(unmapped)}): "
                + ", ".join(p or "(blank)" for p in unmapped)
            ))

        verb = "Would update" if dry_run else "Updated"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {total} tickets across {len(owners)} purposes "
            f"and {len(by_owner)} users."
        ))
        logger.info(
            "map_added_users: dry_run=%s updated=%d purposes=%d users=%d unmapped=%d",
            dry_run, total, len(owners), len(by_owner), len(unmapped),
        )
