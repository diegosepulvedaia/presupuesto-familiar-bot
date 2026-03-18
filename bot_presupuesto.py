import discord
import anthropic
import gspread
from google.oauth2.service_account import Credentials
import json
import os
from datetime import datetime

# ============================================================
# CONFIGURACIÓN - Reemplaza estos valores con los tuyos
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
# CONFIGURACIÓN DE DISCORD
# ============================================================
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# ============================================================
# CONFIGURACIÓN DE GOOGLE SHEETS
# ============================================================
def get_sheet():
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"]
    )
    gc = gspread.authorize(creds)
    sh = gc.open(SPREADSHEET_NAME)
    return sh.worksheet(SHEET_NAME)

def get_budget_sheet():
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"]
    )
    gc = gspread.authorize(creds)
    sh = gc.open(SPREADSHEET_NAME)
    return sh.worksheet("Presupuesto")

# ============================================================
# ANALIZAR BOLETA CON CLAUDE
# ============================================================
def analizar_boleta(image_url, categoria_num):
    anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
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
    
    response_text = message.content[0].text
    # Limpiar el JSON
    response_text = response_text.strip()
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
        sheet = get_sheet()
        gastos = sheet.get_all_records()
        
        budget_sheet = get_budget_sheet()
        presupuestos = budget_sheet.get_all_records()
        
        mes_actual = datetime.now().strftime("%m/%Y")
        
        # Filtrar gastos del mes actual
        gastos_mes = [g for g in gastos if str(g.get("Fecha", "")).endswith(mes_actual[-4:]) 
                     and f"/{mes_actual[:2]}/" in str(g.get("Fecha", ""))]
        
        resumen = f"Gastos del mes ({mes_actual}):\n"
        totales = {}
        for gasto in gastos_mes:
            cat = gasto.get("Categoría", "Otro")
            monto = float(str(gasto.get("Monto", 0)).replace(".", "").replace(",", "") or 0)
            totales[cat] = totales.get(cat, 0) + monto
        
        for cat, total in totales.items():
            resumen += f"- {cat}: ${total:,.0f}\n"
        
        resumen += "\nPresupuestos:\n"
        for p in presupuestos:
            cat = p.get("Categoría", "")
            monto_presupuesto = float(str(p.get("Presupuesto Mensual", 0)).replace(".", "").replace(",", "") or 0)
            gastado = totales.get(cat, 0)
            disponible = monto_presupuesto - gastado
            if monto_presupuesto > 0:
                resumen += f"- {cat}: Presupuesto ${monto_presupuesto:,.0f} | Gastado ${gastado:,.0f} | Disponible ${disponible:,.0f}\n"
        
        anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
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
# EVENTOS DE DISCORD
# ============================================================
@client.event
async def on_ready():
    print(f"✅ Bot conectado como {client.user}")

@client.event
async def on_message(message):
    # Ignorar mensajes del propio bot
    if message.author == client.user:
        return
    
    username = str(message.author.name)
    nombre_real = USUARIOS.get(username, username)
    contenido = message.content.strip()
    
    # ¿Tiene imagen adjunta?
    if message.attachments:
        attachment = message.attachments[0]
        
        # Verificar que es una imagen
        if not any(attachment.filename.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
            await message.reply("❌ Por favor envía la imagen en formato JPG o PNG.")
            return
        
        # Verificar que incluye número de categoría
        categoria_num = contenido.strip() if contenido.strip() in CATEGORIAS else None
        
        if not categoria_num:
            await message.reply(f"📋 Recibí tu boleta. ¿En qué categoría la registro?\n{MENU_CATEGORIAS}\nResponde con el número de la categoría junto a la foto.")
            return
        
        # Procesar la boleta
        await message.reply("🔍 Analizando tu boleta...")
        
        try:
            datos = analizar_boleta(attachment.url, categoria_num)
            
            # Guardar en Google Sheets
            sheet = get_sheet()
            sheet.append_row([
                datos.get("fecha", datetime.now().strftime("%d/%m/%Y")),
                datos.get("comercio", "No disponible"),
                datos.get("categoria", "Otro"),
                datos.get("monto", 0),
                datos.get("descripcion", ""),
                nombre_real
            ])
            
            # Confirmar en Discord
            confirmacion = f"""✅ **Boleta registrada!**
🏪 Comercio: {datos.get('comercio', 'No disponible')}
💰 Monto: ${datos.get('monto', 0):,}
🗂️ Categoría: {datos.get('categoria', 'Otro')}
👤 Responsable: {nombre_real}

`1`Super `2`Trans `3`Salud `4`Hogar `5`EntMari `6`EntDiego `7`Otro"""
            
            await message.reply(confirmacion)
            
        except Exception as e:
            await message.reply(f"❌ Error al procesar la boleta: {str(e)}\nIntenta de nuevo.")
    
    # ¿Es una pregunta sobre el presupuesto?
    elif any(palabra in contenido.lower() for palabra in ["cuánto", "cuanto", "presupuesto", "queda", "gastamos", "gasté", "gaste", "resumen", "disponible"]):
        await message.reply("🤔 Consultando tu presupuesto...")
        respuesta = consultar_presupuesto(contenido, username)
        await message.reply(respuesta)

# ============================================================
# INICIAR BOT
# ============================================================
client.run(DISCORD_TOKEN)
