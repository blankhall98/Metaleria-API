# Auditoría de UI — Fase 2

**Fecha:** 6 de agosto de 2026 · **Método:** captura de las 44 pantallas (roles
super_admin y trabajador) a 1440px y las críticas a 390px, revisión contra
`docs/DESIGN_SYSTEM.md` y contra patrones de referencia de CRMs profesionales
(Stripe Dashboard, Linear, Odoo, Salesforce Lightning). Las capturas viven en
`docs/ui-audit/` con el nombre `<pantalla>--<ancho>.png`.

**Cómo usar este documento:** es el guion del rediseño pantalla por pantalla.
Cada sección tiene función, fortalezas, oportunidades y una propuesta con
prioridad. Se trabaja por orden de prioridad dentro de cada grupo; al terminar
una pantalla se marca aquí y se vuelve a captar.

---

## Estado (actualizado 7-ago-2026, sesión nocturna)

**Fase 0 completa** (commit `05e4292`): tabla canónica de tonos aplicada y
documentada; fechas legibles es-MX de raíz en los filtros; filtro `precio` sin
colas de ceros; IDs internos retirados de listas, defs y sublíneas; cuentas
bancarias recompuestas; "Partners"→"Socios", "Auditar"→"Ver"; comentarios
generados con acento; `toLocaleString('es-MX')`; `inputmode="decimal"`
automático; em dashes.

**Pantallas resueltas después de la fase 0:**

- ✅ **nota-detail** — divulgación progresiva: devoluciones, corrección de pago
  y ambos ajustes plegados (abren solos con retroalimentación); ~4,000→2,800px
  escritorio, ~8,700→5,700px móvil. Bug real corregido de paso: el recálculo
  de precios estaba muerto (`querySelector('.')`).
- ✅ **corte-caja móvil** — el arqueo vuelve a ser grilla (`data-table-mode=
  "grid"`, opt-out nuevo documentado), inputs de 44px.
- ✅ **home admin** — banda clicable, sin alertas duplicadas, actividad
  reciente; el muro de módulos solo vive donde no hay barra lateral fija.
- ✅ **worker-home** — nombre de pila, panel Borradores/En revisión/Aprobadas,
  últimas notas con "Continuar" en borradores.
- ✅ **worker-notes-list** — chips de estado con contadores; el vacío ya no
  repite el CTA del header.
- ✅ **material-precios** — banda Compra/Venta vigente + Margen.
- ✅ **inventario-list** — saldo con drill-down al kardex, kg cero atenuado.
- ✅ **inventario-movimientos** — `anchor_nav` (Resumen·Notas·Ajustes·Kardex).
- ✅ **inventario-valor** — total general en banda + fila de totales.
- ✅ **cuenta-detail** — actividad mensual sin desierto de ceros.
- ✅ **cuenta-scrap360-detail** — captura plegada, header en orden canónico.
- ✅ **partner-record** — estado de cuenta antes que asistencias; header en
  orden canónico (ambos records).
- ✅ **transferencias** — "Disponible en origen: N kg" por renglón con aviso
  de saldo negativo.
- ✅ **materiales-list** — descripción como sublínea, estado solo si inactivo.
- ✅ **login** — línea de auxilio de contraseña. **user-form** — sin doble
  Volver/Cancelar. **perfil** — contraseñas apiladas, tarjeta centrada.
- ✅ **notas-list / directorios** — "Sucursal Sucursal" recortado, acción de
  fila unificada a "Ver", Pagada≠Aprobada en tono.

**Pendientes principales:** worker-nota-nueva (indicador de pasos + total en
dockbar — tocar con calma, el wizard es delicado), corte de caja escritorio
(tres bloques de ceros → uno), contabilidad (fusión de los dos filtros),
banda en clientes/comisionarios/cuentas-scrap360, presets de rango de fechas,
modal propio en vez de `confirm()`, previsualización de foto en perfil.

---

## Temas transversales

Estos patrones se repitieron en los cuatro grupos auditados; conviene
resolverlos **una vez a nivel de sistema** (macro, filtro Jinja o regla CSS) y
no pantalla por pantalla. Ordenados por impacto:

1. **La semántica de color miente en dinero.** Egresos normales en rojo
   (contabilidad, cuenta-scrap360-detail, reporte-saldos), "PAGO DE COMPRA" en
   verde, saldo a favor del cliente en rojo, APROBADA azul en unos módulos y
   verde en otros, Aprobada y Pagada compartiendo verde en notas-list. Fix de
   sistema: una **tabla canónica de tonos por estado y por naturaleza de
   movimiento**, aplicada desde el macro `badge()` y documentada en el design
   system. Rojo = solo problemas (vencido, cancelado, reverso); verde = solo
   confirmación; el dinero que sale con normalidad va en tinta con signo.
2. **Fechas ISO crudas por todo el sistema.** "2026-01-30", "2026-01-14 06:42"
   en notas-list, contabilidad, cuenta-detail, material-precios, inventario…
   Los filtros `datetime_local`/`date_local` existen; falta aplicarlos de
   forma exhaustiva (barrido único por plantillas).
3. **Locale numérico de inputs.** Inputs que muestran "100,000" o "1000,00"
   (coma) junto a lecturas "$1,000.00" (punto) en nota-edit y corte-caja.
   Regla dura: punto decimal + `inputmode="decimal"` + alineación derecha en
   toda captura de kilos/pesos. Toca dinero: prioridad máxima.
4. **IDs internos en pantalla.** Columna ID en 6+ listas, "ID 5" bajo nombres,
   usuario "1" crudo en contabilidad, "#8" junto al folio. El id es de la base
   de datos; el usuario ve folios mono y nombres.
5. **Todo desplegado, nada plegado.** nota-detail (~8,700px móvil con 7
   formularios correctivos abiertos), cuenta-scrap360-detail (captura antes
   que consulta), corte de caja (tres bloques de ceros). El dispositivo
   `.s-collapse` existe y el corte ya lo usa: extenderlo a todo lo correctivo,
   cerrado por defecto, con el "Siguiente paso recomendado" decidiendo qué
   llega abierto.
6. **Bandas y totales ausentes donde el dueño pregunta "¿cuánto?"**
   inventario-valor sin total general, clientes/comisionarios/cuentas-scrap360
   sin banda, listas gemelas asimétricas (proveedores con banda, clientes
   sin). Toda lista de dinero lleva banda con el agregado que la motiva.
7. **Sobrecarga de acciones en headers de records.** 5-6 botones en
   proveedor/cliente-record; el orden canónico se diluye. Patrón: Volver ·
   `⋯` (exports y acciones raras) · primaria.
8. **CTA duplicado en la misma vista.** "Nueva nota" tres veces en el home del
   trabajador; estados vacíos que repiten el botón del header; la cuadrícula
   del home admin duplicando la barra lateral completa. Regla: el estado vacío
   ofrece la acción solo si el header no la tiene a la vista.
9. **Registros espejo sin explicación.** "Sucursal Sucursal Centro" como
   proveedor/cliente en listas y notas — jerga de implementación visible.
   Badge "Vinculado"/"Transferencia" + naming limpio.
10. **Decimales de base de datos.** Precios "$20.00000", placeholders
    "0.00000" — formatear con `money()`/2 decimales en todo lo visible.
11. **Escritorio como móvil estirado.** Formularios de una columna lógica
    (ajustes de inventario, altas de catálogo, perfil) a ancho completo con
    60% de vacío; `.s-narrow` existe y casi nadie lo usa.
12. **Vacíos "-" en vez de em dash**, "ACTIVO" cuando todos están activos,
    columnas TIPO con un solo valor — información cero que quema columnas.
13. **Inglés residual y jerga.** Dock móvil "Partners"; "Auditar" por "Ver";
    "Auto (compra) nota #8" en comentarios generados; "Cancelacion" sin acento
    en copy generado por el sistema.
14. **Saludos y atribuciones con username.** "Hola, qa_worker", TRABAJADOR
    "test_worker" — usar `nombre_completo` con el username como meta.
15. **Diálogos nativos `confirm()`** en las acciones más delicadas; un modal
    propio mantendría tono y trazabilidad. **`toLocaleString('en-US')`** en el
    JS del corte — frágil aunque hoy visualmente idéntico.

---

## Pantallas

<!-- Las secciones por pantalla se integran por grupo: Operación, Socios y
     comisiones, Inventario y catálogo, Finanzas y administración, Trabajador. -->

### Grupo Operación

#### home (Inicio / Resumen de hoy)

- **Función**: Panel de arranque del día — estado de la operación (vencidas, por vencer, saldos, inventario) y lanzadera hacia todos los módulos.
- **Fortalezas**:
  - La banda de indicadores condensa las seis lecturas clave del día en una sola superficie, con el rojo reservado a "Vencidas".
  - Acciones rápidas correctas en el encabezado (+ Compra, + Venta, Corte de caja) — cubren el 90% de lo que se hace al llegar.
  - El lanzador de módulos agrupa por las mismas categorías que la barra lateral, con icono + descripción de una línea.
- **Oportunidades**:
  - La cuadrícula de módulos duplica 1:1 la barra lateral en escritorio (mismos 18 destinos, mismos grupos); en 1440px se pagan ~1,000px de alto por una navegación que ya está siempre visible a la izquierda. En móvil son ~15 pantallas de scroll.
  - La alerta roja "1 nota con pago vencido — Ver vencidas" repite el dato del indicador VENCIDAS que está 20px arriba; el design system prohíbe repetir una cifra visible en la misma página.
  - "POR COBRAR A CLIENTES $0.00" y "NOTAS DE HOY 0" renderizan el cero en negro pleno; un cero saldado debe ir muted (`.s-zero`).
  - El sub del encabezado dice "Usa el menú lateral para llegar a cada módulo" también en móvil, donde no hay menú lateral sino dock y drawer.
  - No hay nada de actividad reciente: el panel dice cuánto, pero no qué pasó (últimas notas, último corte) — el usuario debe saltar a Notas para saber si algo se movió.
- **Propuesta**:
  1. Sustituir la cuadrícula de módulos por un bloque de **actividad reciente** (últimas 5 notas con estado + último corte por sucursal), al estilo del Home de Stripe Dashboard: métricas arriba, movimiento reciente abajo. La lanzadera completa puede quedar solo en móvil (donde sí sustituye a la barra).
  2. Hacer el indicador "Vencidas" clicable (→ notas filtradas) y eliminar la banda de alerta duplicada.
- **Prioridad**: **Alta** — es la primera pantalla de cada sesión y hoy es 80% navegación redundante.

#### notas-list (Control de notas)

