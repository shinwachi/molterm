"""Start VisiData for one visitor, in the pseudo-terminal the live server opened: limits first, then VisiData in this process's place.

The live server runs this as a new session leader on the terminal's far end. It makes that terminal the controlling one, so that resizes
reach VisiData, caps memory, CPU time, file size and open files, and replaces itself with VisiData on the library, configured by
demo/live.visidatarc, which loads the plugin and then the lockdown.

    python demo/sandbox.py <the library's CSV>
"""
import fcntl
import os
import resource
import sys
import termios

HERE = os.path.dirname(os.path.abspath(__file__))
LIMITS = {
    resource.RLIMIT_AS: 1536 * 2**20,  # memory: VisiData with a thousand rows, its drawings and threads take a few hundred MB
    resource.RLIMIT_CPU: 20 * 60,  # seconds of CPU, more than a session of thirty minutes uses
    resource.RLIMIT_FSIZE: 4 * 2**20,  # nothing to write but VisiData's input history
    resource.RLIMIT_NOFILE: 256,
    resource.RLIMIT_CORE: 0,
}


def main():
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    for limit, value in LIMITS.items():
        resource.setrlimit(limit, (value, value))
    os.execvp("vd", ["vd", "--config", os.path.join(HERE, "live.visidatarc"), "--", sys.argv[1]])


if __name__ == "__main__":
    main()
