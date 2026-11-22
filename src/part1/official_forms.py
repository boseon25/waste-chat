"""Public form originals packaged with their source and usage information."""
import json
from pathlib import Path


DIRECTORY = Path(__file__).resolve().parent / 'official_forms'


def catalog(topic=None):
    entries = json.loads((DIRECTORY / 'catalog.json').read_text(encoding='utf-8'))
    return [entry for entry in entries if topic is None or entry['topic'] == topic]
