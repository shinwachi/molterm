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
    assert not [a for a in sent(stub) if a.startswith("cell_width=")]


def test_the_theme_is_fitted_to_the_terminals_colours(stub):
    run_in_terminal(stub, KITTY_OK + DARK + b"\x1b]10;rgb:dddd/eeee/ffff\x1b\\" + CELL + DA1_PLAIN)
    assert {"theme=dark", "background=202830", "foreground=ddeeff"} <= set(sent(stub))


def test_a_chosen_theme_is_not_fitted_to_the_terminal(stub):
    run_in_terminal(stub, KITTY_OK + DARK + CELL + DA1_PLAIN, args=["-t", "nord", "CCO"])
    assert "theme=nord" in sent(stub)
    assert not [a for a in sent(stub) if a.startswith(("background=", "foreground="))]


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


# the guided session

STORY = "First step\tgrid\tBefore one.\tAfter one.\nSecond step\tfilter pains=0\tBefore two.\tAfter two.\n"


def session_stub(stub, first_line="0 a,b"):
    """A curl that answers like the server, and logs each command with the working set it was sent."""
    (stub / "story.txt").write_text(STORY)
    log = stub / "curl.log"
    (stub / "curl").write_text(f"""#!/bin/sh
for a in "$@"; do
    printf "%s\\n" "$a" >>"{log}"
    case $a in *story.txt) cat "{stub}/story.txt"; exit 0 ;; esac
done
printf "{first_line}\\nOUTPUT\\n\\n200"
""")
    (stub / "sleep").write_text("#!/bin/sh\nexit 0\n")
    (stub / "sleep").chmod(0o755)
    return stub


