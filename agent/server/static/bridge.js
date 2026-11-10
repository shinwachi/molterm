// What a page needs to host molterm_tui.App, in one module a host can vendor whole (it imports nothing). page.js is molterm's own
// page built from it; another host builds its own the same way, with its own choices where this module offers them.
//
// Three kinds of thing are in a page like page.js, and this module keeps them apart:
//   contract  what the TUI needs from any page, whoever hosts it: the bridge's members (the TUI lists them, BRIDGE in app.py, and
//             App refuses a bridge without the required ones), and Shift+Enter sent as the kitty protocol writes it. Change these
//             and the TUI breaks.
//   policy    choices made for molterm's page that travel, each an option here with molterm's choice as its default: how text is
//             copied (copyText), and how clicks give the terminal the keyboard (focusPolicy).
//   the page  what suits only a page that is nothing but the terminal, such as every click being a click on it. That isn't here;
//             page.js asks for it by name (focusPolicy("page", ...)), and a page with other controls on it shouldn't.

// -- contract ---------------------------------------------------------------------------------------------------------------------

// Shift+Enter, as the kitty keyboard protocol writes it; the TUI reads it (and xterm's \x1b[27;2;13~) as a new line in a note or the
// message. A terminal sends Shift+Enter as a plain Enter unless asked for more, and asking would change how every other key comes.
export const SHIFT_ENTER = "\x1b[13;2u";

// A POST with JSON, its answer as JSON; the server's error message if it failed.
export async function api(path, data) {
    const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
                                         body: data === undefined ? undefined : JSON.stringify(data) });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error?.message || `${path} answered ${response.status}`);
    return result;
}

// A POST whose answer is wanted as text, with the server's error message if it failed.
export async function post(path, data) {
    const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
    const text = await response.text();
    if (response.ok) return text;
    let message = text.trim();
    try { message = JSON.parse(text).error?.message || message; } catch {}
    throw new Error(message || `the server answered ${response.status}`);
}

// The bridge App(bridge, cols, rows) is given: what the TUI calls to reach the terminal and the server.
//   session  the session's id
//   app      () => the App, once there is one (the bridge is made first)
//   cell     () => a terminal cell's size in device pixels, {width, height}, which molterm's editor draws to
//   write    text => the terminal: what the TUI writes goes to it, as from any program
//   base     where the agent server is, "" for this origin
//   copy     text => the clipboard, for the selection menu's Copy (copyText by default); null leaves it out, and the TUI then writes
//            OSC 52 for the terminal to copy, if it allows that
//   tutor    false leaves out tutor(name), with which /tutor [name] starts tutorial mode on a session that has no tutor yet
export function createBridge({ session, app, cell, write, base = "", copy = copyText, tutor = true }) {
    const at = `${base}/api/v1/sessions/${session}`;
    const failed = error => app()?.event(JSON.stringify({ type: "error", message: error.message }));
    const bridge = {
        write,
        post_message: (text, cols) => api(`${at}/messages`, { text, cols }).catch(failed),
        post_tool_result: (id, json) => api(`${at}/tool_results`, { id, result: JSON.parse(json) }).catch(failed),
        interrupt: () => api(`${at}/interrupt`).catch(() => {}),
        // molterm's editor, for a molecule edited where it's shown: the TUI sends the fields, and gets the editor's answer as text
        edit: json => {
            const { width, height } = cell();
            post(`${at}/edit`, { ...JSON.parse(json), cell_width: Math.round(width), cell_height: Math.round(height) })
                .then(text => app().edit_answer(text), error => app().edit_failed(error.message));
        },
        // a molecule's picture with its annotations, the notes left for the TUI to put in bubbles; the request comes back with the answer
        picture: request => post(`${at}/picture`, JSON.parse(request))
            .then(text => app().picture_answer(request, text), error => app().picture_failed(request, error.message)),
    };
    if (copy) bridge.copy = copy;
    // tutorial mode from now on, whatever the session's agent: the server starts its tutor, and tells the TUI in the event stream
    if (tutor) bridge.tutor = name => api(`${at}/tutor`, { tutor: name || true }).catch(failed);
    return bridge;
}

// Shift+Enter to the TUI as SHIFT_ENTER, caught before the terminal sees the key, while the focus is in `box` (the terminal's
// element). `key` is the App's key(). Returns a function that stops it.
export function sendShiftEnter({ box, key }) {
    const listener = event => {
        if (event.key !== "Enter" || !event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return;
        if (!box.contains(document.activeElement)) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        key(SHIFT_ENTER);
    };
    window.addEventListener("keydown", listener, true);
    return () => window.removeEventListener("keydown", listener, true);
}

// -- policy -----------------------------------------------------------------------------------------------------------------------

// Text to the clipboard. The Clipboard API needs a secure page (https or localhost), and molterm's may be served over plain http, so
// the older way is tried when it's missing or refuses: a hidden textarea selected and copied, which a click (the menu's Copy)
// allows. That takes the focus, so `refocus` gives it back. A host served only over https can pass its own copy instead.
export function copyText(text, { refocus } = {}) {
    const fallback = () => {
        const area = document.createElement("textarea");
        area.value = text;
        area.setAttribute("readonly", "");
        area.style.cssText = "position: fixed; top: 0; left: 0; opacity: 0";
        document.body.append(area);
        area.select();
        try { document.execCommand("copy"); } catch (error) { console.error(error); }
        area.remove();
        refocus?.();
    };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).catch(fallback);
    else fallback();
}

// Which clicks give the terminal the keyboard. The terminal has the keys only while it has the focus, and the TUI can't show that
// it hasn't: it draws its own screen, and the terminal's cue, a hollow cursor, is hidden while the TUI waits for an answer.
//   "terminal"  (the default) a click anywhere in `box`, its margin too, focuses the terminal; the rest of the page keeps its clicks.
//               For a page with other things on it: text boxes, panels.
//   "page"      every click on the page that isn't on a control (a, button, input, textarea, select, contenteditable) focuses the
//               terminal. Only for a page that is nothing but the terminal, as molterm's is; elsewhere it takes the keyboard from
//               every text box the host has.
//   "none"      nothing: the host decides.
// A click that ends a text selection is left alone, either way: that's the user taking a copy. Returns a function that stops it.
export function focusPolicy(policy = "terminal", { box, focus }) {
    if (policy === "none") return () => {};
    if (policy !== "terminal" && policy !== "page") throw new Error(`focusPolicy: "terminal", "page" or "none", not ${JSON.stringify(policy)}`);
    const target = policy === "page" ? document : box;
    const listener = event => {
        if (window.getSelection?.()?.isCollapsed === false) return;
        if (!event.target.closest?.("a, button, input, textarea, select, [contenteditable]")) focus();
    };
    target.addEventListener("click", listener);
    return () => target.removeEventListener("click", listener);
}
