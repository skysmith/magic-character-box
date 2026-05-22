import unittest

from magic_box.audio_prep import AUDIO_PREP_FILTER


class AudioPrepTests(unittest.TestCase):
    def test_audio_prep_filter_normalizes_and_fades(self) -> None:
        self.assertIn("loudnorm", AUDIO_PREP_FILTER)
        self.assertIn("afade=t=in", AUDIO_PREP_FILTER)


if __name__ == "__main__":
    unittest.main()
