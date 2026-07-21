"""
app.py
==================================================================
Finanzas VE — Dashboard personal multimoneda (Streamlit + Supabase)

Diseñada mobile-first para gestionarse desde el iPhone (Safari o la
app de Streamlit). Se despliega gratis en Streamlit Community Cloud.

Requiere en .streamlit/secrets.toml (local) o en Streamlit Cloud > Settings > Secrets:

    SUPABASE_URL = "https://xxxx.supabase.co"
    SUPABASE_ANON_KEY = "xxxxx"
    GEMINI_API_KEY = "xxxxx"
==================================================================
"""

import json
import datetime
import pandas as pd
import streamlit as st
from supabase import create_client, Client

# ---------------------------------------------------------------------
# Configuración general de la página
# ---------------------------------------------------------------------
st.set_page_config(
    page_title="Finanzas VE",
    page_icon="💰",
    layout="centered",  # "centered" se ve mejor en móvil que "wide"
)

# Las categorías ya no están fijas en el código: se administran desde la
# tabla `categorias` y la pestaña "⚙️ Cuentas y Categorías" de la app.

# ---------------------------------------------------------------------
# Conexión a Supabase (cacheada)
# ---------------------------------------------------------------------
@st.cache_resource
def get_client() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_ANON_KEY"]
    return create_client(url, key)


supabase = get_client()


# ---------------------------------------------------------------------
# Helpers de datos (con cache corto para no golpear la API en cada click)
# ---------------------------------------------------------------------
@st.cache_data(ttl=30)
def cargar_cuentas() -> pd.DataFrame:
    data = supabase.table("cuentas").select("*").order("id").execute().data
    return pd.DataFrame(data)


@st.cache_data(ttl=30)
def cargar_transacciones(limit: int = 500) -> pd.DataFrame:
    data = (
        supabase.table("transacciones")
        .select("*")
        .order("creado_en", desc=True)
        .limit(limit)
        .execute()
        .data
    )
    return pd.DataFrame(data)


@st.cache_data(ttl=30)
def cargar_presupuestos() -> pd.DataFrame:
    data = supabase.table("presupuestos").select("*").order("categoria").execute().data
    return pd.DataFrame(data)


@st.cache_data(ttl=30)
def cargar_categorias() -> pd.DataFrame:
    data = supabase.table("categorias").select("*").order("nombre").execute().data
    return pd.DataFrame(data)


def lista_categorias(tipo: str | None = None) -> list[str]:
    """
    Devuelve los nombres de categoría disponibles para un tipo dado
    ('Gasto' o 'Ingreso'). Las categorías tipo 'Ambos' aplican a los dos.
    Si la tabla está vacía, devuelve un fallback mínimo para que la app
    no se rompa antes de que crees tu primera categoría.
    """
    cats = cargar_categorias()
    if cats.empty:
        return ["Otros"]
    if tipo:
        cats = cats[cats["tipo"].isin([tipo, "Ambos"])]
    nombres = cats["nombre"].tolist()
    return nombres if nombres else ["Otros"]


@st.cache_data(ttl=30)
def cargar_pagos_pendientes() -> pd.DataFrame:
    data = (
        supabase.table("pagos_programados")
        .select("*")
        .eq("estado", "Pendiente")
        .order("fecha_vencimiento")
        .execute()
        .data
    )
    return pd.DataFrame(data)


@st.cache_data(ttl=300)
def ultima_tasa_bcv() -> float | None:
    data = (
        supabase.table("tasas_cambio")
        .select("valor_usd")
        .eq("tipo_tasa", "BCV")
        .order("fecha", desc=True)
        .limit(1)
        .execute()
        .data
    )
    return float(data[0]["valor_usd"]) if data else None


def limpiar_cache():
    st.cache_data.clear()