def interact(stub, args, keys, reply=KITTY_OK + DARK + DA1_PLAIN, **extra):
    """Run the client in a pty, typing each of `keys` when a new prompt appears. Return (output, exit code, settings restored)."""
    import fcntl
    import termios
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, __import__("struct").pack("HHHH", 30, 90, 0, 0))
    process = subprocess.Popen(["sh", SCRIPT, *args], stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                               env=environment(stub, **extra), preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
    before = termios.tcgetattr(slave)
    seen, answered, typed, started = b"", False, 0, time.monotonic()
    pending = list(keys)
    while process.poll() is None or select.select([master], [], [], 0.05)[0]:
        if select.select([master], [], [], 0.05)[0]:
            try:
                seen += os.read(master, 65536)
            except OSError:
                break
        if not answered and QUESTION_END in seen:
            os.write(master, reply)
            answered = True
        if pending and seen.count(b"medchem$\x1b[0m ") - seen.count(b"medchem$\x1b[0m grid") > typed:
            os.write(master, pending.pop(0))
            typed += 1
        if time.monotonic() - started > 10:
            process.kill()
            pytest.fail(f"the client did not finish: {seen!r}")
    process.wait()
    restored = before == termios.tcgetattr(slave)
    os.close(master)
    os.close(slave)
    return seen, process.returncode, restored


def lines_sent(stub):
    return [a[5:] for a in sent(stub) if a.startswith("line=")]


def test_demo_runs_each_step_on_enter_then_takes_commands(stub):
    session_stub(stub)
    output, code, restored = interact(stub, ["demo"], [b"\n", b"\n", b"ls\n", b"q\n"])
    assert code == 0 and restored
    assert lines_sent(stub) == ["grid", "filter pains=0", "ls"]
    for expected in (b"Step 1 of 2: First step", b"Before one.", b"After one.", b"Step 2 of 2", b"After two.", b"Your turn", b"OUTPUT"):
        assert expected in output
    assert b"medchem$\x1b[0m grid" in output


def test_demo_uses_the_mode_it_detected_and_the_real_width(stub):
    session_stub(stub)
    interact(stub, ["demo"], [b"q\n", b"\n"])
    session_stub(stub)
    interact(stub, ["demo"], [b"\n", b"q\n"])
    assert "display=kitty" in sent(stub) and "cols=90" in sent(stub) and "theme=dark" in sent(stub)


def test_demo_lets_you_try_something_else_without_losing_the_step(stub):
    session_stub(stub)
    output, _, _ = interact(stub, ["demo"], [b"ls\n", b"\n", b"q\n"])
    assert lines_sent(stub) == ["ls", "grid"] and b"After one." in output


def test_demo_carries_the_working_set(stub):
    session_stub(stub)
    interact(stub, ["demo"], [b"\n", b"\n", b"q\n"])
    log = sent(stub)
    assert "selection=a,b" in log[log.index("line=filter pains=0"):]
    assert not [a for a in log[:log.index("line=grid") + 6] if a.startswith("selection=")]


def test_demo_quits_cleanly_at_the_first_prompt_and_at_end_of_input(stub):
    session_stub(stub)
    assert interact(stub, ["demo"], [b"q\n"])[1:] == (0, True)
    session_stub(stub)
    assert interact(stub, ["demo"], [b"\x04"])[1:] == (0, True)


def test_demo_text_mode_for_braille(stub):
    session_stub(stub)
    interact(stub, ["-m", "braille", "demo"], [b"\n", b"q\n"])
    assert "display=text" in sent(stub)


def test_clear_clears_the_screen_and_kitty_images(stub):
    session_stub(stub, "1 a,b")
    output, _, _ = interact(stub, ["demo"], [b"clear\n", b"q\n"])
    assert b"\x1b[2J" in output and b"\x1b_Ga=d" in output


def test_a_typed_line_never_reaches_a_shell(stub, tmp_path):
    session_stub(stub)
    marker = tmp_path / "touched"
    interact(stub, ["demo"], [f"$(touch {marker}); touch {marker}\n".encode(), b"q\n"])
    assert not marker.exists()
    assert lines_sent(stub) == [f"$(touch {marker}); touch {marker}"]


def test_play_runs_every_step_without_input(stub):
    session_stub(stub)
    output, code, restored = interact(stub, ["play"], [])
    assert code == 0 and restored and lines_sent(stub) == ["grid", "filter pains=0"]
    assert b"After two." in output and b"Your turn" not in output


@pytest.mark.parametrize("word", ["demo", "play"])
def test_the_session_needs_a_terminal(stub, word):
    session_stub(stub)
    result = subprocess.run(["sh", SCRIPT, word], env=environment(stub), capture_output=True, stdin=subprocess.DEVNULL)
    assert result.returncode == 1 and b"needs a terminal" in result.stderr


# the editor


def editor_stub(stub, states=("0 STATE1", "0 STATE2", "1 "), output="FRAME"):
    """A curl for the editor: logs its arguments, and gives each answer in turn. The last answer's output is the SMILES."""
    log = stub / "curl.log"
    count = stub / "count"
    count.write_text("0")
    answers = "\n".join(f'{i}) printf "{state}\\n{"CCO" if state.startswith("1") else output}\\n200" ;;' for i, state in enumerate(states))
    (stub / "curl").write_text(f"""#!/bin/sh
for a in "$@"; do printf "%s\\n" "$a" >>"{log}"; done
n=$(cat "{count}")
echo $((n + 1)) >"{count}"
case $n in
{answers}
*) printf "1 \\nCCO\\n200" ;;
esac
""")
    return stub


def run_editor(stub, keys, args=("edit",), reply=KITTY_OK + DARK + CELL + b"\x1b[?1016;2$y" + DA1_PLAIN, **extra):
    """Run the editor in a pty; type each of `keys` after each frame. Return (bytes seen, exit code, settings restored, stdout)."""
    import fcntl
    import struct
    import termios
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 90, 0, 0))
    out = stub / "stdout"
    with open(out, "wb") as stdout:
        process = subprocess.Popen(["sh", SCRIPT, *args], stdin=slave, stdout=stdout, stderr=subprocess.PIPE, start_new_session=True,
                                   env=environment(stub, **extra), preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
    before = termios.tcgetattr(slave)
    seen, answered, started = b"", False, time.monotonic()
    pending = list(keys)
    sent_after = 0
    while process.poll() is None:
        if select.select([master], [], [], 0.05)[0]:
            seen += os.read(master, 65536)
        if not answered and QUESTION_END in seen:
            os.write(master, reply)
            answered = True
        frames = seen.count(b"FRAME")
        if pending and frames > sent_after:
            sent_after = frames
            os.write(master, pending.pop(0))
        if time.monotonic() - started > 10:
            process.kill()
            pytest.fail(f"the editor did not finish: {seen!r}")
    if select.select([master], [], [], 0.05)[0]:
        seen += os.read(master, 65536)
    stderr = process.stderr.read()
    process.stderr.close()
    process.wait()
    restored = before == termios.tcgetattr(slave)
    os.close(master)
    os.close(slave)
    return seen, process.returncode, restored, out.read_bytes(), stderr


def values(stub, name):
    return [a[len(name) + 1:] for a in sent(stub) if a.startswith(name + "=")]


def test_editor_passes_on_input_and_carries_the_state(stub):
    editor_stub(stub)
    _, code, restored, stdout, _ = run_editor(stub, [b"a", b"\x1b[<0;5;6M"])
    assert code == 0 and restored and stdout == b"CCO\n"
    assert values(stub, "events") == ["", "61", "1b5b3c303b353b364d"]
    assert values(stub, "state") == ["STATE1", "STATE2"]


def test_editor_reports_the_terminal_to_the_server(stub):
    editor_stub(stub)
    run_editor(stub, [b"a", b"b"])
    for name, value in (("cols", "90"), ("rows", "30"), ("cell_width", "9"), ("cell_height", "20"), ("pixels", "1"), ("theme", "dark")):
        assert value in values(stub, name)


def test_editor_starts_from_a_smiles(stub):
    editor_stub(stub)
    run_editor(stub, [b"a", b"b"], args=("edit", "c1ccccc1"))
    assert values(stub, "smiles") == ["c1ccccc1"]


def test_editor_turns_the_mouse_on_and_everything_off_again(stub):
    editor_stub(stub)
    seen, _, restored, _, _ = run_editor(stub, [b"a", b"b"])
    for on in (b"\x1b[?1049h", b"\x1b[?1003h", b"\x1b[?1006h", b"\x1b[?1016h", b"\x1b[?25l"):
        assert on in seen
    for off in (b"\x1b[?1049l", b"\x1b[?1003l", b"\x1b[?1006l", b"\x1b[?1016l", b"\x1b[?25h"):
        assert off in seen
    assert restored and seen.index(b"\x1b[?1049h") < seen.index(b"\x1b[?1049l")


def test_editor_uses_cells_when_the_terminal_has_no_pixel_mouse(stub):
    editor_stub(stub)
    seen, _, _, _, _ = run_editor(stub, [b"a", b"b"], reply=KITTY_OK + DARK + CELL + DA1_PLAIN)
    assert b"\x1b[?1016h" not in seen and not values(stub, "pixels")


def test_editor_refuses_a_terminal_without_kitty_graphics(stub):
    editor_stub(stub)
    _, code, restored, stdout, stderr = run_editor(stub, [], reply=DA1_SIXEL)
    assert code == 1 and restored and stdout == b"" and b"needs Kitty graphics" in stderr
    assert not (stub / "curl.log").exists()


def test_editor_cleans_up_when_the_server_goes_away(stub):
    editor_stub(stub)
    (stub / "curl").write_text('#!/bin/sh\necho "curl: (7) refused" >&2\nexit 7\n')
    seen, code, restored, _, stderr = run_editor(stub, [])
    assert code == 1 and restored and b"\x1b[?1049l" in seen and b"did not answer" in stderr


def test_editor_cleans_up_on_a_refused_request(stub):
    (stub / "curl").write_text('#!/bin/sh\nprintf "That is not a drawing.\\n\\n400"\n')
    seen, code, restored, _, stderr = run_editor(stub, [])
    assert code == 1 and restored and b"\x1b[?1049l" in seen and b"not a drawing" in stderr


def test_editor_reports_a_failure_from_the_server(stub):
    (stub / "curl").write_text('#!/bin/sh\nprintf "2 \\nNot valid SMILES.\\n200"\n')
    seen, code, restored, stdout, stderr = run_editor(stub, [], args=("edit", "C("))
    assert code == 1 and restored and stdout == b"" and b"Not valid SMILES." in stderr


def test_editor_redraws_when_the_window_changes_size(stub):
    import fcntl
    import struct
    import termios
    editor_stub(stub, states=("0 S1", "0 S2", "0 S3", "1 "))
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 90, 0, 0))
    process = subprocess.Popen(["sh", SCRIPT, "-m", "kitty", "edit"], stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                               env=environment(stub), preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
    seen, resized, started = b"", False, time.monotonic()
    while process.poll() is None and time.monotonic() - started < 10:
        if select.select([master], [], [], 0.05)[0]:
            seen += os.read(master, 65536)
        if not resized and b"FRAME" in seen:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 100, 0, 0))
            resized = True
        if resized and "100" in values(stub, "cols"):
            os.write(master, b"q")
    process.kill()
    process.wait()
    os.close(master)
    os.close(slave)
    assert "90" in values(stub, "cols") and "100" in values(stub, "cols")


