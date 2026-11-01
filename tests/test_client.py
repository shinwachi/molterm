"""The terminal client, run under a fake terminal that answers its questions. curl is replaced by a stub."""
import os
import pty
import select
import stat
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
from app import app  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "server", "client", "molterm.sh")
QUESTION_END = b"\x1b[c"
KITTY_OK = b"\x1b_Gi=31;OK\x1b\\"
DA1_PLAIN = b"\x1b[?1;2c"
DA1_SIXEL = b"\x1b[?62;4;22c"
DARK = b"\x1b]11;rgb:2020/2828/3030\x1b\\"
LIGHT = b"\x1b]11;rgb:ffff/ffff/ffff\x07"
CELL = b"\x1b[6;20;9t"


@pytest.fixture
def stub(tmp_path):
    """A curl that records its arguments and reports success."""
    log = tmp_path / "curl.log"
    curl = tmp_path / "curl"
    curl.write_text(f'#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a" >>"{log}"; done\nprintf "DRAWING\\n\\n200"\n')
    curl.chmod(curl.stat().st_mode | stat.S_IEXEC)
    return tmp_path


def environment(stub, **extra):
    env = {"PATH": f"{stub}:{os.environ['PATH']}", "TERM": "xterm-256color", "MOLTERM_URL": "http://test"}
    env.update(extra)
    return env


def run_in_terminal(stub, reply, args=("CCO",), answer=True, **extra):
    """Run the client in a pty with no other controlling terminal; return (output, asked, seconds, final termios)."""
    master, slave = pty.openpty()
    started = time.monotonic()
    process = subprocess.Popen(["sh", SCRIPT, *args], stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                               env=environment(stub, **extra),
                               preexec_fn=lambda: __import__("fcntl").ioctl(0, __import__("termios").TIOCSCTTY, 0))
    before = __import__("termios").tcgetattr(slave)
    seen, answered = b"", False
    while True:
        ready, _, _ = select.select([master], [], [], 0.05)
        if ready:
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            seen += data
            if answer and not answered and QUESTION_END in seen:
                os.write(master, reply)
                answered = True
        elif process.poll() is not None:
            break
        if time.monotonic() - started > 10:
            process.kill()
            pytest.fail("the client did not finish")
    process.wait()
    after = __import__("termios").tcgetattr(slave)
    os.close(master)
    os.close(slave)
    return seen, QUESTION_END in seen, time.monotonic() - started, before == after


def sent(stub):
    return (stub / "curl.log").read_text().split("\n")


@pytest.mark.parametrize("reply, mode", [
    (KITTY_OK + DA1_SIXEL, "kitty"),                                 # kitty wins over sixel
    (b"\x1bP>|iTerm2 3.5\x1b\\" + DA1_SIXEL, "iterm"),
    (b"\x1bP>|WezTerm 2024\x1b\\" + DA1_PLAIN, "iterm"),
    (DA1_SIXEL, "sixel"),
    (b"\x1b[?4;6c", "sixel"),                                        # 4 first
    (b"\x1b[?62;22;4c", "sixel"),                                    # 4 last
    (DA1_PLAIN, "braille"),
    (b"\x1b[?64;14;22c", "braille"),                                 # contains a 4 only inside other numbers
])
def test_chooses_the_best_mode(stub, reply, mode):
    output, asked, _, _ = run_in_terminal(stub, reply)
    assert asked
    assert f"mode={mode}" in sent(stub)
    assert b"DRAWING" in output


def test_silent_terminal_falls_back_without_hanging(stub):
    _, asked, seconds, _ = run_in_terminal(stub, b"", answer=False)
    assert asked and seconds < 3
    assert "mode=braille" in sent(stub)
    assert "color=1" in sent(stub)


def test_terminal_settings_are_restored(stub):
    assert run_in_terminal(stub, DA1_SIXEL)[3]
    assert run_in_terminal(stub, b"", answer=False)[3]


@pytest.mark.parametrize("reply, theme", [(DARK + DA1_PLAIN, "dark"), (LIGHT + DA1_PLAIN, "light")])
def test_theme_follows_the_background(stub, reply, theme):
    run_in_terminal(stub, reply)
    assert f"theme={theme}" in sent(stub)


def test_an_explicit_theme_wins(stub):
    run_in_terminal(stub, LIGHT + DA1_PLAIN, args=("-t", "dark", "CCO"))
    assert "theme=dark" in sent(stub) and "theme=light" not in sent(stub)


def test_sixel_reports_cell_and_background(stub):
    run_in_terminal(stub, DARK + CELL + DA1_SIXEL)
    assert "cell_width=9" in sent(stub) and "background=202830" in sent(stub)


def test_short_colour_replies_are_scaled(stub):
    run_in_terminal(stub, b"\x1b]11;rgb:ab/c/12\x07" + DA1_SIXEL)
    assert "background=abcc12" in sent(stub)


