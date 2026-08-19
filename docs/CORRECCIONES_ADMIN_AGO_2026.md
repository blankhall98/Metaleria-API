# Correcciones — reportes de la administradora (agosto 2026)

**Documento vivo.** Doce puntos levantados del hilo de WhatsApp del 13-14 de
agosto de 2026 (audios, capturas y un PDF marcado a mano), más un hallazgo
propio surgido al revisar el material.

> **Estado 2026-08-14:** las 14 tareas implementadas en 15 commits
> (`b30f592..aa254da`), un commit por punto. Verificación local completa
> (pytest, 8 suites de guardas incl. la nueva `test_comision_auto`,
> `check_pages` sobre 28 rutas, `fix_accents`, `check_templates`,
> `check_ui.js` 84 vistas) y revisión adversarial con 3 endurecimientos
> aplicados. Verificado contra producción: proveedor 74 (rubro saldo a favor)
> y direcciones de sucursal (§2: cadena literal `"None"` en 02 MT y 03 N).
>
> **Segunda tanda 2026-08-18 (v215, `65c8185..c1bf46c`):** cuatro reportes tras
> el primer uso en producción, todos verificados en prod —
> (a) *Editar nota*: los inputs de subpesaje colapsaban a 23 px (`s-table-edit`);
> (b) comisión al aprobar rediseñada **por material** con precio/kg libre y
> sucursal visible en el desplegable (`test_comision_auto` 32 aserciones);
> (c) selects de todo el sistema con la flecha encima del texto (padding
> derecho borrado en `scrap360.css`) + home sin encimar en 1500/1280/390;
> (d) valuación del inventario: en prod **no hay listas de venta**, solo de
> compra — el respaldo y los modos `lista_*` buscan ahora venta→compra por
> tipo de cliente, un manual en 0 cuenta como vacío y la tabla deja de
> congelar los automáticos como manuales al guardar. 02 MT: 10 de 13 ceros
> resueltos por lista; 4 materiales siguen sin ninguna lista.

Fuentes originales y transcripciones: fuera del repo (material de la clienta).
Bitácora de intake con la evidencia punto por punto: `triage-admin-2026-08-14.md`.

## Lectura del lote

Nueve de los doce puntos son de **presentación**, no de operación: ningún reporte
señala un cálculo de dinero mal hecho ni inventario descuadrado. Lo que falla es
cómo se lee la información, y casi siempre **desde el teléfono** — la
administradora trabaja en móvil y las pantallas se diseñaron para monitor.

Dos puntos no son correcciones y conviene nombrarlos así frente a la clienta:

- **§14** (comisión al aprobar nota) es **funcionalidad nueva**. Por decisión del
  14-ago entra en este lote, pero se aísla y se ejecuta al final.
- **§9** (contenedores) **no tiene defecto**: el módulo hace lo que ella pide, un
  nivel más abajo. Necesita dos textos de ayuda y una sesión de capacitación.

> **Sobre la numeración.** Los números de este documento (§1 a §14) son **orden de
> ejecución**, no los del intake. Tres reportes se partieron en tareas separadas
> porque se verifican distinto: el PDF de comisión dio §1 y §2; la pantalla de
> movimientos de inventario dio §4 y §5; el "ver más" de comisiones se fusionó en
> §4 y su orden por fecha quedó en §6. La trazabilidad al reporte original está en
> la bitácora de intake.

## Decisiones de alcance tomadas

| Tema | Resolución |
|---|---|
| Tabla vs. tarjetas en móvil | **Solo inventario.** Las otras ~80 tablas conservan el reapilado. |
| "Ver más" | **Barrido completo de la app**, no solo las pantallas reportadas. |
| Rubro "Saldo a favor" | **Corregir de raíz**, no solo ocultarlo del PDF. |
| Comisión al aprobar nota | **Entra al lote**, aislada y al final. |

## Reglas de ejecución

1. **Un commit por punto.** Despliegue único al final, pero reversión quirúrgica
   si algo falla en producción.
