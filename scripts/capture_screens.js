/* Captura de pantallas para la auditoría de UI (docs/UI_AUDIT_FASE2.md).
 *
 *   CHROME_PATH="C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" \
 *     node scripts/capture_screens.js
 *
 * Requiere el servidor local en el puerto 8010. Guarda PNG a página completa
 * en docs/ui-audit/ — 1440px para todas las rutas y 390px para las marcadas
 * como críticas en móvil.
 */
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright-core');

const BASE = process.env.BASE || 'http://127.0.0.1:8010';
const OUT = path.join(__dirname, '..', 'docs', 'ui-audit');
const A = '/web/admin';

/* [nombre, ruta, tambienMovil] */
const ADMIN = [
  ['home', '/web', true],
  ['notas-list', `${A}/notas`, true],
  ['nota-detail', `${A}/notas/1`, true],
  ['nota-edit', `${A}/notas/9/editar`, false],
  ['nota-evidencias', `${A}/notas/1/evidencias`, false],
  ['nota-compra-admin', `${A}/notas/compra-administrativa`, false],
  ['nota-venta-admin', `${A}/notas/venta-administrativa`, false],
  ['proveedores-list', `${A}/proveedores`, true],
  ['proveedor-form', `${A}/proveedores/nuevo`, false],
  ['proveedor-record', `${A}/proveedores/1/record`, true],
  ['clientes-list', `${A}/clientes`, false],
  ['cliente-record', `${A}/clientes/1/record`, false],
  ['comisionarios-list', `${A}/comisionarios`, false],
  ['comisionario-record', `${A}/comisionarios/1/record`, false],
  ['comisionario-notas', `${A}/comisionarios/notas`, false],
  ['comisionario-nota-form', `${A}/comisionarios/notas/nueva`, false],
  ['comisionario-nota-detail', `${A}/comisionarios/notas/1`, false],
  ['materiales-list', `${A}/materiales`, false],
  ['material-precios', `${A}/materiales/1/precios`, false],
  ['inventario-list', `${A}/inventario`, true],
  ['inventario-movimientos', `${A}/inventario/movimientos`, false],
  ['inventario-valor', `${A}/inventario/valor`, false],
  ['inventario-ajuste', `${A}/inventario/ajuste`, false],
  ['inventario-aumentar', `${A}/inventario/aumentar`, false],
  ['conversiones', `${A}/conversiones-materiales`, false],
  ['conversion-detail', `${A}/conversiones-materiales/1`, false],
  ['transferencias', `${A}/transferencias`, false],
  ['contabilidad', `${A}/contabilidad`, false],
  ['capital', `${A}/capital`, false],
  ['corte-caja', `${A}/corte-caja`, true],
  ['reporte-asistencias', `${A}/reporte-asistencias`, false],
  ['reporte-saldos', `${A}/reporte-saldos`, false],
  ['cuentas-list', `${A}/cuentas`, false],
  ['cuenta-detail', `${A}/cuentas/1`, false],
  ['cuentas-scrap360', `${A}/cuentas-scrap360`, false],
  ['cuenta-scrap360-detail', `${A}/cuentas-scrap360/1`, false],
  ['users-list', `${A}/users`, false],
  ['user-form', `${A}/users/nuevo`, false],
  ['sucursales-list', `${A}/sucursales`, false],
  ['perfil', '/web/perfil', false],
];

/* El login se captura antes de iniciar sesión: con sesión activa la ruta
   redirige a Inicio y la captura sale mal. */
const LOGGED_OUT = [
  ['login', '/web/login', true],
];

const WORKER = [
  ['worker-home', '/web', true],
  ['worker-notes-list', '/web/worker/notes', true],
  ['worker-nota-nueva', '/web/worker/notes/nueva', true],
];

async function login(page, user, pass) {
  await page.goto(`${BASE}/web/logout`).catch(() => {});
  await page.goto(`${BASE}/web/login`);
  await page.fill('#username', user);
  await page.fill('#password', pass);
  await Promise.all([page.waitForNavigation(), page.click('button[type=submit]')]);
}

async function shoot(page, name, url, vp) {
  const resp = await page.goto(BASE + url, { waitUntil: 'networkidle', timeout: 25000 });
  if (!resp || resp.status() !== 200) {
    console.log(`SKIP ${name} (${vp}): HTTP ${resp ? resp.status() : 'sin respuesta'}`);
    return;
  }
  await page.waitForTimeout(250);
  await page.screenshot({ path: path.join(OUT, `${name}--${vp}.png`), fullPage: true });
  console.log(`ok   ${name} (${vp})`);
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH });

  for (const [role, creds, routes] of [
    ['admin', ['AVRC', 'scrap360$1123'], ADMIN],
    ['worker', ['qa_worker', 'QaWorker#2026'], WORKER],
  ]) {
    for (const [vp, width, height] of [['1440', 1440, 900], ['390', 390, 844]]) {
      const wanted = routes.filter(([, , mobile]) => vp === '1440' || mobile);
      if (!wanted.length) continue;
      const ctx = await browser.newContext({ viewport: { width, height } });
      const page = await ctx.newPage();
      if (role === 'admin') {
        for (const [name, url, mobile] of LOGGED_OUT) {
          if (vp === '1440' || mobile) {
            try { await shoot(page, name, url, vp); } catch (err) {
              console.log(`FAIL ${name} (${vp}): ${err.message.split('\n')[0]}`);
            }
          }
        }
      }
      await login(page, creds[0], creds[1]);
      for (const [name, url] of wanted) {
        try {
          await shoot(page, name, url, vp);
        } catch (err) {
          console.log(`FAIL ${name} (${vp}): ${err.message.split('\n')[0]}`);
        }
      }
      await ctx.close();
    }
  }

  await browser.close();
})();