# ---------------------------------------------------------------------
# Cálculo de saldo consolidado
# ---------------------------------------------------------------------
def calcular_balances(cuentas: pd.DataFrame, transacciones: pd.DataFrame, tasa_bcv: float):
    """
    Devuelve una copia de `cuentas` con dos columnas nuevas:
      - balance_nativo: balance_inicial + movimientos netos en moneda nativa
      - balance_usd: balance_nativo convertido a USD

    Ingreso  -> suma a cuenta_id
    Gasto    -> resta de cuenta_id
    Transferencia -> resta monto_original de cuenta_id (origen) y suma
                     monto_destino a cuenta_destino_id (destino). Si
                     origen y destino tienen la misma moneda, monto_destino
                     normalmente es igual a monto_original; si son monedas
                     distintas (ej. compra de dólares), monto_destino ya
                     viene convertido con la tasa que el usuario indicó.
    """
    cuentas = cuentas.copy()
    ajustes: dict[int, float] = {}

    if not transacciones.empty:
        for _, t in transacciones.iterrows():
            tipo = t["tipo"]
            cuenta_id = t["cuenta_id"]
            monto = float(t["monto_original"])

            if tipo == "Ingreso":
                ajustes[cuenta_id] = ajustes.get(cuenta_id, 0.0) + monto
            elif tipo == "Gasto":
                ajustes[cuenta_id] = ajustes.get(cuenta_id, 0.0) - monto
            elif tipo == "Transferencia":
                ajustes[cuenta_id] = ajustes.get(cuenta_id, 0.0) - monto
                destino = t.get("cuenta_destino_id")
                monto_destino = t.get("monto_destino")
                if pd.notna(destino) and pd.notna(monto_destino):
                    destino = int(destino)
                    ajustes[destino] = ajustes.get(destino, 0.0) + float(monto_destino)

    cuentas["balance_nativo"] = cuentas.apply(
        lambda row: float(row["balance_inicial"]) + ajustes.get(row["id"], 0.0),
        axis=1,
    )

    def a_usd(row):
        if row["moneda_nativa"] == "USD":
            return row["balance_nativo"]
        if tasa_bcv:
            return row["balance_nativo"] / tasa_bcv
        return None

    cuentas["balance_usd"] = cuentas.apply(a_usd, axis=1)
    return cuentas


# ---------------------------------------------------------------------
# Encabezado
# ---------------------------------------------------------------------
st.title("💰 Finanzas VE")

tab_dashboard, tab_presupuestos, tab_pagos, tab_mover, tab_ia, tab_ajustes = st.tabs(
    ["📊 Dashboard", "🎯 Presupuestos", "📅 Pagos", "🔄 Mover dinero", "🤖 Asistente IA", "⚙️ Ajustes"]
)

# =======================================================================
# MÓDULO 1 · DASHBOARD
# =======================================================================
with tab_dashboard:
    cuentas = cargar_cuentas()
    transacciones = cargar_transacciones()
    tasa_bcv = ultima_tasa_bcv()

    if cuentas.empty:
        st.warning("Aún no tienes cuentas registradas. Créalas en Supabase (tabla `cuentas`).")
    else:
        cuentas_calc = calcular_balances(cuentas, transacciones, tasa_bcv)
        total_usd = cuentas_calc["balance_usd"].sum(skipna=True)

        st.metric("Saldo total consolidado", f"US$ {total_usd:,.2f}")
        st.caption(
            f"Tasa BCV de referencia: {tasa_bcv:,.2f} VES/USD"
            if tasa_bcv
            else "⚠️ No hay tasa BCV registrada todavía."
        )

        st.subheader("Saldos por cuenta")
        for _, c in cuentas_calc.iterrows():
            col1, col2 = st.columns([2, 1])
            with col1:
                st.write(f"**{c['nombre']}**")
                st.caption(c["moneda_nativa"])
            with col2:
                st.write(f"{c['balance_nativo']:,.2f} {c['moneda_nativa']}")
                if pd.notna(c["balance_usd"]):
                    st.caption(f"≈ US$ {c['balance_usd']:,.2f}")

        st.divider()
        st.subheader("Últimos movimientos")
        if transacciones.empty:
            st.info("Todavía no registras transacciones.")
        else:
            vista = transacciones.head(15)[
                ["creado_en", "tipo", "categoria", "monto_original", "moneda_original", "monto_usd"]
            ].copy()
            vista["creado_en"] = pd.to_datetime(vista["creado_en"]).dt.strftime("%d/%m %H:%M")
            st.dataframe(vista, hide_index=True, use_container_width=True)

        if st.button("🔄 Refrescar datos"):
            limpiar_cache()
            st.rerun()


