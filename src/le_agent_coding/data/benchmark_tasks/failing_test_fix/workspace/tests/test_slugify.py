import unittest

from slugify import slugify


class SlugifyTests(unittest.TestCase):
    def test_slugify_collapses_repeated_spaces(self) -> None:
        self.assertEqual(slugify("Le Agent   Agent"), "le-agent-agent")
