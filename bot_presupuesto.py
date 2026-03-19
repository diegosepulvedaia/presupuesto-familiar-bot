import discord
import gspread
from google.oauth2.service_account import Credentials
import json
import os
import httpx
from anthropic import Anthropic
from datetime import datetime

# ============================================================
# CONFIGURACIÓN
# ============================================================
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")
SPREADSHEET_NAME = "Presupuesto Familiar 2026"
SHEET_NAME = "Gastos"

# Mapeo de usuarios Discord a nombres reales
USUARIOS = {
    "diego.sepu3908": "Diego",
    # Agrega aquí el username de Mariana: "username_mariana": "Mariana",
}

CATEGORIAS = {
    "1": "Supermercado",
    "2": "Transporte",
    "3": "Salud",
    "4": "Hogar",
    "5": "Entretenimiento Mariana",
    "6": "Entretenimiento Diego",
    "7": "Otro"
}

MENU_CATEGORIAS = """
1️⃣ Supermercado
2️⃣ Transporte
3️⃣ Salud
4️⃣ Hogar
5️⃣ Entretenimiento Mariana
6️⃣ Entretenimiento Diego
7️⃣ Otro
"""

# ============================================================
# MEMORIA CONVERSACIONAL
# Guarda el estado de conversaciones pendientes por usuario
# formato: {user_id: {"accion": "esperando_comentario", "fila": N}}
# ============================================================
estado_usuarios = {}

# ============================================================
# CONFIGURACIÓN DE DISCORD
# ============================================================
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# ============================================================
# CONFIGURACIÓN DE GOOGLE SHEETS
# ============================================================
def get_creds():
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    return Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"]
    )

def get_sheet():
    gc = gspread.authorize(get_creds())
    sh = gc.open(SPREADSHEET_NAME)
    return sh.worksheet(SHEET_NAME)

def get_budget_sheet():
    gc = gspread.authorize(get_creds())
    sh = gc.open(SPREADSHEET_NAME)
    return sh.worksheet("Presupuesto")

# ============================================================
# ANALIZAR BOLETA CON CLAUDE
# ============================================================
def analizar_boleta(image_url, categoria_num):
    anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY, http_client=httpx.Client())
    categoria = CATEGORIAS.get(categoria_num, "Otro")

    message = anthropic_client.messages.create(
        model="claude-opus-4-20250514",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "url",
                            "url": image_url,
                        },
                    },
                    {
                        "type": "text",
                        "text": f"""Eres un asistente de control presupuestario familiar chileno.
Analiza esta foto de boleta o ticket y extrae la información en formato JSON.
La categoría ya fue seleccionada por el usuario: {categoria}

Responde SOLO con este JSON exacto, sin texto adicional:
{{
  "fecha": "DD/MM/YYYY",
  "comercio": "nombre del local",
  "categoria": "{categoria}",
  "monto": 0,
  "descripcion": "resumen breve de la compra"
}}

Si no puedes leer algún dato, usa "No disponible" para texto o 0 para monto.
El monto debe ser solo el número, sin puntos ni comas ni símbolo $."""
                    }
                ],
            }
        ],
    )

    response_text = message.content[0].text.strip()
    if response_text.startswith("```"):
        response_text = response_text.split("```")[1]
        if response_text.startswith("json"):
            response_text = response_text[4:]

    return json.loads(response_text.strip())

