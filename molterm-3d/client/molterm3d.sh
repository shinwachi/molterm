#!/bin/sh
# molterm3d: a structure in 3D in this terminal, drawn by Mol* on the server: turn it with the mouse, zoom with the wheel.
#
#   molterm3d 1crn                       a PDB entry, by its id
#   molterm3d 'CC(=O)Oc1ccccc1C(=O)O'    anything molterm reads, put in 3D by RDKit
#   molterm3d ligand.sdf                 a file: PDB, mmCIF, SDF or MOL, MOL2 or XYZ
#   molterm3d -s spacefill -c element 4hhb
#
# In the viewer: drag to turn, right-drag (or Shift-drag) to move, the wheel or middle-drag to zoom, arrows to turn a step,
# s and c to step through styles and colours, h for hydrogens, r to reset, ? for the keys, q to quit.
# Needs Kitty graphics (Kitty, Ghostty, and both inside tmux with allow-passthrough on), and only sh, curl, stty, dd and od.
set -u
url=${MOLTERM3D_URL:-@@URL@@}
style=""; colour=""; theme=""; hydrogens=""; force=0

usage() {
    cat <<USAGE
usage: molterm3d [-s style] [-c colour] [-H] [-t light|dark] [-k] <PDB id | SMILES | file>
  -s  auto, ball-and-stick, spacefill, licorice, cartoon or surface (default auto: cartoon for proteins, else ball-and-stick)
  -c  auto, element, chain, secondary, rainbow, residue or b-factor
  -H  start with hydrogens hidden
  -t  light or dark, when the terminal doesn't say which it is
  -k  use Kitty graphics even if the terminal doesn't seem to have them
The server is $url (or \$MOLTERM3D_URL).
USAGE
}

while getopts s:c:t:Hkh option; do
    case $option in
        s) style=$OPTARG ;;
        c) colour=$OPTARG ;;
        t) theme=$OPTARG ;;
        H) hydrogens=0 ;;
        k) force=1 ;;
        h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
shift $((OPTIND - 1))
if [ $# -ne 1 ]; then usage >&2; exit 2; fi
input=$1

command -v curl >/dev/null 2>&1 || { echo "molterm3d: this needs curl" >&2; exit 1; }
if ! { : </dev/tty; } 2>/dev/null; then echo "molterm3d: this needs a terminal" >&2; exit 1; fi

reply=""; saved=""
restore() { if [ -n "$saved" ]; then stty "$saved" </dev/tty 2>/dev/null; saved=""; fi; }

in_multiplexer() {
    [ -n "${TMUX:-}" ] || [ -n "${STY:-}" ] && return 0
    case ${TERM:-} in screen*|tmux*) return 0 ;; esac
    return 1
}
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

# Ask the terminal for Kitty graphics, its background colour, its cell size and whether it reports the mouse in pixels, ending with
# its device attributes, which every terminal answers, so that we know when to stop reading
ask_terminal() {
    saved=$(stty -g </dev/tty 2>/dev/null) || { saved=""; return 0; }
    trap 'restore; exit 130' INT TERM HUP
    trap restore EXIT
    stty -echo -icanon min 0 time 5 </dev/tty 2>/dev/null || { saved=""; return 0; }
    in_multiplexer || printf '\033_Gi=31,s=1,v=1,a=q,t=d,f=24;AAAA\033\\' >/dev/tty
    printf '\033]11;?\033\\\033[16t\033[?1016$p\033[c' >/dev/tty
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
ask_terminal

placeholders=0
kitty=0
if in_multiplexer; then
    if outer_kitty; then
        kitty=1; placeholders=1
        passthrough_on || { echo "molterm3d: tmux passes Kitty graphics on only with: tmux set -g allow-passthrough on" >&2; exit 1; }
    fi
else
    case $reply in *'_Gi=31;OK'*) kitty=1 ;; esac
    case ${TERM_PROGRAM:-}:${TERM:-} in ghostty:*|*:xterm-kitty|*:xterm-ghostty) kitty=1 ;; esac
fi
[ $force = 1 ] && kitty=1 && [ -n "${TMUX:-}" ] && placeholders=1
if [ $kitty = 0 ]; then
    echo "molterm3d: this needs Kitty graphics, as in Kitty or Ghostty (inside tmux too, with allow-passthrough on). -k overrides this." >&2
    exit 1
fi

