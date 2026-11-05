'use strict';
// The renderer's OpenGL: headless-gl with EXT_frag_depth, so that Mol* draws atoms and bonds as impostors.
const test = require('node:test');
const assert = require('node:assert');
const renderer = require('../src/renderer');

test('EXT_frag_depth is offered, and a shader that writes the depth compiles', () => {
    const gl = renderer.gl(4, 4);
    assert.ok(gl.getSupportedExtensions().includes('EXT_frag_depth'));
    assert.ok(gl.getExtension('EXT_frag_depth'));
    assert.ok(gl.getExtension('OES_texture_float'), 'Mol*\'s impostors need float textures too');
    const shader = gl.createShader(gl.FRAGMENT_SHADER);
    gl.shaderSource(shader, '#extension GL_EXT_frag_depth : enable\nprecision mediump float;\n'
        + 'void main() { gl_FragColor = vec4(1.0); gl_FragDepthEXT = 0.5; }');
    gl.compileShader(shader);
    assert.ok(gl.getShaderParameter(shader, gl.COMPILE_STATUS), gl.getShaderInfoLog(shader));
    gl.getExtension('STACKGL_destroy_context').destroy();
});