- **Función**: Explorador central de notas de compra/venta — filtrar, revisar cobranza y entrar al detalle.
- **Fortalezas**:
  - Banda de indicadores con "Total selección · Suma de la vista filtrada" — la suma viva del filtro es una idea excelente y poco común.
  - La tabla fusiona bien folio+fecha y estado+pago en columnas compuestas; el menú `⋯` por fila evita la columna de acciones interminable.
  - Tabs de estado con contadores (Todas 7 · En revisión 0 · Aprobadas 5) + orden Recientes/Antiguas — triage rápido.
- **Oportunidades**:
  - **Aprobada y Pagada comparten el verde** en la misma celda — exactamente el ejemplo que el design system marca como prohibido ("dos estados nunca comparten tono"). Pagada debería distinguirse (p. ej. verde sólo Aprobada; Pagada en badge muted con ✓ o tono info).
  - La columna SEGUIMIENTO imprime fechas ISO crudas ("2026-01-30") en vez de `date_local`; conviven "188 DÍAS VENCIDA" (pill roja), "Devolución registrada" y "Sin seguimiento" — tres vocabularios en una columna.
  - El partner de transferencias sale como "Sucursal Sucursal Centro" — jerga interna que rompe la lectura (debería ser "Transferencia → Sucursal Centro" o similar).
  - La alerta roja bajo los indicadores vuelve a repetir el dato de VENCIDAS (mismo patrón que home).
  - "Total selección" (etiqueta) vs "Suma de la vista filtrada" (sub): dos nombres para lo mismo; el filtro se llama "vista", el KPI "selección".
  - En móvil, el toggle "Ver 4 campos más" aparece **antes** de Partner/Estado/Total — el pliegue queda arriba del contenido principal de la tarjeta; y los dos botones administrativos (+Venta/+Compra administrativa) ocupan el primer viewport siendo acciones raras de super admin.
- **Propuesta**:
  1. Rediseñar la celda de estado como badge doble con tonos distintos (Aprobada `ok` / Pagada `muted`+icono, Pendiente `warn`, Cancelada `bad`) y normalizar SEGUIMIENTO a "Vence {date_local}" + pill sólo cuando está vencida — patrón de columnas de estado de Linear (un tono por dimensión).
  2. En móvil, mover "Ver N campos más" al pie de la tarjeta y demover las altas administrativas al menú `⋯` del encabezado.
- **Prioridad**: **Alta** — es la pantalla más usada por admins y concentra las inconsistencias de tono/fecha.

#### nota-detail (Detalle de nota)

- **Función**: Expediente completo de una nota — revisión, pagos, devoluciones, ajustes e historial con trazabilidad.
- **Fortalezas**:
  - `anchor_nav` como índice honesto (Saldo, Devoluciones, Ajustes, Historial) — correcto para un expediente largo.
  - Banda financiera clara (Monto pagado / Saldo pendiente / Saldo a favor) y defs de contexto completos (sucursales contable/inventario, vencimiento, pagos revertidos).
  - Los paneles de impacto estimado ("Esta acción solo modifica el saldo efectivo…") explican consecuencias antes de ejecutar — excelente para dinero.
- **Oportunidades**:
  - **Todo está desplegado siempre**: en una nota PAGADA y en cero se renderizan abiertos los formularios de devolución parcial, devolución total, corregir pago inicial, nuevo abono, referencia de pago, ajuste de saldo y ajuste manual — ~4,000px en escritorio y **~8,700px en móvil** (≈10 pantallas). El patrón insignia "Siguiente paso recomendado" queda enterrado.
  - "Corregir pago inicial" y "Registrar nuevo abono" son dos formularios gemelos lado a lado con los mismos campos — el ojo no distingue cuál es el flujo normal (abono) y cuál la excepción (corrección).
  - Tres clústers de pills "0 REGISTROS · 0 ACTIVOS · 0 REVERSIONES" meten metadata de auditoría vacía al nivel visual de los títulos.
  - Zeros en negro pleno en la banda financiera ("$0.00" de Saldo pendiente/a favor) en vez de muted.
  - TRABAJADOR muestra "test_worker" (username interno) junto a "Trabajador UNO"; VENCE imprime ISO "2026-01-30".
  - "Revertir pago" en rojo outline en cada renglón del historial de abonos — tres botones destructivos permanentes para una acción excepcional.
- **Propuesta**:
  1. **Divulgación progresiva**: dejar visible resumen + materiales + pagos (historial), y plegar todo lo correctivo (devoluciones, correcciones, ajustes) en secciones `.s-collapse` cerradas por defecto — el mismo dispositivo que ya usa corte de caja. La tarjeta "Siguiente paso recomendado" decide qué sección llega abierta. Referencia: el detalle de un Payment en Stripe — resumen y timeline arriba, acciones excepcionales detrás de un menú.
  2. Fundir "Corregir pago inicial" dentro del historial (acción por renglón del pago inicial) y dejar un solo formulario visible: "Registrar abono".
  3. Mover "Revertir pago" al menú `⋯` de cada renglón.
- **Prioridad**: **Alta** — es el corazón del producto y hoy su costo de scroll castiga justo al flujo diario (revisar → aprobar → cobrar).

#### nota-edit (Edición super admin)

- **Función**: Corrección de una nota aprobada por super admin — kilos, precios y kg reales de inventario, con impacto contable.
- **Fortalezas**:
  - Workspace de dos columnas con riel derecho pegajoso: impacto (Monto pagado/Saldo) + comentario obligatorio de edición + acciones — el patrón correcto para editar con consecuencias.
  - El helper "Si cambias neto, el descuento se balancea automáticamente" anticipa el comportamiento del recálculo.
  - "Kg reales inventario — Interno. No aparece en la orden PDF" explica bien la asimetría kg_neto/kg_real.
- **Oportunidades**:
  - Los inputs numéricos muestran "100,000" (coma) para 100 kg mientras la banda dice "100.00 kg" — dos locales de número en la misma pantalla; en es-MX la coma lee como millar.
  - La tabla de materiales aprieta 8 columnas con selects e inputs: el select de precio se trunca ("gula…" de Regular) y los helpers dentro de celda agrandan las filas.
  - FOLIO muestra "-" con sub "Solo superadmin" — un guion como valor y una nota de permiso como subtítulo confunden (el folio no existe hasta aprobar; dígalo: "Se asigna al aprobar").
  - Tres puertas a ajustes compiten: botón "Ajuste manual" en el header, tarjeta "Abrir ajuste manual de inventario" y "Abrir ajuste de saldo" en el riel.
  - Zeros en negro en la banda ("$0.00", "SALDO A FAVOR $0.00").
- **Propuesta**:
  1. Normalizar formato numérico de inputs a punto decimal + `inputmode="decimal"` y alinear a la derecha con tabulares (regla dura para toda captura de kilos/pesos).
  2. Sacar los helpers de las celdas a una sola línea bajo la tabla y dar ancho mínimo real a Precio/Kg (la tabla puede perder KG DESC en pantallas medianas plegándolo al modo compacto).
- **Prioridad**: **Media** — pantalla de excepción bien resuelta en estructura; lo urgente es el locale numérico porque toca dinero.

#### nota-evidencias (Evidencias de la nota)

- **Función**: Galería de evidencia fotográfica por subpesaje + fotos extra, con completitud por material.
- **Fortalezas**:
  - Encabezado defs con "Evidencias 2/2 · Pendientes 0 · Fotos extra 4" — la completitud se lee de un vistazo.
  - Badges "CON EVIDENCIA" por pesaje y "COMPLETO" por material — el estado correcto en el lugar correcto.
  - Tarjetas de pesaje con bruto/descuento y CTA "Agregar evidencia" consistente.
- **Oportunidades**:
  - En la galería adicional, cada miniatura carga un botón "Ver" + timestamp idéntico repetido 4 veces — ruido; la miniatura debería ser clicable completa.
  - "Máx. 8 MB por imagen" aparece bajo una tarjeta sí y otra no.
  - "TOTAL: 4" como chip flotante duplica "Fotos extra 4" del encabezado.
  - Los timestamps van en gris pleno "2026-01-10 13:08" — legibles, pero como caption deberían ser `--s-text-muted` micro.
- **Propuesta**:
  1. Hacer la miniatura entera el enlace (lightbox o nueva pestaña), quitar "Ver", y dejar el timestamp como caption muted — patrón de galería de adjuntos de Linear/Notion.
- **Prioridad**: **Baja** — pantalla ya funcional y ordenada; son remates.

#### nota-compra-admin (Compra administrativa)

- **Función**: Alta manual de una nota de compra por super admin, sin báscula, que queda en borrador para revisión.
- **Fortalezas**:
  - Estructura por fieldsets clara (operación → materiales → comentarios/evidencia) con helpers específicos por campo.
  - El pie fija expectativas: "Este registro quedará en estado borrador y será visible para revisión."
  - El submit se deshabilita hasta que el formulario es válido.
- **Oportunidades**:
  - Con un solo material vacío, "TOTAL DE ESTE MATERIAL" y "TOTAL DE LA NOTA" muestran dos bloques idénticos de ceros en negro — ocho $0.00/0.00 fuertes antes de capturar nada.
  - Dos botones destructivos rojos visibles ("Eliminar material", "Eliminar" del subpesaje) en un formulario recién abierto con un único material que no se puede quedar sin nada.
  - El submit deshabilitado es un botón primario gris a todo lo ancho al fondo de ~2,000px — no dice qué falta para habilitarse.
  - "Tipo de precio: Regular" aparece como select deshabilitado sin explicación de por qué no es editable aquí.
- **Propuesta**:
  1. Mostrar "Total de la nota" solo cuando hay ≥2 materiales (o fundir ambos totales en uno) y renderizar ceros muted hasta que existan kilos.
  2. Ocultar "Eliminar material" cuando solo hay uno, y al deshabilitar el submit acompañarlo de la razón ("Faltan proveedor y al menos un pesaje") — patrón de formularios de Odoo.
- **Prioridad**: **Media** — flujo poco frecuente pero es la cara del super admin; el costo de arreglo es bajo.

#### nota-venta-admin (Venta administrativa)

- **Función**: Alta manual de una nota de venta, con la opción de registrar la venta contra un cliente o contra un proveedor-que-también-compra (neteo).
- **Fortalezas**:
  - El par "En venta, registrar como" + helper del neteo ("…si su saldo debe netearse en su mismo registro") explica una regla de negocio difícil en una línea.
  - Misma columna vertebral que la compra administrativa — cero costo de aprendizaje entre ambas.
