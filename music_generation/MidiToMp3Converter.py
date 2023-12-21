from os import system as execute_command
from pathlib import Path


class MidiToMp3Converter:
    def __init__(self, script_path: Path, tempo: int, max_duration: int):
        self.script_path = script_path
        self.tempo = tempo
        self.max_duration = max_duration

    def convert(self, config: Path | str, mid_input: Path | str, mp3_output: Path | str):
        execute_command(f"{self.script_path} {self.tempo} {self.max_duration} {mid_input} {mp3_output} {config}")
