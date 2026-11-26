"""A real interactive shell on a pseudo-terminal, for tests that TAB the way a person does.

``Terminal`` starts the shell as the session leader of its own terminal (so ``/dev/tty`` is that terminal, as
it is for a person), types keys into it and waits — always with a total deadline — for text to appear on the
screen. The screen text is the shell's output with terminal control sequences removed.
"""

from __future__ import annotations

import fcntl
import os
import pty
import re
import select
import signal
import struct
import termios
import time
import warnings
from typing import Dict, List

#: Terminal control sequences: CSI (cursor moves, colours, bracketed paste), OSC (titles), keypad modes.
_CONTROL = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[=>]")


def screen_text(raw: bytes) -> str:
    """``raw`` as a person reads it: control sequences, bells and carriage returns removed."""
    text = _CONTROL.sub("", raw.decode(errors="replace"))
    return text.replace("\r", "").replace("\x07", "")


class Terminal:
    """One interactive shell on its own pseudo-terminal of ``rows`` × ``cols``."""

    def __init__(self, argv: List[str], env: Dict[str, str], rows: int = 40, cols: int = 110) -> None:
        # forkpty warns when the test process has threads (a logging sink's): the child only execs the shell, so
        # the lock it could inherit is never taken.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            pid, fd = pty.fork()
        if pid == 0:  # the child: the session leader of the new terminal
            try:
                os.execvpe(argv[0], argv, env)
            finally:
                os._exit(127)  # never run the test suite's code in the child
        self.pid, self.fd = pid, fd
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        self.raw = b""

    @property
    def text(self) -> str:
        return screen_text(self.raw)

    def send(self, keys: str) -> None:
        os.write(self.fd, keys.encode())

    def wait_for(self, expected: str, deadline: float = 20.0) -> str:
        """Read until ``expected`` is on the screen; fail within ``deadline`` seconds, naming it."""
        stop = time.monotonic() + deadline
        while expected not in self.text:
            left = stop - time.monotonic()
            if left <= 0:
                raise AssertionError(
                    f"waited {deadline:.0f} s for {expected!r}; the screen shows:\n{self.text[-3000:]}"
                )
            ready, _, _ = select.select([self.fd], [], [], min(left, 0.2))
            if ready:
                try:
                    self.raw += os.read(self.fd, 65536)
                except OSError:  # the shell exited
                    raise AssertionError(f"the shell exited before {expected!r} appeared:\n{self.text[-3000:]}")
        return self.text

    def close(self) -> None:
        """Close the terminal, then kill the shell: a process killed with unread output waits for it to drain."""
        os.close(self.fd)
        try:
            os.kill(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        os.waitpid(self.pid, 0)
