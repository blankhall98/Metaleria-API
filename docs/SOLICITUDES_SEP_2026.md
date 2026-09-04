# Solicitudes de la administradora — septiembre 2026 (kilos por material)

**Documento vivo.** Tres solicitudes reenviadas por WhatsApp el 04-sep-2026, con
dos capturas de apoyo: el estado de cuenta en pantalla del proveedor BRAVO y la
hoja de Excel con la que ella concilia ese mismo proveedor.

> **Estado 2026-09-04:** assessment aprobado por el usuario; en implementación
> en el orden 1 → 2 → 3, un commit por punto, despliegue a Heroku + GitHub al
> cerrar cada uno. Tablero al final del documento.

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
| Ranking (punto 2) | Solo compras a proveedores; la consulta queda simétrica para ventas a clientes |
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
% del total, pie de totales y exportación a Excel.

Registro en tres lugares: menú lateral (`base.html`), mosaico del home
(`home.html`) y lista de `scripts/check_ui.js`. Visible para admin y super
admin, como los otros dos reportes; el admin queda acotado a sus sucursales.

El selector de material lista todos los materiales, también los inactivos,
porque el reporte es histórico.

**Verificación.** `pytest`; `check_pages` de la ruta con y sin material;
`check_ui.js`; acentos; 390/1440 px.

### 3. Desglose por material en el estado de cuenta

**Cambio.** Cada evento "Nota aprobada" del ledger lleva `lineas`:
material, kilos, precio y subtotal, cargadas con `selectinload` para no disparar
una consulta por nota. Aplica a `_build_partner_ledger` y a su gemelo unificado.

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
| 1. Kilos por material en el expediente | **Hecho** 2026-09-04 | ver bitácora | Servicio `kilos_material_service.kg_por_material` + 10 pruebas; tarjeta con filtro propio; hoja `KilosPorMaterial` en el Excel; `check_ui` record 6/6 sin incumplimientos |
| 2. Reporte ranking por material | En curso | — | — |
| 3. Desglose en el estado de cuenta | Pendiente | — | — |