# =======================================================================
# MÓDULO 2 · PRESUPUESTOS
# =======================================================================
with tab_presupuestos:
    st.subheader("Presupuestos por categoría (en USD)")

    presupuestos = cargar_presupuestos()
    transacciones = cargar_transacciones()

    hoy = datetime.date.today()
    inicio_semana = hoy - datetime.timedelta(days=hoy.weekday())
    inicio_mes = hoy.replace(day=1)

    if not transacciones.empty:
        transacciones["fecha"] = pd.to_datetime(transacciones["creado_en"]).dt.date

    if presupuestos.empty:
        st.info("No hay presupuestos creados todavía. Agrega uno abajo.")
    else:
        for _, p in presupuestos.iterrows():
            inicio_periodo = inicio_semana if p["periodo"] == "Semanal" else inicio_mes

            gasto_real = 0.0
            if not transacciones.empty:
                filtro = (
                    (transacciones["categoria"] == p["categoria"])
                    & (transacciones["tipo"] == "Gasto")
                    & (transacciones["fecha"] >= inicio_periodo)
                )
                gasto_real = transacciones.loc[filtro, "monto_usd"].astype(float).sum()

            limite = float(p["monto_limite_usd"])
            progreso = min(gasto_real / limite, 1.0) if limite > 0 else 0

            st.write(f"**{p['categoria']}** · {p['periodo']}")
            st.progress(progreso, text=f"US$ {gasto_real:,.2f} / US$ {limite:,.2f}")

            nuevo_limite = st.number_input(
                f"Ajustar límite — {p['categoria']} ({p['periodo']})",
                min_value=0.0,
                value=limite,
                step=5.0,
                key=f"presupuesto_{p['id']}",
                label_visibility="collapsed",
            )
            if nuevo_limite != limite:
                if st.button("Guardar nuevo límite", key=f"guardar_{p['id']}"):
                    supabase.table("presupuestos").update(
                        {"monto_limite_usd": nuevo_limite}
                    ).eq("id", int(p["id"])).execute()
                    limpiar_cache()
                    st.success("Presupuesto actualizado.")
                    st.rerun()
            st.divider()

    with st.expander("➕ Nuevo presupuesto"):
        with st.form("form_nuevo_presupuesto"):
            categoria = st.selectbox("Categoría", lista_categorias())
            monto_limite = st.number_input("Límite (USD)", min_value=0.0, step=5.0)
            periodo = st.selectbox("Periodo", ["Semanal", "Mensual"])
            if st.form_submit_button("Crear presupuesto"):
                supabase.table("presupuestos").upsert(
                    {
                        "categoria": categoria,
                        "monto_limite_usd": monto_limite,
                        "periodo": periodo,
                    },
                    on_conflict="categoria,periodo",
                ).execute()
                limpiar_cache()
                st.success("Presupuesto guardado.")
                st.rerun()


