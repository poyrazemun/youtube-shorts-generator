"""
Background music selector.
Randomly picks an MP3/WAV from assets/music/.
Falls back gracefully (returns None) if no tracks are found.
"""
import logging
import random
from pathlib import Path

import config

logger = logging.getLogger(__name__)


def select_track() -> Path | None:
    """
    Select a random royalty-free track from assets/music/.
    Returns the Path to the track, or None if directory is empty or missing.

    To use background music: drop royalty-free MP3 files into assets/music/.
    """
    music_dir = config.MUSIC_DIR
    if not music_dir.exists():
        logger.debug("[music] No assets/music/ directory — skipping background music.")
        return None

    tracks = list(music_dir.glob("*.mp3")) + list(music_dir.glob("*.wav"))
    if not tracks:
        logger.info("[music] No tracks in assets/music/ — skipping background music.")
        return None

    track = random.choice(tracks)
    logger.info(f"[music] Selected background track: {track.name}")
    return track
