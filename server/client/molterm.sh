#!/bin/sh
# molterm: draw a molecule in this terminal, as sharply as the terminal allows.
#
#   molterm 'CC(=O)Oc1ccccc1C(=O)O'
#   molterm -m braille -t light 'CCO'
#   molterm demo     a guided medicinal chemistry session, one step at a time, then your own commands
#   molterm play     the same session, playing by itself
#
# Asks the terminal what it can do, then uses Kitty graphics, iTerm2 images or Sixel, and falls back to Braille text.
# Needs only sh, curl, stty and dd. The molecule goes to the server named below, or in $MOLTERM_URL.
set -u
url=${MOLTERM_URL:-@@URL@@}
mode=${MOLTERM_MODE:-auto}
theme=""; cols=""; rows=""; verbose=0

usage() {
    cat <<USAGE
usage: molterm [-m mode] [-t theme] [-c cols] [-r rows] [-v] <SMILES>
       molterm [-m mode] [-t theme] [-c cols] [-v] demo|play
  -m  auto, kitty, iterm, sixel, octant or braille (default auto, or \$MOLTERM_MODE)
  -t  dark or light, to suit the background (default: ask the terminal)
  -c  most columns to use   -r  most rows to use
  -v  say which mode was chosen, on stderr
USAGE
}

while getopts m:t:c:r:vh option; do
    case $option in
        m) mode=$OPTARG ;;
        t) theme=$OPTARG ;;
        c) cols=$OPTARG ;;
        r) rows=$OPTARG ;;
        v) verbose=1 ;;
        h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
shift $((OPTIND - 1))
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

# Ask the terminal four things, ending with the one every terminal answers, so that we know when to stop reading:
# a Kitty graphics query, its name and version, its background colour and cell size, and its device attributes.
ask_terminal() {
    [ -t 1 ] || return 0
    case ${TERM:-dumb} in dumb|unknown) return 0 ;; esac
    { : </dev/tty; } 2>/dev/null || return 0
    saved=$(stty -g </dev/tty 2>/dev/null) || { saved=""; return 0; }
    trap 'restore; exit 130' INT TERM HUP
    trap restore EXIT
    stty -echo -icanon min 0 time 5 </dev/tty 2>/dev/null || { saved=""; return 0; }
    # tmux would take the Kitty query for a window title
    in_multiplexer || printf '\033_Gi=31,s=1,v=1,a=q,t=d,f=24;AAAA\033\\' >/dev/tty
    printf '\033[>q\033]11;?\033\\\033[16t\033[c' >/dev/tty
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

# Replies carry the background as rgb:RRRR/GGGG/BBBB, with one to four hex digits per channel.
background=""
read_background() {
    color=$(printf '%s' "$reply" | sed -n 's/.*\]11;rgb:\([0-9a-fA-F]*\)\/\([0-9a-fA-F]*\)\/\([0-9a-fA-F]*\).*/\1 \2 \3/p')
    [ -n "$color" ] || return 0
    set -- $color
    r=$(printf '%.2s' "$1$1"); g=$(printf '%.2s' "$2$2"); b=$(printf '%.2s' "$3$3")
    background=$r$g$b
    if [ -z "$theme" ]; then
        if [ $(( 299 * 0x$r + 587 * 0x$g + 114 * 0x$b )) -gt 127500 ]; then theme=light; else theme=dark; fi
    fi
}

cell_width=""
read_cell_size() {
    cell_width=$(printf '%s' "$reply" | sed -n 's/.*\[6;[0-9]*;\([0-9]*\)t.*/\1/p')
}

case $mode in
    auto|kitty|iterm|sixel) ask_terminal ;;
esac
[ "$mode" = auto ] && mode=$(choose_mode)
read_background
read_cell_size
[ $verbose = 1 ] && echo "molterm: using $mode" >&2

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
    case $mode in braille|octant) display=text ;; *) display=$mode ;; esac
    set -- -sS -G --data-urlencode "line=$1" --data-urlencode "display=$display" --data-urlencode "cols=$(columns)"
    [ $have_selection = 1 ] && set -- "$@" --data-urlencode "selection=$selection"
    [ -n "$theme" ] && set -- "$@" --data-urlencode "theme=$theme"
    if [ "$mode" = sixel ]; then
        [ -n "$background" ] && set -- "$@" --data-urlencode "background=$background"
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
        [ "$mode" = kitty ] && printf '\033_Ga=d,q=2\033\\'
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
[ -n "$cols" ] && set -- "$@" --data-urlencode "cols=$cols"
[ -n "$rows" ] && set -- "$@" --data-urlencode "rows=$rows"
if [ "$mode" = sixel ]; then
    [ -n "$background" ] && set -- "$@" --data-urlencode "background=$background"
    [ -n "$cell_width" ] && set -- "$@" --data-urlencode "cell_width=$cell_width"
fi
case $mode in
    braille|octant) [ -t 1 ] && set -- "$@" --data-urlencode "color=1" ;;
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