# =======================================================================
# MÓDULO 3 · PAGOS PROGRAMADOS / CUENTAS POR PAGAR
# =======================================================================
with tab_pagos:
    st.subheader("Pagos programados (pendientes)")

    pagos = cargar_pagos_pendientes()
    cuentas = cargar_cuentas()

    if pagos.empty:
        st.info("No tienes pagos pendientes registrados. 🎉")
    else:
        for _, pago in pagos.iterrows():
            vence = pd.to_datetime(pago["fecha_vencimiento"]).date()
            dias_restantes = (vence - hoy).days if 'hoy' in dir() else (vence - datetime.date.today()).days

            urgencia = "🔴" if dias_restantes <= 3 else ("🟡" if dias_restantes <= 7 else "🟢")

            col1, col2 = st.columns([3, 1])
            with col1:
                st.write(f"{urgencia} **{pago['descripcion']}**")
                st.caption(
                    f"{pago['monto_original']} {pago['moneda_original']} "
                    f"(≈ US$ {pago['monto_usd']:,.2f})  ·  Vence: {vence.strftime('%d/%m/%Y')}"
                )
            with col2:
                if st.button("✅ Pagado", key=f"pagar_{pago['id']}"):
                    cuenta_id = pago["cuenta_id"]
                    if pd.isna(cuenta_id) and not cuentas.empty:
                        cuenta_id = int(cuentas.iloc[0]["id"])

                    # 1. Crear la transacción de gasto correspondiente
                    supabase.table("transacciones").insert(
                        {
                            "cuenta_id": int(cuenta_id),
                            "tipo": "Gasto",
                            "categoria": pago["categoria"],
                            "monto_original": float(pago["monto_original"]),
                            "moneda_original": pago["moneda_original"],
                            "notas": f"Pago programado: {pago['descripcion']}",
                        }
                    ).execute()

                    # 2. Marcar el pago programado como Pagado
                    supabase.table("pagos_programados").update(
                        {"estado": "Pagado"}
                    ).eq("id", int(pago["id"])).execute()

                    limpiar_cache()
                    st.success("Pago registrado como gasto. ✅")
                    st.rerun()
            st.divider()

    with st.expander("➕ Nuevo pago programado"):
        with st.form("form_nuevo_pago"):
            descripcion = st.text_input("Descripción (ej: Alquiler, Internet)")
            monto = st.number_input("Monto", min_value=0.0, step=1.0)
            moneda = st.selectbox("Moneda", ["VES", "USD", "EUR"])
            fecha_venc = st.date_input("Fecha de vencimiento", min_value=datetime.date.today())
            categoria = st.selectbox("Categoría", lista_categorias("Gasto"), key="cat_pago")
            cuenta_sel = None
            if not cuentas.empty:
                cuenta_sel = st.selectbox(
                    "Cuenta de pago", cuentas["nombre"], key="cuenta_pago"
                )
            if st.form_submit_button("Guardar pago programado"):
                cuenta_id = None
                if cuenta_sel is not None:
                    cuenta_id = int(cuentas.loc[cuentas["nombre"] == cuenta_sel, "id"].iloc[0])

                supabase.table("pagos_programados").insert(
                    {
                        "descripcion": descripcion,
                        "monto_original": monto,
                        "moneda_original": moneda,
                        "fecha_vencimiento": fecha_venc.isoformat(),
                        "categoria": categoria,
                        "cuenta_id": cuenta_id,
                    }
                ).execute()
                limpiar_cache()
                st.success("Pago programado creado.")
                st.rerun()


