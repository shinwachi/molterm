'use strict';
// Mol* in Node: a few headless plugin contexts, each holding one structure drawn one way, rendering it from any angle to a PNG.
// Rendering is software OpenGL on a virtual display (see the Dockerfile), so the work is queued: one picture at a time.

const gl = require('gl');
const pngjs = require('pngjs');
const { HeadlessPluginContext } = require('molstar/lib/commonjs/mol-plugin/headless-plugin-context');
const { DefaultPluginSpec } = require('molstar/lib/commonjs/mol-plugin/spec');
const { Vec3, Quat, Mat3 } = require('molstar/lib/commonjs/mol-math/linear-algebra');
const { structureLayingTransform } = require('molstar/lib/commonjs/mol-plugin-state/manager/focus-camera/orient-axes');
const { MolScriptBuilder: MS } = require('molstar/lib/commonjs/mol-script/language/builder');

const WITHOUT_WATER = MS.struct.modifier.exceptBy({
    0: MS.struct.generator.all(),
    by: MS.struct.generator.atomGroups({ 'entity-test': MS.core.rel.eq([MS.ammp('entityType'), 'water']) }),
});
const { Color } = require('molstar/lib/commonjs/mol-util/color');

const SCENES = 3; // contexts kept, each with its own GL context; the least recently used is cleared for a new structure
const MAX_PIXELS = 1600 * 1000; // larger windows get a smaller picture, which the terminal scales up
const FOV = Math.PI / 4; // narrower than the plugin's 90 degrees, so near atoms aren't swollen
const CANVAS = { width: 640, height: 480 }; // the contexts' own canvas; pictures are drawn into targets of the size asked for

// What `s`, `c` and `l` step through in the terminal, and the web page's choices
const STYLES = ['auto', 'ball-and-stick', 'spacefill', 'licorice', 'cartoon', 'surface'];
const COLOURS = ['auto', 'element', 'chain', 'secondary', 'rainbow', 'residue', 'b-factor', 'illustrative'];
// Illustrative is Mol*'s style of that name: flat colour, black outlines and ambient occlusion, after David Goodsell's drawings
const LIGHTINGS = ['illustrative', 'standard', 'occlusion', 'outline'];

const OUTLINE = { name: 'on', params: { scale: 1, color: Color(0x000000), threshold: 0.33, includeTransparent: true } };
const OCCLUSION = { name: 'on', params: { multiScale: { name: 'off', params: {} }, radius: 5, bias: 0.8, blurKernelSize: 15,
    blurDepthBias: 0.5, samples: 32, resolutionScale: 1, color: Color(0x000000), transparentThreshold: 0.4 } };
const OFF = { name: 'off', params: {} };
const POSTPROCESSING = {
    illustrative: { outline: OUTLINE, occlusion: OCCLUSION, shadow: OFF },
    standard: { outline: OFF, occlusion: OFF, shadow: OFF },
    occlusion: { outline: OFF, occlusion: OCCLUSION, shadow: OFF },
    outline: { outline: OUTLINE, occlusion: OFF, shadow: OFF },
};

const COLOUR_THEMES = {
    illustrative: { color: 'illustrative', colorParams: { style: { name: 'chain-id', params: {} } } },
    element: { color: 'element-symbol', colorParams: { carbonColor: { name: 'element-symbol', params: {} } } },
    chain: { color: 'chain-id' },
    secondary: { color: 'secondary-structure' },
    rainbow: { color: 'sequence-id' },
    residue: { color: 'residue-name' },
    'b-factor': { color: 'uncertainty' },
};

let queue = Promise.resolve();
/** Run `work` after everything queued before it, so that only one thing touches OpenGL at a time. */
function serial(work) {
    const run = queue.then(work, work);
    queue = run.catch(() => {});
    return run;
}

class Scene {
    constructor() {
        this.plugin = null;
        this.key = null; // structure key + style + colour + hydrogens, of what's built
        this.used = 0;
        this.info = null;
    }

    async init() {
        this.plugin = new HeadlessPluginContext({ gl, pngjs }, DefaultPluginSpec(), CANVAS, {
            imagePass: { transparentBackground: true, cameraHelper: { axes: { name: 'off', params: {} } }, multiSample: { mode: 'off' } },
        });
        await this.plugin.init();
    }