def test_other_modes_do_not_send_sixel_options(stub):
    run_in_terminal(stub, KITTY_OK + DARK + CELL + DA1_PLAIN)
    assert not [a for a in sent(stub) if a.startswith(("cell_width=", "background="))]


def fake_tmux(stub, features):
    tmux = stub / "tmux"
    tmux.write_text(f'#!/bin/sh\necho "{features}"\n')
    tmux.chmod(tmux.stat().st_mode | stat.S_IEXEC)


def test_tmux_ignores_inherited_terminal_names(stub):
    fake_tmux(stub, "bpaste,focus")
    run_in_terminal(stub, DA1_PLAIN, TMUX="/tmp/tmux,1,0", TERM_PROGRAM="iTerm.app", LC_TERMINAL="iTerm2")
    assert "mode=braille" in sent(stub)


@pytest.mark.parametrize("features, mode", [("256,bpaste,RGB,sixel,title", "sixel"), ("sixel", "sixel"), ("bpaste,title", "braille"),
                                            ("notsixel,title", "braille"), ("", "braille")])
def test_tmux_decides_by_the_outer_terminals_features(stub, features, mode):
    # tmux claims Sixel in its device attributes even inside a terminal without it, as real Kitty showed
    fake_tmux(stub, features)
    run_in_terminal(stub, DA1_SIXEL, TMUX="/tmp/tmux,1,0", TERM="tmux-256color")
    assert f"mode={mode}" in sent(stub)


def test_tmux_is_not_sent_the_kitty_query(stub):
    # it would take the query for a window title
    fake_tmux(stub, "sixel")
    output, _, _, _ = run_in_terminal(stub, DA1_SIXEL, TMUX="/tmp/tmux,1,0")
    assert b"\x1b_G" not in output


def test_a_multiplexer_without_tmux_stays_with_text(stub):
    run_in_terminal(stub, KITTY_OK + DA1_SIXEL, STY="123.pts", TERM="screen")
    assert "mode=braille" in sent(stub)


def test_ssh_from_inside_tmux_stays_with_text(stub):
    # TMUX does not survive ssh, but TERM does, and there is no local tmux to ask
    run_in_terminal(stub, DA1_SIXEL, TERM="tmux-256color")
    assert "mode=braille" in sent(stub)


@pytest.mark.parametrize("extra, mode", [({"TERM_PROGRAM": "ghostty"}, "kitty"), ({"TERM_PROGRAM": "iTerm.app"}, "iterm"),
                                         ({"TERM": "xterm-kitty"}, "kitty")])
def test_names_are_used_when_the_terminal_stays_silent(stub, extra, mode):
    run_in_terminal(stub, DA1_PLAIN, **extra)
    assert f"mode={mode}" in sent(stub)


def test_a_chosen_mode_is_not_overridden(stub):
    _, asked, _, _ = run_in_terminal(stub, KITTY_OK + DA1_PLAIN, args=("-m", "braille", "CCO"))
    assert not asked
    assert "mode=braille" in sent(stub)


def test_environment_can_choose_the_mode(stub):
    run_in_terminal(stub, DA1_PLAIN, MOLTERM_MODE="sixel")
    assert "mode=sixel" in sent(stub)


def test_dumb_terminal_is_not_asked(stub):
    _, asked, _, _ = run_in_terminal(stub, DA1_SIXEL, TERM="dumb")
    assert not asked
    assert "mode=braille" in sent(stub)


def test_piped_output_is_plain_text_and_asks_nothing(stub):
    result = subprocess.run(["sh", SCRIPT, "CCO"], env=environment(stub), capture_output=True, stdin=subprocess.DEVNULL)
    assert result.stdout == b"DRAWING\n"
    assert "mode=braille" in sent(stub) and "color=1" not in sent(stub)
    assert b"\x1b" not in result.stdout


def test_a_server_error_goes_to_stderr_with_a_failing_status(stub):
    (stub / "curl").write_text('#!/bin/sh\nprintf "Not valid SMILES.\\n\\n400"\n')
    result = subprocess.run(["sh", SCRIPT, "-m", "braille", "bad("], env=environment(stub), capture_output=True,
                            stdin=subprocess.DEVNULL)
    assert result.returncode == 1 and b"Not valid SMILES." in result.stderr and result.stdout == b""


def test_missing_molecule_shows_usage(stub):
    result = subprocess.run(["sh", SCRIPT], env=environment(stub), capture_output=True, stdin=subprocess.DEVNULL)
    assert result.returncode == 2 and b"usage" in result.stderr


def test_script_is_served_pointing_at_this_server():
    response = app.test_client().get("/molterm", base_url="http://example.test")
    text = response.get_data(as_text=True)
    assert response.status_code == 200 and text.startswith("#!/bin/sh")
    assert "url=${MOLTERM_URL:-http://example.test}" in text and "@@URL@@" not in text


def test_script_is_valid_sh():
    assert subprocess.run(["sh", "-n", SCRIPT]).returncode == 0
