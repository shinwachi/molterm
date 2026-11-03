#!/bin/sh
# molterm: draw a molecule in this terminal, as sharply as the terminal allows.
#
#   molterm 'CC(=O)Oc1ccccc1C(=O)O'
#   molterm -m braille -t light 'CCO'
#   molterm -t mono -f 40 'CCO'         black on white, softly diffused, as if seen through fogged glass
#   molterm demo     a guided medicinal chemistry session, one step at a time, then your own commands
#   molterm play     the same session, playing by itself
#   molterm edit     draw a molecule with the mouse and keyboard, in a terminal with Kitty graphics; prints its SMILES on quitting,
#                    as CXSMILES if it has annotations (highlights, notes, atom numbers, stereo labels)
#   molterm tui      a list of structures: click one and the editor opens in a window over the list, right-click one to copy it as
#                    SMILES, SVG or PNG; prints the list on quitting
#
# Asks the terminal what it can do, then uses Kitty graphics, iTerm2 images or Sixel, and falls back to Braille text.
# Needs only sh, curl, stty and dd. The molecule goes to the server named below, or in $MOLTERM_URL.
set -u
url=${MOLTERM_URL:-@@URL@@}
mode=${MOLTERM_MODE:-auto}
theme=""; cols=""; rows=""; verbose=0; box=""; title=""; fog=""

usage() {
    cat <<USAGE
usage: molterm [-m mode] [-t theme] [-f fog] [-c cols] [-r rows] [-v] <SMILES>
       molterm [-m mode] [-t theme] [-c cols] [-v] demo|play
       molterm [-t theme] [-v] [-b row,col,rows,cols] [-T title] edit [SMILES]
       molterm [-t theme] [-v] tui ['SMILES name' | 'ro:SMILES name' ...]   (a SMILES may be CXSMILES with annotations)
  -m  auto, kitty, iterm, sixel, octant, braille or ascii (default auto, or \$MOLTERM_MODE)
  -t  a colour theme: dark, light, mono (black on white), mono-dark, solarized-dark, solarized-light, gruvbox-dark,
      gruvbox-light, nord, dracula, catppuccin-mocha, catppuccin-latte or tokyo-night
      (default: dark or light, fitted to the colours the terminal reports)
  -f  fog: how far the lines diffuse, from 0 (none) to 100, for one structure in a graphics mode
  -c  most columns to use   -r  most rows to use
  -v  say which mode was chosen, on stderr
  -b  keep the editor in this box of the window, for use from another text interface: from 0 at the top left, counting the border
      It prints the SMILES on Done, and exits with 3 on Cancel. The host redraws what the box covered, and turns its own mouse on again.
  -T  a title for the box
USAGE
}

while getopts m:t:f:c:r:b:T:vh option; do
    case $option in
        m) mode=$OPTARG ;;
        t) theme=$OPTARG ;;
        f) fog=$OPTARG ;;
        c) cols=$OPTARG ;;
        r) rows=$OPTARG ;;
        v) verbose=1 ;;
        b) box=$OPTARG ;;
        T) title=$OPTARG ;;
        h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
shift $((OPTIND - 1))
start=""
if [ $# -eq 2 ] && [ "$1" = edit ]; then
    start=$2
    set -- edit
elif [ "${1:-}" = tui ]; then
    shift
    for item in "$@"; do start=$start$item'
'; done
    set -- tui
fi
if [ $# -ne 1 ]; then usage >&2; exit 2; fi
smiles=$1

command -v curl >/dev/null 2>&1 || { echo "molterm: this needs curl" >&2; exit 1; }

reply=""
saved=""
restore() { if [ -n "$saved" ]; then stty "$saved" </dev/tty 2>/dev/null; saved=""; fi; }

in_multiplexer() {
    [ -n "${TMUX:-}" ] || [ -n "${STY:-}" ] && return 0
    case ${TERM:-} in screen*|tmux*) return 0 ;; esac
    return 1
}

# Inside tmux, Kitty graphics go through as Unicode placeholders, which tmux keeps as text (see server/placeholders.py), when the
# terminal outside it is Kitty or Ghostty and tmux lets them through. placeholders=1 asks the server for them.
placeholders=0
outer_kitty() {
    [ -n "${TMUX:-}" ] || return 1
    outer=$(tmux display -p '#{client_termname} #{client_termtype}' 2>/dev/null)
    case $outer in *kitty*|*ghostty*|*Ghostty*) return 0 ;; esac
    return 1
}
passthrough_on() {
    case $(tmux show -Apv allow-passthrough 2>/dev/null) in *on*|*all*) return 0 ;; esac
    return 1
}

