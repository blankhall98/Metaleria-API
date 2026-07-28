/* ==========================================================================
   SCRAP360 — behaviour layer for the design system
   --------------------------------------------------------------------------
   Progressive enhancement only: every screen must still render and submit
   with this file absent. See docs/DESIGN_SYSTEM.md.
   ========================================================================== */
(function () {
    'use strict';

    var MOBILE_QUERY = '(max-width: 767.98px)';

    /* ----------------------------------------------------------------------
       Navigation drawer
       ---------------------------------------------------------------------- */

    function toggleDrawer(open) {
        var drawer = document.getElementById('navDrawer');
        var overlay = document.getElementById('navOverlay');
        var trigger = document.querySelector('.menu-trigger');
        if (!drawer || !overlay) return;

        document.body.classList.toggle('drawer-open', !!open);
        drawer.classList.toggle('open', !!open);
        overlay.classList.toggle('open', !!open);
        drawer.setAttribute('aria-hidden', open ? 'false' : 'true');
        if (trigger) trigger.setAttribute('aria-expanded', open ? 'true' : 'false');

        if (open) {
            var first = drawer.querySelector('a, button');
            if (first) first.focus({ preventScroll: true });
        } else if (trigger) {
            trigger.focus({ preventScroll: true });
        }
    }
    window.toggleDrawer = toggleDrawer;

    /* ----------------------------------------------------------------------
       Tables
       --------------------------------------------------------------------------
       Each data table is annotated once so CSS can restack it into record
       cards on phones: every cell carries its column name, action cells and
       empty cells are tagged, and wide tables get a sticky header and pinned
       first column on desktop.
       ---------------------------------------------------------------------- */

    var EMPTY_TOKENS = ['', '-', '–', '—', 'n/a', 'N/A', '--'];

    function cellIsEmpty(cell) {
        if (cell.querySelector('img, svg, input, select, textarea, button, a')) return false;
        return EMPTY_TOKENS.indexOf(cell.textContent.trim()) !== -1;
    }

    function cellIsActions(cell) {
        var interactive = cell.querySelectorAll('a.btn, button, .s-actions, .btn');
        if (!interactive.length) return false;
        // A cell is an action cell when it is essentially nothing but controls.
        var text = cell.textContent.replace(/\s+/g, ' ').trim();
        var controlText = Array.prototype.map
            .call(interactive, function (el) { return el.textContent.replace(/\s+/g, ' ').trim(); })
            .join(' ');
        return text.length <= controlText.length + 4;
    }

    function annotateTable(table) {
        if (table.dataset.s360Table === 'done') return;
        table.dataset.s360Table = 'done';

        var headRow = table.tHead ? table.tHead.rows[table.tHead.rows.length - 1] : null;
        if (!headRow) return;

        var labels = Array.prototype.map.call(headRow.cells, function (th) {
            // Prefer an explicit short label when the header holds extra markup.
            var explicit = th.getAttribute('data-label');
            if (explicit !== null) return explicit;
            return th.textContent.replace(/\s+/g, ' ').trim();
        });

        var columnCount = Array.prototype.reduce.call(headRow.cells, function (n, th) {
            return n + (th.colSpan || 1);
        }, 0);

        Array.prototype.forEach.call(table.tBodies, function (tbody) {
            Array.prototype.forEach.call(tbody.rows, function (row) {
                var index = 0;
                Array.prototype.forEach.call(row.cells, function (cell) {
                    var span = cell.colSpan || 1;
                    // A cell spanning the whole row is a message, not a field.
                    if (span >= columnCount) {
                        cell.setAttribute('data-label', '');
                        cell.classList.add('s-cell-full');
                        index += span;
                        return;
                    }
                    if (!cell.hasAttribute('data-label')) {
                        cell.setAttribute('data-label', labels[index] || '');
                    }
                    if (cellIsActions(cell)) {
                        cell.classList.add('s-cell-actions');
                        cell.setAttribute('data-label', '');
                    } else if (cellIsEmpty(cell)) {
                        cell.classList.add('s-cell-empty');
                    } else if (/^(id|#|no\.?|num\.?)$/i.test(labels[index] || '') &&
                               /^#?\d+$/.test(cell.textContent.trim())) {
                        // A bare row id tells a phone user nothing.
                        cell.classList.add('s-cell-id');
                    }
                    index += span;
                });
            });
        });

        markTitleCells(table);

        // Tables with real column structure restack into cards on phones.
        // Two-column key/value tables already read fine as they are.
        if (columnCount >= 3) {
            var shell = table.closest('.table-responsive');
            if (shell) shell.classList.add('s-table-cards');
            table.classList.add('s-table-sticky');
            if (columnCount >= 6) {
                table.classList.add('s-table-pin');
                addProgressiveCards(table);
            }
        }
    }

    /* A wide table turns into a tall card on a phone. Show the fields that
       identify and quantify the record, and fold the rest behind a toggle so a
       twenty-row ledger stays scannable. Desktop is untouched — the toggle and
       the folding only exist under the mobile breakpoint. */
    var VISIBLE_FIELDS = 4;

    function addProgressiveCards(table) {
        var headRow = table.tHead ? table.tHead.rows[table.tHead.rows.length - 1] : null;
        if (!headRow) return;

        /* A template marks the columns a phone user actually came for with
           data-mobile-primary. Those always show; the rest fold. Without any
           marks we keep the first few columns, which is the sensible default
           for a narrow table but the wrong guess for a wide ledger. */
        var primary = [];
        var hasPrimary = false;
        Array.prototype.forEach.call(headRow.cells, function (th, i) {
            primary[i] = th.hasAttribute('data-mobile-primary');
            if (primary[i]) hasPrimary = true;
        });

        Array.prototype.forEach.call(table.tBodies, function (tbody) {
            Array.prototype.forEach.call(tbody.rows, function (row) {
                var title = null;
                var shown = 0;
                var hidden = 0;

                Array.prototype.forEach.call(row.cells, function (cell, index) {
                    if (cell.classList.contains('s-cell-title')) { title = cell; return; }
                    if (cell.classList.contains('s-cell-actions') ||
                        cell.classList.contains('s-cell-empty') ||
                        cell.classList.contains('s-cell-id') ||
                        cell.classList.contains('s-cell-full')) return;

                    if (hasPrimary) {
                        if (primary[index]) return;
                    } else if (shown < VISIBLE_FIELDS) {
                        shown++;
                        return;
                    }
                    cell.classList.add('s-cell-secondary');
                    hidden++;
                });

                if (!hidden || !title || title.querySelector('.s-card-more')) return;

                var toggle = document.createElement('button');
                toggle.type = 'button';
                toggle.className = 's-card-more';
                toggle.setAttribute('aria-expanded', 'false');
                toggle.textContent = 'Ver ' + hidden + ' campo' + (hidden === 1 ? '' : 's') + ' más';
                toggle.addEventListener('click', function () {
                    var open = row.classList.toggle('is-expanded');
                    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
                    toggle.textContent = open
                        ? 'Ver menos'
                        : 'Ver ' + hidden + ' campo' + (hidden === 1 ? '' : 's') + ' más';
                });
                title.appendChild(toggle);
            });
        });
    }

    /* On a phone each row becomes a card, and the card needs a headline. The
       first column is usually an id ("3"), which makes a poor title, so the
       title is the first column that actually names the record. A template can
       override the choice with data-title-col on the <th>. */
    function markTitleCells(table) {
        var headRow = table.tHead ? table.tHead.rows[table.tHead.rows.length - 1] : null;
        if (!headRow) return;

        var explicit = -1;
        Array.prototype.forEach.call(headRow.cells, function (th, i) {
            if (th.hasAttribute('data-title-col')) explicit = i;
        });

        Array.prototype.forEach.call(table.tBodies, function (tbody) {
            Array.prototype.forEach.call(tbody.rows, function (row) {
                var index = explicit;
                if (index < 0) {
                    for (var i = 0; i < row.cells.length; i++) {
                        var cell = row.cells[i];
                        if (cell.classList.contains('s-cell-actions')) continue;
                        var text = cell.textContent.replace(/\s+/g, ' ').trim();
                        // Skip ids, counters and blanks — they don't name anything.
                        if (!text || text.length < 4 || /^#?\d+$/.test(text)) continue;
                        index = i;
                        break;
                    }
                }
                if (index >= 0 && row.cells[index]) {
                    row.cells[index].classList.add('s-cell-title');
                }
            });
        });
    }

    function annotateTables(root) {
        (root || document).querySelectorAll('table').forEach(annotateTable);
    }

    /* Wrap bare tables so they always get the scroll shell and card behaviour. */
    function ensureTableShells(root) {
        (root || document).querySelectorAll('table.table').forEach(function (table) {
            if (table.closest('.table-responsive')) return;
            var wrap = document.createElement('div');
            wrap.className = 'table-responsive';
            table.parentNode.insertBefore(wrap, table);
            wrap.appendChild(table);
        });
    }

    /* ----------------------------------------------------------------------
       Filters: collapse the filter block behind a summary on phones
       ---------------------------------------------------------------------- */

    function setupFilterCollapse() {
        var mq = window.matchMedia(MOBILE_QUERY);
        document.querySelectorAll('.s-filters-collapse').forEach(function (details) {
            var apply = function () { details.open = !mq.matches; };
            apply();
            if (mq.addEventListener) mq.addEventListener('change', apply);
            else if (mq.addListener) mq.addListener(apply);
        });
    }

    /* ----------------------------------------------------------------------
       Forms
       ---------------------------------------------------------------------- */

    function setupForms() {
        /* Stop double submission and show that work is happening. */
        document.addEventListener('submit', function (event) {
            var form = event.target;
            if (!form.dataset || !form.dataset.submitOnce) return;
            form.querySelectorAll('button[type="submit"], input[type="submit"]').forEach(function (btn) {
                btn.disabled = true;
                btn.setAttribute('aria-busy', 'true');
                if (btn.dataset.loadingText) btn.textContent = btn.dataset.loadingText;
            });
        }, true);

        /* Confirm destructive actions that declare themselves as such. */
        document.addEventListener('click', function (event) {
            var el = event.target.closest('[data-confirm]');
            if (!el) return;
            if (!window.confirm(el.getAttribute('data-confirm'))) {
                event.preventDefault();
                event.stopPropagation();
            }
        }, true);

        /* Reveal a password without retyping it. */
        document.addEventListener('click', function (event) {
            var toggle = event.target.closest('[data-password-toggle]');
            if (!toggle) return;
            var field = document.getElementById(toggle.getAttribute('data-password-toggle'));
            if (!field) return;
            var shown = field.type === 'text';
            field.type = shown ? 'password' : 'text';
            toggle.setAttribute('aria-pressed', shown ? 'false' : 'true');
            toggle.setAttribute('aria-label', shown ? 'Mostrar contraseña' : 'Ocultar contraseña');
        });

        /* A stray scroll must never silently change a weight or an amount. */
        document.addEventListener('wheel', function () {
            var active = document.activeElement;
            if (active && active.type === 'number') active.blur();
        }, { passive: true });
    }

    /* ----------------------------------------------------------------------
       Boot
       ---------------------------------------------------------------------- */

    function init() {
        ensureTableShells();
        annotateTables();
        setupFilterCollapse();
        setupForms();

        document.querySelectorAll('.nav-drawer a').forEach(function (link) {
            link.addEventListener('click', function () { toggleDrawer(false); });
        });

        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') toggleDrawer(false);
        });

        /* Content injected after load (evidence pickers, added rows) still
           gets table annotation. */
        if (window.MutationObserver) {
            var observer = new MutationObserver(function (mutations) {
                var sawTable = mutations.some(function (m) {
                    return Array.prototype.some.call(m.addedNodes, function (n) {
                        return n.nodeType === 1 && (n.matches('table') || n.querySelector('table'));
                    });
                });
                if (sawTable) { ensureTableShells(); annotateTables(); }
            });
            observer.observe(document.body, { childList: true, subtree: true });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