- **Oportunidades**:
  - Hereda todo lo de compra-admin (totales duplicados en cero, eliminars rojos, submit gris sin razón).
  - "Cliente comprador" es a la vez label, placeholder del select y texto del helper — tres veces la misma frase en 60px.
  - El label "Kg reales de inventario" queda huérfano visualmente del input (el bloque se separa del fieldset de pesajes).
- **Propuesta**:
  1. Aplicar el mismo paquete de fixes de compra-admin (una sola fuente de totales, destructivos contextuales, submit con razón).
  2. Diferenciar placeholder del label ("Selecciona un cliente…").
- **Prioridad**: **Media** — mismos fixes que compra: hacerlos juntos.

#### corte-caja (Corte de caja — estado sin abrir)

- **Función**: Conciliación diaria de efectivo por sucursal: abrir caja con saldo inicial, registrar el día y cerrar con arqueo.
- **Fortalezas**:
  - La página de 3 estados (sin abrir → abierto → cerrado) con chip de estado "SIN ABRIR" y saldo inicial heredado del corte anterior ya trae la lógica correcta.
  - El arqueo por denominaciones con totales vivos es el corazón operativo bien resuelto en escritorio (dos tablas compactas lado a lado).
  - El aviso "Primero revisa y cuenta. Cuando termines, abre la caja" ordena el flujo.
- **Oportunidades**:
  - Los ceros se repiten **tres veces** en la misma pantalla: banda de indicadores (Cobros/Pagos/Neto), resumen del arqueo (Monedas/Billetes/Total) y bloque final (Cobros/Pagos/Neto/Conteo) — y dentro de la banda el estilo es inconsistente (tres $0.00 en negro, "Neto" en gris).
  - El input de saldo muestra "1000,00" con coma — otra vez el locale — junto a "SALDO INICIAL SUGERIDO $1,000.00" con formato correcto.
  - **Móvil**: el restack genérico convierte cada denominación en una tarjeta (CANTIDAD/TOTAL apilados) → 13 tarjetas y ~2,300px solo de arqueo; contar efectivo con el teléfono en la mano es EL caso de uso y hoy es el peor caso de scroll.
  - El botón "Ir" del selector de fecha es un misterio de dos letras; y la fecha va en input ISO nativo.
  - El arqueo completo se muestra antes de abrir la caja compitiendo con el CTA "Abrir caja" (que queda arriba, lejos del conteo que se pide hacer primero).
- **Propuesta**:
  1. **Excluir la tabla de denominaciones del restack móvil** y darle una grilla propia de 3 columnas fijas (denominación · cantidad · total) con inputs `inputmode="numeric"` grandes — una fila por denominación cabe perfectamente en 390px. Es el fix de mayor impacto operativo de toda la auditoría móvil.
  2. Un solo bloque de totales (el `.s-dockbar` flotante que ya existe en el corte abierto puede cargar Neto + Conteo también en este estado) y ceros muted.
  3. Unificar locale numérico del input de saldo inicial.
- **Prioridad**: **Alta** — flujo diario obligado, con dinero físico, y el móvil está castigado justo donde más se usa.

### Grupo Trabajador

#### worker-home (`/web` como trabajador)

- **Función:** punto de entrada del trabajador: capturar una nota o revisar las
  enviadas.
- **Fortalezas:** jerarquía clara (eyebrow + saludo + una línea de propósito);
  las dos tarjetas dicen exactamente a qué van; el dock móvil repite las mismas
  dos rutas, así el pulgar nunca viaja lejos.
- **Oportunidades:**
  - "Nueva nota" aparece tres veces en la misma vista (botón primario del
    header, tarjeta "Nueva nota", pestaña "Nueva" del dock): la tarjeta es
    redundante con el botón que tiene encima.
  - En 1440px la pantalla son dos tarjetas pequeñas flotando en un lienzo
    vacío — el escritorio es un móvil estirado.
  - El saludo usa el username crudo ("Hola, qa_worker") en vez del nombre de
    pila; es la pantalla que el trabajador ve todos los días.
  - No hay ningún dato vivo: cuántas notas tiene en borrador, cuántas le
    devolvieron a corrección — el trabajador entra a ciegas.
- **Propuesta:** convertir el home en un **panel de estado**: banda `s-stats`
  compacta (Borradores, En revisión, Devueltas) + lista de las últimas 3-5
  notas con su estado + un solo CTA primario. Patrón "inbox primero" de
  Linear: lo pendiente arriba, la acción a un clic.
- **Prioridad:** alta — es la pantalla diaria del rol con más usuarios.

#### worker-notes-list (`/web/worker/notes`)

- **Función:** historial de notas del trabajador con estado y total.
- **Fortalezas:** estado vacío ejemplar (icono, qué pasó, cómo empezar);
  header con la acción a la derecha como dicta el sistema.
- **Oportunidades:**
  - El estado vacío repite "Nueva nota" cuando el mismo botón está visible
    en el header — dos llamadas idénticas a un scroll de distancia.
  - Sin filtros por estado: cuando el historial crezca, encontrar "las que me
    devolvieron" costará scroll (el admin sí tiene filtros en su lista).
- **Propuesta:** chips de filtro por estado (`Borrador · En revisión ·
  Aprobada · Devuelta`) usando el rail móvil `s-rail` ya existente; retirar el
  CTA duplicado del estado vacío cuando el header lo muestra.
- **Prioridad:** media — hoy el volumen es bajo, pero es fricción que crece
  con los datos.

#### worker-nota-nueva (`/web/worker/notes/nueva`)

- **Función:** el corazón del rol: capturar una nota de pesaje con materiales,
  subpesajes y evidencia.
- **Fortalezas:** captura por tarjetas de material con neto autocalculado;
  totales vivos por material y por nota; ayuda contextual bajo cada control;
  el envío queda bloqueado mientras hay subidas en curso.
- **Oportunidades:**
  - "Volver" es un botón a todo lo ancho arriba del formulario: ocupa el lugar
    visual de una acción principal y empuja el contenido; en el sistema el
    regreso vive en el header.
  - El wizard es conceptualmente de 4 pasos pero se presenta como un solo
    scroll largo sin indicador de progreso; en móvil no se sabe cuánto falta.
  - "Guardar borrador" deshabilitado al fondo es casi invisible (gris sobre
    gris) y no explica qué falta para habilitarse.
  - El placeholder "Ej. 150" se corta a "Ej. 15" en 390px.
  - Totales por material + totales de la nota duplican cuatro lecturas cada
    uno y alargan la página; el total de la nota bastaría fijo en un
    `s-dockbar` (el componente ya existe para el corte de caja).
- **Propuesta:** indicador de pasos arriba (1 Operación · 2 Materiales · 3
  Comentarios · 4 Evidencia), regreso al header, y el **total de la nota en
  barra flotante** `s-dockbar` con el botón de guardar siempre visible y su
  estado explicado ("Completa el material 1 para guardar"). Es el patrón de
  checkout de punto de venta: el total y la acción nunca se van del viewport.
- **Prioridad:** alta — es la pantalla de captura de todo el dinero que entra.

### Grupo Socios y comisiones

#### proveedores-list

- **Función**: Directorio de proveedores con saldo neto y acceso a su historial.
- **Fortalezas**:
  - La banda de indicadores (Total / Compra y venta / Con saldo neto) lee como un solo instrumento y el punto de tono azul en "Con saldo neto" comunica sin gritar.
  - Filtros en una sola fila con jerarquía correcta (búsqueda ancha + sucursal + modo + Filtrar) y un solo `.btn-primary` en el header.
  - Tabla limpia: saldo alineado a la derecha en tabular, `$0.00` en muted y `ACTIVO` como badge tintado.
- **Oportunidades**:
  - La columna ID gasta un ancho de columna para un dato interno (3, 1, 2 desordenados); el design system ya lo esconde en móvil — en escritorio también es ruido.
  - "Sucursal Sucursal Centro" como nombre de proveedor delata registros espejo generados por el sistema; sin una insignia que explique su origen, el usuario los lee como basura de datos.
  - Columnas Placas y Correo casi siempre vacías ("-") pero ocupan un tercio del ancho; la densidad informativa de la fila es baja.
  - El botón "Historial" + menú `⋯` por fila está bien, pero "Historial" es el término de la tabla mientras el header del record dice "Estado de cuenta": mismo concepto, dos nombres.
  - El vacío bajo la tabla (media pantalla) hace ver la página inacabada con pocos registros; no hay resumen de saldos agregados (¿cuánto debo en total?).
- **Propuesta**:
  1. Fundir Teléfono/Correo en una columna "Contacto" de dos líneas y quitar ID y Placas del escritorio (quedan en el record) — patrón Stripe Customers: menos columnas, cada una con contenido real.
  2. Añadir a la banda un indicador de dinero ("Por pagar total: $1,800.00") — es la cifra que un dueño busca al abrir esta lista.
  3. Etiquetar los registros espejo con un badge `muted` "Vinculado" y explicar el vínculo en el record.
- **Prioridad**: media — la pantalla funciona; las mejoras son de densidad y vocabulario.

#### proveedores-list (390px)

- **Función**: La misma lista en teléfono, con filas restacadas como tarjetas.
- **Fortalezas**:
  - El restack automático funciona: título, "Ver 3 campos más", saldo y estado con acciones al pie.
  - Filtros plegados por defecto — los datos son lo primero en pantalla.
- **Oportunidades**:
  - La banda de KPIs parte 2+1: "Con saldo neto" queda como celda viuda a ancho completo con enorme aire — la banda pide un patrón 3-en-línea compacto en móvil o priorizar solo las 2 lecturas útiles.
  - La tarjeta del proveedor abre con "Ver 3 campos más" antes que el dato clave: el saldo debería ser lo primero bajo el nombre (marcar `data-mobile-primary` correcto).
  - El dock tapa media tarjeta en el punto de scroll capturado; el ritmo de tarjetas altas + botón "Historial" + `⋯` hace la lista larga para 3 registros.
- **Propuesta**:
  1. En móvil, banda de estadísticas como fila única desplazable o 3 columnas compactas (valor 17px) — el patrón "stat strip" de Linear Mobile.
  2. Subir Saldo neto a línea secundaria del título de la tarjeta y dejar "Ver campos más" para contacto/placas.
- **Prioridad**: media.

#### proveedor-form

- **Función**: Alta de un proveedor con contacto, sucursal de origen y placas.
- **Fortalezas**:
  - Fieldsets Identidad/Operación con leyendas micro-caps: estructura clara, helper text consistente en cada campo.
  - El toggle "Permitir ventas directas" en subpanel hundido con explicación honesta del neteo — divulgación progresiva bien hecha.
  - Acciones al pie en el orden canónico (Cancelar · Crear proveedor).