# ============================================================
# CONSULTAS DE PRESUPUESTO
# ============================================================
def consultar_presupuesto(pregunta, username):
    try:
        ahora = datetime.now()
        mes_actual = ahora.month
        año_actual = ahora.year
        mes_str = ahora.strftime("%m/%Y")

        sheet = get_sheet()
        gastos = sheet.get_all_records()
        gastos_mes = [
            g for g in gastos
            if f"/{ahora.strftime('%m')}/" in str(g.get("Fecha", ""))
            and str(g.get("Fecha", "")).endswith(str(año_actual))
        ]

        totales = {}
        for gasto in gastos_mes:
            cat = gasto.get("Categoría", "Otro")
            monto = float(str(gasto.get("Monto", 0)).replace(".", "").replace(",", "") or 0)
            totales[cat] = totales.get(cat, 0) + monto

        resumen = f"Gastos del mes ({mes_str}):\n"
        for cat, total in totales.items():
            resumen += f"- {cat}: ${total:,.0f}\n"

        budget_sheet = get_budget_sheet()
        presupuestos = budget_sheet.get_all_records()
        presupuestos_mes = [
            p for p in presupuestos
            if str(p.get("Mes", "")).strip() == str(mes_actual)
            and str(p.get("Año", "")).strip() == str(año_actual)
        ]

        resumen += "\nPresupuestos del mes:\n"
        for p in presupuestos_mes:
            cat = p.get("Categoría", "")
            monto_presupuesto = float(str(p.get("Presupuesto", 0)).replace(".", "").replace(",", "") or 0)
            gastado = totales.get(cat, 0)
            disponible = monto_presupuesto - gastado
            if monto_presupuesto > 0:
                resumen += f"- {cat}: Presupuesto ${monto_presupuesto:,.0f} | Gastado ${gastado:,.0f} | Disponible ${disponible:,.0f}\n"

        anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY, http_client=httpx.Client())
        response = anthropic_client.messages.create(
            model="claude-opus-4-20250514",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": f"""Eres un asistente de presupuesto familiar amigable.
El usuario pregunta: "{pregunta}"

Aquí están los datos actuales:
{resumen}

Responde de forma concisa y amigable en español, usando emojis. Máximo 5 líneas."""
            }]
        )

        return response.content[0].text

    except Exception as e:
        return f"❌ Error al consultar el presupuesto: {str(e)}"

# ============================================================
# MODIFICAR PRESUPUESTO
# ============================================================
def modificar_presupuesto(mensaje):
    try:
        ahora = datetime.now()
        mes_actual = ahora.month
        año_actual = ahora.year

        # Usar Claude para extraer categoría y monto del mensaje
        anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY, http_client=httpx.Client())
        response = anthropic_client.messages.create(
            model="claude-opus-4-20250514",
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": f"""Extrae la categoría y el monto del siguiente mensaje de cambio de presupuesto.
Las categorías válidas son: Supermercado, Transporte, Salud, Hogar, Entretenimiento Mariana, Entretenimiento Diego, Otro.

Mensaje: "{mensaje}"

Responde SOLO con este JSON exacto:
{{"categoria": "nombre exacto de la categoría", "monto": 0}}

Si no puedes identificar la categoría o el monto, responde:
{{"categoria": null, "monto": null}}"""
            }]
        )

        data = json.loads(response.content[0].text.strip())
        categoria = data.get("categoria")
        monto = data.get("monto")

        if not categoria or not monto:
            return "❌ No pude entender la categoría o el monto. Intenta así: 'cambia presupuesto salud a 200000'"

        # Buscar y actualizar en Google Sheets
        budget_sheet = get_budget_sheet()
        presupuestos = budget_sheet.get_all_records()

        for i, p in enumerate(presupuestos):
            if (str(p.get("Mes", "")).strip() == str(mes_actual)
                    and str(p.get("Año", "")).strip() == str(año_actual)
                    and p.get("Categoría", "").lower() == categoria.lower()):
                # +2 porque get_all_records es 0-indexed y hay fila de encabezado
                fila = i + 2
                col_presupuesto = 4  # Columna D
                budget_sheet.update_cell(fila, col_presupuesto, monto)
                return f"✅ Presupuesto de **{categoria}** actualizado a **${monto:,}** para {ahora.strftime('%B %Y')} 📊"

        # Si no existe la fila para este mes, la creamos
        budget_sheet.append_row([mes_actual, año_actual, categoria, monto])
        return f"✅ Presupuesto de **{categoria}** establecido en **${monto:,}** para {ahora.strftime('%B %Y')} 📊"

    except Exception as e:
        return f"❌ Error al modificar el presupuesto: {str(e)}"

# ============================================================
# EVENTOS DE DISCORD
# ============================================================
@client.event
async def on_ready():
    print(f"✅ Bot conectado como {client.user}")

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    username = str(message.author.name)
    user_id = message.author.id
    nombre_real = USUARIOS.get(username, username)
    contenido = message.content.strip()

    # ============================================================
    # ESTADO: esperando comentario
    # ============================================================
    if user_id in estado_usuarios and estado_usuarios[user_id]["accion"] == "esperando_comentario":
        fila = estado_usuarios[user_id]["fila"]
        del estado_usuarios[user_id]

        if contenido.lower() in ["no", "n", "omitir", "-"]:
            await message.reply("👍 Boleta guardada sin comentario.")
        else:
            try:
                sheet = get_sheet()
                col_comentario = 7  # Columna G
                sheet.update_cell(fila, col_comentario, contenido)
                await message.reply(f"💬 Comentario guardado: *\"{contenido}\"*")
            except Exception as e:
                await message.reply(f"❌ Error al guardar comentario: {str(e)}")
        return

    # ============================================================
    # IMAGEN: registrar boleta
    # ============================================================
    if message.attachments:
        attachment = message.attachments[0]

        if not any(attachment.filename.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
            await message.reply("❌ Por favor envía la imagen en formato JPG o PNG.")
            return

        categoria_num = contenido.strip() if contenido.strip() in CATEGORIAS else None

        if not categoria_num:
            await message.reply(f"📋 Recibí tu boleta. ¿En qué categoría la registro?\n{MENU_CATEGORIAS}\nResponde con el número junto a la foto.")
            return

        await message.reply("🔍 Analizando tu boleta...")

        try:
            datos = analizar_boleta(attachment.url, categoria_num)

            sheet = get_sheet()
            sheet.append_row([
                datos.get("fecha", datetime.now().strftime("%d/%m/%Y")),
                datos.get("comercio", "No disponible"),
                datos.get("categoria", "Otro"),
                datos.get("monto", 0),
                datos.get("descripcion", ""),
                nombre_real,
                ""  # Comentario vacío por ahora
            ])

            # Obtener número de la última fila agregada
            ultima_fila = len(sheet.get_all_values())

            # Guardar estado esperando comentario
            estado_usuarios[user_id] = {
                "accion": "esperando_comentario",
                "fila": ultima_fila
            }

            confirmacion = f"""✅ **Boleta registrada!**
🏪 Comercio: {datos.get('comercio', 'No disponible')}
💰 Monto: ${datos.get('monto', 0):,}
🗂️ Categoría: {datos.get('categoria', 'Otro')}
👤 Responsable: {nombre_real}

💬 ¿Quieres agregar un comentario? Escríbelo o responde `no` para omitir.

`1`Super `2`Trans `3`Salud `4`Hogar `5`EntMari `6`EntDiego `7`Otro"""

            await message.reply(confirmacion)

        except Exception as e:
            await message.reply(f"❌ Error al procesar la boleta: {str(e)}\nIntenta de nuevo.")

    # ============================================================
    # MODIFICAR PRESUPUESTO
    # ============================================================
    elif any(palabra in contenido.lower() for palabra in ["cambia presupuesto", "cambiar presupuesto", "actualiza presupuesto", "modifica presupuesto", "cambia el presupuesto", "cambiar el presupuesto"]):
        await message.reply("⚙️ Actualizando presupuesto...")
        respuesta = modificar_presupuesto(contenido)
        await message.reply(respuesta)

    # ============================================================
    # CONSULTAR PRESUPUESTO
    # ============================================================
    elif any(palabra in contenido.lower() for palabra in ["cuánto", "cuanto", "presupuesto", "queda", "gastamos", "gasté", "gaste", "resumen", "disponible"]):
        await message.reply("🤔 Consultando tu presupuesto...")
        respuesta = consultar_presupuesto(contenido, username)
        await message.reply(respuesta)

# ============================================================
# INICIAR BOT
# ============================================================
client.run(DISCORD_TOKEN)