# =======================================================================
# MÓDULO 4 · MOVER DINERO (transferencias entre cuentas y compra/venta
# de dólares). Una compra de dólares es, en el fondo, una transferencia
# entre una cuenta VES y una cuenta USD con una tasa de conversión que
# tú indicas manualmente (porque la tasa paralela varía mucho y no
# siempre coincide con la del BCV).
# =======================================================================
with tab_mover:
    st.subheader("Transferencia entre cuentas / Compra o venta de dólares")

    cuentas = cargar_cuentas()

    if len(cuentas) < 2:
        st.info("Necesitas al menos 2 cuentas creadas para transferir entre ellas. Créalas en ⚙️ Ajustes.")
    else:
        nombres_cuentas = cuentas["nombre"].tolist()

        col_o, col_d = st.columns(2)
        with col_o:
            origen_nombre = st.selectbox("Cuenta origen (de dónde sale)", nombres_cuentas, key="mover_origen")
        with col_d:
            opciones_destino = [n for n in nombres_cuentas if n != origen_nombre]
            destino_nombre = st.selectbox("Cuenta destino (a dónde entra)", opciones_destino, key="mover_destino")

        origen = cuentas.loc[cuentas["nombre"] == origen_nombre].iloc[0]
        destino = cuentas.loc[cuentas["nombre"] == destino_nombre].iloc[0]

        monto_origen = st.number_input(
            f"Monto que sale de {origen_nombre} ({origen['moneda_nativa']})",
            min_value=0.0, step=1.0, key="mover_monto_origen",
        )

        monto_destino_final = monto_origen  # por defecto: sin conversión
        tasa_conversion = None

        if origen["moneda_nativa"] != destino["moneda_nativa"]:
            st.caption(
                f"⚠️ {origen_nombre} es {origen['moneda_nativa']} y {destino_nombre} es "
                f"{destino['moneda_nativa']} — indica la tasa que usaste en esta operación "
                "(la del mercado paralelo suele variar, así que no se asume la del BCV)."
            )
            tasa_conversion = st.number_input(
                "Tasa de conversión usada (VES por 1 USD)",
                min_value=0.0, step=0.5, key="mover_tasa",
            )
            if tasa_conversion and tasa_conversion > 0:
                if origen["moneda_nativa"] == "VES":
                    monto_destino_final = round(monto_origen / tasa_conversion, 2)
                else:
                    monto_destino_final = round(monto_origen * tasa_conversion, 2)

        monto_destino_final = st.number_input(
            f"Monto que entra a {destino_nombre} ({destino['moneda_nativa']}) — puedes ajustarlo (ej. si hubo comisión)",
            min_value=0.0, step=1.0, value=float(monto_destino_final), key="mover_monto_destino",
        )

        categoria_mov = st.selectbox(
            "Categoría (opcional, útil para compra/venta de divisas)",
            lista_categorias(), key="mover_categoria",
        )
        nota_mov = st.text_input("Nota (opcional)", key="mover_nota")

        if st.button("💸 Registrar movimiento", key="btn_mover"):
            if monto_origen <= 0 or monto_destino_final <= 0:
                st.error("El monto debe ser mayor a 0.")
            else:
                payload = {
                    "cuenta_id": int(origen["id"]),
                    "tipo": "Transferencia",
                    "categoria": categoria_mov,
                    "monto_original": monto_origen,
                    "moneda_original": origen["moneda_nativa"],
                    "notas": nota_mov,
                    "cuenta_destino_id": int(destino["id"]),
                    "monto_destino": monto_destino_final,
                }
                # Si hubo conversión manual, se pasa la tasa para que el
                # trigger calcule monto_usd correctamente (en vez de usar
                # la última tasa BCV, que puede no reflejar el mercado
                # paralelo del momento).
                if tasa_conversion:
                    payload["tasa_usada"] = tasa_conversion

                supabase.table("transacciones").insert(payload).execute()
                limpiar_cache()
                st.success(
                    f"✅ Movido: {monto_origen} {origen['moneda_nativa']} ({origen_nombre}) → "
                    f"{monto_destino_final} {destino['moneda_nativa']} ({destino_nombre})"
                )
                st.rerun()