- **Oportunidades**:
  - Tres columnas de inputs a 1440px estiran el formulario a ~1100px de ancho; el ojo salta de Nombre a Correo cruzando media pantalla. Un alta de catálogo es una tarea de lectura vertical.
  - El campo Placas con botón "Agregar" no muestra dónde aterrizan las placas agregadas (no hay chips visibles vacíos ni ejemplo) — el usuario descubre el patrón a prueba y error.
  - "Volver" en el header y "Cancelar" en las acciones conviven — el design system pide uno u otro por pantalla.
- **Propuesta**:
  1. Limitar el formulario a `.s-narrow` (60rem) o a 2 columnas máx — patrón Odoo/Salesforce de formularios de catálogo: columna de lectura, no sábana.
  2. Renderizar el área de chips de placas siempre visible (vacía con placeholder "Sin placas registradas") para que "Agregar" tenga destino visual.
- **Prioridad**: media-baja — se usa poco, pero es la primera impresión al dar de alta.

#### proveedor-record

- **Función**: Expediente completo del proveedor: saldos, asistencias, estado de cuenta, ajustes, notas y pagos.
- **Fortalezas**:
  - La banda financiera (Compras aprobadas / Pagado / Saldo pendiente / A favor / Ajustes) responde de un vistazo la pregunta central: ¿cuánto le debo?
  - `anchor_nav` (Estado de cuenta · Asistencias · Notas · Pagos) es el índice honesto que la página de este largo necesita.
  - El estado de cuenta con Cargo/Abono/Saldo acumulado y folios en chips mono es contabilidad legible; "Saldo acumulado (por pagar al proveedor)" traduce el signo a lenguaje llano.
- **Oportunidades**:
  - La página apila seis tarjetas de sección a altura completa (~3000px); Asistencias — la sección menos financiera — está antes que Estado de cuenta, el corazón del expediente.
  - "Ajuste manual de saldo" (acción correctiva de super admin) vive entre dos secciones de lectura, con el mismo peso visual que ellas; invita a usarlo como si fuera rutina.
  - En Estado de cuenta la columna Cuenta repite "Cuenta 1 - Proveedor Uno | BBVA" en cada fila — 8 repeticiones idénticas; Método repite "transferencia" en minúscula (enum crudo).
  - "Cancelacion nota #6" sin acento en un comentario generado por el sistema.
  - El toggle "Recientes primero / Cronológico" parece tab pero es orden — dos pills donde una etiqueta "Ordenar:" + select sería inequívoco.
  - Historiales de notas y pagos duplican información ya visible en el estado de cuenta (mismos folios, mismos montos) — tres tablas cuentan la misma historia.
- **Propuesta**:
  1. Reordenar: banda → datos → **Estado de cuenta** → Notas → Pagos → Asistencias → Ajuste manual al final tras un separador con copy de advertencia — jerarquía tipo Stripe Customer: balance primero, actividad después, zona administrativa al fondo.
  2. Colapsar la columna Cuenta a un tooltip/segunda línea del método ("Transferencia · BBVA") y capitalizar el método con vocabulario humano.
  3. Convertir Historial de pagos en pestaña visual dentro de la misma tarjeta que Notas (dos `anchor_nav` internos) o al menos comprimirlo — la página pierde fuerza por repetición.
- **Prioridad**: alta — es la pantalla más consultada del módulo de relaciones y hoy exige demasiado scroll para contar una historia repetida.

#### proveedor-record (390px)

- **Función**: El mismo expediente en teléfono.
- **Fortalezas**:
  - Todas las secciones sobreviven el restack; las tarjetas del estado de cuenta llevan "Ver N campos más" y los montos se conservan alineados.
  - Los botones del header pasan a dos filas envueltas sin romperse.
- **Oportunidades**:
  - ~6,700px de alto: el estado de cuenta de 8 movimientos se vuelve 8 tarjetas de ~150px cada una y los tres historiales lo triplican; encontrar "Pagos" exige un scroll heroico aunque el `anchor_nav` ayude.
  - Cinco botones de header (Volver, Exportar PDF, Exportar Excel, Cuentas, Editar) empujan el contenido bajo el fold antes de mostrar un solo dato.
  - Las tarjetas del ledger abren con FECHA como primer campo y esconden Cargo/Abono tras "Ver más campos" en algunas filas — el dato monetario debe ser el primario en móvil.
- **Propuesta**:
  1. En móvil, mover Exportar PDF/Excel y Cuentas a un menú `⋯` del header (overflow pattern de Material) dejando Volver + Editar visibles.
  2. Marcar `data-mobile-primary` en Cargo/Abono/Saldo del ledger para que cada tarjeta muestre el movimiento completo sin desplegar.
- **Prioridad**: alta (comparte causa con el record de escritorio).

#### clientes-list

- **Función**: Directorio de clientes con saldo y vínculos con proveedores.
- **Fortalezas**:
  - Consistencia total con Proveedores: mismos filtros, misma tabla, mismo lenguaje — cero costo de aprendizaje.
  - Un solo `.btn-primary` y sub del header que explica la particularidad del módulo (clientes vinculados).
- **Oportunidades**:
  - Sin banda de indicadores: Proveedores la tiene y Clientes no — asimetría injustificada entre pantallas gemelas (¿total de clientes? ¿por cobrar?).
  - Mismos problemas heredados: ID visible, Placas/Correo vacíos, "Sucursal Sucursal Sur" como nombre espejo sin explicación.
  - El módulo dice en el sub "clientes vinculados con un proveedor" pero la tabla no tiene ninguna columna/badge de vínculo — la promesa del sub no se cumple visualmente.
- **Propuesta**:
  1. Banda de 3 lecturas espejo de Proveedores: Total / Vinculados / Por cobrar — simetría entre los dos directorios.
  2. Badge `info` "Vinculado" en filas con relación proveedor⇄cliente.
- **Prioridad**: media.

#### cliente-record

- **Función**: Expediente del cliente: ventas, cobros, saldo y su eventual vínculo como proveedor.
- **Fortalezas**:
  - Estructura idéntica al record de proveedor — el aprendizaje se transfiere.
  - El ledger muestra reversos y devoluciones con folio y comentario ("Reverso pago nota #2"): trazabilidad contable visible.
  - "Vincular como proveedor" en el header expone la capacidad distintiva del módulo.
- **Oportunidades**:
  - Seis acciones en el header (Volver, PDF, Excel, Cuentas, Vincular, Editar) — el orden canónico del design system se diluye y "Vincular como proveedor" (acción de configuración rara) pesa igual que Editar.
  - Banda con cuatro `$0.00` consecutivos: correcto que sean muted, pero una banda entera en cero para un cliente cancelado no dice nada — falta un estado narrativo ("Sin operaciones vivas; su única nota fue cancelada").
  - `-$500.00` en rojo en la columna Saldo: es saldo a favor del cliente, no un error — viola "una cifra negativa no es un problema" del design system.
  - "Cancelacion nota #2" sin acento (mismo bug de copy generado que en proveedor).
  - Historial de notas de una fila y de pagos de una fila: dos tarjetas completas con buscador para un registro — el buscador debería aparecer a partir de N filas.
- **Propuesta**:
  1. Podar el header a Volver · `⋯`(PDF/Excel/Cuentas/Vincular) · Editar.
  2. Regla de ledger: negativos a favor del socio en azul/neutral con sufijo "(a favor)", nunca rojo — como Stripe muestra los créditos.
  3. Ocultar buscadores de historial cuando hay &lt;5 filas.
- **Prioridad**: alta (comparte el patrón de record con proveedor; corregir ambos a la vez).

#### comisionarios-list

- **Función**: Directorio de comisionistas con acceso a sus notas y cuentas.
- **Fortalezas**:
  - Reusa el patrón de directorio al pie de la letra; sub del header explica el alcance ("notas, cuentas e historial").
  - La segunda línea bajo el nombre ("Impacta saldos de Sucursal Centro") anticipa la consecuencia contable — buen copy.
- **Oportunidades**:
  - Sin indicadores: ¿cuánto se debe en comisiones globalmente? Es la pregunta del dueño y la lista no la responde.
  - Columna Correo con "-" para el único registro; ID de nuevo visible.
  - Tres acciones por fila (Historial, Notas, `⋯`) para una entidad con dos vistas — "Historial" y "Notas" compiten sin jerarquía clara; el récord ya contiene las notas.
  - Una sola fila en un lienzo de 900px — el vacío pide al menos el estado "1 comisionista activo · $190.00 pendiente".
- **Propuesta**:
  1. Banda con Total comisionistas / Comisiones pendientes ($) / Pagado en el período.
  2. Una sola acción primaria por fila ("Ver") y el resto al `⋯` — patrón Linear: la fila entera es el enlace.
- **Prioridad**: media.

#### comisionario-record

- **Función**: Expediente del comisionista: saldo de comisiones, notas, registro de pagos.
- **Fortalezas**:
  - Banda de 5 lecturas con la historia completa (comisiones/pagado/pendiente/a favor); "Registrar pago" explica la aplicación FIFO ("se aplica a las notas más antiguas") — transparencia de regla de negocio ejemplar.
  - Empty state de pagos con icono, título y recuperación ("Usa el formulario de arriba…") — de libro.
  - El formulario de pago encadena método → cuenta con helper "Obligatoria para transferencia o cheque".
- **Oportunidades**:
  - "Nota comision" sin acento en el ledger (enum crudo en pantalla).
  - El ledger de un movimiento y el Historial de notas de una fila repiten $190.00 tres veces (banda, ledger, historial) en la misma vista — redundancia que la regla "nunca repitas una cifra visible" del design system prohíbe.
  - "Registrar pago" está después del historial de notas pero antes del historial de pagos; el flujo natural (ver deuda → pagar → ver pagos) queda interrumpido por el buscador de notas.
  - La columna NOTA muestra "#1" pelón — sin el folio mono que el resto del sistema usa.
- **Propuesta**:
  1. Reordenar: banda → datos → Registrar pago (es LA acción del expediente) → Notas → Pagos — como la pantalla de "Pay invoice" de Odoo, la acción cerca del saldo que la motiva.
  2. Normalizar los tipos de movimiento a vocabulario humano acentuado ("Nota de comisión", "Pago").
- **Prioridad**: media-alta — el módulo cobra importancia en fase 2.

#### comisionario-notas

- **Función**: Lista global de notas de comisión con estado y saldo.
- **Fortalezas**:
  - Tabla mínima correcta: estado como badge, montos tabulares a la derecha, `$0.00` muted.
  - Filtros reducidos a lo que importa (número + comisionista).
