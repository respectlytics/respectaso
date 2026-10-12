"""One set of rules for every tool. AGENTS.md is a link to CLAUDE.md, for the tools that look for that name;
a tool that saves a file of its own over the link makes a second copy, and the two drift apart."""

import os

from django.test import SimpleTestCase

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class OneSetOfRules(SimpleTestCase):
    def test_agents_md_is_a_link_to_claude_md(self):
        if not os.path.exists(os.path.join(REPO, "CLAUDE.md")):
            self.skipTest("this tree ships without CLAUDE.md (the public copy), so there is no link to guard")
        link = os.path.join(REPO, "AGENTS.md")
        self.assertTrue(
            os.path.islink(link),
            "AGENTS.md must be a link to CLAUDE.md, not a file of its own: move any change into CLAUDE.md, then  ln -sf CLAUDE.md AGENTS.md",
        )
        self.assertEqual(os.readlink(link), "CLAUDE.md")
        with open(link, encoding="utf-8") as through_the_link, open(os.path.join(REPO, "CLAUDE.md"), encoding="utf-8") as the_file:
            self.assertEqual(through_the_link.read(), the_file.read())

