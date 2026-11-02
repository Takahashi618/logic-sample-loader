from __future__ import annotations
import re
__all__ = ['MIDI_MIN', 'MIDI_MAX', 'MIDDLE_C_NOTE', 'MIDDLE_C_OCTAVE', 'NOTE_NAMES', 'note_name', 'parse_note_name']
NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
MIDI_MIN = 0
MIDI_MAX = 127
MIDDLE_C_NOTE = 60
MIDDLE_C_OCTAVE = 3
_PITCH_CLASS = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
_NOTE_RE = re.compile('^([A-Ga-g])([#b]?)(-?\\d+)$')

def note_name(note: int) -> str:
    if not MIDI_MIN <= note <= MIDI_MAX:
        raise ValueError(f'MIDI ノート番号が範囲外です: {note}')
    octave = (note - MIDDLE_C_NOTE) // 12 + MIDDLE_C_OCTAVE
    return f'{NOTE_NAMES[note % 12]}{octave}'

def parse_note_name(name: str) -> int:
    text = name.strip().replace('♯', '#').replace('♭', 'b')
    matched = _NOTE_RE.match(text)
    if matched is None:
        raise ValueError(f'音名として解釈できません: {name!r}')
    letter, accidental, octave_text = matched.groups()
    semitone = _PITCH_CLASS[letter.upper()]
    if accidental == '#':
        semitone += 1
    elif accidental == 'b':
        semitone -= 1
    note = semitone + (int(octave_text) - MIDDLE_C_OCTAVE) * 12 + MIDDLE_C_NOTE
    if not MIDI_MIN <= note <= MIDI_MAX:
        raise ValueError(f'MIDI ノート番号が範囲外です: {name!r} -> {note}')
    return note
