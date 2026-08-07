# Fase 2 — Plan de trabajo y registro

**Documento vivo.** Estructura la fase 2 de Scrap360 (Propuesta de Ajustes v1.1, 27-jul-2026)
y lleva la bitácora de avance. Se actualiza al cerrar cada bloque de trabajo.

- Alcance contratado: Google Doc *"Scrap360 — Propuesta de Ajustes v1.1"* — 13 puntos + 2 extras, 2 semanas, $35,000 MXN (50/50).
- El reloj de entrega corre **desde la recepción de los 3 archivos de Excel** del cliente (fórmulas de los puntos 2, 3 y 4).
- Los avances se publican en producción durante el proceso (Heroku `scrap360`).

---

## 1. Objetivo real del proyecto

Scrap360 dirige la operación diaria de una chatarrera multi-sucursal: pesar y aprobar
notas de compra/venta, mantener inventario en kg, cuadrar caja cada día y saber
en todo momento **cuánto dinero se le debe a quién**. El usuario final trabaja en dos
contextos opuestos: el administrador concilia dinero en un monitor grande (la tabla es
el producto) y el trabajador captura pesajes con una mano, en el patio, desde un
teléfono. Cada decisión de UI y de datos se juzga contra eso.

Invariantes que gobiernan todo cambio (detalle en `BUSINESS_RULES.md`):
dinero e inventario se corrigen **solo con registros compensatorios**; los efectos
contables ocurren únicamente al aprobar una nota; precios append-only;
`metodo_pago == "efectivo"` es lo que enruta dinero por el corte de caja.

## 2. Estructura de la fase

| Etapa | Contenido | Condición de entrada |
|---|---|---|
| **Etapa 0 — Pulido del front-end** | Cerrar la homologación del rediseño (backlog §3) | Ninguna; empieza ya |
| **Etapa 1 — Ajustes y correcciones** | Puntos 1, 5, 6, 7, 8, 9, 10, 11, 12, 13 | Etapa 0 cerrada (o en paralelo si no toca las mismas pantallas) |
| **Etapa 2 — Módulos nuevos** | Puntos 2, 3, 4 + remate de rediseño/móvil | **Los 3 Excel del cliente** |

## 3. Etapa 0 — Backlog de homologación del front-end

Contexto: el rediseño ("classic CRM", `DESIGN_SYSTEM.md`) ya migró el grueso —
20 de 49 pantallas están 100 % conformes, 83/83 tablas tienen `.table-responsive`
+ `data-title-col`, y el vocabulario bespoke bajó de 454 usos a 22. Lo que queda,
priorizado por impacto (auditoría del 06-ago-2026):