    async build(structure, look) {
        const flat = look.lighting === 'illustrative'; // the representations ignore the light; the rest of a lighting is per picture
        const key = `${structure.key}|${look.style}|${look.colour}|${look.hydrogens ? 1 : 0}|${flat ? 1 : 0}`;
        if (this.key === key) return this.info;
        this.key = null;
        const plugin = this.plugin;
        await plugin.clear();
        const builders = plugin.builders.structure;
        const data = await plugin.builders.data.rawData({ data: structure.data, label: structure.label });
        const trajectory = await builders.parseTrajectory(data, structure.format);
        const model = await builders.createModel(trajectory);
        const whole = await builders.createStructure(model);
        const stats = whole.cell.obj.data;
        const polymer = await builders.tryCreateComponentStatic(whole, 'polymer');
        const atoms = { ignoreHydrogens: !look.hydrogens };
        const colour = (fallback) => COLOUR_THEMES[look.colour === 'auto' ? fallback : look.colour] || {};
        const add = (component, type, typeParams, fallback) => component && builders.representation.addRepresentation(component,
            { type, typeParams: { ...typeParams, ignoreLight: flat }, ...colour(fallback) });
        let drawn = null; // the component the picture is fitted to
        let style = look.style;
        if (style === 'auto') style = polymer && stats.polymerResidueCount > 2 ? 'cartoon' : 'ball-and-stick';
        if (style === 'cartoon' && !(polymer && stats.polymerResidueCount > 2)) style = 'ball-and-stick';
        if (style === 'cartoon') {
            await add(polymer, 'cartoon', {}, 'chain');
            drawn = polymer;
            const ligand = await builders.tryCreateComponentStatic(whole, 'ligand');
            await add(ligand, 'ball-and-stick', atoms, 'element');
            const branched = await builders.tryCreateComponentStatic(whole, 'branched');
            await add(branched, 'ball-and-stick', atoms, 'element');
        } else {
            // everything but water, which in a crystal structure is a cloud of loose oxygens, as one component, so that a surface is one
            const all = await builders.tryCreateComponentFromExpression(whole, WITHOUT_WATER, 'without-water');
            drawn = all;
            const big = stats.elementCount > 3000;
            const protein = stats.polymerResidueCount > 2;
            // a protein's atoms in illustrative lighting: Goodsell's colours, each chain its own with lighter carbons
            const goodsell = protein && flat;
            if (style === 'ball-and-stick') await add(all, 'ball-and-stick', atoms, big ? 'chain' : 'element');
            if (style === 'licorice') await add(all, 'ball-and-stick', { ...atoms, sizeFactor: 0.2, sizeAspectRatio: 1 }, big ? 'chain' : 'element');
            if (style === 'spacefill') await add(all, 'spacefill', atoms, goodsell ? 'illustrative' : big ? 'chain' : 'element');
            if (style === 'surface') await add(all, 'molecular-surface', atoms, goodsell ? 'illustrative' : protein ? 'chain' : 'element');
        }
        plugin.canvas3d.commit(true);
        // the structure's own sphere, with room for the atoms' own size: the scene's is padded for Mol*'s own camera
        // of what's drawn, which leaves out a crystal's water
        const sphere = (drawn?.cell?.obj?.data ?? stats).boundary.sphere;
        // the home view: Mol*'s "orient axes", the structure laid along its principal axes, the longest across the screen. It
        // depends on the structure alone, so every style and colour of it starts from the same side.
        const toCamera = Mat3.invert(Mat3(), structureLayingTransform([stats]).rotation);
        const home = { position: Vec3.transformMat3(Vec3(), Vec3.create(0, 0, 1), toCamera), target: Vec3.zero(),
            up: Vec3.transformMat3(Vec3(), Vec3.create(0, 1, 0), toCamera) };
        this.info = {
            style, // what `auto` became
            atoms: stats.elementCount,
            residues: stats.polymerResidueCount,
            chains: stats.units.length,
            centre: Vec3.clone(sphere.center),
            radius: Math.max(sphere.radius + (style === 'spacefill' || style === 'surface' ? 1.6 : 0.4), 1.5),
            direction: Vec3.normalize(Vec3(), Vec3.sub(Vec3(), home.position, home.target)),
            up: Vec3.clone(home.up),
            fov: FOV,
        };
        this.key = key;
        return this.info;
    }

