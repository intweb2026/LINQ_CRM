"""
pre_event_docs/qr_badges.py
────────────────────────────
One confirmed person, one PDF, all of them in one ZIP.

NO NEW DEPENDENCY, and that is worth saying because a PDF writer is the obvious
thing to reach for. Pillow is already pinned, it draws text and images, and it
writes PDF; segno already renders the QR. So a badge here is one 1-bit image
saved as a single-page PDF, which is exactly what a badge is: a picture that
gets printed and pointed at a camera. Selectable text would be the argument for
reportlab or fpdf2, and nothing about this artifact wants selectable text.

MODE "1", one bit per pixel, deliberately. Pillow encodes a bilevel image into
PDF with CCITT G4, which is what fax machines used for exactly this kind of
content, so a whole badge lands in about 1.5 KB. The same page as an RGB image
is twenty times that, and the QR has two colours in it.

THE QR IS PASTED AT ITS NATURAL SIZE, never resized. segno is asked for an
integer scale, so every module is a perfect square block of pixels and the
quiet zone is a whole number of modules. Resizing a finished QR to a target
pixel width makes modules of 10.27 pixels, and the rounding shows up as uneven
module edges, which is the one thing a decoder cares about.

WHY THE WHOLE ZIP IS BUILT IN MEMORY. The largest confirmed roster in this
database is 172 people, and 240 events have one at all. At roughly 1.5 KB a
badge that is a quarter of a megabyte, so streaming machinery, a temp file or a
zipstream dependency would all be scaffolding for a scale that does not exist
here. If an event ever runs to thousands, this is the function to revisit and
the comment that says so.
"""
import io
import re
import zipfile

import segno
from PIL import Image, ImageDraw, ImageFont

# The badge page WIDTH in pixels, rendered at DPI below; 600 at 200dpi is about
# 76mm, a lanyard insert. The HEIGHT is computed per badge in badge_pdf and is
# deliberately not a constant: a QR's pixel size depends on its version, which
# depends on the length of the event code inside the token, and a company name
# may wrap to two lines. One constant tall enough for the worst case left every
# ordinary badge with a third of a page of white space under it.
PAGE_WIDTH = 600
DPI = 200.0
MARGIN = 44
# Between the code and the first line of text.
GAP = 34

# Integer module scale, so the QR is crisp; see the module docstring. A version
# 5 code (the usual size for one of our tokens) comes out 405px wide at scale 9
# with a 4-module quiet zone, which fits PAGE_WIDTH with margin to spare.
QR_SCALE = 9
QR_BORDER = 4

# In mode "1", 1 is white and 0 is black.
WHITE, BLACK = 1, 0

# Windows forbids these outright, and a path separator in a ZIP entry name is a
# directory rather than a filename. \x00-\x1f covers control characters.
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _font(size):
    """
    Pillow's own scalable default, so no font file has to be shipped or found.

    load_default(size=) returns a real FreeTypeFont on Pillow 10.1 and later,
    which is what makes both sizing and the `anchor` argument below work; the
    old default was a fixed bitmap face that could do neither.
    """
    return ImageFont.load_default(size=size)


def _wrap(text, font, max_px, max_lines):
    """
    `text` broken to fit `max_px`, at most `max_lines` lines, last one ellipsised.

    Measured with the font rather than by counting characters. A proportional
    face makes a character count a guess, and the guess is wrong in the
    direction that matters here: company names in this data run to
    "ENEOS (Beijing) Management Co.,Ltd" and a badge that clips one is a badge
    the desk cannot match to a person.
    """
    words = " ".join((text or "").split()).split(" ")
    if not words or words == [""]:
        return []
    lines, current = [], words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if font.getlength(candidate) <= max_px:
            current = candidate
        else:
            lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
    else:
        lines.append(current)

    if len(lines) > max_lines:
        lines = lines[:max_lines]
    # Something was dropped, so say so rather than ending mid-word.
    if len(lines) == max_lines and (len(lines) < len(words)):
        tail = lines[-1]
        while tail and font.getlength(tail + "…") > max_px:
            tail = tail[:-1]
        if font.getlength(" ".join(lines)) < font.getlength(" ".join(words)):
            lines[-1] = tail + "…"
    return lines


def qr_png(token, scale=6):
    """
    The bare code as PNG bytes, for showing in an email body.

    Smaller scale than the badge: this is read off a screen at arm's length
    rather than printed, and a 405px image is bigger than most mail clients
    will show without scaling it, which is the one thing a decoder minds.
    """
    buffer = io.BytesIO()
    segno.make(token, error="m").save(
        buffer, kind="png", scale=scale, border=QR_BORDER)
    return buffer.getvalue()


