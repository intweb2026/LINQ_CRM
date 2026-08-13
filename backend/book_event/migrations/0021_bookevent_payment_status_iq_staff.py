"""
Adds "IQ Staff" to BookEvent.PaymentStatus.

Choices-only: no column is rewritten and no data moves. The value is needed
because the Bookings tab now offers it, and a value the model does not declare
fails choice validation on every write path that runs full_clean() — the
mass-update engine (accounts/bulk_update.py) and the choice-typed filter_spec
among them.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('book_event', '0020_bookevent_import_batch_id'),
    ]

    operations = [
        migrations.AlterField(
            model_name='bookevent',
            name='payment_status',
            field=models.CharField(
                choices=[
                    ('Pending', 'Pending'), ('Paid', 'Paid'), ('Unpaid', 'Unpaid'),
                    ('Cancelled', 'Cancelled'), ('Refunded', 'Refunded'), ('Free', 'Free'),
                    ('Credit Pending (Free)', 'Credit Pending (Free)'),
                    ('Credit Pending (Paid)', 'Credit Pending (Paid)'),
                    ('Credit Transferred', 'Credit Transferred'),
                    ('Paid (Transferred)', 'Paid (Transferred)'),
                    ('IQ Staff', 'IQ Staff'),
                ],
                db_index=True, default='Pending', max_length=30,
            ),
        ),
    ]
