"""
A user with no first/last name still has to render as a NAME, not a login handle.
"""
from django.test import SimpleTestCase

from .models import User, humanize_username


class HumanizeUsernameTests(SimpleTestCase):
    def test_shapes(self):
        cases = {
            "arthur.pina": "Arthur Pina",
            "harrison_peck": "Harrison Peck",
            "arthur.pina@iq-hub.com": "Arthur Pina",
            "ada": "Ada",
            "maria.o-neill": "Maria O-Neill",
            "  spaced  out ": "Spaced Out",
            "": "",
            None: "",
        }
        for raw, want in cases.items():
            self.assertEqual(humanize_username(raw), want, raw)


class GetFullNameTests(SimpleTestCase):
    def test_recorded_name_wins(self):
        u = User(username="ada.l", first_name="Ada", last_name="Lovelace")
        self.assertEqual(u.get_full_name(), "Ada Lovelace")

    def test_username_is_humanized_when_no_name_is_recorded(self):
        self.assertEqual(User(username="arthur.pina").get_full_name(), "Arthur Pina")
