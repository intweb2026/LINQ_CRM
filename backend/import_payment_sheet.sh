#!/bin/sh
#
# The whole Payment Collection migration, in one run.
#
#   sh backend/import_payment_sheet.sh --dry-run    read the report first
#   sh backend/import_payment_sheet.sh              commit it
#
# Any argument is passed straight to the import step, so --dry-run and
# --skip-activity work here exactly as they do on the command itself.
#
# THREE STEPS AND EACH IS LOAD-BEARING.
#   migrate                 widens invoice_type_basis; without it the long
#                           invoice-type notes cannot be written.
#   refresh_credit_control  creates and buckets the leads the import overlays
#                           onto. Cron already runs this hourly in production,
#                           so it usually changes nothing; running it here is
#                           what makes the script safe on a fresh database,
#                           where every invoice would otherwise report as
#                           having no lead.
#   import_payment_sheet    the overlay. Reads the workbook committed at
#                           credit_control/data/payment_collection.xlsx, so
#                           there is no path to pass and no file to copy into a
#                           container.
#
# `set -e` matters. A failed migration must stop the run rather than let the
# import write 181 leads and then fail on the first long note.
#
# Safe to run twice. The import is keyed on the invoice number and de-duplicates
# its touches, so a second run writes the same values and adds no rows.
set -e

cd "$(dirname "$0")"

python manage.py migrate credit_control
python manage.py refresh_credit_control --skip-hubspot
python manage.py import_payment_sheet "$@"