| # | Tarea | Detalle | Estado |
|---|---|---|---|
| 0.1 | **Reactivar la puerta de calidad `check_ui.js`** | Falla en `require('playwright-core')`: no hay `package.json` ni `node_modules`. Sin ella, overflow, dock, tarjetas anidadas, ceros rojos y emoji quedan sin vigilar. Crear `package.json` con `playwright-core` y documentar `CHROME_PATH`/`BASE`. | **Hecho** 2026-08-06 |
| 0.2 | **Migrar `precios_material.html`** | Única pantalla pre-rediseño: `.page-header` legado con título blanco invisible, enums crudos (`compra`, `mayoreo`), `.pill`, badges Bootstrap, sin estado vacío. | **Hecho** 2026-08-06 |
| 0.3 | **Un solo `.btn-primary` en `home.html` y `note_detail.html`** | Home: "Corte de caja" + "Revisar ahora" simultáneos. Nota: "Evidencias" compite con "Aprobar nota" — el acento debe caer en aprobar, que es lo que mueve dinero. | **Hecho** 2026-08-06 |
| 0.4 | **Sustituir glifos por `icon()` en `corte_caja.html`** | `✎` ×4 (dispara la regla de emoji), `⌄` ×5 como chevron, `→`, `↳`, `×` en otros templates. | **Hecho** 2026-08-06 |
| 0.5 | **Valores de solo lectura como `def()`, no inputs** | `corte_caja.html:879` (Saldo contado), `worker/notes_form.html:146,296` (Neto), textarea `disabled` en `:222`. En teléfono se leen como controles rotos. | **Hecho** 2026-08-06 |
| 0.6 | **Desmantelar la familia `corte-*` (57 usos)** | `corte-sdiv*` duplica `.s-subpanel`+`<details>`, `corte-pago-pill` duplica `badge()`, `corte-floating-bar` duplica la barra de acciones. ~200 líneas de CSS dedicadas en la pantalla de conciliación de dinero. | **Hecho** 2026-08-06 |
| 0.7 | **Estados vacíos faltantes** | `transferencias`, `comisionario_nota_form`, `note_edit`, `precios_material` renderizan `<thead>` pelado; 16 templates con `<tbody>` sin `empty_row()`. | **Hecho** 2026-08-06 |
| 0.8 | **Ampliar diccionario de `fix_accents`** | Pasa en verde pero se le escapan: `Accion`, `Linea`, `fisica`, `seguira`, "log contable" (minúsculas), `Stock`→`Existencias`, enums vía `.value\|capitalize` (`contabilidad_list:253`, `cuenta_detail:478`). | **Hecho** 2026-08-06 |
| 0.9 | **Retirar `style.css`** | Bloqueo real: solo 10 clases vivas en 2 archivos (footer ×5 y dock ×3 en `base.html`; `note-subpesaje-row`, `is-reverted` en `note_detail`). Portarlas a `scrap360.css`, borrar la hoja, y podar la sección 15 "LEGACY REMAP" (~270 líneas que ya no protegen nada). ~46 clases ya están muertas. | **Hecho** 2026-08-06 |
| 0.10 | **Una sola fila `.s-stats` por pantalla** | `cuenta_detail` tiene 4; `corte_caja` 3; `home`, `capital_real`, `partner_record` 2. Regla: cifras que no aparecen en ningún otro lado. | **Hecho** 2026-08-06 |
| 0.11 | Menores: `data-mobile-primary` en 7 tablas angostas; `anchor_nav()` macro en `partner_record`/`comisionario_record`; hex en `<meta theme-color>` | Bajo impacto, cierre rápido. | **Hecho** 2026-08-06 |
| 0.12 | **Actualizar `docs/UI_UX.md`** | Describe el mundo pre-rediseño (Plus Jakarta Sans, style.css como sistema, "cero macros", shell a 1200 px) y contradice a `DESIGN_SYSTEM.md`. Conservar solo el mapa de rutas/roles y los patrones UX; el resto remite al design system. | **Hecho** 2026-08-06 |

Verificación de cada tarea: `bash scripts/check_pages.sh` sobre las rutas tocadas,
`python -m scripts.fix_accents --check app/templates`, `python -m scripts.check_templates`,
y revisión visual a 390 px y 1440 px. Cuando 0.1 esté listo, `node scripts/check_ui.js` completo.

## 4. Etapa 1 — Los diez ajustes (mapeo técnico)

Resultado de la auditoría de backend (06-ago-2026). Esfuerzo: ▪ trivial · ▪▪ acotado · ▪▪▪ requiere diseño.

