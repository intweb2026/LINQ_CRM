"""
Gives each delegate its own booking_code, seeded from the invoice it sits on.

WHY A NEW COLUMN
booking_code lived only on BookEvent, so every delegate on one invoice was
forced to share a single code. The Bookings tab now edits it per delegate,
which an invoice-level column cannot express: a Speaker and a Group Pass on the
same invoice would overwrite one another, last writer winning silently.

WHY THE BACKFILL IS PART OF THIS MIGRATION, NOT A SEPARATE COMMAND
The read side switches to the delegate column in the same deploy
(book_delegate/serializers.py, filters.py, views.py). If the copy did not run
here, every one of the ~14.8k delegate rows would read as an empty booking code
between the schema change and the backfill — and revenue classification is
driven by that string, so "blank" is not a cosmetic gap.

The copy is ONE correlated UPDATE, not a loop. The first version of this migration
ran an .update() per distinct code with the matching invoice numbers materialised
into an IN list — 5,736 values for "Speaker" alone — and had to be killed after
five minutes. The correlated form matches on book_events.invoice_number, which is
unique and indexed, and it deliberately avoids save() (which would re-derive
event_code/edition as a side effect of a data copy).
"""
from django.db import migrations, models
from django.db.models import OuterRef, Subquery, Value
from django.db.models.functions import Coalesce


def copy_invoice_codes_to_delegates(apps, schema_editor):
    BookDelegate = apps.get_model("book_delegate", "BookDelegate")
    BookEvent = apps.get_model("book_event", "BookEvent")

    # The FK is to_field=invoice_number, so invoice_id IS the invoice number.
    invoice_code = (
        BookEvent.objects
        .filter(invoice_number=OuterRef("invoice_id"))
        .values("booking_code")[:1]
    )
    # Coalesce, because the FK carries db_constraint=False: a delegate whose
    # invoice row is missing would otherwise be handed NULL for a NOT NULL column.
    BookDelegate.objects.update(
        booking_code=Coalesce(Subquery(invoice_code), Value(""))
    )


def clear_delegate_codes(apps, schema_editor):
    """Reverse: the invoice column is untouched by the forward pass, so dropping
    the delegate values loses nothing that was not already stored there."""
    BookDelegate = apps.get_model("book_delegate", "BookDelegate")
    BookDelegate.objects.update(booking_code="")


class Migration(migrations.Migration):

    dependencies = [
        ('book_delegate', '0011_bookdelegate_import_batch_id'),
        # The backfill reads BookEvent.booking_code.
        ('book_event', '0021_bookevent_payment_status_iq_staff'),
    ]

    operations = [
        migrations.AddField(
            model_name='bookdelegate',
            name='booking_code',
            field=models.CharField(blank=True, db_index=True, default='', max_length=100),
        ),
        migrations.RunPython(copy_invoice_codes_to_delegates, clear_delegate_codes),
    ]