# =======================================================================
# MÓDULO 5 · ASISTENTE IA (Gemini) — texto libre / foto de factura
# =======================================================================
with tab_ia:
    st.subheader("Registrar con IA (texto o comprobante)")

    import google.generativeai as genai

    if "GEMINI_API_KEY" not in st.secrets:
        st.warning("Configura GEMINI_API_KEY en Secrets para usar este módulo.")
    else:
        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
        modelo = genai.GenerativeModel("gemini-1.5-flash")

        cuentas = cargar_cuentas()

        PROMPT_BASE = f"""
Eres un asistente que extrae datos de una transacción financiera a partir de
texto libre en español (puede venir de una nota de voz transcrita) o del
texto de una factura/comprobante.

Categorías válidas: {lista_categorias()}
Cuentas disponibles: {cuentas['nombre'].tolist() if not cuentas.empty else []}

Responde ÚNICAMENTE con un JSON válido, sin texto adicional, sin backticks,
con esta forma exacta:
{{
  "tipo": "Ingreso" | "Gasto",
  "monto_original": <numero>,
  "moneda_original": "VES" | "USD" | "EUR",
  "categoria": "<una de las categorías válidas, la más cercana>",
  "cuenta_sugerida": "<una de las cuentas disponibles o null>",
  "notas": "<breve descripción de la transacción>"
}}
"""

        modo = st.radio("¿Cómo quieres registrar?", ["✍️ Texto / voz", "📷 Foto de comprobante"])

        contenido_extra = None
        texto_usuario = ""

        if modo == "✍️ Texto / voz":
            texto_usuario = st.text_area(
                "Describe el gasto o ingreso",
                placeholder="Ej: pagué 15 dólares de almuerzo en efectivo",
            )
        else:
            foto = st.file_uploader("Sube la foto del comprobante", type=["png", "jpg", "jpeg"])
            if foto:
                contenido_extra = {"mime_type": foto.type, "data": foto.getvalue()}
                st.image(foto, caption="Comprobante cargado", use_container_width=True)

        if st.button("🤖 Analizar con IA"):
            partes = [PROMPT_BASE]
            if texto_usuario:
                partes.append(f"Texto del usuario: {texto_usuario}")
            if contenido_extra:
                partes.append(contenido_extra)
                partes.append("Extrae los datos de la factura/comprobante de la imagen.")

            with st.spinner("Analizando con Gemini..."):
                try:
                    respuesta = modelo.generate_content(partes)
                    texto_json = respuesta.text.strip().strip("```json").strip("```").strip()
                    datos = json.loads(texto_json)
                    st.session_state["ia_borrador"] = datos
                except Exception as exc:
                    st.error(f"No se pudo interpretar la respuesta de la IA: {exc}")

        # -----------------------------------------------------------
        # Formulario de confirmación (siempre editable antes de guardar)
        # -----------------------------------------------------------
        if "ia_borrador" in st.session_state:
            st.divider()
            st.write("**Revisa y confirma antes de guardar:**")
            borrador = st.session_state["ia_borrador"]

            with st.form("form_confirmar_ia"):
                tipo = st.selectbox(
                    "Tipo", ["Gasto", "Ingreso"],
                    index=0 if borrador.get("tipo") != "Ingreso" else 1,
                )
                monto = st.number_input(
                    "Monto", min_value=0.0, value=float(borrador.get("monto_original", 0))
                )
                moneda = st.selectbox(
                    "Moneda", ["VES", "USD", "EUR"],
                    index=["VES", "USD", "EUR"].index(borrador.get("moneda_original", "USD"))
                    if borrador.get("moneda_original") in ["VES", "USD", "EUR"] else 1,
                )
                categorias_disp = lista_categorias(tipo)
                categoria = st.selectbox(
                    "Categoría", categorias_disp,
                    index=categorias_disp.index(borrador["categoria"])
                    if borrador.get("categoria") in categorias_disp else len(categorias_disp) - 1,
                )
                cuenta_sel = None
                if not cuentas.empty:
                    nombres = cuentas["nombre"].tolist()
                    sugerida = borrador.get("cuenta_sugerida")
                    idx_default = nombres.index(sugerida) if sugerida in nombres else 0
                    cuenta_sel = st.selectbox("Cuenta", nombres, index=idx_default)
                notas = st.text_input("Notas", value=borrador.get("notas", ""))

                if st.form_submit_button("💾 Guardar transacción"):
                    cuenta_id = int(cuentas.loc[cuentas["nombre"] == cuenta_sel, "id"].iloc[0])
                    supabase.table("transacciones").insert(
                        {
                            "cuenta_id": cuenta_id,
                            "tipo": tipo,
                            "categoria": categoria,
                            "monto_original": monto,
                            "moneda_original": moneda,
                            "notas": notas,
                        }
                    ).execute()
                    del st.session_state["ia_borrador"]
                    limpiar_cache()
                    st.success("Transacción guardada. ✅")
                    st.rerun()