# A Kitty graphics command, wrapped for tmux to pass on when that's where it goes
kitty_command() {
    if [ $placeholders = 1 ]; then printf '\033Ptmux;\033\033_G%s\033\033\\\033\\' "$1"; else printf '\033_G%s\033\\' "$1"; fi
}

# Ask the terminal four things, ending with the one every terminal answers, so that we know when to stop reading:
# a Kitty graphics query, its name and version, its background and text colours and cell size, and its device attributes.
ask_terminal() {
    # the editor draws on the terminal itself, so its output can go elsewhere: s=$(molterm edit)
    [ -t 1 ] || [ "$smiles" = edit ] || [ "$smiles" = tui ] || return 0
    case ${TERM:-dumb} in dumb|unknown) return 0 ;; esac
    { : </dev/tty; } 2>/dev/null || return 0
    saved=$(stty -g </dev/tty 2>/dev/null) || { saved=""; return 0; }
    trap 'restore; exit 130' INT TERM HUP
    trap restore EXIT
    stty -echo -icanon min 0 time 5 </dev/tty 2>/dev/null || { saved=""; return 0; }
    # tmux would take the Kitty query for a window title
    in_multiplexer || printf '\033_Gi=31,s=1,v=1,a=q,t=d,f=24;AAAA\033\\' >/dev/tty
    printf '\033[>q\033]11;?\033\\\033]10;?\033\\\033[16t\033[?1016$p\033[c' >/dev/tty
    tries=0
    while [ $tries -lt 20 ]; do
        tries=$((tries + 1))
        chunk=$(dd bs=1024 count=1 </dev/tty 2>/dev/null | tr -d '\000')
        [ -n "$chunk" ] || break
        reply=$reply$chunk
        case $reply in *'[?'*c*) break ;; esac
    done
    restore
    trap - INT TERM HUP EXIT
}

choose_mode() {
    # A multiplexer answers for itself: tmux claims Sixel even inside a terminal without it, and passes on no other images.
    # Only tmux's own list of the outer terminal's features can be trusted.
    if in_multiplexer; then
        if outer_kitty; then
            if passthrough_on; then echo kitty; return; fi
            echo "molterm: this tmux is in $outer, which shows Kitty graphics, but tmux passes them on only with" \
                 "allow-passthrough. To use them: tmux set -g allow-passthrough on (and add that to ~/.tmux.conf to keep it)." >&2
        fi
        features=""
        [ -n "${TMUX:-}" ] && features=$(tmux display -p '#{client_termfeatures}' 2>/dev/null)
        case ,$features, in *,sixel,*) echo sixel ;; *) echo braille ;; esac
        return
    fi
    case $reply in *'_Gi=31;OK'*) echo kitty; return ;; esac
    case $reply in *'>|iTerm2'*|*'>|WezTerm'*|*'>|mintty'*) echo iterm; return ;; esac
    attributes=$(printf '%s' "$reply" | sed -n 's/.*\[?\([0-9;]*\)c.*/;\1;/p')
    case $attributes in *';4;'*) echo sixel; return ;; esac
    # Some terminals don't answer those questions, so their names come last.
    case ${TERM_PROGRAM:-} in
        ghostty) echo kitty; return ;;
        iTerm.app|WezTerm|mintty) echo iterm; return ;;
    esac
    case ${TERM:-} in xterm-kitty|xterm-ghostty) echo kitty; return ;; esac
    echo braille
}

