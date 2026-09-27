'use strict';
const assert = require('node:assert');
const { rutaCorta } = require('../ruta-corta.js');

// El home del usuario se abrevia a ~ (/home/<user> y /root).
assert.strictEqual(rutaCorta('/home/ana/projects/app'), '~/projects/app');
assert.strictEqual(rutaCorta('/root/projects/app'), '~/projects/app');
assert.strictEqual(rutaCorta('/home/ana'), '~');

// Rutas cortas quedan enteras.
assert.strictEqual(rutaCorta('/srv/app'), '/srv/app');
assert.strictEqual(rutaCorta('~/a/b'), '~/a/b');

// Rutas largas: se conserva la RAÍZ y los 2 últimos tramos (lo que identifica
// la carpeta); el medio se cambia por "…". Antes las cards cortaban por el
// FINAL y mostraban justo el prefijo inútil («/tmp/claude-0/-home-…»).
assert.strictEqual(rutaCorta('/tmp/claude-0/-home-user-x/ffef/scratchpad/demo-app'),
                   '/tmp/…/scratchpad/demo-app');
assert.strictEqual(rutaCorta('/home/ana/code/clientes/acme/web'), '~/…/acme/web');
assert.strictEqual(rutaCorta('/home/ana/code/web/'), '~/code/web');   // barra final fuera

// Basura → string vacío (nunca "undefined" en pantalla).
assert.strictEqual(rutaCorta(''), '');
assert.strictEqual(rutaCorta(null), '');
assert.strictEqual(rutaCorta(undefined), '');
console.log('ruta-corta.test.js OK');