| Punto | Esfuerzo | Dónde vive | Notas de implementación |
|---|---|---|---|
| **1. Quitar "stock inicial"** | ▪ | `Inventario.stock_inicial` (`app/models/inventory.py:18`) | **La columna no se muestra en ninguna pantalla** (grep exhaustivo: cero lecturas). O el cliente ve otra etiqueta ("Existencias actuales/anteriores/resultantes" en las pantallas de ajuste) o pide borrar el dato. ⚠️ **Confirmar con el cliente qué está viendo** antes de tocar. Si es borrar la columna: batch ALTER + limpiar 3 escrituras muertas. |
| **5. Archivar sucursales** | ▪▪ | `SucursalStatus.inactiva` existe pero **nunca se escribe**; 56 `db.query(Sucursal)` sin filtro de estado | Sin cambio de esquema. Rutas `archivar`/`reactivar` (super_admin), helper `_active_sucursales()` aplicado solo a los **selectores** (formularios de nota, transferencias, corte, usuarios) — listas y reportes siguen mostrando archivadas para conservar historial. Guardas previas: corte ABIERTO, usuarios asignados, stock ≠ 0, notas en borrador/revisión. |
| **6. Pago FIFO a comisionista** | ▪▪ | `comision_service.add_comisionario_pago` (hoy es estrictamente por nota) | Nueva `pay_comisionario_fifo()`: notas aprobadas con saldo, orden `(created_at, id)` asc, un solo `db.commit()` al final. Ruta `POST /comisionarios/{id}/pago` + formulario en `comisionario_record.html`. ⚠️ Los movimientos Scrap360 de comisiones son egreso-sin-reversa — cruza con el punto 12. |
| **7. Bug notas neteadas "pendientes"** | ▪▪▪ | Motor de neteo: `_build_effective_note_balance_map` (`admin.py:620`) | El neteo se aplica en la lista de notas pero se lo saltan: (a) el record de partner **vinculado** (guard `not unified_summary`, `admin.py:3623`); (b) `notas_pendientes` del reporte de contabilidad (`contabilidad_report_service.py:509-538`); (c) el resumen del home (`app/main.py:63`, ignora hasta `NotaAjusteSaldo`). Defecto extra: el motor agrupa por identidad, no por par vinculado — un crédito del cliente nunca cubre notas del proveedor ligado. Arreglar el motor primero, luego los tres consumidores. |
| **8. Saldo de vinculados como cliente** | ▪▪ | `_classify_partner_group_balances` — copia viva en `contabilidad_report_service.py:143` (la de `admin.py:478` es **código muerto**) | Hoy el par vinculado se clasifica por signo (positivo → proveedores, negativo → clientes): exactamente lo que el cliente rechaza. Fijar al bucket cliente con ambos signos. Además: `reporte_saldos` emite al vinculado **dos veces** (doble conteo en los totales globales) y `capital_real` ni siquiera agrupa. Consolidar el triplete muerto de `admin.py` antes de construir encima. |
| **9. Notas antigua→reciente** | ▪ | `admin.py:9644` (único `order_by` de la página) | Parámetro `?orden=` + control segmentado, hilado por los 4 builders de links de filtros. ⚠️ Con orden ascendente, los cortes `[:200]` y `[:10]` ("recientes") se quedarían con las más viejas — revisar el truncado junto con el flip. |
| **10. Historial proveedor reciente arriba** | ▪ | `_build_partner_ledger` (`admin.py:1066`) y gemelo unificado (`:1356`) | Invertir **al renderizar**, no al calcular: el saldo acumulado exige el paso ascendente. ⚠️ Dos consumidores leen `[-1]` como saldo de cierre (`:3543`, `:3575`) — capturar el final antes de invertir. Afecta también PDF/Excel del estado de cuenta. |
| **11. Saldos en orden alfabético** | ▪ | `reporte_saldos.html:60,109` (el sort vive en Jinja) | `?orden=alfabetico\|cantidad` + control segmentado (patrón de `capital_real.html:28`). Nota: sin el fix del punto 8, los vinculados aparecen duplicados en ambas tablas. |
| **12. Deshacer movimientos** | ▪▪▪ | Ocho acciones **ya tienen** deshacer (pagos de nota, ajustes de saldo/inventario, devoluciones, conversiones) | Faltantes reales: `AjusteSaldoPartner` (sin columnas de reversa → **migración**), `ComisionarioPago` (ídem + Scrap360 egreso-sin-reversa), ajuste de `CuentaScrap360`, gastos y movimientos manuales del corte. Usar los dos mecanismos existentes (zero-out con etiqueta `DESHECHO`, o trío `reversal_of_id`/`reverted_at`/`reverted_by`) — no inventar un tercero. El cierre de corte encadena `saldo_inicial` al día siguiente: **deshacer un cierre no es operación local** y se propone dejarlo fuera del alcance. |
| **13. Ocultar "Cuentas" del menú** | ▪ | `base.html:133` (drawer) + `home.html:193` (mosaico) | Dos supresiones de template; las rutas y los deep-links contextuales (`?owner_key=…` desde partners/comisionarios) siguen vivos — y deben seguir, porque pagos por transferencia/cheque **requieren** una `Cuenta`. No confundir con "Cuentas Scrap360" (tesorería propia), que se queda. |

Orden propuesto de la etapa 1 (dependencias primero):
**7 → 8** (comparten el motor de agrupación; consolidar código muerto una sola vez) →
**12** (migraciones) → **6** (usa la decisión de reversa de 12) → **5** → **9, 10, 11, 13, 1** (rápidos).

