"""Skill-format validation for both projections + OpenClaw independence."""
import re
import subprocess
import unittest

from helpers import ROOT

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


def openai_default_prompt():
    text = CODEX_YAML.read_text()
    m = re.search(r'default_prompt:\s*"(.*)"\s*\n?\Z', text, re.DOTALL)
    assert m, "default_prompt field not found in openai.yaml"
    return m.group(1)


class TestNativeGoalBootstrap(unittest.TestCase):
    """Native /goal is an operator-owned prerequisite for durable autonomous
    use, documented identically (in engine-native syntax) across every
    projection — never a capability goal-loop detects, verifies, or
    controls."""

    def test_claude_bootstrap_sequence_ordered(self):
        text = CLAUDE_SKILL.read_text()
        self.assertRegex(text, r"(?s)/goal\b.{0,160}?/goal-loop\b")
        self.assertNotRegex(text, r"\[[^\]]*/goal[^\]]*\]\([^)]*\)")
        self.assertNotRegex(text, r"<a[^>]*goal")

    def test_codex_bootstrap_sequence_ordered(self):
        text = CODEX_SKILL.read_text()
        self.assertRegex(text, r"(?s)/goal\b.{0,160}?\$goal-loop\b")
        self.assertNotRegex(text, r"\[[^\]]*/goal[^\]]*\]\([^)]*\)")
        self.assertNotRegex(text, r"<a[^>]*goal")

    def test_openai_yaml_default_prompt_bootstrap(self):
        prompt = openai_default_prompt()
        self.assertRegex(prompt, r"(?s)/goal\b.{0,160}?\$goal-loop\b")

    def test_host_owns_native_goal_status(self):
        for path in (CLAUDE_SKILL, CODEX_SKILL, LOOP, README):
            text = path.read_text()
            self.assertRegex(
                text,
                r"(?i)(host|operator|session)[\w\s-]*owned",
                f"{path} missing host/operator-ownership statement",
            )
            self.assertRegex(
                text,
                r"(?i)does\s+not\s+detect,\s+attest\s+to,\s+or\s+(record|control)",
                f"{path} missing non-detection/non-control statement",
            )

    def test_completion_boundary_stated(self):
        for path in (CLAUDE_SKILL, CODEX_SKILL, LOOP, README):
            text = path.read_text()
            self.assertRegex(
                text,
                r"(?i)durable\s+done\s+definition",
                f"{path} missing durable-done-definition boundary language",
            )
            self.assertRegex(
                text,
                r"(?i)operator\s+(completes|takes)",
                f"{path} missing operator-completes-afterward language",
            )

    def test_loop_encodes_engine_neutral_framing(self):
        text = LOOP.read_text()
        self.assertIn("/goal-loop", text)
        self.assertIn("$goal-loop", text)
        self.assertIn("start native `/goal`", text)
        self.assertRegex(text, r"(?i)engine-neutral\s+objective,\s+contract,\s+ledger")

    def test_no_recursive_goal_invocation_claimed(self):
        agency_pattern = re.compile(
            r"goal-loop\s+(invokes|starts|calls|triggers|completes)\s+"
            r"(native\s+)?(/goal|/goal-loop|\$goal-loop)"
            r"|automatically\s+starts?\s+/goal"
            r"|recursively\s+invoke",
            re.IGNORECASE,
        )
        for path, text in (
            (CLAUDE_SKILL, CLAUDE_SKILL.read_text()),
            (CODEX_SKILL, CODEX_SKILL.read_text()),
            (LOOP, LOOP.read_text()),
            (README, README.read_text()),
            (CODEX_YAML, openai_default_prompt()),
        ):
            self.assertIsNone(
                agency_pattern.search(text),
                f"{path} claims goal-loop recursively invokes native /goal",
            )
        # Positive control: legitimate instructional phrasing must not
        # false-positive on the same pattern.
        self.assertIsNone(agency_pattern.search(
            "start native `/goal`, then invoke `/goal-loop`"))


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