# =======================================================================
# MÓDULO 6 · AJUSTES (crear cuentas y administrar categorías, sin SQL)
# =======================================================================
with tab_ajustes:
    st.subheader("Cuentas")

    cuentas = cargar_cuentas()
    if not cuentas.empty:
        st.dataframe(
            cuentas[["nombre", "moneda_nativa", "balance_inicial"]],
            hide_index=True, use_container_width=True,
        )
    else:
        st.info("Todavía no tienes cuentas creadas.")

    with st.expander("➕ Nueva cuenta"):
        with st.form("form_nueva_cuenta"):
            nombre_cuenta = st.text_input("Nombre (ej: Banesco, Binance, Efectivo USD)")
            moneda_cuenta = st.selectbox("Moneda de la cuenta", ["VES", "USD", "EUR"])
            balance_inicial = st.number_input("Balance inicial (en esa moneda)", step=1.0)
            if st.form_submit_button("Crear cuenta"):
                if not nombre_cuenta.strip():
                    st.error("El nombre no puede estar vacío.")
                elif not cuentas.empty and nombre_cuenta.strip() in cuentas["nombre"].values:
                    st.error(f"Ya existe una cuenta llamada '{nombre_cuenta.strip()}'.")
                else:
                    supabase.table("cuentas").insert(
                        {
                            "nombre": nombre_cuenta.strip(),
                            "moneda_nativa": moneda_cuenta,
                            "balance_inicial": balance_inicial,
                        }
                    ).execute()
                    limpiar_cache()
                    st.success(f"Cuenta '{nombre_cuenta.strip()}' creada.")
                    st.rerun()

    st.divider()
    st.subheader("Categorías")

    categorias_df = cargar_categorias()
    if not categorias_df.empty:
        st.dataframe(
            categorias_df[["nombre", "tipo"]],
            hide_index=True, use_container_width=True,
        )
    else:
        st.info("Todavía no tienes categorías creadas.")

    with st.expander("➕ Nueva categoría"):
        with st.form("form_nueva_categoria"):
            nombre_cat = st.text_input("Nombre de la categoría")
            tipo_cat = st.selectbox(
                "¿Para qué aplica?", ["Ambos", "Gasto", "Ingreso"],
                help="'Ambos' hace que aparezca tanto al registrar gastos como ingresos.",
            )
            if st.form_submit_button("Crear categoría"):
                if not nombre_cat.strip():
                    st.error("El nombre no puede estar vacío.")
                elif not categorias_df.empty and nombre_cat.strip() in categorias_df["nombre"].values:
                    st.error(f"Ya existe la categoría '{nombre_cat.strip()}'.")
                else:
                    supabase.table("categorias").insert(
                        {"nombre": nombre_cat.strip(), "tipo": tipo_cat}
                    ).execute()
                    limpiar_cache()
                    st.success(f"Categoría '{nombre_cat.strip()}' creada.")
                    st.rerun()

    with st.expander("🗑️ Eliminar una categoría"):
        if categorias_df.empty:
            st.caption("No hay categorías para eliminar.")
        else:
            cat_a_borrar = st.selectbox(
                "Selecciona la categoría a eliminar", categorias_df["nombre"].tolist(),
                key="cat_borrar",
            )
            st.caption(
                "⚠️ Esto no borra transacciones/presupuestos ya creados con esa "
                "categoría, solo evita que siga apareciendo como opción nueva."
            )
            if st.button("Eliminar categoría", key="btn_borrar_cat"):
                supabase.table("categorias").delete().eq("nombre", cat_a_borrar).execute()
                limpiar_cache()
                st.success(f"Categoría '{cat_a_borrar}' eliminada.")
                st.rerun()