## 5. Etapa 2 — Módulos nuevos (requieren los Excel)

| Punto | Naturaleza | Esqueleto propuesto |
|---|---|---|
| **2. Bitácora de llamadas** | Módulo nuevo (no existe nada) | Tablas `llamadas_proveedor` + `llamadas_proveedor_materiales` (precio cotizado **desacoplado** de `tablas_precios`, como `ComisionarioNotaMaterial.precio_por_kg`); `llamada_service.py`; historial como pestaña en `partner_record.html`; flag `entregada` con auditoría. Modelo estructural a copiar: módulo comisionarios. |
| **3. Tratos de venta de contenedores** | Módulo nuevo, **el más grande de la fase** | Tablas `tratos_venta` + `tratos_venta_notas` (M2M a notas de venta). Kilos vendidos se **leen** de notas aprobadas vinculadas, jamás se escriben de vuelta. No existe concepto de moneda/FX en todo el sistema — LME, dólar y premio necesitan `Numeric(12,5)` y la fórmula exacta del Excel del cliente. |
| **4. Capital contable automático** | Híbrido: `capital_real.html` ya cubre ~60 % | Falta: (a) usar `CorteCaja.saldo_cierre` en vez de `CuentaScrap360.saldo_actual` para el efectivo; (b) parámetro fecha + desglose por sucursal (hoy todo es "ahora" y agregado); (c) columna `moneda` en `cuentas_scrap360` + tipo de cambio elegible; (d) tabla `capital_ajustes_manuales` para el comodín (registro, no mutación). Lo difícil es el histórico: los saldos son desnormalizados — reconstruir un día pasado exige reproducir los ledgers (`saldo_resultante` en movimientos + cortes por fecha). Base de inventario: modo `promedio` (precio de compra actual) ya existe. |

## 5b. Análisis de los Excel del cliente (recibidos 2026-08-06, `docs/excel_ejemplos/`)

### BITÁCORA DE ENTREGAS METALERIA.xlsx → punto 2

Una fila por (llamada × material): FECHA · PROVEEDOR · MATERIAL · **FECHA ESTIMADA ENTREGA**
· PRECIO · COMENTARIOS · ESTATUS ENTREGA · KG APROXIMADOS (casi siempre vacía).
Hallazgos que ajustan el diseño propuesto:

- El estatus real **no es booleano** "entregada": usan PENDIENTE / ENTREGADO (+fecha) /
  NO CONFIRMÓ, y en comentarios llevan el seguimiento ("LLAMAR MIÉRCOLES 27").
- Existe un campo **fecha estimada de entrega** que la propuesta v1.1 no menciona —
  a veces fecha, a veces texto ("Semana del 25 al 29 de mayo").
- El precio a veces no es número ("Lista Viernes 22 Mayo", "NO CERRAMOS PRECIO").
- La misma llamada genera N filas (una por material) — coincide con el diseño
  cabecera + líneas.

### PEDIDOS JORGE ALFARO 2026.xlsx → punto 3 (fórmulas confirmadas)

Una hoja por (material × comprador); una fila por **contenedor** de un contrato
(ORDEN = "2,3" → contenedor 2 de 3). Cadena de cálculo exacta:

```
precio_lb_usd   = (LME × DESCUENTO / 1000) / 2.204623      # LME USD/ton; DESCUENTO ej. 0.665
libras          = kg × 2.204623
total_usd       = libras × precio_lb_usd
total_pesos     = usd_tc1 × TC1 + usd_tc2 × TC2            # el pago puede partirse en 2 tipos de cambio
precio_kg_mxn   = (precio_lb_usd × 2.204623) × TC
premio          = precio_kg_mxn × 5.5 %                     # a veces 6 % — debe ser editable por fila
precio_c_premio = precio_kg_mxn + premio
total_venta     = kg × precio_c_premio
```

Campos de la fila: fecha PO, fecha vencimiento, contrato, orden (n,m), contenedor/caja,
material, fecha de carga, LME, descuento, kg. Casos especiales vistos:
filas "extra" sin fecha (kg adicionales liquidados a otro TC), material EBONY con
precio/lb capturado directo (sin LME), kg en 0 hasta que el contenedor se carga.

