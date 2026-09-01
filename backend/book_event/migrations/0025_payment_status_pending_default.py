"""
BookEvent.payment_status goes back to defaulting "Pending", and stops being blankable.

0024 made it blank; a payment status is never blank in this CRM. Payment TYPE
stays blank — that one genuinely is unknown until somebody pays. Metadata only:
default= and blank= live in Django, not in the column, so no stored value moves.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('book_event', '0024_payment_status_blank_default'),
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
