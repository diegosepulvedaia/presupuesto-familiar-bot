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
# ============================================================
estado_usuarios = {}

# ============================================================
# CONFIGURACIÓN DE DISCORD
# ============================================================
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# ============================================================
# GOOGLE SHEETS
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
    return gc.open(SPREADSHEET_NAME).worksheet(SHEET_NAME)

def get_budget_sheet():
    gc = gspread.authorize(get_creds())
    return gc.open(SPREADSHEET_NAME).worksheet("Presupuesto")

def get_contexto_financiero():
    """Obtiene todos los datos financieros del mes actual para dárselos al agente."""
    ahora = datetime.now()
    mes_actual = ahora.month
    año_actual = ahora.year
    mes_str = ahora.strftime("%B %Y")

    # Gastos del mes
    sheet = get_sheet()
    gastos = sheet.get_all_records()
    gastos_mes = [
        g for g in gastos
        if f"/{ahora.strftime('%m')}/" in str(g.get("Fecha", ""))
        and str(g.get("Fecha", "")).endswith(str(año_actual))
    ]

    totales = {}
    detalle_gastos = []
    for g in gastos_mes:
        cat = g.get("Categoría", "Otro")
        monto = float(str(g.get("Monto", 0)).replace(".", "").replace(",", "") or 0)
        totales[cat] = totales.get(cat, 0) + monto
        detalle_gastos.append(f"- {g.get('Fecha','')} | {g.get('Comercio','')} | {cat} | ${monto:,.0f} | {g.get('Responsable','')}")

    # Presupuestos del mes
    budget_sheet = get_budget_sheet()
    presupuestos = budget_sheet.get_all_records()
    presupuestos_mes = [
        p for p in presupuestos
        if str(p.get("Mes", "")).strip() == str(mes_actual)
        and str(p.get("Año", "")).strip() == str(año_actual)
    ]

    resumen_presupuesto = []
    for p in presupuestos_mes:
        cat = p.get("Categoría", "")
        presupuesto = float(str(p.get("Presupuesto", 0)).replace(".", "").replace(",", "") or 0)
        gastado = totales.get(cat, 0)
        disponible = presupuesto - gastado
        porcentaje = (gastado / presupuesto * 100) if presupuesto > 0 else 0
        resumen_presupuesto.append(
            f"- {cat}: Presupuesto ${presupuesto:,.0f} | Gastado ${gastado:,.0f} | Disponible ${disponible:,.0f} | {porcentaje:.0f}% usado"
        )

    contexto = f"""=== CONTEXTO FINANCIERO FAMILIAR - {mes_str} ===

GASTOS DEL MES:
{chr(10).join(detalle_gastos) if detalle_gastos else 'Sin gastos registrados aún'}

RESUMEN POR CATEGORÍA:
{chr(10).join([f'- {cat}: ${total:,.0f}' for cat, total in totales.items()]) if totales else 'Sin gastos'}

PRESUPUESTOS VS GASTOS:
{chr(10).join(resumen_presupuesto) if resumen_presupuesto else 'Sin presupuestos definidos para este mes'}

TOTAL GASTADO: ${sum(totales.values()):,.0f}
DÍAS DEL MES: {ahora.day} de {ahora.strftime('%B')}
"""
    return contexto, presupuestos_mes, totales, mes_actual, año_actual

# ============================================================
# ANALIZAR BOLETA CON CLAUDE
# ============================================================
def analizar_boleta(image_url, categoria_num):
    anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY, http_client=httpx.Client())
    categoria = CATEGORIAS.get(categoria_num, "Otro")

    message = anthropic_client.messages.create(
        model="claude-opus-4-20250514",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "url", "url": image_url},
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
        }],
    )

    response_text = message.content[0].text.strip()
    if response_text.startswith("```"):
        response_text = response_text.split("```")[1]
        if response_text.startswith("json"):
            response_text = response_text[4:]

    return json.loads(response_text.strip())

# ============================================================
# AGENTE IA - procesa cualquier mensaje de texto
# ============================================================
def agente_ia(mensaje, nombre_real):
    try:
        contexto, presupuestos_mes, totales, mes_actual, año_actual = get_contexto_financiero()
        anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY, http_client=httpx.Client())

        response = anthropic_client.messages.create(
            model="claude-opus-4-20250514",
            max_tokens=800,
            system=f"""Eres un asistente financiero familiar amigable llamado "Presupuesto Familiar MyD".
Ayudas a Diego y Mariana a llevar el control de sus gastos mensuales.

Tienes acceso a los datos financieros del mes actual y puedes:
1. Responder preguntas sobre gastos y presupuesto
2. Dar consejos financieros
3. Indicar si quieren MODIFICAR un presupuesto (detecta la intención)

Si el usuario quiere MODIFICAR un presupuesto, responde SOLO con este JSON exacto:
{{"accion": "modificar_presupuesto", "categoria": "nombre exacto", "monto": 0}}

Las categorías válidas son: Supermercado, Transporte, Salud, Hogar, Entretenimiento Mariana, Entretenimiento Diego, Otro.

Para cualquier otra consulta, responde de forma conversacional, amigable y con emojis. Máximo 6 líneas.
El usuario que escribe ahora es: {nombre_real}

{contexto}""",
            messages=[{"role": "user", "content": mensaje}]
        )

        respuesta = response.content[0].text.strip()

        # Verificar si el agente quiere modificar presupuesto
        if respuesta.startswith("{") and "modificar_presupuesto" in respuesta:
            try:
                data = json.loads(respuesta)
                categoria = data.get("categoria")
                monto = data.get("monto")

                if categoria and monto:
                    budget_sheet = get_budget_sheet()
                    presupuestos = budget_sheet.get_all_records()

                    actualizado = False
                    for i, p in enumerate(presupuestos):
                        if (str(p.get("Mes", "")).strip() == str(mes_actual)
                                and str(p.get("Año", "")).strip() == str(año_actual)
                                and p.get("Categoría", "").lower() == categoria.lower()):
                            budget_sheet.update_cell(i + 2, 4, monto)
                            actualizado = True
                            break

                    if not actualizado:
                        budget_sheet.append_row([mes_actual, año_actual, categoria, monto])

                    gastado = totales.get(categoria, 0)
                    disponible = monto - gastado
                    return f"✅ **Presupuesto de {categoria} actualizado a ${monto:,}**\n💰 Gastado: ${gastado:,} | Disponible: ${disponible:,}"

            except json.JSONDecodeError:
                pass

        return respuesta

    except Exception as e:
        return f"❌ Error: {str(e)}"

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
                sheet.update_cell(fila, 7, contenido)
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
                ""
            ])

            ultima_fila = len(sheet.get_all_values())
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
    # CUALQUIER MENSAJE DE TEXTO → AGENTE IA
    # ============================================================
    elif contenido:
        await message.reply("🤔 Consultando...")
        respuesta = agente_ia(contenido, nombre_real)
        await message.reply(respuesta)

# ============================================================
# INICIAR BOT
# ============================================================
client.run(DISCORD_TOKEN)
