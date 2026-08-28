"""
BookEvent.payment_status defaults to BLANK instead of "Pending".

Metadata only: default= lives in Django, not in the column, so nothing is
rewritten and no stored value changes. A booking created by hand, by the website
webhook or by the intake endpoint now records "nobody has said yet" rather than
asserting Pending on its behalf; blank=True is what lets full_clean() and the
choice-validated write paths accept it.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('book_event', '0023_canonical_booking_codes'),
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
                blank=True, db_index=True, default='', max_length=30,
            ),
        ),
    ]
