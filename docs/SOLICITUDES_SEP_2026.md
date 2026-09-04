# Solicitudes de la administradora — septiembre 2026 (kilos por material)

**Documento vivo.** Tres solicitudes reenviadas por WhatsApp el 04-sep-2026, con
dos capturas de apoyo: el estado de cuenta en pantalla del proveedor BRAVO y la
hoja de Excel con la que ella concilia ese mismo proveedor.

> **Estado 2026-09-04:** los tres puntos implementados y en producción el mismo
> día (commits `fd1d3c0`, `5f4e727`, `1498651`), un commit por punto, cada uno
> desplegado a Heroku y empujado a GitHub. Pendiente: aceptación de la clienta
> desde su teléfono. Tablero al final del documento.

## Lo que pide, en sus palabras

1. *"Que en el perfil de cada proveedor me dé el total de kilos comprados de
   cada material en un lapso de tiempo a elegir."*
2. *"Que haya una sección que podamos seleccionar algún material específico y
   también por determinado lapso de tiempo, nos dé de mayor a menor lo que nos
   ha vendido de ese material cada proveedor en kilos."*
3. *"Quiero que cuando me vaya a algún proveedor en específico me desglose en la
   vista del programa, el material, los kilos que corresponden a cada movimiento
   como si fuera un Excel."* — con la captura de su hoja: una fila por material
   de cada nota, y el cargo de la nota junto con el saldo corrido en la última
   fila del grupo.

## Lectura del lote

Los tres puntos son **lectura de datos que ya existen**: cada nota guarda sus
líneas por material en `nota_materiales` con kilos netos, precio unitario y
subtotal. No hay migración, no se toca dinero ni inventario, y ninguna nota
aprobada se edita. El riesgo se concentra en el punto 3 porque modifica el
estado de cuenta, pantalla que la clienta validó dos veces en agosto
(`CORRECCIONES_ADMIN_AGO_2026.md` §7 y §10) y cuyo PDF envía a los proveedores.

Hallazgos del código que condicionan el diseño:

- **La base son los kilos netos (`kg_neto`)**, lo que se le paga al socio. Su
  Excel cuadra con eso: 3,195 kg × $156 = $498,420. `kg_real` solo difiere
  cuando un super admin lo fuerza y es la cifra que entra al inventario.
- **Las devoluciones parciales ya reescriben la línea** (`note_service.py`,
  `_recalc_material` tras aplicar la devolución): `kg_neto`, precio y subtotal
  quedan actualizados. Sumar líneas ya descuenta lo devuelto. Las cancelaciones
  totales pasan la nota a CANCELADA y salen del filtro `estado == APROBADA`.
- **Los traspasos entre sucursales son notas normales** con un socio ficticio
  "Sucursal X". El ranking los excluye con el mismo criterio que
  `_is_internal_partner_name` en el reporte de saldos.
- **Fecha de referencia: la de captura de la nota (`created_at`).** Es la que
  usan el reporte de asistencias y el Excel de la clienta. El estado de cuenta
  muestra la fecha de aprobación (la del movimiento contable), por eso su Excel
  dice 21/07 donde la app dice 23 jul. No es un error; conviene explicárselo.
- **No hay librerías de Excel ni PDF.** Ambos se arman a mano en
  `partner_report_service.py`. El Excel admite columnas nuevas; el PDF es carta
  vertical con anchos fijos y no caben cuatro columnas más.

## Decisiones tomadas (aprobadas 04-sep-2026)

| Tema | Resolución |
|---|---|
| Kilos | `kg_neto` en los tres puntos |
| Fecha de los rangos | `Nota.created_at`, cortes de día en hora local, intervalo semiabierto |
| Ranking (punto 2) | Compras a proveedores por defecto y, desde el 04-sep por la tarde, también ventas a clientes (`?operacion=ventas`); las ventas directas a un proveedor vinculado se funden en su cliente |
| Estado de cuenta (punto 3) | Filas planas al estilo Excel: una fila por material, cargo y saldo en la última del grupo |
| Exportación del punto 3 | Excel con las mismas filas y columnas; PDF con una línea compacta de texto bajo cada nota |
| Socios internos | Excluidos del ranking |

