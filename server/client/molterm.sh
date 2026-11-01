#!/bin/sh
# molterm: draw a molecule in this terminal, as sharply as the terminal allows.
#
#   molterm 'CC(=O)Oc1ccccc1C(=O)O'
#   molterm -m braille -t light 'CCO'
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