def test_script_is_served_pointing_at_this_server():
    response = app.test_client().get("/molterm", base_url="http://example.test")
    text = response.get_data(as_text=True)
    assert response.status_code == 200 and text.startswith("#!/bin/sh")
    assert "url=${MOLTERM_URL:-http://example.test}" in text and "@@URL@@" not in text


def test_script_is_valid_sh():
    assert subprocess.run(["sh", "-n", SCRIPT]).returncode == 0


def test_a_boxed_editor_leaves_the_screen_to_its_host(stub):
    editor_stub(stub)
    seen, code, restored, stdout, _ = run_editor(stub, [b"a", b"b"], args=("-b", "2,4,26,100", "-T", "Compound 3", "edit", "CCO"))
    assert code == 0 and restored and stdout == b"CCO\n"
    assert values(stub, "box")[0] == "2,4,26,100" and values(stub, "title")[0] == "Compound 3" and values(stub, "smiles") == ["CCO"]
    assert b"\x1b[?1049" not in seen and b"\x1b_Ga=d,q=2" not in seen
    assert b"\x1b_Ga=d,d=I,i=7001,q=2" in seen and b"\x1b_Ga=d,d=I,i=7021,q=2" in seen
    assert b"\x1b[?1003l" in seen and b"\x1b[?25h" in seen


