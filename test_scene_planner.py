"""
Unit tests for the scene planning layer.

Run:  python -m unittest test_scene_planner -v
"""

import tempfile
import unittest
from pathlib import Path

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

    def test_plan_json_roundtrip(self):
        plan = plan_scenes(SAMPLE_SCRIPT, 27.0, "dramatic_history")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "plan.json"
            plan.save(path)
            loaded = ScenePlan.load(path)
        self.assertEqual(loaded.preset, plan.preset)
        self.assertEqual(len(loaded.scenes), len(plan.scenes))
        self.assertEqual(loaded.scenes[0].role, plan.scenes[0].role)
        self.assertEqual(loaded.scenes[0].image_prompt, plan.scenes[0].image_prompt)

    def test_legacy_json_with_retired_fields_loads(self):
        """ScenePlan.from_dict must ignore retired `motion`/`overlays` keys."""
        legacy = {
            "event_index": 0,
            "preset": "documentary_clean",
            "total_duration": 20.0,
            "scenes": [
                {
                    "index": 0,
                    "role": "hook",
                    "text": "t",
                    "duration": 20.0,
                    "image_prompt": "p",
                    "start": 0.0,
                    "motion": "slow_push_in",                # retired — must be ignored
                    "overlays": [{"block": "title_card"}],   # retired — must be ignored
                }
            ],
        }
        plan = ScenePlan.from_dict(legacy)
        self.assertEqual(len(plan.scenes), 1)
        self.assertEqual(plan.scenes[0].role, "hook")

    def test_missing_roles_are_skipped(self):
        script = dict(SAMPLE_SCRIPT)
        script["rehook"] = ""   # omit
        plan = plan_scenes(script, 20.0)
        self.assertNotIn("rehook", [s.role for s in plan.scenes])


if __name__ == "__main__":
    unittest.main()
