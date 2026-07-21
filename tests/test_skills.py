"""Skill-format validation for both projections + OpenClaw independence."""
import re
import subprocess
import unittest

from helpers import ROOT

CLAUDE_SKILL = ROOT / "adapters" / "claude" / "SKILL.md"
CODEX_SKILL = ROOT / "adapters" / "codex" / "SKILL.md"
CODEX_YAML = ROOT / "adapters" / "codex" / "agents" / "openai.yaml"
LOOP = ROOT / "workflow" / "LOOP.md"


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


class TestNativeGoalBootstrapMandate(unittest.TestCase):
    """Every projection must document the native /goal + goal-loop
    entrypoint, the self-attestation disclosure, the fail-closed exit-8
    language, the native-completion limitation, and the no-false-claim
    instruction."""

    def test_claude_skill_documents_native_goal_mandate(self):
        text = CLAUDE_SKILL.read_text()
        self.assertIn("/goal", text)
        self.assertIn("/goal-loop", text)
        self.assertRegex(text, r"(?i)self-attest")
        self.assertRegex(text, r"(?i)not (independently )?(detect|verif)")
        self.assertIn("exit code 8", text)
        self.assertRegex(text, r"(?i)cannot intercept")
        self.assertRegex(text, r"(?i)never claim")

    def test_codex_skill_documents_native_goal_mandate(self):
        text = CODEX_SKILL.read_text()
        self.assertIn("/goal", text)
        self.assertIn("$goal-loop", text)
        self.assertRegex(text, r"(?i)self-attest")
        self.assertRegex(text, r"(?i)not (independently )?(detect|verif)")
        self.assertIn("exit code 8", text)
        self.assertRegex(text, r"(?i)cannot intercept")
        self.assertRegex(text, r"(?i)never claim")

    def test_openai_yaml_documents_native_goal_mandate(self):
        text = CODEX_YAML.read_text()
        self.assertIn("/goal", text)
        self.assertIn("$goal-loop", text)
        self.assertRegex(text, r"(?i)self-attest")
        self.assertIn("exit code 8", text)

    def test_loop_md_documents_native_goal_bootstrap_phase(self):
        text = LOOP.read_text()
        self.assertIn("Phase 0", text)
        self.assertIn("/goal-loop", text)
        self.assertIn("$goal-loop", text)
        self.assertRegex(text, r"(?i)self-attestation")
        self.assertRegex(text, r"(?i)cannot independently verify")
        self.assertIn("exit code 8", text)
        self.assertRegex(text, r"(?i)terminal/read-only boundary")
        self.assertRegex(text, r"(?i)no code path back into the")
        self.assertRegex(text, r"(?i)native-completion rule")
        self.assertRegex(text, r"(?i)(cannot|no way to)\s+intercept or block")
        self.assertRegex(text, r"(?i)never claim")


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
