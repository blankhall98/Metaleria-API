/**
 * Design-system regression check.
 *
 * Renders every screen at 1440px and 390px and asserts the rules in
 * docs/DESIGN_SYSTEM.md that can be checked mechanically. Exits non-zero on
 * any violation so it can gate a change.
 *
 *   CHROME_PATH=... node scripts/check_ui.js
 *   CHROME_PATH=... node scripts/check_ui.js notas      # only matching screens
 *
 * Needs playwright-core on NODE_PATH and the dev server on BASE (8010).
 */
const { chromium } = require('playwright-core');

const BASE = process.env.BASE || 'http://127.0.0.1:8010';
const ONLY = process.argv[2] || null;
const A = '/web/admin';

const ADMIN = [
  ['home', '/web'],
  ['notas-list', `${A}/notas`],
  ['nota-detail', `${A}/notas/1`],
  ['nota-edit', `${A}/notas/9/editar`],
  ['nota-evidencias', `${A}/notas/1/evidencias`],
  ['nota-compra-admin', `${A}/notas/compra-administrativa`],
  ['nota-venta-admin', `${A}/notas/venta-administrativa`],
  ['proveedores-list', `${A}/proveedores`],
  ['proveedor-form', `${A}/proveedores/nuevo`],
  ['proveedor-record', `${A}/proveedores/1/record`],
  ['clientes-list', `${A}/clientes`],
  ['cliente-record', `${A}/clientes/1/record`],
  ['comisionarios-list', `${A}/comisionarios`],
  ['comisionario-record', `${A}/comisionarios/1/record`],
  ['comisionario-notas', `${A}/comisionarios/notas`],
  ['comisionario-nota-form', `${A}/comisionarios/notas/nueva`],
  ['comisionario-nota-detail', `${A}/comisionarios/notas/1`],
  ['materiales-list', `${A}/materiales`],
  ['material-precios', `${A}/materiales/1/precios`],
  ['inventario-list', `${A}/inventario`],
  ['inventario-movimientos', `${A}/inventario/movimientos`],
  ['inventario-valor', `${A}/inventario/valor`],
  ['inventario-ajuste', `${A}/inventario/ajuste`],
  ['inventario-aumentar', `${A}/inventario/aumentar`],
  ['conversiones', `${A}/conversiones-materiales`],
  ['conversion-detail', `${A}/conversiones-materiales/1`],
  ['transferencias', `${A}/transferencias`],
  ['contabilidad', `${A}/contabilidad`],
  ['capital', `${A}/capital`],
  ['corte-caja', `${A}/corte-caja`],
  ['reporte-asistencias', `${A}/reporte-asistencias`],
  ['reporte-saldos', `${A}/reporte-saldos`],
  ['cuentas-list', `${A}/cuentas`],
  ['cuenta-detail', `${A}/cuentas/1`],
  ['cuentas-scrap360', `${A}/cuentas-scrap360`],
  ['cuenta-scrap360-detail', `${A}/cuentas-scrap360/1`],
  ['users-list', `${A}/users`],
  ['user-form', `${A}/users/nuevo`],
  ['sucursales-list', `${A}/sucursales`],
];

const WORKER = [
  ['w-home', '/web'],
  ['w-notes-list', '/web/worker/notes'],
  ['w-notes-form', '/web/worker/notes/nueva'],
];