- **Oportunidades**:
  - "ID" como columna y "#1" como formato — inconsistente con los folios mono del resto (01_C_4); las notas de comisión merecen folio propio.
  - Sin indicadores ni totales: la lista no suma lo pendiente que muestran sus filas.
  - "Ver" como única acción hace que la fila entera pida ser clickeable (hoy solo el botón lo es).
  - El vacío de 500px bajo una fila única — misma observación que comisionarios-list.
- **Propuesta**:
  1. Folio mono para notas de comisión (p. ej. `COM_1`) — consistencia con el sistema de folios.
  2. Fila de totales al pie de la tabla (Total/Pagado/Saldo) — patrón de las tablas contables de Odoo.
- **Prioridad**: baja-media.

#### comisionario-nota-form

- **Función**: Alta manual de una nota de comisión con materiales, kg y precio.
- **Fortalezas**:
  - La tabla de renglones con SUBTOTAL calculado en vivo y "Eliminar" por fila sigue el patrón del wizard de notas.
  - Helper de sucursal contable ("Se asigna automáticamente desde el comisionario…") anticipa el efecto en saldos.
- **Oportunidades**:
  - El bloque de totales es un panel gris con cifras sin etiquetas visibles alineadas a la derecha (0.00 / $0.00) — kg y dinero comparten formato visual y pueden confundirse a primera vista.
  - El renglón de material nace con un solo row y el botón para agregar más no es visible junto a la tabla (queda fuera del área visible del formulario) — el patrón "Agregar renglón" debe vivir pegado a la tabla.
  - "Eliminar" rojo con icono en el único renglón existente: destructivo deshabilitado sería más honesto cuando solo hay una fila.
  - PRECIO/KG con seis decimales (1.00000 en el detail) sugiere que este form acepta precisión que el negocio no usa.
- **Propuesta**:
  1. Etiquetar los totales ("Kg totales" / "Total a pagar") con el patrón defs() y separar tipográficamente kg (con sufijo kg) de dinero.
  2. Botón `btn-outline-primary` "Agregar material" inmediatamente bajo la tabla, ancho completo en móvil — como los line-items de facturas en Stripe Invoicing.
- **Prioridad**: media.

#### comisionario-nota-detail

- **Función**: Detalle de una nota de comisión: totales, renglones y registro de abonos.
- **Fortalezas**:
  - Banda Total/Pagado/Saldo — la lectura mínima correcta para un documento por pagar.
  - Renglones con kg en sufijo y montos alineados; empty state de pagos con recuperación clara.
  - Header con acciones bien jerarquizadas (Volver / Ver comisionario / Exportar PDF).
- **Oportunidades**:
  - PRECIO/KG muestra "1.00000" — cinco decimales para un precio de $1.00; formatear a 2 decimales salvo que exista precisión real.
  - El formulario "Agregar pago" es idéntico al de comisionario-record pero con layout distinto (aquí 4 columnas, allá 4+comentario full-width) — dos implementaciones del mismo componente.
  - ESTADO "APROBADA" en badge azul aquí, mientras las notas de pesaje usan verde para aprobada — el mismo estado con dos tonos en módulos hermanos contradice la regla "dos estados nunca comparten tono… y un estado nunca cambia de tono".
  - La fecha "2026-01-21 14:42" aparece como def pero el resto del sistema formatea fechas locales legibles — verificar filtro `datetime_local`.
- **Propuesta**:
  1. Unificar el bloque "Registrar/Agregar pago" como componente único (mismo orden de campos, mismo copy) entre record y nota.
  2. Tabla de tonos por estado documentada y aplicada: APROBADA=ok en todo el sistema.
- **Prioridad**: media.

### Grupo Inventario y catálogo

#### materiales-list

- **Función**: Catálogo base de materiales que alimenta notas, inventario, conversiones y precios.
- **Fortalezas**:
  - La banda de KPIs (Total / Activos / Inactivos) es sobria, con el punto de tono verde en "Activos" y subtítulos que explican cada cifra.
  - La tabla usa micro-caps en encabezados, zebra y acciones consistentes ("Precios", "Editar", menú `⋯`).
  - El sub del encabezado explica el rol del catálogo en una frase — buen copy.
- **Oportunidades**:
  - El ID se repite bajo cada nombre ("ID 5") aportando poco; en un catálogo de 5 renglones el ID es ruido que compite con el nombre.
  - La columna "Descripción" repite casi literalmente el nombre ("Arena" → "Arena para construcción."): densidad sin información.
  - El estado "ACTIVO" en verde en 5 de 5 renglones no discrimina nada; cuando todos comparten estado, la columna es ruido visual.
  - Los 3 KPIs son triviales (5/5/0) y ocupan una banda entera; "Inactivos 0" es una lectura vacía.
  - No hay búsqueda ni orden visible; con catálogos reales (30+ materiales) la tabla no escala.
- **Propuesta**:
  1. Colapsar "Estado" a un badge solo cuando sea `Inactivo` (patrón Linear: el estado por defecto no se pinta) y fundir descripción como línea secundaria bajo el nombre, liberando una columna para "Unidad".
  2. Sustituir la banda de KPIs por una sola lectura útil ("5 materiales · 5 activos") integrada al encabezado de la tarjeta, o enriquecer los KPIs con datos operativos (kg en inventario por material, notas del mes que lo usan).
  3. Añadir buscador inline (patrón Odoo/Stripe: filtro de texto siempre visible en catálogos).
- **Prioridad**: media — pantalla de baja frecuencia, pero es la puerta a precios (crítico del negocio).

#### material-precios

- **Función**: Historial de versiones de precio por operación y tipo de cliente para un material.
- **Fortalezas**:
  - El título contextual "Precios · Varilla 3/8"" con la unidad en el sub ubica perfectamente.
  - Precio alineado a la derecha con tabulares; "VIGENTE" en badge verde correcto (estado notable).
  - Orden de acciones del header correcto: Volver → primaria.
- **Oportunidades**:
  - `$20.00000` con cinco decimales lee como volcado de base de datos, no como precio; el design system pide `$1,234.56`.
  - "Vigente desde 2026-01-10 04:51" es fecha cruda técnica; falta pasar por `datetime_local` con formato legible.
  - "Vigente hasta —" en todas las filas: la columna solo aporta cuando hay historial; con una versión por combinación queda vacía.
  - No hay agrupación visual entre Compra y Venta: cuando existan versiones históricas, mezclar ambas operaciones en una tabla plana dificultará leer "el precio vigente de compra".
  - La página queda 70% vacía; no aprovecha para mostrar lo más útil: el diferencial compra/venta (margen) del material.
- **Propuesta**:
  1. Formatear precio con `money()` (2 decimales; si el negocio usa fracciones, 4 máximo y consistentes) y fechas con `date_local`.
  2. Separar en dos secciones "Compra" y "Venta" con el precio vigente destacado en banda `s-stats` (Compra $20.00 · Venta $25.00 · Margen $5.00) y el historial debajo — patrón Stripe (valor actual grande, historial secundario).
- **Prioridad**: alta — el precio es la variable más sensible del negocio y hoy su lectura es la menos cuidada.

#### inventario-list (1440)

- **Función**: Saldo vivo en kg por sucursal y material, con fecha del último movimiento.
- **Fortalezas**:
  - KPIs con unidades reales ("420.00 kg") y subtítulos que definen el alcance ("Suma de los saldos de esta vista").
  - Encabezado con las tres acciones bien jerarquizadas (dos secundarias + una primaria).
  - Números a la derecha con tabulares; fila de 0.00 kg presente sin alarmismo.
- **Oportunidades**:
  - "Varilla 3/8" — 0.00 kg" ocupa un renglón igual que los saldos reales; no hay distinción visual para saldo cero (el kg cero debería ir muted como el `$0.00`).
  - La tabla repite "Sucursal Centro" tres veces; sin agrupación por sucursal la lectura por sede exige escanear la columna.
  - "Actualizado 2026-01-14 06:38" formato técnico otra vez; además "hace 3 h" sería más útil operativamente.
  - El filtro ocupa una tarjeta entera para un solo select; el patrón `.s-filters-collapse` existe pero el desktop gasta ~90px de alto en un control.
  - No hay enlace por renglón a los movimientos de ese material/sucursal (la pregunta natural: "¿por qué tengo 400 kg?").
- **Propuesta**:
  1. Agrupar por sucursal con subencabezado y subtotal de kg (patrón Odoo inventory: grupo + subtotal), o al menos ordenar y fundir la celda repetida.
  2. Hacer el renglón clicable hacia `/inventario/movimientos?material=X&sucursal=Y` — cada número debe poder explicarse (patrón Stripe: every figure drills down).
  3. kg cero en `.s-zero` muted y fecha relativa con `title` absoluto.
- **Prioridad**: alta — es la pantalla central del grupo Inventario y hoy es una tabla plana sin drill-down.

#### inventario-list (390)

- **Función**: La misma vista en teléfono, restackeada a tarjetas.
- **Fortalezas**:
  - El restack a record-cards funciona: título del material, campos etiquetados, nada cortado.
  - Los tres botones del header pasan a bloque completo, tocables (≥44px).
- **Oportunidades**:
  - La banda de KPIs deja un hueco blanco a la derecha de "Sucursales" (celda vacía de la rejilla 2×2 con 3 lecturas) — se percibe inacabado.
  - Tres acciones apiladas empujan el contenido: el usuario móvil llega a los datos tras ~350px de botones.
  - En tarjeta, "SUCURSAL: Sucursal Centro" repite la palabra sucursal; el prefijo del grupo sobra en móvil.
- **Propuesta**:
  1. En móvil, mover "Ver movimientos"/"Valor del inventario" a enlaces secundarios bajo la banda y dejar solo la primaria arriba (regla content-priority móvil).
  2. Para bandas con N impar, hacer que la última lectura ocupe las dos columnas (`grid-column: span 2`) y evitar el hueco.
- **Prioridad**: media — funcional, pero el orden de prioridades móvil está invertido.

#### inventario-movimientos

- **Función**: Kardex del rango: compras, ventas, ajustes y conversiones con saldo resultante, más resúmenes por material.
- **Fortalezas**:
  - La página responde preguntas reales: resumen por material con precio promedio, notas del rango, ajustes y kardex completo con folio enlazado.
  - Badges de tipo (COMPRA/VENTA/AJUSTE/CONVERSIÓN) con tonos semánticos consistentes.
  - Cada sección lleva subtítulo con los totales del rango ("5 notas · 626.00 kg · $17,530.00") — excelente hábito.