2. **Verificación local antes de desplegar**, nunca descubrimiento en producción:
   `bash scripts/check_pages.sh <rutas>` · `python -m pytest tests -q` ·
   `python -m scripts.test_neteo` · `python -m scripts.fix_accents --check app/templates`
   · `python -m scripts.check_templates` · revisión visual a 390 px y 1440 px.
3. **La prueba en producción es aceptación con la administradora**, no búsqueda
   de errores.
4. **Ninguna corrección edita registros aprobados.** Toda corrección posterior es
   un registro compensatorio (`docs/BUSINESS_RULES.md`).
5. Al terminar: `git push heroku main` **y** `git push origin main`.

---

# Orden de ejecución

El orden no es arbitrario. La ola 1 no depende de nadie y se puede cerrar de
corrido; la ola 2 arrastra dependencias o verificaciones; la ola 3 es la
funcionalidad nueva.

## Ola 1 — sin dependencias ni riesgo

### 1. Acentos rotos en todos los PDF ⚠️ prerrequisito de §7

**Defecto.** Las fuentes se declaran sin codificación:
`<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>`. El contenido se escribe
en latin-1, donde `Ó` es `0xD3`; sin `/Encoding` el visor aplica la
*StandardEncoding* de la fuente, donde ese byte no tiene glifo → `.notdef`.
Por eso la nota de comisión muestra **NOTA DE COMISI□N**.

**Alcance — 7 declaraciones en 3 constructores independientes:**

| Archivo | Líneas | Documentos afectados |
|---|---|---|
| `contabilidad_report_service.py` | 883-884 | Contabilidad · estados de cuenta proveedor/cliente · Cuentas Scrap360 |
| `corte_caja_report_service.py` | 579-581 | Corte de caja |
| `invoice_service.py` | 89-90 | Notas de compra/venta · notas de comisión |

**Cambio.** Añadir `/Encoding /WinAnsiEncoding` a las siete. WinAnsi coincide con
latin-1 en `0xA0-0xFF`, así que los acentos del español salen correctos.

**Efecto colateral valioso.** Todos los PDF están redactados sin acentos a
propósito ("Metodo", "Operacion", "Direccion", "continuacion") porque se rompían.
Corregir la codificación **desbloquea** la acentuación de todos los documentos.

**Verificación.** Generar un PDF de cada uno de los tres constructores y
confirmar que los acentos se ven.

### 2. "Direccion: None" en la nota de comisión

El guard existe (`invoice_service.py:353`), así que la hipótesis es que la
sucursal **02 MT guarda la cadena literal `"None"`**, que es truthy y pasa el
guard. No verificable en local: la base de desarrollo tiene datos de semilla
ficticios.

**Primero verificar en producción:** `SELECT id, nombre, direccion FROM sucursales;`

- Cadena `"None"` → corrección de dato + revisar qué formulario la escribió.
- `NULL` y aun así imprime `None` → hay una ruta sin el guard.

### 3. Cuentas Scrap360 — quitar "Saldo de apertura" de la lista

**Cambio.** Eliminar la columna (`cuentas_scrap360_list.html:78`) y ajustar el
subtítulo (`:8`), que hoy menciona ambos saldos.

**No se toca:** el campo del formulario (`cuenta_scrap360_form.html:84,95`) —
sin él no se puede dar de alta una cuenta — ni el movimiento contable inicial
(`admin.py:9202`), que es el asiento real que sostiene el saldo actual.

**Pendiente de preguntarle:** si también sale del detalle de la cuenta
(`cuenta_scrap360_detail.html:33`) y de sus reportes Excel/PDF.

**Ojo.** Al quitar una columna cambia el reapilado en teléfono: revisar
`data-title-col` y `data-mobile-primary` de esa tabla.

### 4. Barrido de "Ver más" en toda la app

**Contexto.** Pedido tres veces en una semana: expediente del socio (resuelto en
`dbb037e`), movimientos de inventario, comisiones. El atributo `data-ver-mas="10"`
ya existe en `app/static/js/app.js:294` y solo dibuja el botón si sobran filas,
así que marcar una tabla corta no cuesta nada.