def badge_pdf(badge):
    """
    One person's badge as a single-page PDF, as bytes.

    `badge` is one row of the projection in views.qr_codes: the token plus the
    facts a person is identified by at a door.
    """
    buffer = io.BytesIO()
    segno.make(badge["token"], error="m").save(
        buffer, kind="png", scale=QR_SCALE, border=QR_BORDER)
    buffer.seek(0)
    qr_image = Image.open(buffer).convert("1")

    text_width = PAGE_WIDTH - 2 * MARGIN

    # WRAPPED BEFORE THE PAGE EXISTS, which is what lets the page be the size of
    # its contents. font.getlength needs no drawing context, so the whole text
    # block is measured first and the image is made once, at the right height.
    lines = []
    for text, size, max_lines in (
        (badge["name"], 34, 2),
        (badge["company"], 24, 2),
        # THE EVENT NAME, NOT ITS CODE. "REU - RS" is a key this database sorts
        # on; the person holding the badge and the person at the door both read
        # the name. Two lines because catalogue names run long. The code is the
        # fallback only when the delegate's event does not resolve in the
        # catalogue exactly, which services.event_meta leaves blank on purpose.
        (badge.get("event_name") or " ".join(
            str(part) for part in
            (badge["event_code"], badge["edition"]) if part), 22, 2),
    ):
        if not text:
            continue
        font = _font(size)
        block = _wrap(text, font, text_width, max_lines)
        for index, line in enumerate(block):
            # The 8px block separator rides on the LAST line of each block, so
            # the height below is exactly the sum of what gets drawn.
            trailing = 8 if index == len(block) - 1 else 0
            lines.append((line, font, size + 6 + trailing))

    height = (MARGIN + qr_image.height + GAP
              + sum(step for _, _, step in lines) + MARGIN)

    page = Image.new("1", (PAGE_WIDTH, height), WHITE)
    draw = ImageDraw.Draw(page)
    # The code, centred, at its own size.
    page.paste(qr_image, ((PAGE_WIDTH - qr_image.width) // 2, MARGIN))

    y = MARGIN + qr_image.height + GAP
    for line, font, step in lines:
        # anchor="ma" is horizontal middle, vertical ascender, so every line is
        # centred on the page and spaced from a predictable top edge.
        draw.text((PAGE_WIDTH // 2, y), line, font=font, fill=BLACK, anchor="ma")
        y += step

    out = io.BytesIO()
    page.save(out, format="PDF", resolution=DPI)
    return out.getvalue()


def pdf_name(first_name, last_name, taken):
    """
    "Firstname Lastname.pdf", and never the same name twice in one ZIP.

    A SPACE between the two names, not an underscore. The file is read by a
    person looking for one badge in an extracted folder, so it is spelled the
    way the name is spelled. Spaces are legal in ZIP entry names and on every
    filesystem this runs on; what is not legal is in _ILLEGAL below.

    THE SUFFIX IS A DELIBERATE DEVIATION from the requested format, and the
    alternative was losing people. 30 events in this database have two confirmed
    attendees sharing a first and last name -- Karl Kennedy twice on one, David
    Agnew twice on another, and three rows reading TBA on a third. A ZIP cannot
    hold two entries under one name: the second write either replaces the first
    or produces a duplicate entry that unzips to one file. Either way somebody
    who is coming to the event has no badge, and nothing about the export says
    so. So the first Karl Kennedy is "Karl Kennedy.pdf" and the second is
    "Karl Kennedy 2.pdf".

    `taken` is a dict carried across the whole ZIP by the caller, so the numbers
    are stable in roster order rather than per company or per page.
    """
    parts = [_clean(first_name), _clean(last_name)]
    stem = " ".join(part for part in parts if part) or "Attendee"

    seen = taken.get(stem.casefold(), 0) + 1
    taken[stem.casefold()] = seen
    return f"{stem}.pdf" if seen == 1 else f"{stem} {seen}.pdf"


def _clean(value):
    """One name, safe as a filename, with the person's own spelling intact."""
    value = " ".join((value or "").split())
    value = _ILLEGAL.sub("", value)
    # Accents and non-Latin scripts are kept. They are somebody's name, ZIP
    # entry names are UTF-8, and every extractor this century reads them.
    # A trailing dot or space is stripped because Windows silently drops them,
    # which would leave "Ana ." on disk as "Ana" and collide with a real Ana.
    return " ".join(value.split()).strip("._ ")


def zip_badges(badges):
    """
    An iterable of badge rows in, one ZIP of one PDF per person out, as bytes.

    Takes an ITERABLE and not a list, so the caller can hand over a generator
    and no badge exists in memory for longer than it takes to write it into the
    archive. Nothing renders a sheet, a grid or a preview on the way.
    """
    out = io.BytesIO()
    taken, written = {}, 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for badge in badges:
            archive.writestr(
                pdf_name(badge["first_name"], badge["last_name"], taken),
                badge_pdf(badge),
            )
            written += 1
    return out.getvalue(), written
