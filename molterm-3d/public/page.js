'use strict';
// The web page: Mol*'s own viewer, given structures by this server (/api/v1/structure), and the terminal's picture beside it.
(async () => {
    const message = document.getElementById('message');
    const field = document.getElementById('input');
    const picture = document.getElementById('picture');
    const choices = ['style', 'colour', 'lighting', 'turn'].map((id) => document.getElementById(id));
    const say = (text, error) => { message.textContent = text; message.classList.toggle('err', !!error); };

    let viewer;
    try {
        viewer = await molstar.Viewer.create('viewer', {
            layoutIsExpanded: false, layoutShowControls: false, layoutShowLog: false, layoutShowSequence: true,
            viewportShowExpand: true, viewportShowSelectionMode: false, viewportShowAnimation: false,
            collapseLeftPanel: true, pdbProvider: 'rcsb', emdbProvider: 'rcsb',
        });
    } catch (e) {
        say(`Mol* couldn't start here: ${e.message}. Your browser may have WebGL turned off.`, true);
        return;
    }

    function showPicture() {
        const input = field.value.trim();
        if (!input) return;
        const [style, colour, lighting, turn] = choices.map((c) => c.value);
        const dark = matchMedia('(prefers-color-scheme: dark)').matches;
        picture.src = '/api/v1/render.png?' + new URLSearchParams({ input, style, colour, lighting, turn, width: 600, height: 450,
            background: dark ? '1f1f1e' : 'ffffff' });
    }

    async function show(input) {
        input = input.trim();
        if (!input) return;
        say(`Loading ${input}…`);
        const answer = await fetch('/api/v1/structure?' + new URLSearchParams({ input }));
        const text = await answer.text();
        if (!answer.ok) { say(text.trim(), true); return; }
        const format = answer.headers.get('X-Molterm-Format');
        const name = decodeURIComponent(answer.headers.get('X-Molterm-Name') || input);
        await viewer.plugin.clear();
        await viewer.loadStructureFromData(text, format, { dataLabel: name });
        say(name);
        history.replaceState(null, '', '?' + new URLSearchParams({ input }));
        showPicture();
    }

    document.getElementById('controls').addEventListener('submit', (event) => { event.preventDefault(); show(field.value); });
    for (const button of document.querySelectorAll('.sample')) {
        button.addEventListener('click', () => { field.value = button.dataset.input; show(field.value); });
    }
    for (const choice of choices) choice.addEventListener('change', showPicture);
    picture.addEventListener('error', () => say('The picture could not be drawn.', true));
    const start = new URLSearchParams(location.search).get('input');
    if (start) field.value = start;
    show(field.value);
})();