**Cambio.** Inventariar todas las tablas alimentadas por consultas sin límite y
marcarlas. Hoy lo usan 7 tablas en 2 plantillas.

**Verificación.** `bash scripts/check_pages.sh` sobre cada ruta tocada; confirmar
en una tabla larga y en una corta que el botón aparece solo donde corresponde.

### 5. Movimientos de inventario — subir el kardex

**Cambio.** Reordenar `inventario_movimientos.html`: *Movimientos del rango*
(`#inv-kardex`, hoy al final) pasa a primera posición. Mover también el
`anchor_nav` (`:103-106`) para que el orden de anclas coincida.

**Fundamento.** Es la única de las cuatro tablas con columna `Saldo` (`:254`) —
justo la cifra que ella nombra: *"lo que va quedando de saldo en kg"*. Las otras
tres son análisis, no operación.

**Prioridad alta:** es el único punto que reportó dos veces.

### 6. Comisiones — orden por fecha

**Cambio.** Permitir ordenar por fecha más antigua / más reciente. Reusar el
patrón `.s-segmented` ya resuelto en `partner_record.html:276` ("Orden del estado
de cuenta") en lugar de inventar un control nuevo.

### 7. Estado de cuenta — quitar el bloque *Resumen* ⚠️ depende de §1

**Evidencia.** La clienta imprimió el estado de cuenta del proveedor JOSE
GUTIERREZ (ID 74) y **tachó con plumón**. No hay ambigüedad:

- **Se elimina:** todo el bloque *Resumen* — los cuatro conteos por estado
  (aprobadas, en revisión, borrador, canceladas) y las seis cifras de dinero.
- **Se conserva intacto:** `Notas totales`, el `Saldo acumulado (por pagar al
  proveedor)` y **el ledger completo** — notas, pagos y ajustes con saldo corrido.

Coincide con su audio: *"sí que se desglose todas las notas y los abonos"*.

**Cambio.** Acotado a `build_partner_statement_pdf`
(`partner_report_service.py:146`). El diccionario `report` (`admin.py:3860`)
**no se toca**, así que el Excel conserva sus tres hojas y no se pierde lógica.

Sustituir el bloque por tres cifras en el lenguaje de ella —**Total entregado ·
Total pagado · Saldo por pagar**— usando `ledger_final`, que es la misma cifra
que cierra el estado de cuenta en pantalla y la que ella dejó sin tachar. Se
descartó `saldo_pendiente` (sobrestima la deuda cuando hay notas sobrepagadas) y
un neto calculado, que crearía una cuarta fórmula de saldo — el patrón que
produjo `KNOWN_ISSUES` #4.

**Además:** fecha de corte en el encabezado ("Saldo al …") y acentos, que el
punto 1 vuelve posibles.

**Aplica también al estado de cuenta del cliente**, por compartir función
(`admin.py:7013`).

### 8. Home — encabezado encimado

**Causa probable.** `home.html:183` pone tres elementos en un renglón
`d-flex flex-wrap`, pero `.s-collapse__trigger` declara `width: 100%`
(`scrap360.css:3079`). Un hijo al 100 % dentro de un contenedor con `flex-wrap`
se queda el renglón entero y expulsa al selector de sucursal y al botón
"Ver todas" a una segunda línea, en cualquier ancho.

**Antes de tocar:** reproducir y **preguntarle si lo ve en teléfono, en
computadora o en ambos** — su trazo verde cubre justo la zona reportada.

**Cuidado.** `.s-collapse__trigger` es compartida; el `width: 100%` es correcto
donde el disparador va solo. El arreglo va en el contenedor de `home.html`, no en
la regla global.

### 9. Contenedores — textos de ayuda

**No hay defecto.** El LME, el descuento, los tipos de cambio y el premio se
capturan **por contenedor** (`trato_contenedor_form.html:67-112`), y el precio
final se calcula en vivo desde `:148` con la fórmula de `trato_service.py:6-13`,
réplica declarada del Excel de la clienta. Ella miró el formulario de *trato*,
que es el contrato marco.

**Cambio mínimo:**
- Subtítulo de "Nuevo trato": hoy describe campos, debe describir el flujo — que
  el LME y el tipo de cambio se capturan al cargar cada contenedor.
- Estado vacío de la lista: explicar el flujo de dos pasos.

**Lo que de verdad resuelve el punto no es código.** El módulo tiene **cero
registros**: se entregó en la fase 2 y nunca se ha usado. Agendar una sesión de
10 minutos mostrando el flujo.

---

## Ola 2 — requieren verificación o migración

### 10. Rubro "Saldo a favor de la empresa" absorbe los ajustes

**Síntoma.** En el estado de cuenta de JOSE GUTIERREZ:

| Rubro | Valor |
|---|---|
| Saldo a favor de la empresa | ≈ $4,229,25x.90 |
| Ajustes manuales | ≈ $4,229,299.xx |
| **Saldo real (ledger)** | **$2,299,794.50 por pagar** |

**Causa probable.** `admin.py:2724-2730`: cuando `ajustes_delta` es negativo (los
abonos que entran como "Ajuste manual / Comentario: DEP"), el monto completo se
acumula en `saldo_favor`. El ledger, en cambio, lo aplica al saldo corrido. El
mismo dinero se reporta dos veces con dos significados.

**Gravedad.** El documento se le envía al proveedor: hoy le dice que la empresa
tiene $4.2 M a su favor cuando se le deben $2.3 M.

**Sospecha relacionada.** En `admin.py:3644-3672`, `_build_partner_record_rows`
recibe `effective_balance_map` (el neteo del punto 7 de la fase 2) pero
`_aggregate_partner_record_summary` **no**. Puede que el resumen vaya sin netear
mientras las filas van neteadas.

**Procedimiento.** Verificar primero contra el proveedor 74 en producción; luego
corregir donde nace, para que el Excel y la pantalla del expediente también dejen
de mostrar la cifra engañosa.

**Verificación.** `python -m pytest tests -q` y `python -m scripts.test_neteo`
completos: el cálculo toca el motor de saldos.

### 11. Inventario en móvil — tabla en lugar de tarjetas

**Petición.** *"Mi primo quiere que se vea la tablita en el celular igual que en
la computadora."* Hoy cada material se reapila como tarjeta: ~3 materiales por
pantalla contra ~13 renglones de la tabla.

**Decisión de alcance: solo inventario.** Las tablas de inventario conservan
formato de tabla con scroll horizontal en móvil; las otras ~80 mantienen el
reapilado. La app queda con dos comportamientos en teléfono — es un costo
aceptado a cambio de no deshacer la etapa 0 de la fase 2.

**Cambio.** Excluir del reapilado de `app.js:215` las tablas de
`inventario_list.html` y `inventario_movimientos.html`, probablemente con un
atributo de exclusión explícito para que la excepción quede documentada en el
marcado y no escondida en un selector.

**Verificación.** 390 px y 1440 px, y confirmar que ninguna otra tabla cambió.

**Interlocutor nuevo.** "Mi primo" es quien tiene esta preferencia. Conviene
saber quién decide en materia de UI antes de rehacer trabajo dos veces.

### 12. Bitácora — marcar la entrega por viaje

**Petición.** *"Cerré tres viajes de bote, por eso le puse como tres materiales,
pero solo deja marcar como entregado… todo."*

**Causa.** El estatus vive solo en la cabecera: `LlamadaProveedor.estatus`
(PENDIENTE / ENTREGADO / NO_CONFIRMO). Las líneas
(`LlamadaProveedorMaterial`) no tienen estatus. Su lectura del sistema es correcta.

**Trampa de modelado.** Está usando "material" como sustituto de "viaje": son
tres entregas del **mismo** material. Estatus por línea resuelve su caso hoy,
pero consagra un modelo donde el concepto real —la entrega— no existe. Se elige
la vía barata a sabiendas.

**Cambio.** Migración sobre `llamadas_proveedor_materiales` añadiendo estatus
(y marca de quién/cuándo). El estatus de la cabecera pasa a **derivarse**: todas
entregadas → ENTREGADO; alguna pendiente → PENDIENTE.

**Restricciones de migración.** Debe correr en SQLite y Postgres;
`render_as_batch` no está configurado, así que usar `op.batch_alter_table(...)`
a mano. Cabeza lineal única.

### 13. Capital real — valuar por lista de precios

**Petición.** Que "Base del inventario" permita calcular sobre menudeo, mayoreo,
etc. Hoy el selector (`capital_real.html:80-82`) tiene dos opciones: configuración
manual y promedio de compra.

**Pendiente de definir con ella, y es lo que bloquea:**
- Qué listas exactamente.
- **Qué versión de precio se usa.** Los precios son append-only y versionados;
  la foto de capital real se guarda para comparar días. Si la base salta de
  versión, dos fotos dejan de ser comparables. Hay que elegir entre precio
  vigente al calcular o vigente a la fecha de la foto — y dejarlo escrito en la
  pantalla.

---

## Ola 3 — funcionalidad nueva, aislada

### 14. Generar la comisión al aprobar la nota

**Petición.** Que al autorizar una nota se ofrezca generar la comisión a un
comisionista ya dado de alta, creando la nota de comisión en automático, con
**monto variable** capturado en ese momento, y vinculada al comisionista. La vía
manual actual **se conserva**.

**Por qué va al final y en commit propio.** La aprobación es el único momento en
que la nota produce efectos —inventario, contabilidad, folio y caja— y ocurre en
**una sola transacción** (`note_service.approve_note`). Colgar de ahí la creación
de una `ComisionarioNota` mete una segunda máquina de estados dentro de esa
transacción. Es el único punto del lote que puede corromper dinero si sale mal,
y las suites existentes no lo cubren.

**Decisiones de diseño a cerrar antes de escribir código:**

1. **Si la nota se cancela o se reversa, ¿qué pasa con la comisión?** No puede
   borrarse: sería edición retroactiva. Propuesta por defecto: cancelar la nota
   de comisión con su propio registro compensatorio, nunca un `DELETE`.
   **Confirmar con la clienta.**
2. **Vínculo persistido** entre `Nota` y `ComisionarioNota`: hoy no existe →
   FK nueva → migración (SQLite + Postgres, `batch_alter_table`).
3. **Campos condicionales** en el formulario de aprobación: comisionista y monto,
   ambos opcionales.
4. **Validación del monto.** Ella dice que es variable; definir si hay tope o
   advertencia cuando excede algún porcentaje del total de la nota.
5. `ComisionarioNotaEstado` se almacena **por valor** (mayúsculas) — respetarlo
   en migración y SQL.

**Pruebas propias, obligatorias.** Cubrir al menos: aprobación con comisión,
aprobación sin comisión, cancelación de la nota con comisión viva, y que el
camino manual siga intacto.

---

# Cierre

## Preguntas abiertas con la clienta

| Punto | Pregunta | Bloquea |
|---|---|---|
| §8 Home | ¿El encimado lo ve en teléfono, en computadora o en ambos? | No — se puede reproducir |
| §3 Cuentas | ¿El saldo de apertura también sale del detalle y de los reportes? | No — se hace la lista y se amplía después |
| §13 Capital real | ¿Qué listas, y a precio de hoy o al vigente en la fecha de la foto? | **Sí** |
| §14 Comisión | Si se cancela la nota, ¿qué debe pasar con la comisión ya generada? | **Sí** |

## Verificaciones contra producción, antes de tocar código

1. Proveedor 74: si `ledger_final` coincide con el resumen del expediente.
2. `SELECT id, nombre, direccion FROM sucursales;` — la cadena `"None"`.

## Aceptación

Al desplegar, pedirle que confirme punto por punto desde su teléfono, que es
donde trabaja. Los puntos 5 y 9 no se cierran con código: se cierran con la
sesión de capacitación del módulo de contenedores.