/* Every rule returns an array of human-readable violations. */
const RULES = {
  /* A phone must never scroll sideways. */
  horizontalOverflow: (d) =>
    d.overflow > 2 ? [`scroll horizontal de ${d.overflow}px`] : [],

  /* Exactly one primary action competing for attention. */
  primaryButtons: (d) =>
    d.primaryButtons > 1 ? [`${d.primaryButtons} botones .btn-primary`] : [],

  /* One document title. */
  headings: (d) => {
    const out = [];
    if (d.h1 === 0) out.push('sin <h1>');
    if (d.h1 > 1) out.push(`${d.h1} elementos <h1>`);
    return out;
  },

  /* Icon-only controls need an accessible name. */
  unlabelledControls: (d) =>
    d.unlabelled.length ? [`controles sin nombre accesible: ${d.unlabelled.join(', ')}`] : [],

  /* Inputs need a real label. */
  unlabelledInputs: (d) =>
    d.unlabelledInputs.length
      ? [`campos sin <label>: ${d.unlabelledInputs.slice(0, 5).join(', ')}`]
      : [],

  /* Emoji are never icons. */
  emoji: (d) => (d.emoji.length ? [`emoji en la interfaz: ${d.emoji.join(' ')}`] : []),

  /* The retired instruction hints must stay gone. */
  scrollHints: (d) =>
    d.scrollHints ? [`${d.scrollHints} textos "arrastra con el mouse"`] : [],

  /* A settled zero is not an error. */
  redZero: (d) => (d.redZero ? [`${d.redZero} importes $0.00 en rojo`] : []),

  /* Missing accents in the chrome. */
  accents: (d) => (d.accents.length ? [`sin acento: ${d.accents.join(', ')}`] : []),

  /* Nothing hidden behind the fixed dock. */
  dockOverlap: (d) => (d.dockOverlap ? [`contenido bajo el dock (${d.dockOverlap}px)`] : []),

  /* A visual decision belongs in scrap360.css, on the tokens. A style
     attribute or a <style> block is a decision nobody else can inherit —
     corte de caja carried 130 such lines with ~40 raw hex values.
     Showing and hiding is behaviour, not styling, so a lone `display` set by
     the page's own script does not count. */
  inlineStyles: (d) => {
    const out = [];
    if (d.styleAttrs) out.push(`${d.styleAttrs} atributos style= en línea`);
    if (d.styleBlocks) out.push(`${d.styleBlocks} bloques <style> en la página`);
    return out;
  },

  /* A card never nests in a card: use .s-subpanel for a region inside one. */
  nestedCards: (d) =>
    d.nestedCards ? [`${d.nestedCards} tarjetas anidadas en otra tarjeta`] : [],

  /* Every screen opens the same way. Without the page header the eyebrow,
     title and actions land wherever each template decided. */
  pageHeader: (d) => (d.pageHead ? [] : ['sin encabezado de página (.s-page-head)']),
};

/* Words whose unaccented form is always wrong in this app. */
const ACCENT_WORDS = [
  'Operacion', 'OPERACION', 'Revision', 'REVISION', 'Administracion', 'ADMINISTRACION',
  'Catalogo', 'CATALOGO', 'Descripcion', 'DESCRIPCION', 'Direccion', 'DIRECCION',
  'Telefono', 'TELEFONO', 'Numero', 'NUMERO', 'Vinculo', 'VINCULO', 'Metodo', 'METODO',
  'Comision', 'COMISION', 'Devolucion', 'Conversion', 'Contrasena', 'Busqueda',
  'Seleccion', 'Relacion', 'Valuacion', 'Configuracion', 'Ultimos', 'Ultimas',
];