    /** A PNG of the scene as `view` turns it: a quaternion `q` [x, y, z, w] applied to Mol*'s home view, a zoom `z` (1 fits the
     * structure to the picture) and a shift `p` [x, y] across the screen, in structure radii. Or, instead of `q`, `turn` about the
     * screen's vertical and then `tilt` about its horizontal, in radians. */
    async render(view, width, height, background) {
        const info = this.info;
        // the home view the state was made with, so that a turn means the same after a change of style moves Mol*'s own
        const homeDirection = view.d ? Vec3.create(...view.d) : info.direction;
        const homeUp = view.u ? Vec3.create(...view.u) : info.up;
        let rotation = Quat.normalize(Quat(), Quat.create(...(view.q || [0, 0, 0, 1])));
        if (view.turn || view.tilt) {
            const homeRight = Vec3.normalize(Vec3(), Vec3.cross(Vec3(), homeUp, homeDirection));
            rotation = Quat.multiply(Quat(), Quat.setAxisAngle(Quat(), homeRight, -(view.tilt || 0)),
                Quat.setAxisAngle(Quat(), homeUp, -(view.turn || 0)));
        }
        const direction = Vec3.transformQuat(Vec3(), homeDirection, rotation);
        const up = Vec3.transformQuat(Vec3(), homeUp, rotation);
        const right = Vec3.normalize(Vec3(), Vec3.cross(Vec3(), up, direction));
        const aspect = width / height;
        const half = Math.min(FOV / 2, Math.atan(Math.tan(FOV / 2) * aspect));
        // the sphere's outline just inside the picture, at its narrower side
        const distance = info.radius / Math.tan(half) / view.z;
        const target = Vec3.clone(info.centre);
        Vec3.scaleAndAdd(target, target, right, -view.p[0] * info.radius);
        Vec3.scaleAndAdd(target, target, up, view.p[1] * info.radius);
        const position = Vec3.scaleAndAdd(Vec3(), target, direction, distance);
        const canvas3d = this.plugin.canvas3d;
        const props = { renderer: { backgroundColor: Color(background ?? 0xffffff) } };
        canvas3d.setProps(props);
        canvas3d.camera.setState({ position, target, up, fov: FOV, radius: info.radius * 1.2, radiusMax: info.radius * 1.2 }, 0);
        canvas3d.commit(true);
        const raw = await this.plugin.getImageRaw({ width, height }, POSTPROCESSING[view.lighting] || POSTPROCESSING.illustrative);
        const png = new pngjs.PNG({ width, height });
        png.data = Buffer.from(raw.data.buffer, raw.data.byteOffset, raw.data.byteLength);
        return pngjs.PNG.sync.write(png, { deflateLevel: 1 });
    }
}

const scenes = [];

async function sceneFor(structure) {
    let scene = scenes.find((s) => s.key && s.key.startsWith(structure.key + '|'));
    if (!scene && scenes.length < SCENES) {
        scene = new Scene();
        await scene.init();
        scenes.push(scene);
    }
    if (!scene) scene = scenes.reduce((a, b) => (a.used <= b.used ? a : b));
    scene.used = Date.now();
    return scene;
}

/** Fit a picture of width by height pixels within MAX_PIXELS, keeping its shape. */
function fit(width, height) {
    const scale = Math.min(1, Math.sqrt(MAX_PIXELS / (width * height)));
    return [Math.max(16, Math.round(width * scale)), Math.max(16, Math.round(height * scale))];
}

/** Draw a structure: { png, info }. `look` is { style, colour, hydrogens, lighting }, `view` as Scene.render takes it. */
function draw(structure, look, view, width, height, background) {
    look = { ...look, lighting: LIGHTINGS.includes(look.lighting) ? look.lighting : LIGHTINGS[0] };
    return serial(async () => {
        const scene = await sceneFor(structure);
        const info = await scene.build(structure, look);
        const [w, h] = fit(width, height);
        return { png: await scene.render({ ...view, lighting: look.lighting }, w, h, background), info };
    });
}

module.exports = { draw, fit, STYLES, COLOURS, LIGHTINGS };
