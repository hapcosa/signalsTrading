#!/usr/bin/env python3
"""
Script para obtener tu Telegram User ID
Ejecuta este script para conocer tu ID de Telegram
"""

from telethon.sync import TelegramClient
import os
from dotenv import load_dotenv

load_dotenv()

# Obtener credenciales del .env
api_id = int(os.getenv("TELEGRAM_API_ID"))
api_hash = os.getenv("TELEGRAM_API_HASH")
phone = os.getenv("TELEGRAM_PHONE")

print("=" * 60)
print("🔍 OBTENIENDO TU TELEGRAM USER ID")
print("=" * 60)

# Crear cliente temporal
client = TelegramClient('get_id_session', api_id, api_hash)
client.start(phone=phone)

# Obtener tu propia información
me = client.get_me()

print(f"\n✅ TU INFORMACIÓN:")
print(f"   Nombre: {me.first_name} {me.last_name or ''}")
print(f"   Username: @{me.username}" if me.username else "   Username: (no configurado)")
print(f"   📱 TELEGRAM USER ID: {me.id}")
print(f"\n💡 Agrega esta línea a tu .env:")
print(f"   TELEGRAM_USER_ID={me.id}")
print(f"   TELEGRAM_USERNAME='{me.first_name} {me.last_name or ''}'.strip()")

# Si quieres ver los IDs de los miembros del grupo
chat_id = int(os.getenv("TELEGRAM_CHAT_ID"))
print(f"\n📋 MIEMBROS DEL GRUPO (chat {chat_id}):")
print("=" * 60)

try:
    participants = client.get_participants(chat_id)
    for user in participants:
        name = f"{user.first_name or ''} {user.last_name or ''}".strip()
        username = f"@{user.username}" if user.username else "(sin username)"
        user_type = "🤖 BOT" if user.bot else "👤 Usuario"
        print(f"{user_type} | ID: {user.id:15} | {name:30} | {username}")
except Exception as e:
    print(f"⚠️ No se pudo obtener la lista de miembros: {e}")

print("\n" + "=" * 60)
print("✅ Para configurar el segundo usuario:")
print("   1. Pídele a la otra persona que ejecute este script")
print("   2. Agrega su ID al .env como TELEGRAM_USER_ID2")
print("=" * 60)

client.disconnect()