def test_a_cancelled_box_exits_3_and_prints_nothing(stub):
    editor_stub(stub, states=("0 STATE1", "3 "))
    _, code, restored, stdout, _ = run_editor(stub, [b"\x03"], args=("-b", "2,4,26,100", "edit"))
    assert code == 3 and restored and stdout == b""


def test_the_list_sends_its_items_once_to_its_own_endpoint(stub):
    editor_stub(stub)
    seen, code, restored, stdout, _ = run_editor(stub, [b"a", b"b"], args=("tui", "CCO ethanol", "ro:c1ccccc1 benzene"))
    assert code == 0 and restored and stdout == b"CCO\n"
    assert values(stub, "items") == ["CCO ethanol"] and "ro:c1ccccc1 benzene" in sent(stub)  # the log has one line per line
    assert "http://test/api/v1/tui" in sent(stub) and "http://test/api/v1/edit" not in sent(stub)
    assert b"\x1b[?1049h" in seen and b"\x1b[?1049l" in seen


def test_the_list_needs_kitty_graphics_too(stub):
    editor_stub(stub)
    _, code, _, _, stderr = run_editor(stub, [], args=("tui",), reply=DA1_SIXEL)
    assert code == 1 and b"Kitty" in stderr


def test_the_editor_still_uses_its_endpoint(stub):
    editor_stub(stub)
    run_editor(stub, [b"a", b"b"])
    assert "http://test/api/v1/edit" in sent(stub)
