"""
Unit tests for the Phase 1 scene planning layer.

Run:  python -m unittest test_scene_planner -v
"""

import json
import tempfile
import unittest
from pathlib import Path

from pipeline.overlay_blocks import list_blocks, render_block
from pipeline.motion import build_motion_filter, list_motions
from pipeline.presets import DEFAULT_PRESET, get_preset, list_presets
from pipeline.scene_planner import plan_scenes
from pipeline.scene_spec import ALL_ROLES, ScenePlan


SAMPLE_SCRIPT = {
    "event_index": 3,
    "title": "The Moonlit Coup",
    "hook": "A man once sold the Eiffel Tower — twice.",
    "context": "In 1925, Victor Lustig forged government letters. He convinced scrap dealers Paris was demolishing the Tower.",
    "rehook": "But here is where it gets stranger.",
    "twist": "The first buyer was too embarrassed to report him, so he came back and sold it again.",
    "ending_fact": "Lustig died in Alcatraz, remembered as the man who sold the Eiffel Tower.",
    "source_event": {"year": 1925, "location": "Paris", "event": "Lustig sells the Eiffel Tower"},
    "estimated_seconds": 27,
}


class TestPresets(unittest.TestCase):
    def test_three_presets_available(self):
        names = list_presets()
        self.assertIn("documentary_clean", names)
        self.assertIn("dramatic_history", names)
        self.assertIn("viral_fact_card", names)

    def test_default_preset_resolvable(self):
        self.assertIsNotNone(get_preset(None))
        self.assertEqual(get_preset(None).name, DEFAULT_PRESET)

    def test_unknown_preset_raises(self):
        with self.assertRaises(ValueError):
            get_preset("no_such_preset")

    def test_every_preset_covers_every_role(self):
        for name in list_presets():
            preset = get_preset(name)
            for role in ALL_ROLES:
                direction = preset.for_role(role)
                self.assertIsNotNone(direction)


class TestMotion(unittest.TestCase):
    def test_five_motion_presets(self):
        motions = list_motions()
        for expected in ("slow_push_in", "drift_left", "drift_right",
                         "dramatic_zoom", "static_hold"):
            self.assertIn(expected, motions)

    def test_motion_filter_well_formed(self):
        f = build_motion_filter("slow_push_in", 5.0)
        self.assertIn("zoompan", f)
        self.assertIn("[in]", f)
        self.assertIn("[out]", f)
        self.assertIn("fps=24", f)

    def test_unknown_motion_falls_back_to_static(self):
        f = build_motion_filter("nonexistent", 5.0)
        self.assertIn("zoompan", f)  # still produces something valid


class TestOverlayBlocks(unittest.TestCase):
    def test_three_blocks_registered(self):
        for name in ("title_card", "fact_badge", "era_tag"):
            self.assertIn(name, list_blocks())

    def test_title_card_renders(self):
        frag = render_block("title_card", {"text": "Hello"}, 0.0, 3.0)
        self.assertIn("drawtext", frag)
        self.assertIn("Hello", frag)
        self.assertIn("between(t,0.00,3.00)", frag)

    def test_empty_title_card_returns_empty(self):
        self.assertEqual(render_block("title_card", {}, 0.0, 3.0), "")

    def test_unknown_block_returns_empty(self):
        self.assertEqual(render_block("nope", {}, 0.0, 3.0), "")

    def test_era_tag_builds_from_year_location(self):
        frag = render_block("era_tag", {"year": "1925", "location": "Paris"}, 5.0, 10.0)
        self.assertIn("1925", frag)
        self.assertIn("Paris", frag)


class TestScenePlanner(unittest.TestCase):
    def test_plan_produces_one_scene_per_filled_role(self):
        plan = plan_scenes(SAMPLE_SCRIPT, audio_duration=27.0)
        self.assertEqual(len(plan.scenes), 5)
        roles = [s.role for s in plan.scenes]
        self.assertEqual(roles, ["hook", "context", "rehook", "twist", "ending"])

    def test_scene_durations_sum_to_audio_duration(self):
        plan = plan_scenes(SAMPLE_SCRIPT, audio_duration=27.0)
        total = sum(s.duration for s in plan.scenes)
        self.assertAlmostEqual(total, 27.0, places=1)

    def test_scene_starts_are_monotonic(self):
        plan = plan_scenes(SAMPLE_SCRIPT, audio_duration=27.0)
        starts = [s.start for s in plan.scenes]
        self.assertEqual(starts, sorted(starts))

    def test_preset_affects_motion_and_overlays(self):
        doc = plan_scenes(SAMPLE_SCRIPT, 27.0, "documentary_clean")
        viral = plan_scenes(SAMPLE_SCRIPT, 27.0, "viral_fact_card")
        # Hook motion differs between presets
        self.assertEqual(doc.scenes[0].motion, "slow_push_in")
        self.assertEqual(viral.scenes[0].motion, "dramatic_zoom")
        # Viral preset puts more overlays on the hook
        self.assertGreater(len(viral.scenes[0].overlays), len(doc.scenes[0].overlays))

    def test_image_prompts_are_role_aware(self):
        plan = plan_scenes(SAMPLE_SCRIPT, 27.0, "documentary_clean")
        hook_prompt = plan.scenes[0].image_prompt
        twist_prompt = plan.scenes[3].image_prompt
        self.assertNotEqual(hook_prompt, twist_prompt)
        # Twist prompts should reference heightened contrast
        self.assertTrue(
            any(token in twist_prompt.lower() for token in ("contrast", "chiaroscuro", "accent"))
        )
        # Ending prompt should lean toward clean/negative space per role direction
        ending_prompt = plan.scenes[-1].image_prompt
        self.assertTrue(
            any(tok in ending_prompt.lower() for tok in ("negative space", "clean", "dusk", "calm"))
        )

    def test_title_card_overlay_gets_script_title(self):
        plan = plan_scenes(SAMPLE_SCRIPT, 27.0, "viral_fact_card")
        hook = plan.scenes[0]
        title_overlays = [o for o in hook.overlays if o.block == "title_card"]
        self.assertTrue(title_overlays)
        self.assertEqual(title_overlays[0].params.get("text"), "The Moonlit Coup")

    def test_plan_json_roundtrip(self):
        plan = plan_scenes(SAMPLE_SCRIPT, 27.0, "dramatic_history")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "plan.json"
            plan.save(path)
            loaded = ScenePlan.load(path)
        self.assertEqual(loaded.preset, plan.preset)
        self.assertEqual(len(loaded.scenes), len(plan.scenes))
        self.assertEqual(loaded.scenes[0].motion, plan.scenes[0].motion)

    def test_missing_roles_are_skipped(self):
        script = dict(SAMPLE_SCRIPT)
        script["rehook"] = ""   # omit
        plan = plan_scenes(script, 20.0)
        self.assertNotIn("rehook", [s.role for s in plan.scenes])


if __name__ == "__main__":
    unittest.main()
