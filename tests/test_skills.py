"""Skill-format validation for both projections + OpenClaw independence."""
import re
import subprocess
import unittest

from helpers import ROOT

CLAUDE_SKILL = ROOT / "adapters" / "claude" / "SKILL.md"
CODEX_SKILL = ROOT / "adapters" / "codex" / "SKILL.md"
CODEX_YAML = ROOT / "adapters" / "codex" / "agents" / "openai.yaml"


def frontmatter(path):
    text = path.read_text()
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    assert m, f"{path} missing YAML frontmatter"
    return m.group(1)


def top_level_keys(fm):
    return [line.split(":")[0] for line in fm.splitlines()
            if line and not line.startswith((" ", "\t", "-", "#"))]


class TestClaudeSkill(unittest.TestCase):
    def test_frontmatter_and_name(self):
        fm = frontmatter(CLAUDE_SKILL)
        keys = top_level_keys(fm)
        self.assertIn("name", keys)
        self.assertIn("description", keys)
        self.assertIn("name: goal-loop", fm)

    def test_description_has_trigger_and_negative_guidance(self):
        fm = frontmatter(CLAUDE_SKILL)
        self.assertRegex(fm, r"(?i)backlog")
        self.assertRegex(fm, r"(?i)do not use")


class TestCodexSkill(unittest.TestCase):
    def test_frontmatter_has_only_name_and_description(self):
        keys = top_level_keys(frontmatter(CODEX_SKILL))
        self.assertEqual(sorted(keys), ["description", "name"])

    def test_goal_scoping_language(self):
        text = CODEX_SKILL.read_text()
        self.assertIn("$goal-loop", text)
        self.assertRegex(text, r"(?s)/goal\b.*(?i:durable autonomous ownership)")
        self.assertRegex(text, r"(?i)normal prompt")

    def test_no_bare_goal_invocation(self):
        # The native skill trigger is $goal-loop; a bare $goal is a
        # projection bug (ambiguous with the Codex /goal primitive).
        for path in (CODEX_SKILL, CODEX_YAML):
            strays = re.findall(r"\$goal(?!-loop)", path.read_text())
            self.assertEqual(strays, [], f"stray $goal reference in {path}")

    def test_openai_yaml_shape(self):
        text = CODEX_YAML.read_text()
        self.assertIn("interface:", text)
        for key in ("display_name", "short_description", "default_prompt"):
            self.assertIn(key, text)

    def test_openai_yaml_default_prompt_scoping(self):
        text = CODEX_YAML.read_text()
        self.assertIn("$goal-loop", text)
        self.assertRegex(text, r"/goal\b")
        self.assertRegex(text, r"(?i)normal prompt")


class TestOpenClawIndependence(unittest.TestCase):
    def test_no_openclaw_references_in_shipped_files(self):
        # Everything that gets installed or executed must be OpenClaw-free.
        proc = subprocess.run(
            ["grep", "-ril", "openclaw",
             "state.py", "install.py", "adapters", "workflow", "schemas",
             "fixtures"],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(proc.stdout.strip(), "",
                         f"OpenClaw reference found in: {proc.stdout}")

    def test_state_helper_stdlib_only(self):
        import ast
        tree = ast.parse((ROOT / "state.py").read_text())
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module.split(".")[0])
        allowed = {"argparse", "json", "os", "socket", "sys", "uuid",
                   "datetime", "hashlib", "pathlib"}
        self.assertTrue(mods <= allowed, f"non-stdlib imports: {mods - allowed}")


if __name__ == "__main__":
    unittest.main()