- **Oportunidades**:
  - Página de 4 secciones largas **sin `anchor_nav()`**: el patrón de la casa para páginas sección-tras-sección no se aplica justo donde más falta hace.
  - Los inputs de fecha muestran "yyyy-mm-dd" como placeholder crudo del input nativo; un rango con presets ("Hoy", "Semana", "Mes") ahorraría el 90% de las capturas.
  - La columna "—" final (sin acciones) deja una columna fantasma en varias tablas.
  - "Comentario: Auto (compra) nota #8" es jerga interna repetida en cada renglón; el design system prohíbe jerga ("Auto", "#8" sin formato de folio).
  - Cuatro tablas con estructuras distintas una tras otra sin jerarquía visual entre "lo que resume" y "el detalle" — el kardex (la tabla reina) queda al fondo.
- **Propuesta**:
  1. `anchor_nav()` arriba (Resumen · Notas · Ajustes · Kardex) + presets de rango de fecha (patrón Stripe Dashboard: date-range picker con atajos).
  2. Invertir el orden: kardex primero (es "la" tabla), resúmenes como banda `s-stats` + secciones plegables `.s-collapse` después.
  3. Limpiar el comentario automático: "Compra · nota 01_C_4" con folio en `.s-folio` mono.
- **Prioridad**: alta — es la pantalla más rica en información del inventario y la de mayor esfuerzo de lectura hoy.

#### inventario-valor

- **Función**: Valuación en pesos de las existencias por sucursal.
- **Fortalezas**:
  - Tabla mínima y clara: kg, valor, cobertura de materiales, columna "Sin referencia" para detectar huecos de valuación.
  - `$0.00` de Sucursal Norte correctamente muted.
  - Copy del sub de tarjeta orienta a la acción ("Abre una sucursal para consultar y ajustar sus precios de valuación").
- **Oportunidades**:
  - No hay total general: la pregunta número uno ("¿cuánto vale todo mi inventario?") obliga a sumar mentalmente $17,800 + $380.
  - El filtro de sucursal duplica lo que la tabla ya resuelve con tres renglones y botones "Ver" — dos mecanismos para la misma elección.
  - "MATERIALES CON EXISTENCIAS 2 / 5" merece explicación en la celda o tooltip; el formato fracción sin contexto es ambiguo.
  - "Ver" como única acción en botón; el renglón entero debería ser clicable (área de toque y convención de drill-down).
  - Página 60% vacía: cabe una banda de KPIs (valor total, kg totales, sucursales sin valuar).
- **Propuesta**:
  1. Banda `s-stats` arriba: "Valor total $18,180.00 · 420 kg · 1 sucursal sin existencias" — y fila de totales al pie de la tabla (patrón Salesforce report: grand total row).
  2. Eliminar el filtro y hacer renglones clicables completos.
- **Prioridad**: media — pantalla correcta pero con el dato principal (total) ausente.

#### inventario-ajuste

- **Función**: Corrección puntual de existencias fijando el saldo final de un material en una sucursal.
- **Fortalezas**:
  - El par "Existencias actuales / Existencias resultantes" en lecturas tipo `defs()` comunica el efecto antes de aplicar — exactamente el patrón correcto para una acción de riesgo.
  - Helper text explica destino y trazabilidad del motivo.
- **Oportunidades**:
  - El formulario flota en una tarjeta de ancho completo con ~60% de vacío a la derecha; una corrección puntual pide `.s-narrow`.
  - El textarea de motivo a ancho completo (~1100px) invita a párrafos donde se espera una frase; su medida contradice `line-length-control`.
  - "Existencias resultantes 400.00 kg" idéntico a las actuales cuando no se ha capturado cantidad: mostrar el delta ("sin cambio" / "+50 kg") daría lectura inmediata.
  - El motivo no es obligatorio visualmente (sin `*`) pese a que la trazabilidad del ajuste es su única defensa auditable.
  - Duplicidad con `/inventario/aumentar`: dos rutas del menú aterrizan en formularios casi idénticos titulados igual ("Ajustar existencias") — confusión de arquitectura de información.
- **Propuesta**:
  1. Compactar a `.s-narrow` con el resumen del efecto (actual → resultante, delta coloreado ok/bad) en un `subpanel()` pegado al botón — patrón Linear: preview del efecto junto al submit.
  2. Unificar `/ajuste` y `/aumentar` en una sola pantalla con selector de modo, y renombrar el ítem del menú para que coincida con el título de la página.
- **Prioridad**: alta — acción que muta inventario con fricción de comprensión y ruta duplicada.

#### inventario-aumentar

- **Función**: Alta/baja rápida de kilogramos por cantidad (variante del ajuste con operación Aumentar/Disminuir).
- **Fortalezas**:
  - Fieldsets "Material a ajustar" / "Ajuste" agrupan bien; los tres textos de ayuda son útiles y honestos ("el saldo podrá quedar en negativo").
  - Muestra existencias actuales y resultantes en vivo, como la pantalla gemela.
- **Oportunidades**:
  - **El ítem del menú dice "Aumentar materiales", la página se titula "Ajustar existencias"**: nav-label y título desalineados; además convive con `/inventario/ajuste` casi idéntica.
  - El aviso de saldo negativo vive en helper text permanente; merece ser advertencia contextual (solo cuando la cantidad supere lo disponible).
  - Misma tarjeta ancha con vacío a la derecha y textarea quilométrico que su gemela.
  - "EXISTENCIAS ACTUALES 0.00 kg" sin material seleccionado es una lectura sin sujeto; hasta elegir material debería mostrarse "—".
- **Propuesta**:
  1. Fusionar con `/inventario/ajuste` (una pantalla, dos modos: "fijar saldo final" / "sumar-restar cantidad") y un solo nombre en menú y título — elimina la duplicidad más visible del grupo Inventario.
  2. Validación inline: si Disminuir &gt; disponible, warning ámbar en el campo (patrón Material: validate on blur con recovery path).
- **Prioridad**: alta — por la duplicidad de arquitectura de información, no por el formulario en sí.

#### conversiones

- **Función**: Transformar kilos de un material en otro dentro de la misma sucursal, registrando merma, con historial debajo.
- **Fortalezas**:
  - El concepto origen→destino con existencias vivas de ambos lados en la misma pantalla.
  - Helper de merma explica el modelo mental ("puede diferir del origen: la merma es la diferencia").
  - Historial con badge ACTIVA y acción "Detalle" consistente.
- **Oportunidades**:
  - El formulario y el historial comparten pantalla sin jerarquía: la tabla de historial arranca sin título visible y pegada al formulario.
  - Los bloques origen/destino se distinguen solo por posición vertical; no hay composición visual del flujo (origen → merma → destino).
  - La merma — el número de negocio de esta pantalla — no se muestra calculada en vivo (16 kg → 10 kg = 6 kg de merma, 37.5%: eso debe verse antes de registrar).
  - Textarea de motivo de nuevo a ancho completo.
  - En el historial, "MOTIVO —" columna casi siempre vacía al frente de la tabla.
- **Propuesta**:
  1. Componer el formulario como flujo de dos columnas con flecha central y un `subpanel()` de resumen en vivo: "Salen 16 kg de Alambre → Entran 10 kg de Arena · Merma 6 kg (37.5%)" — el patrón de "preview del efecto" que ya usa el detalle de conversión, movido antes del submit.
  2. Separar historial en su propia tarjeta con título/sub y contadores.
- **Prioridad**: media — funcional y de uso super_admin, pero la merma invisible es una omisión de negocio.

#### conversion-detail

- **Función**: Consulta del impacto de una conversión y punto de reversión compensatoria.
- **Fortalezas**:
  - La banda Salida/Entrada/Existencias con puntos de tono y unidades es la mejor lectura de efecto del módulo — este patrón merece copiarse al formulario de alta.
  - "Alambre recocido → Arena" como título de tarjeta con badge ACTIVA y metadatos (sucursal · usuario · fecha) en una línea.
  - Reversión bien tratada: `btn-outline-danger`, copy que explica la compensación y su condición.
- **Oportunidades**:
  - "MOTIVO DE LA CONVERSIÓN / Sin comentario." — un vacío que ocupa la esquina destacada; con `defs()` el em dash bastaría.
  - La cifra "16.00 kg" en ámbar y "10.00 kg" en verde usan tonos semánticos para describir dirección (salida/entrada) — funciona, pero convendría documentarlo como uso canónico de dirección.
  - 60% de la página vacía: cabe el rastro contable (los dos movimientos de kardex que generó) sin navegar a movimientos.
- **Propuesta**:
  1. Añadir sección "Movimientos generados" con las dos filas del kardex enlazadas — cierra el ciclo de trazabilidad en la misma pantalla (patrón Stripe: related objects en el detalle).
- **Prioridad**: baja — ya es de las pantallas mejor resueltas del grupo.

#### transferencias

- **Función**: Traslado de material entre sucursales generando dos notas aprobadas espejo.
- **Fortalezas**:
  - Helper copy de alto nivel: qué pasa al crear (dos notas, inventario en ambas), qué NO genera (saldo con socios) — el mejor copy explicativo del módulo.
  - La tabla de renglones de material con "Quitar" y subtotal sigue el patrón del wizard de notas.
- **Oportunidades**:
  - El renglón de material vive en una tabla de 6 columnas con inputs de placeholder "0.00000"/"0.000" — precisión de 5 decimales sin sentido para kilos y pesos.
  - "SUBTOTAL —" y la fila de total con "—" a la derecha: estados vacíos crudos donde iría `$0.00` muted o nada.
  - No se ven las existencias disponibles del material en la sucursal origen al capturar — el dato que evita transferir lo que no hay.
  - El comentario "Ej. Traslado de acumulado semanal" es placeholder-como-ejemplo correcto, pero el campo está en la fila superior mezclado con los selects de sucursal: agrupación difusa.
  - Falta el historial de transferencias en la pantalla (o un enlace claro): la ruta es de alta y consulta a la vez, pero solo se ve el alta.
- **Propuesta**:
  1. Mostrar existencias origen por renglón al elegir material (badge junto al select, como hace conversiones) y validar cantidad contra ellas.
  2. Normalizar decimales de captura (2 para dinero, 2 para kg) y estados vacíos con `.s-zero`.
  3. Añadir tabla "Últimas transferencias" bajo el formulario con folios de las notas espejo enlazados (patrón Odoo: form + historial en la misma vista de operación).
- **Prioridad**: media-alta — mueve inventario entre sedes sin mostrar disponibilidad: riesgo operativo directo.

### Grupo Finanzas y administración

#### contabilidad