async function audit(page) {
  return page.evaluate((accentWords) => {
    const text = document.body.innerText;

    /* Content inside a collapsed <details> still reports a box but renders
       nothing, so check for that explicitly rather than trusting the rect. */
    const visible = (el) => {
      if (el.closest('details:not([open])')) return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    };

    /* textContent, not innerText: innerText is empty for unrendered nodes. */
    const name = (el) =>
      (el.getAttribute('aria-label') || el.textContent || el.title || '').trim();

    const unlabelled = [];
    document.querySelectorAll('button, a.btn, [role="button"]').forEach((el) => {
      if (!visible(el)) return;
      if (!name(el)) unlabelled.push(el.className.split(' ')[0] || el.tagName.toLowerCase());
    });

    const unlabelledInputs = [];
    document.querySelectorAll('input, select, textarea').forEach((el) => {
      if (el.type === 'hidden' || !visible(el)) return;
      const hasLabel =
        (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)) ||
        el.closest('label') ||
        el.getAttribute('aria-label') ||
        el.getAttribute('aria-labelledby');
      if (!hasLabel) unlabelledInputs.push(el.name || el.type);
    });

    /* Emoji in rendered text (icons must come from the sprite). */
    const emojiRe = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}]/gu;
    const emoji = [...new Set((text.match(emojiRe) || []))].filter((e) => e !== '❤');

    /* Zero money rendered as an error. */
    let redZero = 0;
    document.querySelectorAll('.text-danger, .bg-danger').forEach((el) => {
      if (/^\s*-?\$?0[.,]00\s*$/.test(el.textContent)) redZero += 1;
    });

    /* Only the chrome is ours to spell. Table bodies and select options carry
       stored records — historical audit comments written years ago are not a
       design regression, and rewriting them would falsify the ledger. */
    const chrome = (() => {
      const clone = document.body.cloneNode(true);
      clone.querySelectorAll('tbody, option, .s-cell-sub').forEach((el) => el.remove());
      return clone.innerText || clone.textContent || '';
    })();

    const accents = accentWords.filter((w) =>
      new RegExp(`(^|[^\\wáéíóúñÁÉÍÓÚÑ])${w}([^\\wáéíóúñÁÉÍÓÚÑ]|$)`).test(chrome)
    );

    const scrollHints = (text.match(/[Aa]rrastra con el mouse|Shift \+ rueda/g) || []).length;

    let primaryButtons = 0;
    document.querySelectorAll('.btn-primary').forEach((el) => {
      if (visible(el)) primaryButtons += 1;
    });

    /* Does the fixed dock cover the end of the document? */
    let dockOverlap = 0;
    const dock = document.querySelector('.mobile-dock');
    if (dock && getComputedStyle(dock).display !== 'none') {
      const main = document.querySelector('main.page-shell');
      if (main) {
        const gap = document.documentElement.scrollHeight -
          (main.getBoundingClientRect().bottom + window.scrollY);
        const dockH = dock.getBoundingClientRect().height;
        if (gap < dockH) dockOverlap = Math.round(dockH - gap);
      }
    }

    /* A card inside a card. .s-subpanel is the sunken region that belongs
       there; a second border inside the first is what we are looking for. */
    let nestedCards = 0;
    document.querySelectorAll('.card, .s-card').forEach((el) => {
      if (!visible(el)) return;
      if (el.parentElement && el.parentElement.closest('.card, .s-card')) nestedCards += 1;
    });

    return {
      styleAttrs: [...document.querySelectorAll('main [style]')].filter((el) => {
        const decls = el.getAttribute('style').split(';').map((s) => s.trim()).filter(Boolean);
        return !decls.every((d) => /^display\s*:/i.test(d));
      }).length,
      styleBlocks: document.querySelectorAll('body style').length,
      nestedCards,
      pageHead: !!document.querySelector('.s-page-head, .page-header'),
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      h1: document.querySelectorAll('h1').length,
      primaryButtons,
      unlabelled: [...new Set(unlabelled)],
      unlabelledInputs: [...new Set(unlabelledInputs)],
      emoji,
      redZero,
      accents,
      scrollHints,
      dockOverlap,
      height: document.documentElement.scrollHeight,
    };
  }, ACCENT_WORDS);
}

async function login(page, username, password) {
  await page.goto(`${BASE}/web/login`, { waitUntil: 'domcontentloaded' });
  await page.fill('input[name="username"]', username);
  await page.fill('input[name="password"]', password);
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'domcontentloaded' }).catch(() => {}),
    page.click('button[type="submit"]'),
  ]);
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH });
  const failures = [];
  let checked = 0;

  for (const [vp, width, height] of [['desktop', 1440, 900], ['mobile', 390, 844]]) {
    for (const [role, creds, routes] of [
      ['admin', ['AVRC', 'scrap360$1123'], ADMIN],
      ['worker', ['qa_worker', 'QaWorker#2026'], WORKER],
    ]) {
      const ctx = await browser.newContext({ viewport: { width, height } });
      const page = await ctx.newPage();
      const consoleErrors = [];
      page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
      await login(page, creds[0], creds[1]);

      for (const [screen, url] of routes) {
        if (ONLY && !screen.includes(ONLY)) continue;
        const before = consoleErrors.length;
        try {
          const resp = await page.goto(BASE + url, { waitUntil: 'networkidle', timeout: 25000 });
          if (resp && resp.status() !== 200) {
            failures.push(`${vp}/${screen}: HTTP ${resp.status()}`);
            continue;
          }
        } catch (e) {
          failures.push(`${vp}/${screen}: ${e.message.split('\n')[0]}`);
          continue;
        }
        checked += 1;
        const data = await audit(page);

        for (const [rule, check] of Object.entries(RULES)) {
          // The dock only exists on mobile.
          if (rule === 'dockOverlap' && vp !== 'mobile') continue;
          if (rule === 'horizontalOverflow' && vp !== 'mobile') continue;
          for (const problem of check(data)) {
            failures.push(`${vp}/${screen}: ${problem}`);
          }
        }
        if (consoleErrors.length > before) {
          failures.push(`${vp}/${screen}: error de consola — ${consoleErrors[before]}`);
        }
      }
      await ctx.close();
    }
  }

  await browser.close();

  console.log(`\nRevisadas ${checked} vistas.`);
  if (!failures.length) {
    console.log('Sin incumplimientos del sistema de diseño.');
    process.exit(0);
  }
  console.log(`\n${failures.length} incumplimiento(s):\n`);
  failures.forEach((f) => console.log('  ' + f));
  process.exit(1);
})();