### CAPITAL COMPAÑÍA DE METALES.xlsx + UTILIDADES LA METALERIA.xlsx → punto 4

Dos vistas del mismo concepto, por foto de fecha:

- **A FAVOR** = inventarios valuados a precio manual por material (compañía + negocio +
  "devoluciones o material en proceso" por lote/proveedor) + efectivo + chequeras del
  negocio + chequeras fiscales (lista por cuenta: Monex, etc.) + dinero en proceso +
  saldo clientes (+ en Utilidades: préstamos a negocio y "notas descontadas de inventario").
- **DEUDA** = deuda negocio + deuda a proveedores (+ capital de socios y "otros" en Utilidades).
- **CAPITAL** = a favor − deuda; y la **utilidad del período** = capital(fecha) − capital(fecha anterior).

Implicaciones: el capital automático debe permitir comparar dos fechas (la utilidad es
la resta), la valuación del inventario es a precio elegido manualmente (ya existe el
modo `manual` y el `promedio` en `/inventario/valor`), y hay categorías que **no existen
en el sistema** (material en proceso por lote, dinero en proceso, préstamos, capital de socios)
que probablemente se cubren con el "comodín manual" de la propuesta — confirmar.

### Preguntas para la junta con el cliente

1. **Punto 1**: "stock inicial" no aparece en ninguna pantalla del sistema — ¿en qué
   pantalla exacta lo está viendo? (Candidatas: Inventario, Ajustar existencias, Aumentar materiales.)
2. **Punto 2**: ¿el estatus de la llamada es solo "entregada sí/no" o quieren los tres
   estados reales del Excel (pendiente / entregado / no confirmó)? ¿La "fecha estimada de
   entrega" (que a veces es texto libre) debe entrar al sistema? ¿Y los kg aproximados?
3. **Punto 3**: ¿el premio es siempre 5.5 % o se captura por trato (vimos 6 %)? ¿El pago
   partido en dos tipos de cambio es común y debe soportarse? ¿EBONY y otros materiales
   sin LME se capturan con precio directo? ¿Los kg del contenedor los ligan a notas de
   venta existentes o el trato vive solo?
4. **Punto 4**: ¿el capital automático es solo de la metalería (los datos que Scrap360 tiene)
   o esperan incluir compañía de metales / chequeras fiscales / dinero en proceso, que el
   sistema no conoce? ¿Esas categorías externas van dentro del comodín manual? ¿El tipo de
   cambio de los dólares se captura al momento o quieren un catálogo de TC por fecha?
5. **Punto 8**: al mostrar el saldo del vinculado "como cliente", ¿desaparece de la tabla
   de proveedores en el reporte de saldos, o aparece en ambas sin sumar doble?
6. **Punto 12**: confirmar que "deshacer el cierre de un corte de caja" queda fuera
   (rompe la cadena de efectivo del día siguiente).
7. La captura de WhatsApp (estado de cuenta de Almacenes La Victoria) muestra el resumen
   con $1,314,992 pendiente mientras el ledger, aplicando el ajuste de −$942,274, termina
   en $264,128 — es el punto 7 en vivo. Confirmar que ese es el comportamiento reclamado.

## 5c. Junta con la clienta — 07-ago-2026 (grabación en `docs/excel_ejemplos/2026-08-07 10-30-56.mkv`)

La clienta recorrió el documento "AJUSTES SCRAP 360" (actualizado ese día) compartiendo
pantalla sobre producción. Qué respondió, qué agregó y qué cambió:

**Respuestas a las preguntas de §5b:**

- **Punto 1**: es la vista **Inventario**: quitar cualquier resto visual de "stock
  inicial" y **alinear la columna de existencias al centro o a la izquierda** (hoy va
  a la derecha). Revisar también el "Saldo inicial" visible en Cuentas Scrap360.
- **Punto 8**: el ejemplo definitivo — cliente debe $5,000, le compro $15,000, saldo
  −$10,000: esos −10,000 **restan del saldo de clientes**; nunca aparecen como saldo
  deudor en proveedores. Aplica en ambos sentidos. (Responde la pregunta 5: el
  vinculado desaparece del bucket contrario; el saldo vive con signo en su bucket de
  origen.)