- **Función**: bitácora contable global — por cobrar, por pagar y cada movimiento del período con su cuenta, sucursal y nota.
- **Fortalezas**:
  - La banda de indicadores resume el neto en cuatro lecturas y "Notas consideradas: 3" declara el alcance del cálculo — honestidad de datos poco común.
  - El panel "Cómo se arma el resumen" explica la reclasificación de saldos en lenguaje llano; es exactamente el tipo de copy que evita llamadas de soporte.
  - La bitácora enlaza cada movimiento a su nota y trae el total del período filtrado arriba de la tabla.
- **Oportunidades**:
  - **Semántica de color rota en TIPO**: "PAGO DE COMPRA" (dinero que sale) va en verde y "COMPRA" en azul; el verde debe reservarse a confirmación/liquidado.
  - Todos los egresos van en **rojo** (−$3,750.00…): el rojo queda reservado a problemas; un egreso normal no es un error. El signo y la columna NATURALEZA ya lo dicen.
  - La columna NATURALEZA muestra un badge gris "−" cuando no aplica — ruido visual; debería ir vacía (em dash sin caja).
  - USUARIO muestra el id crudo "1" — prohibido por el design system; debe ser nombre + @username como ya hace cuenta-detail.
  - Fechas en formato ISO "2026-01-14 06:42" en vez del filtro `datetime_local`.
  - Dos formularios de filtro apilados (entidad y bitácora) con dos botones "Filtrar": duplican el patrón y empujan la tabla —el producto— muy abajo del fold.
- **Propuesta**:
  1. Rediseñar los badges de TIPO con una regla única: azul = operación (compra/venta/comisión), verde = solo pagos conciliados, rojo = solo reversos; egresos en tinta normal con signo. Es el patrón del ledger de Stripe: color en el estado, nunca en el monto.
  2. Fundir ambos filtros en una sola `.s-filters-collapse` (entidad, cuenta, rango) y subir la bitácora al primer scroll.
  3. Sustituir el id de usuario por nombre y formatear fechas con `datetime_local`.
- **Prioridad**: **alta** — es la pantalla de dinero más densa del sistema y hoy su código de color miente.

#### capital

- **Función**: fotografía del capital real — activos (clientes, cuentas, efectivo, inventario) contra pasivos (proveedores, comisionistas) con valuación configurable del inventario.
- **Fortalezas**:
  - Activos y Pasivos como dos columnas espejo con renglones label+descripción+cifra alineada a la derecha: se lee como un balance de verdad.
  - Los `$0.00` renders en gris muted — la regla "un cero no es un error" se cumple.
  - "Valor del inventario" desglosa por sucursal con contexto ("2 materiales con existencias · 410.00 kg").
- **Oportunidades**:
  - El selector de base de valuación ("Configuración manual / promedio de compra") es un par de pills sin marco de grupo: no se percibe que es una elección exclusiva ni cuál está activa a primera vista.
  - La meta-línea "0 manuales · 5 automáticos · 0 sin referencia" repite jerga interna tres veces por sucursal; "sin referencia" no significa nada para el dueño del negocio.
  - "Cuentas Scrap360" incrusta una mini-tabla de una fila con botón "Ver cuentas": una tarjeta entera para un dato que cabría en un renglón de defs.
  - El header solo trae "Volver": una pantalla de reporte pediría exportación (PDF/Excel) como en cuenta-scrap360-detail.
  - Sucursal Norte muestra $0.00 y "0 materiales" en el mismo peso tipográfico que Centro con $17,800 — sin atenuación, todo compite igual.
- **Propuesta**:
  1. Convertir el selector de valuación en un segmented control declarado (fondo sunken, opción activa con surface + borde), patrón de Linear/Stripe para vistas alternativas.
  2. Atenuar sucursales sin existencias y esconder la letanía "0 manuales…" tras un tooltip o una sola línea compacta.
  3. Añadir Exportar PDF/Excel al header, en el orden fijo del design system.
- **Prioridad**: **media** — estructura correcta; le falta afinado de jerarquía y salida imprimible.

#### reporte-asistencias

- **Función**: días con nota registrada por proveedor en un período, para premiar constancia o detectar ausencias.
- **Fortalezas**:
  - Filtro mínimo y correcto (sucursal + rango) con un solo "Filtrar" primario.
  - El estado vacío cumple el patrón: icono, qué pasó, cómo recuperarse.
- **Oportunidades**:
  - El vacío ocupa todo el fold y no ofrece datos de contexto: ni cuántos proveedores existen, ni el último día con actividad.
  - El rango por defecto (6 días) es arbitrario y produce vacíos frecuentes; un preset "Últimos 30 días" evitaría la pantalla en blanco.
  - Sin exportación: un reporte que el cliente pidió en PDF/Excel en otros módulos aquí no tiene salida.
  - No hay banda de indicadores (total de proveedores activos, asistencias del período, promedio) antes de la tabla.
- **Propuesta**:
  1. Presets de rango (Hoy / 7 días / 30 días / Mes actual) como chips sobre el filtro — patrón estándar de dashboards que elimina el 80% de los vacíos.
  2. Banda `s-stats` con "Proveedores con asistencia", "Días con actividad", "Última asistencia".
- **Prioridad**: **baja** — pantalla poco frecuentada; las mejoras son baratas pero el impacto es acotado.

#### reporte-saldos

- **Función**: el neto global de la operación — a quién le debes, quién te debe, socio por socio.
- **Fortalezas**:
  - "SALDO NETO GLOBAL −$1,800.00 / En contra" responde la pregunta del dueño en una lectura.
  - La fila de totales al pie de la tabla y el em dash para ceros están bien ejecutados.
  - El toggle "Por cantidad / Alfabético" es una elección de ordenamiento clara y visible.
- **Oportunidades**:
  - El saldo global negativo va en **rojo** con sub "En contra": el design system declara que un pasivo es cifra neutral, no error. Deber $1,800 a proveedores es operación normal, no alarma.
  - La frase explicativa "Un resultado positivo significa que te deben más de lo que debes…" flota como texto suelto entre el toggle y la tabla, sin ancla visual.
  - El toggle de orden vive pegado al borde derecho, lejos del título de la tabla que ordena — se pierde.
  - Los nombres de socios no enlazan (o no se percibe) a su récord: el flujo natural es saldo → estado de cuenta del socio.
- **Propuesta**:
  1. Neutralizar el color del neto (tinta normal o ámbar si se quiere "atención"), reservando rojo para vencidos — coherente con la regla "liabilities are not errors" y con cómo Odoo/QuickBooks pintan cuentas por pagar.
  2. Hacer cada fila clicable hacia `/record` del socio y mover la explicación a un `form-text` bajo el título de la tabla.
- **Prioridad**: **media** — pantalla correcta con una violación semántica visible en su cifra principal.

#### cuentas-list

- **Función**: directorio de cuentas bancarias de socios y sucursales para pagos por transferencia/cheque.
- **Fortalezas**:
  - Filtros correctos (búsqueda libre + vínculo + estado) en una sola línea con un solo Filtrar.
  - El vínculo se lee en lenguaje natural ("Proveedor: Proveedor Uno").
  - Acciones colapsadas a "Auditar + ⋯" — el patrón de menú de fila operando bien.
- **Oportunidades**:
  - **La columna NOMBRE se duplica a sí misma**: "Cuenta 1 - Cliente Uno" en negritas y abajo "Cuenta 1 - Cliente Uno | Santander" — la sublínea repite el título y el banco (que además tiene su propia columna).
  - TIPO dice "Cuenta bancaria" en las cuatro filas: columna de información cero; ESTADO dice "ACTIVA" en todas — juntas queman dos columnas sin decir nada.
  - Vacíos como "-" en NÚMERO/TITULAR en vez del em dash del sistema.
  - "Auditar" es jerga contable para "ver el historial de la cuenta"; el design system pide "Ver".
  - ID como primera columna gasta el arranque visual de la fila en un dato interno.
- **Propuesta**:
  1. Recomponer la tabla: quitar TIPO y la sublínea redundante, fundir banco+número enmascarado bajo el nombre (patrón "entidad + meta" de las listas de Stripe), y dejar ESTADO solo cuando exista al menos una inactiva.
  2. Renombrar "Auditar" → "Ver" y mover "Eliminar" al menú ⋯ en rojo.
- **Prioridad**: **media** — redundancia evidente, arreglo barato de plantilla.

#### cuenta-detail

- **Función**: expediente de una cuenta bancaria — identificación, actividad mensual, conciliación y sus pagos/notas vinculadas.
- **Fortalezas**:
  - `anchor_nav` presente (Información / Pagos / Notas) — el patrón de página larga honesta.
  - Banda de indicadores con "REVERSOS $0.00" en muted — el cero bien tratado.
  - Bloque "Conciliación" (esperado/pagado/pendiente/conciliados) — condensa la pregunta clave de la cuenta en una línea.
  - CLABE enmascarada (****6789) y usuario como "Super Admin / AVRC" — sin datos crudos.
- **Oportunidades**:
  - **"Actividad mensual" imprime 12 filas donde 11 son $0.00**: una tabla completa para un solo mes con datos. Es el mayor gasto de pantalla del módulo.
  - Estados "APROBADA" en azul y "CANCELADA" en rojo: Aprobada debería ser verde según la semántica del sistema; azul es navegación/neutral.
  - Folio y "#8" conviven en la misma celda: el id interno no aporta al lado del folio legible.
  - Fechas ISO de nuevo ("2026-01-14 06:40").
  - El sub "Últimos 4 registros visibles" no ofrece camino al resto (¿dónde están los demás?).
- **Propuesta**:
  1. Colapsar la actividad mensual: mostrar solo meses con movimiento + una fila "8 meses sin actividad", o sustituir la tabla por micro-barras junto a PROMEDIO/MEJOR/PEOR — patrón de "actividad reciente" de Stripe que elimina el desierto de ceros.
  2. Alinear tonos de estado con el resto del sistema (Aprobada = verde, Cancelada = rojo, En revisión = ámbar) desde el macro `badge`.
- **Prioridad**: **alta** — pantalla de dinero con la mitad del lienzo gastado en ceros.

#### cuentas-scrap360

- **Función**: tesorería interna — cuentas propias con saldo inicial y saldo vivo.
- **Fortalezas**:
  - Tabla limpia con saldos alineados a la derecha y $0.00 inicial en muted.
  - Filtros consistentes con el resto de las listas.
