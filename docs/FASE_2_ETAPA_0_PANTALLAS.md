# Etapa 0 — Plan pantalla por pantalla

Plan de remate del rediseño, pantalla por pantalla, dentro del design system
homologado (`DESIGN_SYSTEM.md` — CRM clásico, la tabla como producto). Basado en
la auditoría del 06-ago-2026 y contrastado con las guías UX de referencia
(accesibilidad WCAG, targets táctiles, formularios, jerarquía de una sola acción
primaria). **Ninguna pantalla cambia de dirección estética**: aquí solo se cierra
la homologación.

Principios de calidad que gobiernan cada corrección (de las guías UX aplicadas
al design system existente):

- **Una acción primaria por pantalla** (`primary-action`): el acento cae en lo que
  mueve dinero o completa la tarea, no en la navegación.
- **Solo lectura ≠ control** (`read-only-distinction`): un valor calculado jamás se
  renderiza como input; en móvil un input muerto se lee como control roto.
- **Iconos vectoriales, nunca glifos/emoji** (`no-emoji-icons`, `icon-style-consistent`):
  todo del sprite `_icons.html`, stroke uniforme, `currentColor`, `aria-hidden`.
- **Estado vacío con recuperación** (`empty-states`): qué pasó + cómo salir de ahí;
  nunca un `<thead>` pelado.
- **El color nunca es el único canal** (`color-not-only`): badge = tono + texto.
- **Táctil ≥44px, inputs 16px** en móvil (ya son tokens del sistema; verificar en
  las pantallas que se toquen).

## Estado por pantalla

✅ = conforme, no se toca. 🔧 = acciones en esta etapa.

### Chrome de la aplicación

| Pantalla | Estado | Acciones |
|---|---|---|
| `base.html` (navbar, drawer, dock, footer) | 🔧 | Portar las 8 clases de footer/dock que aún viven en style.css a `scrap360.css` §3 y retirar la hoja legada (tarea 0.9). Decidir valor del `<meta theme-color>` (hoy hex crudo; pasa a valor del token canvas). |
| `home.html` | 🔧 | Dos `.btn-primary` simultáneos ("Corte de caja" y "Revisar ahora") → el CTA del alerta degrada a `btn-outline-primary` (0.3). Dos filas `.s-stats` → una sola fila con las cifras que no aparecen en otro lado (0.10). Header hecho a mano ×3 → `page_header()` donde aplique. |
| `login.html` | ✅ | Sin encabezado por diseño (tarjeta `.s-narrow`); se agrega exención documentada en `check_ui.js` en vez de forzar un header. |

### Notas (el corazón operativo)

| Pantalla | Estado | Acciones |
|---|---|---|
| `admin/notes_list.html` | 🔧 | Retirar `notes-review-panel` (clase muerta, 0.11). Resto conforme — ya fusiona columnas y marca `data-mobile-primary`. |
| `admin/note_detail.html` | 🔧 | "Aprobar nota" pasa a ser el único primario; "Evidencias" degrada (0.3). `card mb-3 s-subpanel` contradictorio ×2 → decidir card o subpanel (0.10). 25 `s-def__label` a mano → `def()`/`defs()` en las nuevas ediciones (no reescritura masiva: los hooks `note-*` de precios son de comportamiento y NO se tocan). `empty_row()` en los tbody que puedan quedar vacíos (0.7). Copy: "log contable"→"Contabilidad", "Stock"→"Existencias", `Accion/Linea/fisica/seguira` acentuados (0.8). `.pill` ×2 → `badge()`. |
| `admin/note_edit.html` | 🔧 | Igual que detail: copy (0.8) y `empty_row()` en materiales (0.7). Los hooks `note-edit-*` que usa el JS de la pantalla se documentan como hooks (data-attributes en un commit dedicado futuro, NO en esta etapa — regla del design system §"Classes a script holds on to"). |
| `note_evidencias.html` | ✅→🔧 leve | `evidence-*` son hooks JS sin CSS: se documentan junto a los `note-*` en el design system (no se renombran en esta etapa). `def()` para las 9 etiquetas a mano si el cambio es de bajo riesgo. |
| `worker/notes_list.html` | 🔧 | `empty_row()` (0.7). |
| `worker/notes_form.html` | 🔧 | "Neto" (×2) deja de ser input readonly → valor de solo lectura (0.5). Textarea disabled inicial → habilitado por estado o def. Los dos `.btn-primary` son de ramas mutuamente excluyentes (submit / sheet) — se deja constancia y no se toca: en pantalla solo hay uno visible. |

### Socios y comisionistas

| Pantalla | Estado | Acciones |
|---|---|---|
| `admin/proveedores_list.html`, `clientes_list.html` | 🔧 leve | Chip `×` inyectado por JS → icono del sprite con `aria-label` (0.4). Resto conforme. |
| `admin/partner_record.html` | 🔧 | `s-anchor-nav` a mano → macro `anchor_nav()` (0.11). 2 filas stats → 1 (0.10). `empty_row()` en los 4 tbody (0.7). |
| `admin/proveedor_form.html`, `cliente_form.html` | ✅ (salvo chip ×, en 0.4) | |
| `admin/comisionarios_list.html`, `comisionario_form.html`, `comisionario_notas_list.html` | ✅ | |
| `admin/comisionario_record.html` | 🔧 | `anchor_nav()` (0.11), `empty_row()` ×3 (0.7). |
| `admin/comisionario_nota_detail.html` | 🔧 | `empty_row()` (0.7). |
| `admin/comisionario_nota_form.html` | 🔧 | Tabla de materiales sin ningún estado vacío → `empty_row()` inicial (0.7). |

