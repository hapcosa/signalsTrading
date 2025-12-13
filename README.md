# 🤖 NeptuneBot - Trading Bot Multi-Usuario para BingX

Bot de trading automatizado que recibe señales de TradingView vía Telegram y ejecuta órdenes en BingX Futuros USDT (Isolated).

## 🌟 Características

✅ **Multi-usuario**: Cada usuario controla su propia cuenta BingX  
✅ **Configuración personalizada**: Cada usuario tiene su propia configuración de trading  
✅ **Lee mensajes de bots**: Usa Telethon para leer mensajes de otros bots  
✅ **Comandos por usuario**: Cada usuario solo controla sus posiciones  
✅ **Take Profit múltiple**: 3 niveles de TP configurables  
✅ **Stop Loss y Trailing Stop**: Gestión de riesgo automática  
✅ **Futuros USDT Isolated**: Modo aislado para mayor control  

---

## 📋 Requisitos

```bash
pip install python-dotenv telethon requests
```

---

## 🔧 Configuración

### 1. Obtener credenciales de Telegram API

1. Ve a https://my.telegram.org/auth
2. Inicia sesión con tu número de teléfono
3. Ve a "API development tools"
4. Crea una aplicación y obtén:
   - `api_id` (número)
   - `api_hash` (string)

### 2. Obtener el ID del grupo de Telegram

Ejecuta este script temporal:

```python
from telethon.sync import TelegramClient

api_id = 12345678  # Tu API ID
api_hash = "tu_api_hash"
phone = "+tu_telefono"

client = TelegramClient('temp', api_id, api_hash)
client.start(phone=phone)

for dialog in client.iter_dialogs():
    print(f"{dialog.name}: {dialog.id}")
```

Busca tu grupo y copia el ID (será negativo, ejemplo: `-1003415573034`)

### 3. Configurar variables de entorno

Copia `.env.example` a `.env` y completa:

```env
# Telegram API
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=tu_api_hash
TELEGRAM_PHONE=+1234567890
TELEGRAM_CHAT_ID=-1001234567890

# Usuarios de Telegram (sin @)
TELEGRAM_USERNAME=usuario1
TELEGRAM_USERNAME2=usuario2

# BingX Cuenta 1
BINGX_API_KEY=api_key_usuario1
BINGX_SECRET_KEY=secret_key_usuario1

# BingX Cuenta 2
BINGX2_API_KEY=api_key_usuario2
BINGX2_SECRET_KEY=secret_key_usuario2

LOG_LEVEL=INFO
```

### 4. Configurar `config.json`

Edita `config.json` y ajusta la configuración para cada usuario:

```json
{
  "users": {
    "usuario1": {
      "usdt_margin_per_trade": 10.0,
      "default_leverage": 15,
      "min_balance_required": 100,
      "tp1_percent": 2.5,
      "tp2_percent": 4.0,
      "tp3_percent": 6.0,
      "default_sl_percent": 2.0,
      "trailing_stop_percent": 2.5
    },
    "usuario2": {
      "usdt_margin_per_trade": 5.0,
      "default_leverage": 10,
      "min_balance_required": 50,
      "tp1_percent": 2.0,
      "tp2_percent": 3.5,
      "tp3_percent": 5.0,
      "default_sl_percent": 1.8,
      "trailing_stop_percent": 2.0
    }
  }
}
```

**⚠️ IMPORTANTE**: Los nombres de usuario en `config.json` deben coincidir con los valores de `TELEGRAM_USERNAME` y `TELEGRAM_USERNAME2` (sin @).

---

## 🚀 Uso

### Iniciar el bot

```bash
python main.py
```

La primera vez te pedirá un código de verificación que Telegram enviará a tu teléfono.

### Señales automáticas

El bot detecta automáticamente estos mensajes en el grupo:

```
BUY BTC      → Abre posición LONG en BTC-USDT para TODAS las cuentas
SELL ETH     → Abre posición SHORT en ETH-USDT para TODAS las cuentas
CLOSE SUI    → Cierra posición en SUI-USDT para TODAS las cuentas
```

### Comandos por usuario

Cada usuario puede usar comandos que solo afectan su propia cuenta:

```
/balance         → Ver tu balance
/positions       → Ver tus posiciones abiertas
/close BTC       → Cerrar tu posición en BTC
/help            → Ver ayuda
```

---

## 📊 Flujo de trabajo

