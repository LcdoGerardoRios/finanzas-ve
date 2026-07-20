# Atajos de iOS (Apple Shortcuts) — Finanzas VE

Estos atajos hablan directo con Supabase por HTTP, sin pasar por la app de
Streamlit. Necesitas de antemano:

- `SUPABASE_URL` → ej: `https://xxxx.supabase.co`
- `SUPABASE_ANON_KEY` → la anon key de tu proyecto (Settings > API en Supabase)

> 🔒 Guarda estos dos valores en la app **Atajos > Automatización > Variables**,
> o simplemente pégalos directo en cada atajo. No compartas estos valores ni
> los subas a un repositorio público.

---

## Atajo 1: "Registrar Gasto Express"

**Objetivo:** con 3 toques, registrar un gasto/ingreso desde el iPhone
(incluso desde la pantalla de bloqueo o el widget de Atajos).

### Pasos para crearlo

1. Abre la app **Atajos** → pestaña **Atajos** → botón `+` (Nuevo atajo).
2. Nómbralo **"Registrar Gasto Express"**.
3. Agrega las siguientes acciones, en este orden:

   **a) Elegir el tipo**
   - Acción: `Elegir de menú` (*Choose from Menu*)
   - Opciones del menú: `Gasto`, `Ingreso`
   - Guarda el resultado en una variable llamada `Tipo`.

   **b) Elegir la cuenta**
   - Acción: `Elegir de menú`
   - Opciones del menú: escribe el nombre exacto de tus cuentas, ej:
     `Banco Venezuela (VES)`, `Zelle / Banco USA (USD)`, `Efectivo`
   - Guarda el resultado en `CuentaNombre`.
   - (Opcional avanzado: usa `Diccionario` para mapear cada nombre a su
     `cuenta_id` numérico de Supabase y así evitar hardcodear IDs.)

   **c) Elegir la categoría**
   - Acción: `Elegir de menú`
   - Opciones: `Comida`, `Transporte`, `Servicios`, `Salud`,
     `Entretenimiento`, `Ropa`, `Educación`, `Deporte`,
     `KÖMUN (negocio)`, `Ahorro/Inversión`, `Otros`
   - Guarda en `Categoria`.

   **d) Elegir la moneda**
   - Acción: `Elegir de menú`
   - Opciones: `VES`, `USD`, `EUR`
   - Guarda en `Moneda`.

   **e) Pedir el monto**
   - Acción: `Preguntar por` (*Ask for Input*) → tipo `Número`
   - Texto: "¿Cuánto fue?"
   - Guarda en `Monto`.

   **f) (Opcional) Pedir nota**
   - Acción: `Preguntar por` → tipo `Texto`
   - Texto: "Nota (opcional)"
   - Guarda en `Nota`.

   **g) Construir el diccionario del body**
   - Acción: `Diccionario` (*Dictionary*), con las claves:
     ```
     cuenta_id       : <mapea CuentaNombre a su id numérico>
     tipo            : Tipo
     categoria       : Categoria
     monto_original  : Monto
     moneda_original : Moneda
     notas           : Nota
     ```

   **h) Enviar a Supabase**
   - Acción: `Obtener contenido de URL` (*Get Contents of URL*)
   - URL: `https://TU_PROYECTO.supabase.co/rest/v1/transacciones`
   - Método: `POST`
   - Encabezados (Headers):
     | Clave | Valor |
     |---|---|
     | `apikey` | `TU_ANON_KEY` |
     | `Authorization` | `Bearer TU_ANON_KEY` |
     | `Content-Type` | `application/json` |
     | `Prefer` | `return=minimal` |
   - Cuerpo (Body): `JSON` → selecciona el `Diccionario` del paso g.

   **i) Confirmación**
   - Acción: `Mostrar notificación` con el texto:
     "✅ {{Tipo}} de {{Monto}} {{Moneda}} registrado en {{Categoria}}"

4. Guarda el atajo. Agrégalo a tu pantalla de inicio o a "Apps" en el
   Centro de Control para acceder en 1 toque.

> 💡 Tip: usa Siri — di **"Oye Siri, Registrar Gasto Express"** para
> registrar un gasto por voz sin tocar la pantalla.

---

## Atajo 2: "Ajustar Presupuesto"

**Objetivo:** cambiar el límite en USD de una categoría desde el teléfono.

### Pasos

1. Nuevo atajo → nómbralo **"Ajustar Presupuesto"**.
2. Acciones:

   **a) Elegir categoría**
   - `Elegir de menú` con las mismas categorías del Atajo 1 → variable `Categoria`.

   **b) Elegir periodo**
   - `Elegir de menú`: `Semanal`, `Mensual` → variable `Periodo`.

   **c) Pedir nuevo límite**
   - `Preguntar por` → `Número` → "Nuevo límite en USD" → variable `Limite`.

   **d) Diccionario del body**
   - `Diccionario`: `{"monto_limite_usd": Limite}`

   **e) Enviar el PATCH a Supabase**
   - Acción: `Obtener contenido de URL`
   - URL (usa filtros de PostgREST en la query string):
     ```
     https://TU_PROYECTO.supabase.co/rest/v1/presupuestos?categoria=eq.{{Categoria}}&periodo=eq.{{Periodo}}
     ```
   - Método: `PATCH`
   - Headers: los mismos 4 headers del Atajo 1.
   - Body: `JSON` → el diccionario del paso d.

   **f) Confirmación**
   - `Mostrar notificación`: "🎯 Presupuesto de {{Categoria}} ({{Periodo}}) actualizado a US$ {{Limite}}"

3. Guarda el atajo.

---

## Automatización diaria: recordatorio a las 9:00 PM

1. Abre **Atajos** → pestaña **Automatización** → `+` → **Crear automatización personal**.
2. Elige **Hora del día** → configura `9:00 PM` → **Se repite: Diariamente**.
3. En "Agregar acción", busca `Mostrar notificación` (o `Mostrar alerta`) y
   escribe algo como:
   > "📊 ¿Registraste todos tus gastos de hoy? Abre Finanzas VE."
4. (Opcional, más útil) En lugar de solo notificar, agrega la acción
   `Ejecutar atajo` y selecciona **"Registrar Gasto Express"**, así la
   automatización te lleva directo al flujo de captura.
5. Desactiva **"Preguntar antes de ejecutar"** para que corra sola todos los días sin confirmación.
6. Guarda. Listo — todos los días a las 9:00 PM tu iPhone te lo recordará.

---

## Notas de seguridad

- La `anon key` de Supabase junto con Row Level Security (RLS) abierto
  (como está configurado en `schema.sql`) permite que cualquiera con esa
  key y tu URL pueda leer/escribir en tu base de datos. Es aceptable para
  un proyecto 100% personal siempre que no publiques esos valores en
  redes sociales, repos públicos ni capturas de pantalla.
- Si en el futuro quieres más seguridad, se puede añadir autenticación de
  Supabase (email/password) y políticas RLS que filtren por `user_id` —
  eso requeriría modificar el esquema y los atajos para incluir un token
  de sesión, lo cual queda fuera del alcance de esta primera versión.
