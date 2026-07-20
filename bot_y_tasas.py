"""
bot_y_tasas.py
==================================================================
Script diseñado para correr en GitHub Actions (cron diario, gratis).

Hace dos cosas:
  1. Obtiene la tasa oficial BCV (scraping) y el promedio P2P de
     Binance (API pública), y las guarda (upsert) en la tabla
     `tasas_cambio` de Supabase.
  2. Revisa `pagos_programados`: si hay compromisos 'Pendiente' que
     vencen hoy o en los próximos 3 días, envía una alerta por
     Telegram.

Variables de entorno requeridas (se configuran como GitHub Secrets):
  SUPABASE_URL          -> URL del proyecto Supabase
  SUPABASE_SERVICE_KEY  -> service_role key (NO la anon key; este
                            script corre en un servidor de confianza,
                            no en el teléfono del usuario)
  TELEGRAM_BOT_TOKEN    -> token del bot creado con @BotFather
  TELEGRAM_CHAT_ID      -> chat id del usuario (ver guía de despliegue)
==================================================================
"""

import os
import sys
import datetime
import requests
from bs4 import BeautifulSoup
from supabase import create_client, Client

# ---------------------------------------------------------------------
# Configuración / clientes
# ---------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    print("ERROR: faltan SUPABASE_URL / SUPABASE_SERVICE_KEY en el entorno.")
    sys.exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


# ---------------------------------------------------------------------
# 1) Scraping tasa BCV
# ---------------------------------------------------------------------
def obtener_tasa_bcv() -> float | None:
    """
    Extrae la tasa oficial USD del BCV desde su página pública.
    El BCV cambia el HTML de vez en cuando; si esto falla en el futuro,
    revisa el selector CSS/id del div del dólar en bcv.org.ve.
    """
    url = "https://www.bcv.org.ve/"
    try:
        resp = requests.get(url, verify=False, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # El BCV muestra el tipo de cambio dentro de un div con id="dolar"
        contenedor = soup.find(id="dolar")
        if not contenedor:
            print("ADVERTENCIA: no se encontró el contenedor 'dolar' en el HTML del BCV.")
            return None

        texto = contenedor.get_text(strip=True)
        # El texto suele venir como "USD48,1234" -> limpiamos y normalizamos
        texto = texto.replace("USD", "").replace(".", "").replace(",", ".").strip()
        return round(float(texto), 4)

    except Exception as exc:
        print(f"ERROR obteniendo tasa BCV: {exc}")
        return None


# ---------------------------------------------------------------------
# 2) Tasa promedio Binance P2P (USDT/VES, vendedores)
# ---------------------------------------------------------------------
def obtener_tasa_binance_p2p() -> float | None:
    """
    Consulta la API pública C2C de Binance para el par USDT/VES y
    calcula el promedio de los primeros anuncios de venta (SELL),
    que es la referencia típica de "dólar paralelo/Binance".
    """
    url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
    payload = {
        "asset": "USDT",
        "fiat": "VES",
        "tradeType": "SELL",
        "page": 1,
        "rows": 10,
        "payTypes": [],
        "publisherType": None,
    }
    headers = {"Content-Type": "application/json"}

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        precios = [
            float(item["adv"]["price"])
            for item in data.get("data", [])
            if "adv" in item and "price" in item["adv"]
        ]

        if not precios:
            print("ADVERTENCIA: Binance P2P no devolvió anuncios.")
            return None

        promedio = sum(precios) / len(precios)
        return round(promedio, 4)

    except Exception as exc:
        print(f"ERROR obteniendo tasa Binance P2P: {exc}")
        return None


# ---------------------------------------------------------------------
# 3) Guardar tasas en Supabase (upsert manual por fecha + tipo_tasa)
# ---------------------------------------------------------------------
# Nota: se evita el parámetro on_conflict de .upsert() porque en varias
# combinaciones de versiones de supabase-py/postgrest-py genera un error
# "PGRST125 Invalid path specified in request URL" al construir mal la
# URL. En su lugar, se busca si ya existe la fila del día y se decide
# entre update() o insert() — mismo resultado, sin ese bug.
def _guardar_una_tasa(fecha: str, tipo_tasa: str, valor_usd: float) -> None:
    existente = (
        supabase.table("tasas_cambio")
        .select("id")
        .eq("fecha", fecha)
        .eq("tipo_tasa", tipo_tasa)
        .execute()
        .data
    )

    if existente:
        supabase.table("tasas_cambio").update(
            {"valor_usd": valor_usd}
        ).eq("id", existente[0]["id"]).execute()
        print(f"Tasa {tipo_tasa} del {fecha} actualizada a {valor_usd}")
    else:
        supabase.table("tasas_cambio").insert(
            {"fecha": fecha, "tipo_tasa": tipo_tasa, "valor_usd": valor_usd}
        ).execute()
        print(f"Tasa {tipo_tasa} del {fecha} insertada: {valor_usd}")


def guardar_tasas(tasa_bcv: float | None, tasa_binance: float | None) -> None:
    hoy = datetime.date.today().isoformat()

    if not tasa_bcv and not tasa_binance:
        print("No hay tasas para guardar hoy.")
        return

    if tasa_bcv:
        _guardar_una_tasa(hoy, "BCV", tasa_bcv)
    if tasa_binance:
        _guardar_una_tasa(hoy, "Binance", tasa_binance)


# ---------------------------------------------------------------------
# 4) Alertas de Telegram
# ---------------------------------------------------------------------
def enviar_telegram(mensaje: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram no configurado, se omite el envío de alerta.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje,
        "parse_mode": "Markdown",
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        print("Alerta de Telegram enviada.")
    except Exception as exc:
        print(f"ERROR enviando mensaje de Telegram: {exc}")


def revisar_pagos_proximos() -> None:
    """
    Busca pagos_programados 'Pendiente' con vencimiento entre hoy
    y los próximos 3 días, y envía un resumen por Telegram.
    """
    hoy = datetime.date.today()
    limite = hoy + datetime.timedelta(days=3)

    respuesta = (
        supabase.table("pagos_programados")
        .select("*")
        .eq("estado", "Pendiente")
        .gte("fecha_vencimiento", hoy.isoformat())
        .lte("fecha_vencimiento", limite.isoformat())
        .order("fecha_vencimiento")
        .execute()
    )

    pagos = respuesta.data or []
    if not pagos:
        print("No hay pagos próximos a vencer.")
        return

    lineas = ["*Recordatorio de pagos próximos:*"]
    for p in pagos:
        monto = p.get("monto_usd") or p.get("monto_original")
        moneda = "USD" if p.get("monto_usd") else p.get("moneda_original")
        lineas.append(
            f"• {p['descripcion']} — {monto} {moneda} — vence {p['fecha_vencimiento']}"
        )

    enviar_telegram("\n".join(lineas))


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
if __name__ == "__main__":
    print("== Actualizando tasas de cambio ==")
    tasa_bcv = obtener_tasa_bcv()
    tasa_binance = obtener_tasa_binance_p2p()
    guardar_tasas(tasa_bcv, tasa_binance)

    print("== Revisando pagos programados ==")
    revisar_pagos_proximos()

    print("== Listo ==")
