"""Skill-format validation for both projections + OpenClaw independence."""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import INSTALL, ROOT, run_cli

CLAUDE_SKILL = ROOT / "adapters" / "claude" / "SKILL.md"
CODEX_SKILL = ROOT / "adapters" / "codex" / "SKILL.md"
CODEX_YAML = ROOT / "adapters" / "codex" / "agents" / "openai.yaml"
LOOP = ROOT / "workflow" / "LOOP.md"
README = ROOT / "README.md"


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


class TestPipelineMandateProjection(unittest.TestCase):
    """Both projections must require the installed agent-pipeline skill,
    with the engine-native invocation form."""

    def test_claude_projection_requires_pipeline(self):
        text = CLAUDE_SKILL.read_text()
        self.assertIn("/pipeline", text)
        self.assertNotIn("$pipeline", text)
        self.assertIn("pipeline:ready-to-deploy", text)
        self.assertIn("~/.claude/skills/pipeline/scripts/pipeline.mjs", text)
        self.assertRegex(text, r"(?i)fail(s|ed)? closed")
        self.assertRegex(text, r"(?i)no non-pipeline fallback")

    def test_codex_projection_requires_pipeline(self):
        text = CODEX_SKILL.read_text()
        self.assertIn("$pipeline", text)
        self.assertIn("pipeline:ready-to-deploy", text)
        self.assertIn("~/.codex/skills/pipeline/scripts/pipeline.mjs", text)
        self.assertRegex(text, r"(?i)fail(s|ed)? closed")
        self.assertRegex(text, r"(?i)no non-pipeline fallback")

    def test_canonical_workflow_encodes_both_invocations_and_merge_flow(self):
        text = LOOP.read_text()
        self.assertIn("/pipeline <N>", text)
        self.assertIn("$pipeline <N>", text)
        self.assertIn("pipeline:ready-to-deploy", text)
        self.assertIn("merge barrier", text)
        self.assertIn("fast-forward", text)
        self.assertIn("merged_shas", text)

    def test_merge_stays_gated_in_both_projections(self):
        for path in (CLAUDE_SKILL, CODEX_SKILL):
            text = path.read_text()
            self.assertRegex(text, r"(?i)only with explicit merge authority")
            self.assertRegex(text, r"(?i)stop at ready-to-deploy")


class TestNativeGoalBootstrap(unittest.TestCase):
    """Native /goal is an operator-owned prerequisite, never a skill-verified
    or skill-invoked capability. See issue #7."""

    DOCS = (LOOP, CLAUDE_SKILL, CODEX_SKILL, CODEX_YAML, README)

    # Only affirmative, self-claiming phrasing is forbidden (goal-loop
    # recursively invoking /goal itself); the required disclaimer sentence
    # ("does not invoke") must survive this check untouched.
    SELF_CLAIM_RE = re.compile(
        r"\b(invokes|calls|triggers|starts|executes)\s+(the\s+)?(native\s+)?"
        r"(/goal(?:-loop)?|\$goal-loop)\b", re.IGNORECASE)

    def test_claude_bootstrap_sequence(self):
        text = CLAUDE_SKILL.read_text()
        self.assertIn("start native `/goal`, then invoke `/goal-loop`", text)

    def test_codex_bootstrap_sequence(self):
        text = CODEX_SKILL.read_text()
        self.assertIn("start native `/goal`, then invoke `$goal-loop`", text)

    def test_loop_bootstrap_sequence(self):
        text = LOOP.read_text()
        self.assertIn("start native `/goal`, then invoke `/goal-loop`", text)
        self.assertIn("start native `/goal`, then invoke `$goal-loop`", text)

    def test_readme_bootstrap_sequence(self):
        text = README.read_text()
        self.assertIn("start native `/goal`, then invoke `/goal-loop`", text)
        self.assertIn("start native `/goal`, then invoke `$goal-loop`", text)

    def test_non_recursion_disclaimer_present(self):
        for path in (LOOP, CLAUDE_SKILL, CODEX_SKILL, README):
            text = path.read_text()
            self.assertRegex(
                text, r"(?i)does not invoke `?/goal", f"missing disclaimer in {path}")

    def test_non_verifiability_language_present(self):
        for path in (LOOP, CLAUDE_SKILL, CODEX_SKILL, README):
            normalized = re.sub(r"\s+", " ", path.read_text())
            self.assertRegex(
                normalized,
                r"(?i)owned by the host/session and cannot be independently"
                r" verified or controlled",
                f"missing non-verifiability language in {path}")

    def test_bounded_use_rule_present(self):
        for path in (LOOP, CLAUDE_SKILL, CODEX_SKILL, README):
            normalized = re.sub(r"\s+", " ", path.read_text())
            self.assertRegex(normalized, r"(?i)normal prompt")
            self.assertRegex(normalized, r"(?i)outside native goal mode")

    def test_no_markdown_link_wrapping(self):
        for path in self.DOCS:
            text = path.read_text()
            for token in (r"/goal-loop", r"\$goal-loop", r"/goal"):
                self.assertNotRegex(
                    text, r"\]\(" + re.escape(token.lstrip("\\")),
                    f"{token} wrapped as a markdown link in {path}")

    def test_no_self_claimed_recursive_invocation(self):
        for path in self.DOCS:
            text = path.read_text()
            matches = self.SELF_CLAIM_RE.findall(text)
            self.assertEqual(matches, [], f"self-claimed invocation in {path}")

    def test_claude_projection_has_no_codex_entrypoint(self):
        text = CLAUDE_SKILL.read_text()
        self.assertNotIn("start native `/goal`, then invoke `$goal-loop`", text)

    def test_codex_projection_has_no_claude_entrypoint(self):
        text = CODEX_SKILL.read_text()
        self.assertNotIn("start native `/goal`, then invoke `/goal-loop`", text)

    def test_installed_projection_carries_bootstrap_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp)
            proc = run_cli(INSTALL, ["install", "--prefix", str(prefix)])
            self.assertEqual(proc.returncode, 0, proc.stderr)

            claude_skill = (prefix / ".claude" / "skills" / "goal-loop" / "SKILL.md").read_text()
            self.assertIn("start native `/goal`, then invoke `/goal-loop`", claude_skill)

            codex_skill = (prefix / ".codex" / "skills" / "goal-loop" / "SKILL.md").read_text()
            self.assertIn("start native `/goal`, then invoke `$goal-loop`", codex_skill)

            for engine, seq in (
                ("claude", "start native `/goal`, then invoke `/goal-loop`"),
                ("codex", "start native `/goal`, then invoke `$goal-loop`"),
            ):
                loop_ref = (prefix / f".{engine}" / "skills" / "goal-loop"
                            / "references" / "LOOP.md").read_text()
                self.assertIn(seq, loop_ref)


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
