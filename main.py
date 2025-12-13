#!/usr/bin/env python3
"""
Bot de Trading Automatizado para BingX - FUTUROS USDT ISOLATED
Usa Telethon (userbot) para leer mensajes de otros bots en Telegram
"""

import os
import json
import logging
from typing import Dict, Optional, List
from datetime import datetime
from dotenv import load_dotenv
from telethon import TelegramClient, events
import hmac
import hashlib
import time
import requests
import re
import asyncio

load_dotenv()

log_level = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, log_level)
)
logger = logging.getLogger(__name__)


class ConfigManager:
    """Gestor de configuración JSON"""

    def __init__(self, config_path: str = "config.json"):
        with open(config_path, 'r') as f:
            self.config = json.load(f)

    def get(self, *keys, default=None):
        """Obtiene valor de configuración anidada"""
        value = self.config
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
                if value is None:
                    return default
            else:
                return default
        return value


class ExchangeAPI:
    """Clase base para interactuar con exchanges"""

    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret

    def _generate_signature(self, params: str, secret: str) -> str:
        """Genera firma HMAC SHA256"""
        return hmac.new(
            secret.encode('utf-8'),
            params.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()


class BingXAPI(ExchangeAPI):
    """API para BingX - Futuros USDT ISOLATED"""

    def __init__(self, api_key: str, api_secret: str):
        super().__init__(api_key, api_secret)
        self.base_url = "https://open-api.bingx.com"
        self.name = "BingX"

    def is_available(self) -> bool:
        """Verifica si el exchange está disponible"""
        return bool(self.api_key and self.api_secret)

    def get_balance(self) -> float:
        """Obtiene balance USDT disponible"""
        try:
            endpoint = "/openApi/swap/v2/user/balance"
            timestamp = int(time.time() * 1000)
            params = {"timestamp": timestamp}

            response = self._make_request("GET", endpoint, params)
            if response and response.get("code") == 0:
                balance_data = response.get("data", {}).get("balance", {})
                if isinstance(balance_data, dict):
                    available = balance_data.get("availableMargin", "0")
                elif isinstance(balance_data, list):
                    for bal in balance_data:
                        if bal.get("asset") == "USDT":
                            available = bal.get("availableMargin", "0")
                            break
                    else:
                        return 0.0
                else:
                    return 0.0

                return float(available)
            return 0.0
        except Exception as e:
            logger.error(f"Error obteniendo balance BingX: {e}")
            return 0.0

    def get_current_price(self, symbol: str) -> float:
        """Obtiene precio actual del símbolo"""
        try:
            endpoint = "/openApi/swap/v2/quote/ticker"
            params = {"symbol": symbol}
            response = self._make_request("GET", endpoint, params)

            if response and response.get("code") == 0:
                return float(response["data"]["lastPrice"])
            return 0.0
        except Exception as e:
            logger.error(f"Error obteniendo precio BingX: {e}")
            return 0.0

    def get_open_positions(self, symbol: Optional[str] = None) -> List[Dict]:
        """Obtiene posiciones abiertas"""
        try:
            endpoint = "/openApi/swap/v2/user/positions"
            timestamp = int(time.time() * 1000)
            params = {"timestamp": timestamp}

            if symbol:
                params["symbol"] = symbol

            response = self._make_request("GET", endpoint, params)

            if response and response.get("code") == 0:
                positions = response.get("data", [])
                active_positions = [
                    pos for pos in positions
                    if float(pos.get("positionAmt", 0)) != 0
                ]
                return active_positions
            return []
        except Exception as e:
            logger.error(f"Error obteniendo posiciones BingX: {e}")
            return []

    def set_margin_mode(self, symbol: str, margin_type: str = "ISOLATED"):
        """Configura el margin mode en ISOLATED"""
        try:
            endpoint = "/openApi/swap/v2/trade/marginType"
            timestamp = int(time.time() * 1000)
            params = {
                "symbol": symbol,
                "marginType": margin_type,
                "timestamp": timestamp
            }

            response = self._make_request("POST", endpoint, params)

            if response and response.get("code") == 0:
                logger.info(f"✅ Margin mode: {margin_type} para {symbol}")
                return True
            elif response.get("code") == 100412:
                logger.info(f"ℹ️ {symbol} ya está en modo {margin_type}")
                return True
            else:
                logger.warning(f"⚠️ Margin mode response: {response}")
                return False

        except Exception as e:
            logger.error(f"Error configurando margin mode: {e}")
            return False

    def get_contract_info(self, symbol: str) -> Dict:
        """Obtiene información del contrato"""
        try:
            endpoint = "/openApi/swap/v2/quote/contracts"
            params = {"symbol": symbol}
            response = self._make_request("GET", endpoint, params)

            if response and response.get("code") == 0:
                contracts = response.get("data", [])
                for contract in contracts:
                    if contract.get("symbol") == symbol:
                        return contract
            return {}
        except Exception as e:
            logger.error(f"Error obteniendo info del contrato: {e}")
            return {}

    def calculate_position_size(self, symbol: str, usdt_amount: float,
                                leverage: int, current_price: float) -> float:
        """Calcula el tamaño de posición según especificaciones BingX"""
        try:
            contract_info = self.get_contract_info(symbol)

            if not contract_info:
                logger.error(f"No se pudo obtener info del contrato para {symbol}")
                return 0.0

            base_quantity = (usdt_amount * leverage) / current_price

            quantity_precision = int(contract_info.get("quantityPrecision", 0))
            min_qty = float(contract_info.get("minQty", 0))

            quantity = round(base_quantity, quantity_precision)

            if quantity < min_qty:
                logger.error(f"❌ Cantidad {quantity} < mínimo {min_qty} para {symbol}")
                logger.error(
                    f"💡 Aumenta el margen. Actual: ${usdt_amount}, Necesitas: ${(min_qty * current_price / leverage):.2f}")
                return 0.0

            logger.info(f"📊 Cálculo: ${usdt_amount} x {leverage}x / ${current_price} = {quantity} (min: {min_qty})")

            return quantity

        except Exception as e:
            logger.error(f"Error calculando tamaño: {e}")
            return 0.0

    def open_position(self, symbol: str, side: str, usdt_amount: float,
                      leverage: int, tp_percent: List[float], sl_percent: float,
                      trailing_stop_percent: float) -> Dict:
        """Abre posición en futuros USDT ISOLATED"""
        try:
            self.set_margin_mode(symbol, "ISOLATED")

            current_price = self.get_current_price(symbol)
            if current_price == 0:
                return {"success": False, "error": "No se pudo obtener precio"}

            quantity = self.calculate_position_size(symbol, usdt_amount, leverage, current_price)
            if quantity == 0:
                return {"success": False, "error": f"Cantidad = 0. Aumenta margen (actual: ${usdt_amount})"}

            self._set_leverage(symbol, leverage)

            if side == "BUY":
                tp_prices = [current_price * (1 + tp / 100) for tp in tp_percent]
                sl_price = current_price * (1 - sl_percent / 100)
            else:
                tp_prices = [current_price * (1 - tp / 100) for tp in tp_percent]
                sl_price = current_price * (1 + sl_percent / 100)

            endpoint = "/openApi/swap/v2/trade/order"
            timestamp = int(time.time() * 1000)

            params = {
                "symbol": symbol,
                "side": side,
                "positionSide": "LONG" if side == "BUY" else "SHORT",
                "type": "MARKET",
                "quantity": quantity,
                "timestamp": timestamp
            }

            response = self._make_request("POST", endpoint, params)

            if response and response.get("code") == 0:
                order_data = response.get("data", {}).get("order", {})
                order_id = order_data.get("orderId", "unknown")

                logger.info(f"✅ Posición abierta: {order_id}")
                logger.info(f"   {symbol} | {side} | Qty: {quantity}")
                logger.info(f"   Precio: ${current_price:.2f} | Margen: ${usdt_amount}")

                self._set_stop_loss(symbol, side, sl_price, quantity)
                logger.info(f"   SL: ${sl_price:.2f}")

                tp_levels = [
                    {"price": tp_prices[0], "percentage": 30},
                    {"price": tp_prices[1], "percentage": 30},
                    {"price": tp_prices[2], "percentage": 30}
                ]
                self._set_take_profits(symbol, side, tp_levels, quantity)
                logger.info(f"   TP1: ${tp_prices[0]:.2f} | TP2: ${tp_prices[1]:.2f} | TP3: ${tp_prices[2]:.2f}")

                self._set_trailing_stop(symbol, side, trailing_stop_percent)
                logger.info(f"   Trailing: {trailing_stop_percent}%")

                return {
                    "success": True,
                    "order_id": order_id,
                    "quantity": quantity,
                    "price": current_price,
                    "margin_used": usdt_amount,
                    "leverage": leverage,
                    "exchange": "BingX"
                }
            else:
                error_msg = response.get("msg", "Error desconocido")
                logger.error(f"❌ API Error: {response}")
                return {"success": False, "error": f"BingX: {error_msg}"}

        except Exception as e:
            logger.error(f"❌ Error abriendo posición: {e}")
            return {"success": False, "error": str(e)}

    def _set_leverage(self, symbol: str, leverage: int):
        """Configura apalancamiento"""
        endpoint = "/openApi/swap/v2/trade/leverage"

        for side in ["LONG", "SHORT"]:
            params = {
                "symbol": symbol,
                "side": side,
                "leverage": leverage,
                "timestamp": int(time.time() * 1000)
            }
            self._make_request("POST", endpoint, params)

    def _set_stop_loss(self, symbol: str, side: str, price: float, quantity: float):
        """Configura Stop Loss"""
        endpoint = "/openApi/swap/v2/trade/order"
        params = {
            "symbol": symbol,
            "side": "SELL" if side == "BUY" else "BUY",
            "positionSide": "LONG" if side == "BUY" else "SHORT",
            "type": "STOP_MARKET",
            "stopPrice": price,
            "quantity": quantity,
            "timestamp": int(time.time() * 1000)
        }
        return self._make_request("POST", endpoint, params)

    def _set_take_profits(self, symbol: str, side: str, tp_levels: List[Dict], total_quantity: float):
        """Configura Take Profits"""
        for tp in tp_levels:
            quantity = total_quantity * (tp['percentage'] / 100)
            endpoint = "/openApi/swap/v2/trade/order"
            params = {
                "symbol": symbol,
                "side": "SELL" if side == "BUY" else "BUY",
                "positionSide": "LONG" if side == "BUY" else "SHORT",
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": tp['price'],
                "quantity": quantity,
                "timestamp": int(time.time() * 1000)
            }
            self._make_request("POST", endpoint, params)

    def _set_trailing_stop(self, symbol: str, side: str, callback_rate: float):
        """Configura Trailing Stop"""
        endpoint = "/openApi/swap/v2/trade/order"
        params = {
            "symbol": symbol,
            "side": "SELL" if side == "BUY" else "BUY",
            "positionSide": "LONG" if side == "BUY" else "SHORT",
            "type": "TRAILING_STOP_MARKET",
            "activationPrice": 0,
            "callbackRate": callback_rate,
            "timestamp": int(time.time() * 1000)
        }
        return self._make_request("POST", endpoint, params)

    def close_position(self, symbol: str) -> Dict:
        """Cierra posición"""
        try:
            endpoint = "/openApi/swap/v2/trade/closeAllPositions"
            params = {
                "symbol": symbol,
                "timestamp": int(time.time() * 1000)
            }
            response = self._make_request("POST", endpoint, params)
            logger.info(f"✅ Posición cerrada: {symbol}")
            return {"success": True, "response": response, "exchange": "BingX"}
        except Exception as e:
            logger.error(f"❌ Error cerrando posición: {e}")
            return {"success": False, "error": str(e)}

    def _make_request(self, method: str, endpoint: str, params: Dict) -> Dict:
        """Realiza request a la API"""
        try:
            query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
            signature = self._generate_signature(query_string, self.api_secret)

            headers = {"X-BX-APIKEY": self.api_key}
            url = f"{self.base_url}{endpoint}?{query_string}&signature={signature}"

            if method == "POST":
                response = requests.post(url, headers=headers, timeout=10)
            else:
                response = requests.get(url, headers=headers, timeout=10)

            return response.json()
        except Exception as e:
            logger.error(f"Error en request: {e}")
            return {"code": -1, "msg": str(e)}


class TradingBot:
    """Bot principal de trading"""

    def __init__(self, config_path: str = "config.json"):
        self.config = ConfigManager(config_path)
        self.active_positions = {}
        self.exchanges = []

        # Cuenta 1 de BingX
        bingx_key = os.getenv("BINGX_API_KEY")
        bingx_secret = os.getenv("BINGX_SECRET_KEY")

        if bingx_key and bingx_secret:
            bingx1 = BingXAPI(bingx_key, bingx_secret)
            bingx1.name = "BingX-1"
            if bingx1.is_available():
                self.exchanges.append(bingx1)
                logger.info("✅ BingX Cuenta 1 inicializada")

        # Cuenta 2 de BingX
        bingx2_key = os.getenv("BINGX2_API_KEY")
        bingx2_secret = os.getenv("BINGX2_SECRET_KEY")

        if bingx2_key and bingx2_secret:
            bingx2 = BingXAPI(bingx2_key, bingx2_secret)
            bingx2.name = "BingX-2"
            if bingx2.is_available():
                self.exchanges.append(bingx2)
                logger.info("✅ BingX Cuenta 2 inicializada")

        if not self.exchanges:
            logger.error("❌ No hay exchanges configurados")

    def normalize_symbol(self, symbol: str, exchange: ExchangeAPI) -> str:
        """Normaliza símbolo para BingX: BTC -> BTC-USDT"""
        symbol = symbol.upper().strip()

        if ":" in symbol:
            symbol = symbol.split(":")[1]

        if "-USDT" in symbol:
            return symbol

        if symbol.endswith("USDT"):
            symbol = symbol[:-4]

        return f"{symbol}-USDT"

    def parse_signal(self, message: str) -> Optional[Dict]:
        """Parsea señales: BUY BTC, SELL ETH, CLOSE BTC"""
        try:
            message = message.strip().upper()
            pattern = r'^(BUY|SELL|CLOSE)\s+([A-Z0-9:]+)$'
            match = re.match(pattern, message)

            if not match:
                return None

            action = match.group(1)
            symbol = match.group(2)

            if ":" in symbol:
                symbol = symbol.split(":")[1]

            if symbol.endswith("USDT") and len(symbol) > 4:
                symbol = symbol[:-4]

            if action in ["BUY", "SELL"]:
                return {
                    "action": "open",
                    "side": action,
                    "symbol": symbol
                }
            elif action == "CLOSE":
                return {
                    "action": "close",
                    "symbol": symbol
                }

            return None
        except Exception as e:
            logger.error(f"Error parseando señal: {e}")
            return None

    def check_existing_position(self, exchange: ExchangeAPI, symbol: str) -> bool:
        """Verifica si ya existe posición abierta"""
        try:
            positions = exchange.get_open_positions(symbol)
            return len(positions) > 0
        except Exception as e:
            logger.error(f"Error verificando posición: {e}")
            return False

    def check_opposite_position(self, exchange: ExchangeAPI, symbol: str, new_side: str) -> Optional[Dict]:
        """Verifica posición contraria"""
        try:
            positions = exchange.get_open_positions(symbol)

            for pos in positions:
                pos_side = pos.get("positionSide", "")

                if new_side == "BUY" and pos_side == "SHORT":
                    return {"exists": True, "position": pos, "side": "SHORT"}
                elif new_side == "SELL" and pos_side == "LONG":
                    return {"exists": True, "position": pos, "side": "LONG"}

            return None
        except Exception as e:
            logger.error(f"Error verificando posición contraria: {e}")
            return None

    async def execute_signal(self, signal: Dict) -> Dict:
        """Ejecuta la señal"""
        if signal["action"] == "open":
            return await self.open_trade(signal)
        elif signal["action"] == "close":
            return await self.close_trade(signal)
        return {"success": False, "error": "Acción no reconocida"}

    async def open_trade(self, signal: Dict) -> Dict:
        """Abre trade"""
        try:
            usdt_amount = self.config.get("trading", "usdt_margin_per_trade", default=5.0)
            leverage = self.config.get("trading", "default_leverage", default=10)
            min_balance = self.config.get("risk_management", "min_balance_required", default=50)

            if not self.exchanges:
                return {"success": False, "error": "No hay exchanges configurados"}

            exchange = self.exchanges[0]

            balance = exchange.get_balance()
            if balance < min_balance:
                return {"success": False, "error": f"Balance bajo: ${balance:.2f} (min: ${min_balance})"}

            symbol = self.normalize_symbol(signal["symbol"], exchange)
            side = signal["side"]

            logger.info(f"📊 {signal['symbol']} -> {symbol}")

            if self.check_existing_position(exchange, symbol):
                logger.warning(f"⚠️ Ya existe posición para {symbol}")
                return {
                    "success": False,
                    "error": f"Ya hay posición abierta en {symbol}. Usa CLOSE {signal['symbol']} primero"
                }

            opposite_pos = self.check_opposite_position(exchange, symbol, side)

            if opposite_pos and opposite_pos.get("exists"):
                logger.warning(f"⚠️ Posición contraria detectada: {opposite_pos['side']}")
                logger.info(f"🔄 Cerrando {opposite_pos['side']} antes de abrir {side}")

                close_result = exchange.close_position(symbol)

                if not close_result.get("success"):
                    return {
                        "success": False,
                        "error": f"No se pudo cerrar contraria: {close_result.get('error')}"
                    }

                logger.info("✅ Contraria cerrada")
                await asyncio.sleep(2)

            tp_percent = [
                self.config.get("take_profit", "tp1_percent", default=2.0),
                self.config.get("take_profit", "tp2_percent", default=3.5),
                self.config.get("take_profit", "tp3_percent", default=5.0)
            ]

            sl_percent = self.config.get("risk_management", "default_sl_percent", default=1.8)
            trailing_stop = self.config.get("trading", "trailing_stop_percent", default=2.0)

            logger.info(f"🚀 Abriendo {side} en {symbol}")

            result = exchange.open_position(
                symbol, side, usdt_amount, leverage,
                tp_percent, sl_percent, trailing_stop
            )

            if result["success"]:
                self.active_positions[f"{exchange.name}_{symbol}"] = {
                    "order_id": result["order_id"],
                    "side": side,
                    "symbol": symbol,
                    "exchange": exchange.name,
                    "timestamp": datetime.now().isoformat()
                }

            return result
        except Exception as e:
            logger.error(f"❌ Error abriendo trade: {e}")
            return {"success": False, "error": str(e)}

    async def close_trade(self, signal: Dict) -> Dict:
        """Cierra trade en todas las cuentas"""
        try:
            symbol_raw = signal["symbol"]
            results = []
            all_success = True

            for exchange in self.exchanges:
                logger.info(f"\n{'=' * 50}")
                logger.info(f"🎯 Cerrando en {exchange.name}")
                logger.info(f"{'=' * 50}")

                symbol = self.normalize_symbol(symbol_raw, exchange)

                if not self.check_existing_position(exchange, symbol):
                    logger.info(f"ℹ️ {exchange.name}: No hay posición abierta para {symbol}")
                    results.append({
                        "exchange": exchange.name,
                        "success": False,
                        "error": "No hay posición abierta"
                    })
                    continue

                logger.info(f"🔴 Cerrando {symbol}")

                result = exchange.close_position(symbol)

                if result["success"]:
                    key = f"{exchange.name}_{symbol}"
                    if key in self.active_positions:
                        del self.active_positions[key]

                    results.append({
                        "exchange": exchange.name,
                        "success": True
                    })
                else:
                    all_success = False
                    results.append({
                        "exchange": exchange.name,
                        "success": False,
                        "error": result.get("error", "Error desconocido")
                    })

            if not results:
                return {"success": False, "error": "No se encontraron posiciones en ninguna cuenta"}

            return {
                "success": all_success,
                "multi_account": True,
                "results": results,
                "total_accounts": len(self.exchanges),
                "closed_accounts": sum(1 for r in results if r.get("success"))
            }

        except Exception as e:
            logger.error(f"❌ Error cerrando: {e}")
            return {"success": False, "error": str(e)}


# Instancia global del bot
bot = TradingBot()


# ============================================================================
# TELETHON USERBOT - Lee mensajes de otros bots
# ============================================================================

async def main():
    """Función principal con Telethon"""

    # Credenciales de Telegram API (debes obtenerlas de https://my.telegram.org)
    api_id = int(os.getenv("TELEGRAM_API_ID"))
    api_hash = os.getenv("TELEGRAM_API_HASH")
    phone = os.getenv("TELEGRAM_PHONE")  # Tu número de teléfono

    # Chat/grupo donde escuchar
    target_chat_id = int(os.getenv("TELEGRAM_CHAT_ID"))

    # Crear cliente de Telethon
    client = TelegramClient('trading_session', api_id, api_hash)

    await client.start(phone=phone)
    logger.info("✅ Telethon conectado")

    me = await client.get_me()
    logger.info(f"👤 Conectado como: {me.first_name} (@{me.username})")

    @client.on(events.NewMessage(chats=target_chat_id))
    async def handler(event):
        """Maneja todos los mensajes nuevos en el chat"""
        try:
            message = event.message.text

            if not message:
                return

            # Obtener información del remitente
            sender = await event.get_sender()
            sender_name = "Unknown"
            is_bot = False

            if sender:
                sender_name = sender.first_name or "Unknown"
                is_bot = getattr(sender, 'bot', False)

            logger.info(f"📨 Mensaje de {'🤖 BOT' if is_bot else '👤'} {sender_name}: {message}")

            # Parsear señal
            signal = bot.parse_signal(message)

            if not signal:
                logger.info("ℹ️ No es una señal válida")
                return

            logger.info(f"🎯 SEÑAL DETECTADA: {signal}")

            # Ejecutar señal
            result = await bot.execute_signal(signal)

            if result["success"]:
                response = f"✅ {signal['action'].upper()} ejecutado: {signal['symbol']}"
                if signal["action"] == "open":
                    response += f"\n💰 Margen: ${result.get('margin_used', 0):.2f}"
            else:
                response = f"❌ Error: {result.get('error', 'Desconocido')}"

            logger.info(response)

            # Enviar respuesta al chat
            try:
                await event.reply(response)
            except Exception as e:
                logger.warning(f"No se pudo responder: {e}")

        except Exception as e:
            logger.error(f"❌ Error en handler: {e}")

    logger.info("=" * 60)
    logger.info("🤖 NEPTUNEBOT INICIADO CON TELETHON")
    logger.info("=" * 60)
    logger.info(f"🦅 Exchanges: {[ex.name for ex in bot.exchanges]}")
    logger.info(f"💬 Escuchando en chat ID: {target_chat_id}")
    logger.info(f"💰 Margen por trade: ${bot.config.get('trading', 'usdt_margin_per_trade', default=5.0)}")
    logger.info("⚙️ Modo: ISOLATED | 3 TP (30%-30%-30%)")
    logger.info("📡 Escuchando TODOS los mensajes (incluidos bots)...")
    logger.info("=" * 60)

    # Mantener el cliente corriendo
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())