## Puntos

### 1. Kilos por material en el expediente del socio

**Cambio.** Tarjeta nueva "Kilos por material" en `partner_record.html`, debajo
del estado de cuenta, con filtro Desde / Hasta propio (`kilos_from` /
`kilos_to`; los nombres `attendance_from/to` y `q` ya están reservados en la
misma página). Sin fechas muestra todo el historial con la etiqueta de alcance.
Columnas: Material, Kilos, Notas, Importe, con pie de totales.

En el expediente de un proveedor se listan las compras; si además tiene ventas
(proveedor con `permite_ventas` o par vinculado) se muestra una segunda tabla
con los kilos vendidos. En el de un cliente, las ventas.

La agregación vive en `app/services/kilos_material_service.py`
(`kg_por_material`), con pruebas en `tests/test_kilos_material.py`. El Excel
del estado de cuenta gana una cuarta hoja `KilosPorMaterial` con la misma tabla
del alcance activo.

**Verificación.** `pytest tests -q`; `check_pages` sobre el record de un
proveedor, uno con ventas y un cliente; acentos; 390/1440 px.

### 2. Reporte "Kilos por material" (ranking de proveedores)

**Cambio.** Ruta `GET /web/admin/reporte-materiales` en el grupo Reportes, con
filtros Material, Sucursal, Desde y Hasta (`fecha_inicio` / `fecha_fin`, patrón
del reporte de asistencias, mes en curso por defecto). Sin material elegido:
estado vacío que pide elegirlo. Con material: ranking de proveedores de mayor a
menor por kilos comprados con columnas #, Proveedor, Kilos, Notas, Importe y
% del total, pie de totales y exportación a Excel. Un control segmentado cambia
al modo **Ventas a clientes** (`?operacion=ventas`): mismo ranking sobre las
notas de venta, con las ventas directas a proveedores fundidas en su cliente
vinculado o listadas como socio propio si no hay vínculo.

Registro en tres lugares: menú lateral (`base.html`), mosaico del home
(`home.html`) y lista de `scripts/check_ui.js`. Visible para admin y super
admin, como los otros dos reportes; el admin queda acotado a sus sucursales.

El selector de material lista todos los materiales, también los inactivos,
porque el reporte es histórico.

**Verificación.** `pytest`; `check_pages` de la ruta con y sin material;
`check_ui.js`; acentos; 390/1440 px.

### 3. Desglose por material en el estado de cuenta

**Cambio.** Cada evento "Nota aprobada" del ledger lleva `lineas`:
material, kilos, precio y subtotal, cargadas en una sola consulta para todas las
notas (`kilos_material_service.lineas_por_nota`, con `joinedload` del material)
para no disparar una consulta por nota. Aplica a `_build_partner_ledger` y a su
gemelo unificado.

En pantalla la nota se pinta como una fila por material con cuatro columnas
nuevas después de Nota: Material, Kg, Precio, Subtotal. Cargo, Abono y Saldo
solo en la última fila del grupo, como en su Excel. Pagos y ajustes dejan las
cuatro columnas vacías. Si la nota lleva IVA se agrega una fila "IVA" para que
los subtotales cuadren con el cargo. **El saldo corrido no cambia**: se calcula
igual, sobre el evento, antes de expandirlo en filas.

