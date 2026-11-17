
import os
import sys
from itertools import count
from pathlib import Path

import pytest
from conftest import install_host_ports
from support import Plugin


def run(folder, script):
    patch = pytest.MonkeyPatch()
    qt = install_host_ports(patch)
    plugin = Plugin(Path(folder), qt)
    positions = count(1)

    def checkpoint():
        plugin.saved()
        print("saved", flush=True)
        sys.stdin.readline()

    def crash():
        plugin.saved()
        os._exit(23)

    def crash_during_stop():
        plugin.saved()
        os.fsync = lambda descriptor: os._exit(23)
        plugin.stop()
        plugin.saved()
        raise AssertionError("the pending compaction never reached disk sync")

    commands = {
        "start": plugin.start,
        "stroke": lambda: plugin.stroke(next(positions)),
        "undo": plugin.krita.action("edit_undo").triggered.emit,
        "redo": plugin.krita.action("edit_redo").triggered.emit,
        "stop": plugin.stop,
        "checkpoint": checkpoint,
        "crash": crash,
        "crash_during_stop": crash_during_stop,
        "close": plugin.close,
    }
    for command in script.split():
        commands[command]()


if __name__ == "__main__":
    run(*sys.argv[1:])