- **Punto 13 (Cuentas)**: confirma que no le sirve como pantalla; se mantiene **oculto
  del menú** (ya está) y NO se elimina — los pagos por transferencia/cheque lo
  requieren por dentro. Explicado así a la clienta.

**Requisitos nuevos de la junta:**

- **Contenedores (punto 3)**: además de lo del Excel — columna **número de contenedor**,
  y dos columnas nuevas que el Excel no tiene: **"Peso Báscula Pública"** y
  **"Peso de Puerto"** (ambos en kg). Kilos tratados vs. vendidos con botón
  "completada" que la saca de pendientes de entrega.
- **Capital (punto 4)**: confirmado por día y por sucursal con la chequera en USD y
  TC manual; **nuevo**: poder **fusionar sucursales** en el reporte (ej. sucursal 1+3
  como una sola vista).
- **Récord de proveedor — orden de bloques**: "Asistencias" hasta el final; "Ajuste
  manual de saldo" hasta arriba; después "Estado de cuenta". (Ajusta el orden que
  traíamos: Resumen → **Ajuste manual** → **Estado de cuenta** → Notas → Pagos →
  **Asistencias**.)

**Estado real de los puntos tras el trabajo del 6-7 de agosto:**

| Punto | Estado | Nota |
|---|---|---|
| 9 (notas antigua↔reciente) | ✅ En prod | Toggle "Recientes/Antiguas primero" en la lista de notas |
| 10 (historial reciente arriba) | ✅ En prod | Toggle "Recientes primero/Cronológico" en el récord |
| 11 (saldos alfabético) | ✅ En prod | Toggle "Por cantidad/Alfabético" en el visor de saldos |
| 13 (ocultar Cuentas) | ✅ En prod | Menú y mosaico sin la entrada; rutas vivas |
| 12 (deshacer) | ✅ En prod (07-ago, Ronda C) | Las cuatro reversas faltantes: ajuste de socio (compensatorio + trío, con historial en el récord), pago a comisionista (zero-out + restauración de nota + reingreso Scrap360), movimiento manual de tesorería (compensatorio) y gastos/movimientos del corte abierto (zero-out). Migración `b2c3d4e5f6a7`; guardado por `scripts/test_reversas.py`. Deshacer el cierre de un corte queda fuera (rompe la cadena de efectivo) |
| 6 (FIFO comisionista) | ✅ En prod | `pay_comisionario_fifo` implementado y cableado a `POST /comisionarios/{id}/pago`; verificado 07-ago |
| 1, orden del récord | ✅ En prod (07-ago, Ronda A) | Existencias alineadas a la izquierda ("stock inicial" ya no existía en ninguna vista); récord en el orden pedido: Resumen → Ajuste manual → Estado de cuenta → Notas → Pagos → Asistencias |
| 7, 8 (neteo y vinculados) | ✅ En prod (07-ago, Ronda B) | El saldo efectivo es global: el crédito ya no se recorta por sucursal y el FIFO corre sobre todas las notas del socio (causa de los "pendientes fantasma" al filtrar). El par vinculado clasifica SIEMPRE en el bucket de clientes con signo (ejemplo −10,000 de la clienta) en contabilidad, reporte de saldos (sin doble conteo), capital y home. Guardado por `scripts/test_neteo.py` |
| 5 (archivar sucursales) | ✅ En prod | Rutas archivar/reactivar (super_admin) con guardas de corte abierto, notas sin aprobar, usuarios activos y stock; los selectores de captura excluyen archivadas y el historial se conserva. Verificado en vivo 07-ago |
| 2 (bitácora de llamadas) | ✅ En prod (07-ago, Ronda E) | Módulo nuevo: tablas `llamadas_proveedor` + `llamadas_proveedor_materiales` (migración `c3d4e5f6a7b8`), `llamada_service.py`, pantalla `/web/admin/bitacora-llamadas` (captura colapsable + filtros por proveedor/estatus), sección en el récord del proveedor y entrada de menú en Catálogos. Tres estatus reales del Excel (Pendiente/Entregado con auditoría/No confirmó), fecha estimada y precio en texto libre, material "sin definir aún". Guardado por `scripts/test_bitacora.py` |
| 3 (tratos de contenedores) | ✅ En prod (07-ago, Ronda E) | Módulo nuevo: `tratos_venta` + contenedores + vínculo a notas (migración `d4e5f6a7b8c9`), `trato_service.py` con la cadena exacta del Excel (LME×descuento→$/lb→TC ponderado en pagos partidos→premio editable por fila→total venta; EBONY con $/lb directo). Pantallas: lista con avance kg tratados/vendidos, detalle con totales USD/MXN y botón completada, formulario de contenedor con vista previa del cálculo en vivo; columnas nuevas de la junta (número de contenedor, Peso Báscula Pública, Peso de Puerto). Kilos vendidos LEÍDOS de notas de venta aprobadas vinculadas (solo aprobadas, sin doble vínculo, jamás se escriben). Guardado por `scripts/test_tratos.py` (28 checks) |
| 4 (capital diario) | 🔓 Desbloqueado | Los 4 Excel ya están en `docs/excel_ejemplos/` — el reloj de entrega corre |