- **Oportunidades**:
  - Una sola fila en todo el lienzo: sin banda de totales (saldo total de tesorería, entradas/salidas del mes) el módulo no aprovecha su naturaleza de "caja fuerte".
  - SUCURSALES como texto corrido ("Sucursal Centro, Sucursal Norte, Sucursal Sur") crecerá mal; badges por sucursal o "3 sucursales" con tooltip escalan mejor.
  - ID otra vez de primera columna; ESTADO "ACTIVA" única — mismas columnas muertas que cuentas-list.
- **Propuesta**:
  1. Banda `s-stats`: "Saldo total de tesorería", "Entradas del mes", "Salidas del mes", "Cuentas activas" — convierte la lista en un tablero de caja al estilo del "Balances" de Stripe Treasury.
  2. Compactar sucursales a conteo + tooltip.
- **Prioridad**: **media** — hoy sobrevive por tener una sola cuenta; escalará mal sin totales.

#### cuenta-scrap360-detail

- **Función**: libro mayor de una cuenta de tesorería — registrar movimientos manuales y ver el efecto acumulado.
- **Fortalezas**:
  - Banda Entradas/Salidas/Saldo/Movimientos — la lectura de caja correcta.
  - La columna SALDO acumulado tras cada movimiento es exactamente cómo se audita una caja.
  - El aviso "las notas aprobadas por transferencia o cheque no descuentan esta cuenta hasta que registres el pago real" — copy operativo excelente.
  - Usuario con nombre + @username, em dashes correctos.
- **Oportunidades**:
  - **Orden de acciones del header roto**: Volver · Exportar PDF · Exportar Excel · Editar; el design system fija exports → destructiva → Volver → primaria.
  - El formulario "Registrar movimiento" vive incrustado entre la banda y la bitácora: empuja el libro mayor bajo el fold y compite con "Editar" del header.
  - Badges "SALIDA" rojo / "AJUSTE A FAVOR" azul: la salida normal de dinero no es un problema; rojo queda para reversos/errores.
  - Sin filtros de rango en la bitácora — con 3 movimientos sobra, con 300 será inusable.
- **Propuesta**:
  1. Plegar "Registrar movimiento" en un `.s-collapse` cerrado, dejando la bitácora como protagonista — es como Odoo trata "Registrar pago" en asientos.
  2. Corregir el orden del header y bajar el rojo de SALIDA a tinta neutral con signo.
- **Prioridad**: **media** — funciona, pero invierte la jerarquía entre captura y consulta.

#### users-list

- **Función**: control de accesos — quién entra, con qué rol y a qué sucursales.
- **Fortalezas**:
  - La banda de indicadores post-rediseño estrena bien el patrón: una superficie, divisores hairline, punto de tono en ACTIVOS.
  - "No se puede eliminar: tiene 5 notas…" y "No puedes eliminar tu propio usuario" — la mejor microcopy del sistema: explica el porqué del botón ausente.
  - Avatares con foto/inicial tras la feature de perfil — identidad visual en la fila.
- **Oportunidades**:
  - Chips de rol y badges de estado son dos sistemas visuales distintos en la misma celda: caja gris cuadrada vs pill tintada.
  - "ACTIVO" en mayúsculas sostenidas vs "Trabajador" capitalizado — inconsistencia tipográfica dentro de la celda.
  - La banda dedica una celda a cada rol (6 lecturas) cuando "Admins 0" y "Visores 0" rara vez cambian.
  - "SUCURSAL CENTRO" como badge gris en ACCESO y otra vez como texto en SUCURSAL PRIMARIA — el mismo dato dos veces por fila.
- **Propuesta**:
  1. Unificar rol+estado en un solo sistema de badges tintados (rol = tono neutro/info; estado solo cuando ≠ activo), y fundir ACCESO con SUCURSAL PRIMARIA en una columna "Alcance".
  2. Compactar la banda a Total / Activos / "Por rol" — menos celdas, misma información.
- **Prioridad**: **media** — pantalla ya buena; le sobra redundancia por fila.

#### user-form

- **Función**: alta de usuario con rol, protección y alcance por sucursal.
- **Fortalezas**:
  - Explicador de rol vivo (subpanel que describe el rol elegido) — progressive disclosure bien hecha.
  - Helper texts por campo consistentes.
  - Un solo primario ("Crear usuario") con label del design system.
- **Oportunidades**:
  - **"Volver" en el header y "Cancelar" en las acciones**: el design system lo prohíbe explícitamente.
  - Los paneles de alcance (sucursal del trabajador / sucursales del admin) se renderizan como bloques sunken vacíos aun cuando el rol elegido no los usa — se percibe página a medio cargar.
  - La contraseña se pide sin confirmación ni indicador de fortaleza; el helper admite que viajará por WhatsApp — un generador "Sugerir contraseña" reduciría contraseñas débiles.
  - Grid de identidad a 3 columnas deja mucho aire con solo 3 campos.
- **Propuesta**:
  1. Quitar "Volver" del header (queda Cancelar) — cumplimiento directo del sistema.
  2. Ocultar por completo los bloques de alcance no aplicables al rol activo y añadir "Sugerir contraseña" (patrón de admin de Google Workspace).
- **Prioridad**: **media** — una violación clara del sistema y ergonomía mejorable en pantalla de uso esporádico.

#### sucursales-list

- **Función**: catálogo de sedes con responsables, personal y estado operativo.
- **Fortalezas**:
  - Banda compacta (Total/Activas/Trabajadores) proporcional a un catálogo de 3 filas.
  - Acciones claras por fila con "Archivar" correctamente en outline-danger (reversible, no "Eliminar").
  - Sub del directorio explica qué revisar.
- **Oportunidades**:
  - "SIN ADMINS" como badge gris en las tres filas es un dato de alerta disfrazado de neutro: si ninguna sede tiene admin, eso es un pendiente operativo que debería verse ámbar.
  - "2 asignados / 0 asignados" — 0 sin atenuar compite con el 2 (un cero no es un dato caliente).
  - El directorio promete "logo… y presencia visual" pero la fila no muestra ningún avatar/logo de la sede.
  - ID bajo el nombre ("ID 1") — dato interno en la columna título.
- **Propuesta**:
  1. Convertir "SIN ADMINS" en badge ámbar con acción ("Asignar admin" en el menú de fila) — un vacío accionable al estilo de las "setup tasks" de Salesforce.
  2. Añadir el logo/inicial de la sede en la columna título (como users-list ya hace con personas).
- **Prioridad**: **baja** — catálogo chico y estable; las mejoras son de pulido.

#### perfil

- **Función**: cada usuario edita su nombre, foto de perfil y contraseña.
- **Fortalezas**:
  - Usuario y rol como lectura de solo-lectura (defs), no inputs deshabilitados — cumple el sistema.
  - Cambio de contraseña con verificación de la actual y confirmación, cada campo con su helper.
  - Un primario ("Guardar cambios") y Cancelar, sin Volver duplicado.
- **Oportunidades**:
  - La tarjeta `s-narrow` queda pegada al costado del sidebar dejando un vacío enorme a la derecha: en 1440px la página se siente descentrada.
  - Las tres contraseñas en una fila de 3 columnas obligan a leer horizontal un flujo que es secuencial (actual → nueva → confirmar).
  - La sección foto muestra el avatar y el input de archivo sin previsualización de la imagen elegida antes de guardar.
  - Sin última-sesión ni "cuenta creada el…" — datos de confianza que un perfil suele ofrecer.
- **Propuesta**:
  1. Centrar la tarjeta en el espacio restante y apilar el bloque de contraseñas en una columna con ancho de lectura.
  2. Previsualizar la foto seleccionada (FileReader) antes del submit — patrón universal de ajustes de perfil (GitHub/Linear).
- **Prioridad**: **baja** — pantalla nueva y funcional; ajustes de composición.

#### login (1440 y 390)

- **Función**: puerta de entrada — autenticación con credenciales asignadas.
- **Fortalezas**:
  - Panel dividido con lienzo grafito, dibujo técnico de la carátula y eyebrow "SISTEMA DE OPERACIÓN": identidad propia, nada de tarjeta genérica centrada.
  - Formulario primero en el DOM, autofocus en usuario, toggle de contraseña con hit-area correcta.
  - En 390px queda solo el formulario con la marca — móvil sin ruido.
- **Oportunidades**:
  - No hay "¿Olvidaste tu contraseña?" ni siquiera como texto pasivo — el error más común del login queda sin salida.
  - El error de credenciales re-renderiza la página completa; conservar el username tecleado y enfocar contraseña ahorraría un ciclo.
  - La meta "Scrap360 · v0.1.0" del panel izquierdo duplica la versión que ya está en el footer visible bajo la tarjeta.
- **Propuesta**:
  1. Línea de auxilio bajo el botón: "¿No puedes entrar? Pide a tu administrador restablecer tu contraseña" — cierra el callejón sin salida sin construir un flujo de reset.
  2. En el POST fallido, preservar username y enfocar el campo contraseña.
- **Prioridad**: **media** — la puerta ya es excelente; le falta la salida de emergencia.

---

## Hoja de ruta sugerida

**Fase 0 — fixes de sistema** (una sola pasada, beneficia a todas las
pantallas): tabla canónica de tonos por estado (tema 1), barrido de fechas
`datetime_local` (tema 2), locale numérico de inputs (tema 3), retirar IDs
internos (tema 4), em dashes y decimales (temas 10 y 12), "Partners"→"Socios"
y "Auditar"→"Ver" (tema 13).

Después, pantalla por pantalla:

| Orden | Grupo | Pantallas prioritarias (alta) | Razón |
|---|---|---|---|
| 1 | Operación | home admin, notas-list, nota-detail, corte-caja (móvil) | Tráfico diario; el dinero se decide aquí; nota-detail y arqueo móvil son los mayores costos de scroll del sistema |
| 2 | Trabajador | worker-home, worker-nota-nueva | El rol con más usuarios; captura de origen |
| 3 | Socios | proveedor-record + cliente-record (mismo patrón), móvil de records | La pantalla más consultada de relaciones; tres tablas cuentan la misma historia |
| 4 | Inventario | material-precios, inventario-list, movimientos, fusión ajuste/aumentar | Precio = variable más sensible; ruta duplicada de ajuste |
| 5 | Finanzas | contabilidad, cuenta-detail | Código de color que miente; desierto de ceros |
| 6 | Resto | listas y formularios de prioridad media/baja | Pulido incremental con los fixes de sistema ya hechos |

**Nota de captura para la siguiente corrida:** `login--*.png` debe capturarse
con la sesión cerrada (el script fotografió la redirección a Inicio); los
PNG a página completa dibujan la barra lateral fija sobre la franja izquierda
— artefacto del screenshot, no defecto de la UI.