# Replies carry a colour as rgb:RRRR/GGGG/BBBB, with one to four hex digits per channel: 11 for the background, 10 for text.
background=""; foreground=""; fitted=0
read_colour() {
    color=$(printf '%s' "$reply" | sed -n "s/.*\\]$1;rgb:\\([0-9a-fA-F]*\\)\\/\\([0-9a-fA-F]*\\)\\/\\([0-9a-fA-F]*\\).*/\\1 \\2 \\3/p")
    [ -n "$color" ] || return 0
    set -- $color
    r=$(printf '%.2s' "$1$1"); g=$(printf '%.2s' "$2$2"); b=$(printf '%.2s' "$3$3")
    echo "$r$g$b"
}
read_background() {
    background=$(read_colour 11)
    foreground=$(read_colour 10)
    [ -n "$background" ] || return 0
    if [ -z "$theme" ]; then
        # the pictures and the editor take the terminal's own colours, over the base that suits them
        fitted=1
        r=${background%????}; g=${background#??}; g=${g%??}; b=${background#????}
        if [ $(( 299 * 0x$r + 587 * 0x$g + 114 * 0x$b )) -gt 127500 ]; then theme=light; else theme=dark; fi
    fi
}

# Sixel needs the background to blend into whatever the theme, and the rest only when the theme is fitted to the terminal.
colour_options() {
    if [ $fitted = 1 ] || [ "$mode" = sixel ]; then
        [ -n "$background" ] && printf '%s\n' "background=$background"
    fi
    [ $fitted = 1 ] && [ -n "$foreground" ] && printf '%s\n' "foreground=$foreground"
    return 0
}

cell_width=""; cell_height=""
read_cell_size() {
    cell_width=$(printf '%s' "$reply" | sed -n 's/.*\[6;[0-9]*;\([0-9]*\)t.*/\1/p')
    cell_height=$(printf '%s' "$reply" | sed -n 's/.*\[6;\([0-9]*\);[0-9]*t.*/\1/p')
}

case $mode in
    auto|kitty|iterm|sixel) ask_terminal ;;
esac
[ "$mode" = auto ] && mode=$(choose_mode)
if [ "$mode" = kitty ] && [ -n "${TMUX:-}" ]; then
    placeholders=1
    passthrough_on || echo "molterm: tmux passes Kitty graphics on only with allow-passthrough: tmux set -g allow-passthrough on" >&2
fi
read_background
read_cell_size
[ $verbose = 1 ] && echo "molterm: using $mode" >&2
if [ $verbose = 1 ] && [ -n "${TMUX:-}" ]; then
    outer=$(tmux display -p '#{client_termname} #{client_termtype}' 2>/dev/null)
    echo "molterm: in tmux $(tmux -V 2>/dev/null | cut -d' ' -f2), inside '$outer'; allow-passthrough is" \
         "'$(tmux show -Apv allow-passthrough 2>/dev/null)'; pictures as Unicode placeholders: $([ $placeholders = 1 ] && echo yes || echo no)" >&2
fi

# The guided session. The server runs each command, and only the commands it lists; nothing typed here reaches a shell.
TAB=$(printf '\t')
selection=""; have_selection=0

columns() {
    if [ -n "$cols" ]; then echo "$cols"; return; fi
    size=$(stty size </dev/tty 2>/dev/null)
    width=${size#* }
    case $width in ''|*[!0-9]*|0) echo 100 ;; *) echo "$width" ;; esac
}

say() { printf '%s\n' "$1" | fold -s -w "$(($(columns) - 1))"; }

run_line() {
    case $mode in braille|octant|ascii) display=text ;; *) display=$mode ;; esac
    set -- -sS -G --data-urlencode "line=$1" --data-urlencode "display=$display" --data-urlencode "cols=$(columns)"
    [ $have_selection = 1 ] && set -- "$@" --data-urlencode "selection=$selection"
    [ -n "$theme" ] && set -- "$@" --data-urlencode "theme=$theme"
    [ $placeholders = 1 ] && set -- "$@" --data-urlencode "placeholders=1"
    for option in $(colour_options); do set -- "$@" --data-urlencode "$option"; done
    if [ "$mode" = sixel ]; then
        [ -n "$cell_width" ] && set -- "$@" --data-urlencode "cell_width=$cell_width"
    fi
    answer=$(curl "$@" -w '\n%{http_code}' "$url/api/v1/term.txt") || return 1
    status=${answer##*
}
    answer=${answer%
*}
    if [ "$status" != 200 ]; then
        printf 'molterm: %s\n' "$answer" >&2
        return 1
    fi
    first=${answer%%
*}
    output=${answer#"$first"}
    output=${output#
}
    selection=${first#* }
    have_selection=1
    if [ "${first%% *}" = 1 ]; then
        [ "$mode" = kitty ] && kitty_command 'a=d,q=2'
        printf '\033[2J\033[H'
    fi
    [ -z "$output" ] || printf '%s\n' "$output"
}

prompt() { printf '\033[32mmedchem$\033[0m %s' "$1"; }

# Read a line from the terminal itself, since curl | sh leaves the script on stdin. Returns false at the end of input or on q.
ask() {
    prompt ""
    IFS= read -r line </dev/tty || { echo; return 1; }
    case $line in q|quit|exit) return 1 ;; esac
    return 0
}

