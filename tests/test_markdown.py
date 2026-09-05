import shutil
import subprocess
import unittest
from pathlib import Path


APP_JS = Path(__file__).resolve().parents[1] / "static" / "app.js"
NODE_SCRIPT = """
const fs = require("node:fs");
const { renderMarkdown } = require(process.argv[1]);
process.stdout.write(renderMarkdown(fs.readFileSync(0, "utf8")));
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is required for Markdown renderer tests")
class MarkdownRendererTest(unittest.TestCase):
    def render(self, source: str) -> str:
        result = subprocess.run(
            ["node", "-e", NODE_SCRIPT, str(APP_JS)],
            input=source,
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout

    def test_renders_table_alignment_inline_markdown_and_escaped_pipes(self):
        rendered = self.render(
            "| Name | State | Notes |\n"
            "| :--- | :---: | ---: |\n"
            "| **Controller** | ready | `a|b` |\n"
            "| API \\| worker | blocked | <unsafe> |"
        )

        self.assertIn('<div class="markdown-table-wrapper"><table>', rendered)
        self.assertIn('<th scope="col" class="align-left">Name</th>', rendered)
        self.assertIn('<th scope="col" class="align-center">State</th>', rendered)
        self.assertIn('<th scope="col" class="align-right">Notes</th>', rendered)
        self.assertIn('<td class="align-left"><strong>Controller</strong></td>', rendered)
        self.assertIn('<td class="align-right"><code>a|b</code></td>', rendered)
        self.assertIn('<td class="align-left">API | worker</td>', rendered)
        self.assertIn('<td class="align-right">&lt;unsafe&gt;</td>', rendered)
        self.assertEqual(3, rendered.count("<tr>"))
        self.assertNotIn("<p>| Name", rendered)

    def test_rejects_separator_rows_with_fewer_than_three_dashes(self):
        rendered = self.render("| A | B |\n| -- | --- |\n| 1 | 2 |")

        self.assertNotIn("<table>", rendered)
        self.assertIn("<p>| A | B |</p>", rendered)


if __name__ == "__main__":
    unittest.main()