background=$(printf '%s' "$reply" | sed -n 's/.*\]11;rgb:\([0-9a-fA-F][0-9a-fA-F]\)[0-9a-fA-F]*\/\([0-9a-fA-F][0-9a-fA-F]\)[0-9a-fA-F]*\/\([0-9a-fA-F][0-9a-fA-F]\)[0-9a-fA-F]*.*/\1\2\3/p')
cell_width=$(printf '%s' "$reply" | sed -n 's/.*\[6;[0-9]*;\([0-9]*\)t.*/\1/p')
cell_height=$(printf '%s' "$reply" | sed -n 's/.*\[6;\([0-9]*\);[0-9]*t.*/\1/p')
pixels=0
case $reply in *'[?1016;1$y'*|*'[?1016;2$y'*) [ -n "$cell_height" ] && pixels=1 ;; esac

kitty_command() {
    if [ $placeholders = 1 ]; then printf '\033Ptmux;\033\033_G%s\033\033\\\033\\' "$1"; else printf '\033_G%s\033\\' "$1"; fi
}

viewing=0
leave() {
    [ $viewing = 1 ] || return 0
    viewing=0
    for id in 3301 3302; do
        [ $placeholders = 1 ] && id=$(( (id / 255) << 24 | (id % 255 + 1) ))
        kitty_command "a=d,d=I,i=$id,q=2" >/dev/tty
    done
    printf '\033[?1016l\033[?1006l\033[?1003l\033[?25h\033[?1049l' >/dev/tty
    restore
}

state=""; failure=""; outcome=0
step() {
    set -- -sS --data-urlencode "events=$1" --data-urlencode "cols=${2#* }" --data-urlencode "rows=${2%% *}"
    if [ -n "$state" ]; then
        set -- "$@" --data-urlencode "state=$state"
    elif [ -f "$input" ]; then
        set -- "$@" --data-urlencode "data@$input"
    else
        set -- "$@" --data-urlencode "input=$input"
    fi
    [ -n "$style" ] && set -- "$@" --data-urlencode "style=$style"
    [ -n "$colour" ] && set -- "$@" --data-urlencode "colour=$colour"
    [ -n "$hydrogens" ] && set -- "$@" --data-urlencode "hydrogens=$hydrogens"
    [ -n "$theme" ] && set -- "$@" --data-urlencode "theme=$theme"
    [ -n "$background" ] && set -- "$@" --data-urlencode "background=$background"
    [ $placeholders = 1 ] && set -- "$@" --data-urlencode "placeholders=1"
    if [ -n "$cell_width" ] && [ -n "$cell_height" ]; then
        set -- "$@" --data-urlencode "cell_width=$cell_width" --data-urlencode "cell_height=$cell_height"
        [ $pixels = 1 ] && set -- "$@" --data-urlencode "pixels=1"
    fi
    answer=$(curl "$@" -w '\n%{http_code}' "$url/api/v1/view" 2>&1) || { failure="the server did not answer: $answer"; return 1; }
    status=${answer##*
}
    answer=${answer%
*}
    if [ "$status" != 200 ]; then failure=$answer; return 1; fi
    first=${answer%%
*}
    output=${answer#"$first"}
    output=${output#
}
    outcome=${first%% *}
    state=${first#* }
}

saved=$(stty -g </dev/tty 2>/dev/null)
viewing=1
trap 'leave; exit 130' INT TERM HUP
trap leave EXIT
# raw, so that every key and Ctrl-C arrive here; a read gives up after a second, to notice a change of window size
stty raw -echo min 0 time 10 </dev/tty 2>/dev/null
printf '\033[?1049h\033[?25l\033[?1003h\033[?1006h' >/dev/tty
[ $pixels = 1 ] && printf '\033[?1016h' >/dev/tty
printf '\033[HOpening %s ...' "$input" >/dev/tty
events=""; drawn=""
while :; do
    size=$(stty size </dev/tty 2>/dev/null)
    [ -n "$size" ] || size="24 80"
    if [ -n "$events" ] || [ "$size" != "$drawn" ]; then
        step "$events" "$size" || break
        drawn=$size
        [ "$outcome" = 0 ] || break
        [ -z "$output" ] || printf '%s' "$output" >/dev/tty
    fi
    events=$(dd bs=4096 count=1 </dev/tty 2>/dev/null | od -An -v -tx1 | tr -d ' \n')
done
leave
trap - INT TERM HUP EXIT
if [ -n "$failure" ]; then printf 'molterm3d: %s\n' "$failure" >&2; exit 1; fi
if [ "$outcome" = 2 ]; then printf 'molterm3d: %s\n' "$output" >&2; exit 1; fi
exit 0