free_prompt() {
    say "Your turn: help lists the commands, and q quits."
    while ask; do
        [ -z "$line" ] || { run_line "$line"; echo; }
    done
}

heading() { printf '\n\033[1mStep %s of %s: %s\033[0m\n' "$1" "$2" "$3"; say "$4"; echo; }

session() {
    steps=$(curl -sS "$url/api/v1/story.txt") || exit 1
    total=$(printf '%s\n' "$steps" | wc -l | tr -d ' ')
    index=0
    while IFS=$TAB read -r title command before after <&3; do
        index=$((index + 1))
        heading "$index" "$total" "$title" "$before"
        if [ "$1" = play ]; then
            prompt "$command"; echo
            sleep 1
        else
            while :; do
                printf '\033[90mPress Enter to run: %s (or type another command, q quits)\033[0m\n' "$command"
                ask || return 0
                case $line in
                    '') prompt "$command"; echo; break ;;  # so that the transcript shows what ran
                    "$command") break ;;
                esac
                run_line "$line"; echo
            done
        fi
        run_line "$command"
        echo; say "$after"
        [ "$1" = play ] && sleep 4
    done 3<<STEPS
$steps
STEPS
    [ "$1" = play ] || { echo; free_prompt; }
}

# The editor. All of it runs on the server: this only passes on raw input, as hex, and prints what comes back.
editing=0
leave_editor() {
    [ $editing = 1 ] || return 0
    editing=0
    if [ -n "$box" ]; then  # the screen is the host's, so only the editor's own images go
        for id in 7001 7002 7005 7006 7010 7011 7020 7021; do
            # through tmux the editor's pictures go by other ids (see wire_id in server/placeholders.py)
            [ $placeholders = 1 ] && id=$(( (id / 255) << 24 | (id % 255 + 1) ))
            kitty_command "a=d,d=I,i=$id,q=2" >/dev/tty
        done
        printf '\033[?1016l\033[?1006l\033[?1003l\033[?25h' >/dev/tty
    else
        kitty_command 'a=d,q=2' >/dev/tty
        printf '\033[?1016l\033[?1006l\033[?1003l\033[?25h\033[?1049l' >/dev/tty
    fi
    restore
}