La tabla pasa de 9 a 13 columnas; Material y Kg se marcan `data-mobile-primary`
y el paso de "Ver más" sube a 20 filas. Excel: mismas filas y columnas en la
hoja `EstadoCuenta`. PDF: la tabla conserva sus columnas y debajo de cada nota
se imprime una línea compacta ("Bronce 3,195.00 kg × $156.00 = $498,420.00 ·
Radiador …").

**Verificación.** Antes y después del cambio, el saldo final del proveedor de la
captura ($3,101,903.00) y del proveedor 74 en producción deben ser idénticos.
`pytest` y `scripts/test_neteo` completos aunque no toquen el ledger;
`check_pages` sobre el record, el PDF y el Excel; acentos; 390/1440 px.

## Reglas de ejecución

1. Un commit por punto; despliegue a Heroku **y** push a GitHub al cerrar cada
   uno; verificar `healthz` y la página afectada en producción.
2. Verificación local antes de desplegar: `python -m pytest tests -q` ·
   `bash scripts/check_pages.sh <rutas>` ·
   `python -m scripts.fix_accents --check app/templates` ·
   `python -m scripts.check_templates` · revisión visual a 390 y 1440 px.
3. Ninguna corrección edita registros aprobados (`docs/BUSINESS_RULES.md`).
4. Aviso corto a la clienta al cerrar cada punto con lo que ya puede ver.

## Tablero

| Punto | Estado | Commit | Notas |
|---|---|---|---|
| 1. Kilos por material en el expediente | **Hecho** 2026-09-04 | `fd1d3c0` | Servicio `kilos_material_service.kg_por_material` + 10 pruebas; tarjeta con filtro propio; hoja `KilosPorMaterial` en el Excel; `check_ui` record 6/6 sin incumplimientos |
| 2. Reporte ranking por material | **Hecho** 2026-09-04 | `5f4e727` | Ruta `/web/admin/reporte-materiales` + plantilla + Excel; menú lateral, mosaico del home y `check_ui`. Modo **Ventas a clientes** agregado la misma tarde a petición del usuario (2 pruebas más; `check_ui` 6/6) |
| 3. Desglose en el estado de cuenta | **Hecho** 2026-09-04 | `1498651` | `lineas_por_nota` (3 pruebas, incl. renglón de IVA) en los dos constructores del ledger; pantalla y Excel con 13 columnas; PDF con línea compacta por nota; verificado en local: subtotales de cada nota suman su cargo y el saldo final no cambió ($9,525.00 del proveedor 1); `check_ui` record 6/6 |

## Incidente del 04-sep por la tarde — "no se puede editar la nota devuelta"

Audio de la clienta a las 13:38 (hora local) preguntando si "se movió algo"
con los despliegues del día. **No hubo regresión**: los archivos tocados hoy
(expediente, reportes, ledger) no participan en el flujo de captura, devolución
ni edición de notas. Reconstrucción con los logs de Heroku y la copia original
de la nota (`nota_originales`), todo en hora local:

| Hora | Quién | Qué pasó |
|---|---|---|
| 13:16 | Trabajador (sucursal 2) | Captura la nota 3342 y la envía a revisión |
| 13:31:39 | Admin | "Devolver" sin comentario → 400 con el aviso de que el comentario es obligatorio |
| 13:31:49 | Admin | "Devolver" con comentario "EL COBRE 1 NO TIENE DESCUENTO" → BORRADOR |
| 13:36:57 | Trabajador | En su lista pulsa **Enviar a revisión** en vez de **Editar**: la nota vuelve a EN_REVISION sin cambios (la copia original de las 13:36 es idéntica a la de las 13:16) y el botón Editar desaparece, porque el trabajador solo edita en borrador |
| 13:38 | Admin | Audio |
| 13:42 | Admin | Abre "Editar" de la nota en revisión (sí está disponible para super admin), pone 0.100 kg de descuento al Cobre 1 y guarda |
| 13:43 | Admin | Aprueba (folio 02_C_978), registra el pago e imprime la nota |

Resultado: resuelto por ella misma antes de cualquier intervención. Dos trampas
de uso quedan a la vista y se proponen como mejora (pendiente de aprobación):
(1) en la lista del trabajador, una nota devuelta debería mostrar el comentario
del admin y ofrecer **Corregir** como acción principal, con "Enviar a revisión"
secundaria o con confirmación si no hubo cambios desde la devolución; (2) en el
detalle de una nota en revisión, "Editar pesos y precios" vive abajo, en el
bloque de control, y conviene subirlo junto a Aprobar/Devolver.