```
TradingView
    ↓ (webhook)
Bot Telegram 1 (ParasitoBot)
    ↓ (envía mensaje)
Grupo de Telegram
    ↓ (lee mensaje)
NeptuneBot (Telethon)
    ↓ (ejecuta)
BingX (Usuario 1 + Usuario 2)
```

---

## 🎯 Ejemplo de configuración en TradingView

### Alerta de TradingView

**URL del webhook**: `https://api.telegram.org/bot<TU_BOT_TOKEN>/sendMessage`

**Método**: POST

**Cuerpo del mensaje (JSON)**:

```json
{
  "chat_id": "-1001234567890",
  "text": "BUY {{ticker}}"
}
```

Reemplaza:
- `<TU_BOT_TOKEN>` con el token del bot que envía señales
- `-1001234567890` con tu `TELEGRAM_CHAT_ID`
- `BUY` puede ser `BUY`, `SELL`, o `CLOSE`

---

## 📝 Logs

El bot muestra logs detallados:

```
2025-12-12 21:27:54 - INFO - 📨 Mensaje de 🤖 BOT ParasitoBot: SELL SUI
2025-12-12 21:27:54 - INFO - 🎯 SEÑAL DETECTADA: {'action': 'open', 'side': 'SELL', 'symbol': 'SUI'}
2025-12-12 21:27:54 - INFO - ============================================================
2025-12-12 21:27:54 - INFO - 👤 Ejecutando para @usuario1
2025-12-12 21:27:55 - INFO - 💰 Balance de @usuario1: $150.50
2025-12-12 21:27:58 - INFO - ✅ Posición abierta: 1999637394845032449
2025-12-12 21:27:58 - INFO -    SUI-USDT | SELL | Qty: 32.0
2025-12-12 21:27:58 - INFO -    Precio: $1.56 | Margen: $10.0
2025-12-12 21:28:01 - INFO - ============================================================
2025-12-12 21:28:01 - INFO - 👤 Ejecutando para @usuario2
2025-12-12 21:28:02 - INFO - 💰 Balance de @usuario2: $75.25
2025-12-12 21:28:04 - INFO - ✅ Posición abierta: 1999637394845032450
```

---

## 🔒 Seguridad

- ✅ Nunca compartas tus API keys
- ✅ Usa API keys con permisos limitados (solo trading)
- ✅ Mantén el archivo `.env` privado
- ✅ No subas `.env` a GitHub (ya está en `.gitignore`)

---

## 🐛 Solución de problemas

### El bot no abre en la segunda cuenta

**Causa**: En el código anterior solo abría en `self.exchanges[0]`

**Solución**: El nuevo código ejecuta para TODOS los usuarios en `execute_signal_for_all_users()`

### Los comandos no funcionan

**Causa**: El bot no reconoce al usuario

**Solución**: Verifica que:
1. `TELEGRAM_USERNAME` y `TELEGRAM_USERNAME2` estén correctos (sin @)
2. Los nombres en `config.json` coincidan exactamente
3. El usuario esté usando su cuenta de Telegram correcta

### Error "Balance bajo"

**Causa**: No hay suficiente USDT en la cuenta

**Solución**: Deposita más USDT o reduce `usdt_margin_per_trade` en `config.json`

### Error "Cantidad = 0"

**Causa**: El margen es muy bajo para el mínimo del contrato

**Solución**: Aumenta `usdt_margin_per_trade` en `config.json`

---

## 📖 Configuración avanzada

### Personalizar Take Profit

En `config.json`:

```json
{
  "users": {
    "usuario1": {
      "tp1_percent": 2.0,   
      "tp2_percent": 3.5,   
      "tp3_percent": 5.0    
    }
  }
}
```

### Ajustar apalancamiento

```json
{
  "users": {
    "usuario1": {
      "default_leverage": 20 
    }
  }
}
```

### Cambiar Stop Loss

```json
{
  "users": {
    "usuario1": {
      "default_sl_percent": 3.0,  
      "trailing_stop_percent": 2.5 
    }
  }
}
```

---

## 🤝 Soporte

Si tienes problemas:

1. Revisa los logs en la terminal
2. Verifica que todas las credenciales sean correctas
3. Asegúrate de tener balance suficiente en BingX

---

## ⚠️ Disclaimer

El trading de futuros conlleva riesgos significativos. Nunca inviertas dinero que no puedas permitirte perder.

---

## 📄 Licencia

MIT License - Úsalo bajo tu propio riesgo