edit_request() {
    set -- -sS --data-urlencode "events=$1" --data-urlencode "cols=${2#* }" --data-urlencode "rows=${2%% *}"
    if [ -n "$state" ]; then
        set -- "$@" --data-urlencode "state=$state"
    elif [ -n "$start" ] && [ "$smiles" = tui ]; then
        set -- "$@" --data-urlencode "items=$start"
    elif [ -n "$start" ]; then
        set -- "$@" --data-urlencode "smiles=$start"
    fi
    [ -n "$box" ] && set -- "$@" --data-urlencode "box=$box"
    [ -n "$title" ] && set -- "$@" --data-urlencode "title=$title"
    [ -n "$theme" ] && set -- "$@" --data-urlencode "theme=$theme"
    [ $placeholders = 1 ] && set -- "$@" --data-urlencode "placeholders=1"
    for option in $(colour_options); do set -- "$@" --data-urlencode "$option"; done
    if [ -n "$cell_width" ] && [ -n "$cell_height" ]; then
        set -- "$@" --data-urlencode "cell_width=$cell_width" --data-urlencode "cell_height=$cell_height"
        [ "$pixels" = 1 ] && set -- "$@" --data-urlencode "pixels=1"
    fi
    answer=$(curl "$@" -w '\n%{http_code}' "$url/api/v1/$smiles" 2>&1) || { failure="the server did not answer: $answer"; return 1; }
    status=${answer##*
}
    answer=${answer%
*}
    if [ "$status" != 200 ]; then
        failure=$answer
        return 1
    fi
    first=${answer%%
*}
    output=${answer#"$first"}
    output=${output#
}
    outcome=${first%% *}
    state=${first#* }
}

edit_session() {
    if ! { : </dev/tty; } 2>/dev/null; then
        echo "molterm: the editor needs a terminal" >&2
        exit 1
    fi
    if [ "$mode" != kitty ]; then
        if [ -n "${TMUX:-}" ]; then
            outer=$(tmux display -p '#{client_termname} #{client_termtype}' 2>/dev/null)
            if outer_kitty && ! passthrough_on; then
                echo "molterm: the editor needs Kitty graphics, which tmux passes on only with: tmux set -g allow-passthrough on" >&2
            else
                echo "molterm: the editor needs Kitty graphics, and tmux says the terminal outside it is '$outer', not Kitty or Ghostty." \
                     "-m kitty overrides this." >&2
            fi
        else
            echo "molterm: the editor needs Kitty graphics, as in Kitty or Ghostty, and this terminal seems to offer $mode. -m kitty overrides this." >&2
        fi
        exit 1
    fi
    pixels=0
    case $reply in *'[?1016;1$y'*|*'[?1016;2$y'*) [ -n "$cell_height" ] && pixels=1 ;; esac
    state=""; failure=""; outcome=0; drawn=""
    saved=$(stty -g </dev/tty 2>/dev/null)
    editing=1
    trap 'leave_editor; exit 130' INT TERM HUP
    trap leave_editor EXIT
    # raw, so that every key and Ctrl-C arrive here; a read gives up after a second, to notice a change of window size
    stty raw -echo min 0 time 10 </dev/tty 2>/dev/null
    [ -n "$box" ] || printf '\033[?1049h' >/dev/tty
    printf '\033[?25l\033[?1003h\033[?1006h' >/dev/tty
    [ $pixels = 1 ] && printf '\033[?1016h' >/dev/tty
    events=""
    while :; do
        size=$(stty size </dev/tty 2>/dev/null)
        [ -n "$size" ] || size="24 80"
        if [ -n "$events" ] || [ "$size" != "$drawn" ]; then
            edit_request "$events" "$size" || break
            drawn=$size
            [ "$outcome" = 0 ] || break
            [ -z "$output" ] || printf '%s' "$output" >/dev/tty
        fi
        events=$(dd bs=4096 count=1 </dev/tty 2>/dev/null | od -An -v -tx1 | tr -d ' \n')
    done
    leave_editor
    trap - INT TERM HUP EXIT
    if [ -n "$failure" ]; then
        printf 'molterm: %s\n' "$failure" >&2
        exit 1
    fi
    if [ "$outcome" = 2 ]; then
        printf 'molterm: %s\n' "$output" >&2
        exit 1
    fi
    [ "$outcome" = 3 ] && exit 3
    [ -z "$output" ] || printf '%s\n' "$output"
    exit 0
}

if [ "$smiles" = edit ] || [ "$smiles" = tui ]; then
    edit_session
fi

if [ "$smiles" = demo ] || [ "$smiles" = play ]; then
    if ! [ -t 1 ] || ! { : </dev/tty; } 2>/dev/null; then
        echo "molterm: the $smiles needs a terminal" >&2
        exit 1
    fi
    session "$smiles"
    exit 0
fi

set -- -sS -G --data-urlencode "input=$smiles" --data-urlencode "mode=$mode"
[ -n "$theme" ] && set -- "$@" --data-urlencode "theme=$theme"
[ $placeholders = 1 ] && set -- "$@" --data-urlencode "placeholders=1"
[ -n "$cols" ] && set -- "$@" --data-urlencode "cols=$cols"
[ -n "$rows" ] && set -- "$@" --data-urlencode "rows=$rows"
[ -n "$fog" ] && set -- "$@" --data-urlencode "fog=$fog"
for option in $(colour_options); do set -- "$@" --data-urlencode "$option"; done
if [ "$mode" = sixel ]; then
    [ -n "$cell_width" ] && set -- "$@" --data-urlencode "cell_width=$cell_width"
fi
case $mode in
    braille|octant|ascii) [ -t 1 ] && set -- "$@" --data-urlencode "color=1" ;;
esac

body=$(curl "$@" -w '\n%{http_code}' "$url/api/v1/render.txt") || exit 1
status=${body##*
}
body=${body%
*}
if [ "$status" != 200 ]; then
    printf 'molterm: %s' "$body" >&2
    exit 1
fi
printf '%s' "$body"