### Inventario y materiales

| Pantalla | Estado | Acciones |
|---|---|---|
| `admin/inventario_list.html`, `inventario_ajuste.html`, `inventario_aumentar.html`, `conversiones_materiales.html` | ✅ | |
| `admin/inventario_valor.html`, `conversion_detail.html` | 🔧 | `empty_row()` (0.7). |
| `admin/inventario_movimientos.html` | 🔧 | Total en kg a mano → macro `kg()` (0.8); `empty_row()` ×4 (0.7). |
| `admin/inventario_ajuste_detail.html` | 🔧 | Ancla legada `#note-manual-adjustments` (0.11). |
| `admin/materiales_list.html`, `material_form.html`, `precio_form.html` | ✅ | |
| `admin/precios_material.html` | 🔧🔧 | **Migración completa** (0.2): `page_header` (eyebrow Catálogo), título legible (hoy blanco sobre claro), `badge()` para vigencia, `money()` a 5 decimales para precio unitario, enums humanizados (`compra`→Compra, `mayoreo`→Mayoreo), `empty_row()`, `data-title-col`/`data-mobile-primary`, acciones en orden canónico. |

### Finanzas

| Pantalla | Estado | Acciones |
|---|---|---|
| `admin/corte_caja.html` | 🔧🔧 | La mayor obra de la etapa (0.4, 0.5, 0.6, 0.10, 0.11): familia `corte-*` (57 usos) → componentes canónicos (`subpanel`+`details`, `badge()`, barra de acciones estándar); glifos ✎/⌄ → sprite; "Saldo contado" y denominaciones dejan de ser inputs muertos; 3 filas stats → 1; `data-mobile-primary` en sus 6 tablas sin marcar. La lógica de 3 estados (sin abrir/abierto/cerrado) y `data-submit-once` no se tocan. Verificación sección por sección al terminar — es la pantalla de conciliación de dinero. |
| `admin/contabilidad_list.html` | 🔧 | 19 `s-def__label` a mano → `defs()`/`def()`; enum crudo `:253` → humanizado (0.8). |
| `admin/capital_real.html` | 🔧 | 2 filas stats → 1 (0.10); `empty_row()` (0.7). |
| `admin/transferencias.html` | 🔧 | Tabla dinámica sin estado vacío → fila inicial `empty_row()` que el JS reemplaza (0.7). |
| `admin/cuentas_list.html`, `cuenta_form.html`, `cuentas_scrap360_list.html`, `cuenta_scrap360_form.html` | ✅ | |
| `admin/cuenta_detail.html` | 🔧 | 4 filas `.s-stats` → 1 (regla: nunca repetir cifra visible; las secciones descienden a `defs()` o subpanel) (0.10); enum crudo `:478` (0.8); `empty_row()` ×8 donde aplique (0.7). |
| `admin/cuenta_scrap360_detail.html` | 🔧 | `empty_row()` (0.7). |
| `admin/reporte_saldos.html` | 🔧 | `empty_row()` (0.7). (El orden alfabético es punto 11 de la etapa 1, no de esta.) |
| `admin/reporte_asistencias.html` | 🔧 | Glifos → / ↳ → sprite o texto (0.4); `empty_row()` (0.7). |

### Administración

| Pantalla | Estado | Acciones |
|---|---|---|
| `admin/users_list.html`, `user_form.html`, `user_edit.html`, `sucursales_list.html` | ✅ | |
| `admin/sucursal_form.html` | 🔧 | `data-mobile-primary` en su tabla de admins (0.11). |

## Infraestructura de la etapa

| Pieza | Acción |
|---|---|
| `scripts/check_ui.js` | Reactivar: `package.json` + `playwright-core` instalado y run completo en verde (0.1). Exención documentada para `login.html` sin page header. |
| `scripts/fix_accents.py` | Diccionario ampliado: `accion, linea, fisica/o, seguira` + frase "log contable" case-insensitive + `Stock→Existencias` (0.8). |
| `app/static/css/style.css` | Retiro definitivo (0.9): portar 10 clases vivas, borrar hoja, quitar `<link>`, podar §15 remap de scrap360.css, renumerar secciones 19/19/20/20. |
| `docs/UI_UX.md` | Reescritura post-rediseño (0.12). |
| `docs/DESIGN_SYSTEM.md` | Al cierre: documentar los hooks `evidence-*`/`note-edit-*` como clases de comportamiento reconocidas (hoy solo lista los `note-*` de precios). |

## Orden de ejecución

1. **0.1** puerta de calidad (para verificar todo lo demás con ella)
2. **0.2** precios_material (aislada, riesgo nulo)
3. **0.3** botones primarios (2 archivos, quirúrgico)
4. **0.8** copy/acentos (barato, desbloquea el `--check` estricto)
5. **0.7** estados vacíos (mecánico, muchos archivos)
6. **0.4** glifos → iconos (toca corte, preparar sprite antes de 0.6)
7. **0.5** solo-lectura (toca corte y worker form)
8. **0.6** desmantelar `corte-*` (la obra grande, con 0.4/0.5 ya asentados)
9. **0.10** consolidar stats (decisiones de página, después de ver el corte terminado)
10. **0.11** menores
11. **0.9** retirar style.css (al final: para entonces nada legado queda referenciado)
12. **0.12** UI_UX.md + verificación completa (`check_pages.sh` todas las rutas, `check_ui.js` 390/1440)