## 6. Riesgos y dependencias

1. **Los 3 Excel del cliente** — bloquean puntos 2, 3, 4 y arrancan el reloj de 2 semanas. Pedirlos ya.
2. **Ambigüedad del punto 1** — "stock inicial" no se renderiza en ninguna parte; confirmar con el cliente qué etiqueta está viendo.
3. **Punto 8, decisión de producto** — "el saldo se refleja como cliente… aplica en ambos sentidos": confirmar si en `reporte_saldos` el vinculado desaparece de la tabla de proveedores o se muestra en ambas sin doble conteo.
4. **Sin pruebas automatizadas** — ~24k líneas de lógica sin tests; los puntos 6, 7, 8 y 12 tocan dinero. Mitigación mínima: tests de `note_service`/motor de neteo antes de tocar el motor (KNOWN_ISSUES #11 ya lo recomienda).
5. **Se publica en producción durante el proceso** — cada push a Heroku corre migraciones en release; las migraciones de 12 y de la etapa 2 deben ser batch-compatibles (SQLite dev / Postgres prod) y aditivas.
6. **Deshacer el cierre de corte** queda explícitamente fuera del punto 12 (rompe la cadena de efectivo); dejarlo por escrito con el cliente.

## 7. Bitácora de avance

| Fecha | Trabajo | Resultado |
|---|---|---|
| 2026-08-06 | Revisión a fondo: propuesta v1.1, docs del repo, auditoría de homologación front-end (51 templates) y mapeo backend de los 13 puntos | Este documento. 20/49 pantallas conformes; backlog etapa 0 con 12 tareas; 13 puntos mapeados a archivos con esfuerzo estimado |
| 2026-08-06/07 | Sesión nocturna: fase 0 de la auditoría UI (tonos, fechas, precios, IDs) + 16 pantallas de la hoja de ruta + marca/login/footer + colapsables + KPIs en banda | 12 deploys a prod; puntos 9, 10, 11 y 13 de la fase 2 quedaron cubiertos de paso |
| 2026-08-07 | Junta con la clienta (grabación + doc actualizado + 4 Excel ya en repo); análisis de fórmulas de los 3 módulos | §5c: 3 preguntas respondidas, 3 requisitos nuevos; etapa 2 desbloqueada; tablero de estado por punto |
| 2026-08-07 | **Ronda D verificada + Ronda E.1: bitácora de llamadas en prod** (commit `db63e9b`, migración `c3d4e5f6a7b8`) | Punto 5 probado en vivo (archivar → guardas → reactivar). Punto 2 completo: modelo cabecera+líneas, servicio, pantalla con captura colapsable y filtros, sección en el récord del proveedor, menú en Catálogos (ícono `phone` nuevo en el sprite); `scripts/test_bitacora.py` 17/17; humo en dev de crear → entregar → eliminar; prod migrado y ruta con guardia |

### Metodología por punto (acordada 07-ago)

Un punto a la vez, con este ciclo: **(1)** leer su mapeo en §4/§5 y confirmar el
comportamiento actual en el código; **(2)** implementar en un bloque acotado;
**(3)** verificar — `check_pages`, `check_templates`, `fix_accents`, prueba visual
390/1440 y, si toca dinero, prueba funcional con datos; **(4)** deploy a Heroku +
push a GitHub; **(5)** marcar aquí el punto con fecha y commit; **(6)** aviso corto
a la clienta con lo que ya puede ver en prod.

Orden de resolución (dependencias primero, dolor visible primero):

| Ronda | Puntos | Por qué en este orden |
|---|---|---|
| **A** | 1 + orden del récord + verificación en vivo de 6/9/10/11 | Quick wins, un solo deploy; cierra lo cosmético de la junta el mismo día |
| **B** | 7 → 8 | Comparten el motor de agrupación; es el dolor #1 en prod (pendientes fantasma y saldos cruzados); tests mínimos del motor antes de tocarlo |
| **C** | 12 → 6 | Las reversas faltantes requieren migraciones; el FIFO de comisiones reutiliza la decisión de reversa |
| **D** | 5 | Archivar sucursales con guardas |
| **E** | 2 → 3 → 4 | Módulos nuevos con Excel en mano: bitácora (menor), contenedores (mayor, con Peso Báscula Pública/Puerto y premio editable por fila), capital diario (con fusión de sucursales y chequera USD/TC) |
| 2026-08-06 | Análisis de los 4 Excel del cliente + captura WhatsApp | §5b: fórmulas de puntos 2/3/4 descifradas; 7 preguntas para la junta; la captura confirma el bug del punto 7 en vivo |
| 2026-08-06/07 (noche) | **Etapa 1: 7 puntos resueltos sin esperar junta** — 13 (Cuentas oculto del menú, rutas y accesos contextuales vivos) · 9 (explorador de notas con control Recientes/Antiguas primero, corte a 200 después de ordenar) · 10 (estado de cuenta del socio con toggle Recientes primero/Cronológico; el saldo acumulado sigue calculándose en orden cronológico) · 11 (reporte de saldos Por cantidad/Alfabético, orden en servidor e insensible a acentos) · 5 (archivar/reactivar sucursales con guardas accionables: corte abierto, notas sin aprobar, usuarios asignados, existencias; selectores solo-activas, listas y reportes conservan historial; corte bloquea abrir en archivada) · 6 (`pay_comisionario_fifo`: una transacción, un pago por nota consumida en orden antiguo→reciente, formulario en el record con desglose y validaciones; probado de punta a punta) · 7 (**motor de neteo movido a `note_service.build_effective_note_balance_map`, consciente del par vinculado** — el crédito de una identidad alcanza a la otra con la aritmética de signos correcta; consumidores corregidos: record unificado, notas_pendientes del reporte de contabilidad y resumen del home). **Primeras pruebas automatizadas del proyecto**: `tests/test_neteo.py` (7 casos, incluido el escenario exacto de la captura del cliente). Verificación integral: ajuste −$1,800 al Proveedor Uno netea home/notas/reporte y desaparece al revertirlo; 84 vistas del gate en verde. Nota operativa local: uvicorn `--reload` no recarga Python en este entorno (y vigilar el cwd reinicia con cada escritura a metalleria.db) — reiniciar el server a mano tras cambios de backend |
| 2026-08-06 | **Etapa 0 ejecutada completa** (plan pantalla por pantalla en `FASE_2_ETAPA_0_PANTALLAS.md`) | `check_ui.js` reactivado (npm + playwright-core) y **84 vistas en verde a 1440/390 px**; `precios_material` migrada; un solo botón primario en home y nota; 33 tablas con estado vacío; glifos → sprite (`chevron-down` nuevo); solo-lectura como `<output>`/texto (corte y wizard); familia `corte-*` promovida a componentes canónicos `s-collapse`/`s-cat-*`/`s-method-pill`/`s-dockbar` (§21 de scrap360.css); stats consolidadas (defs() en secciones); **`style.css` eliminado** (drawer/dock/footer portados con tokens; remap legado podado de 270 a 20 líneas); diccionario de acentos +12 palabras y 6 frases; `UI_UX.md` reescrito post-rediseño; `KNOWN_ISSUES` #19 resuelto. Verificación: import OK, 51 plantillas compilan, acentos 0 pendientes, 29 rutas 200, capturas antes/después comparadas |
