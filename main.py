#!/usr/bin/env python3
"""
Bot de Trading Automatizado para BingX - FUTUROS USDT ISOLATED
Multi-usuario: cada usuario controla su propia cuenta BingX
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
    """Gestor de configuración JSON por usuario"""

    def __init__(self, config_path: str = "config.json"):
        with open(config_path, 'r') as f:
            self.config = json.load(f)

    def get_user_config(self, username: str) -> Dict:
        """Obtiene configuración específica de un usuario"""
        users = self.config.get("users", {})
        user_config = users.get(username, users.get("default", {}))

        # Merge con configuración por defecto si existe
        default_config = users.get("default", {})
        merged_config = {**default_config, **user_config}

        # Asegurar que existan las distribuciones de TP
        if "tp1_distribution" not in merged_config:
            merged_config["tp1_distribution"] = 30
        if "tp2_distribution" not in merged_config:
            merged_config["tp2_distribution"] = 30
        if "tp3_distribution" not in merged_config:
            merged_config["tp3_distribution"] = 40  # El resto para TP3

        logger.info(
            f"📋 Config para {username}: margen=${merged_config.get('usdt_margin_per_trade')}, leverage={merged_config.get('default_leverage')}x, TP dist: {merged_config.get('tp1_distribution')}/{merged_config.get('tp2_distribution')}/{merged_config.get('tp3_distribution')}%")
        return merged_config

    def get(self, *keys, default=None):
        """Obtiene valor de configuración global"""
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
                      trailing_stop_percent: float, tp_distribution: List[int] = None) -> Dict:
        """Abre posición en futuros USDT ISOLATED"""
        try:
            # Distribución por defecto si no se especifica
            if tp_distribution is None:
                tp_distribution = [30, 30, 40]

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

                # Usar distribución configurable
                tp_levels = [
                    {"price": tp_prices[0], "percentage": tp_distribution[0]},
                    {"price": tp_prices[1], "percentage": tp_distribution[1]},
                    {"price": tp_prices[2], "percentage": tp_distribution[2]}
                ]
                self._set_take_profits(symbol, side, tp_levels, quantity)
                logger.info(
                    f"   TP1: ${tp_prices[0]:.2f} ({tp_distribution[0]}%) | TP2: ${tp_prices[1]:.2f} ({tp_distribution[1]}%) | TP3: ${tp_prices[2]:.2f} ({tp_distribution[2]}%)")

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
    """Bot principal de trading multi-usuario"""

    def __init__(self, config_path: str = "config.json"):
        self.config = ConfigManager(config_path)
        self.active_positions = {}
        self.user_exchanges = {}  # Map: user_identifier -> exchange
        self.user_id_to_name = {}  # Map: telegram_id -> user_identifier

        # Usuario 1
        username1 = os.getenv("TELEGRAM_USERNAME", "").strip().strip("'\"")
        user1_id = os.getenv("TELEGRAM_USER_ID")  # Nuevo: ID de Telegram
        bingx_key1 = os.getenv("BINGX_API_KEY")
        bingx_secret1 = os.getenv("BINGX_SECRET_KEY")

        if username1 and bingx_key1 and bingx_secret1:
            bingx1 = BingXAPI(bingx_key1, bingx_secret1)
            bingx1.name = f"BingX-{username1}"
            if bingx1.is_available():
                self.user_exchanges[username1] = bingx1
                if user1_id:
                    self.user_id_to_name[int(user1_id)] = username1
                logger.info(f"✅ BingX para {username1} inicializada (ID: {user1_id})")
        else:
            logger.warning(f"⚠️ Usuario 1 no configurado correctamente")

        # Usuario 2
        username2 = os.getenv("TELEGRAM_USERNAME2", "").strip().strip("'\"")
        user2_id = os.getenv("TELEGRAM_USER_ID2")  # Nuevo: ID de Telegram
        bingx_key2 = os.getenv("BINGX2_API_KEY")
        bingx_secret2 = os.getenv("BINGX2_SECRET_KEY")

        if username2 and bingx_key2 and bingx_secret2:
            bingx2 = BingXAPI(bingx_key2, bingx_secret2)
            bingx2.name = f"BingX-{username2}"
            if bingx2.is_available():
                self.user_exchanges[username2] = bingx2
                if user2_id:
                    self.user_id_to_name[int(user2_id)] = username2
                logger.info(f"✅ BingX para {username2} inicializada (ID: {user2_id})")
        else:
            logger.warning(f"⚠️ Usuario 2 no configurado correctamente")

        if not self.user_exchanges:
            logger.error("❌ No hay exchanges configurados")

        logger.info(f"👥 Usuarios configurados: {list(self.user_exchanges.keys())}")

    def get_user_exchange(self, user_identifier: str) -> Optional[BingXAPI]:
        """Obtiene el exchange de un usuario específico"""
        return self.user_exchanges.get(user_identifier)

    def get_user_identifier_from_telegram_id(self, telegram_id: int) -> Optional[str]:
        """Obtiene el identificador de usuario desde el Telegram ID"""
        return self.user_id_to_name.get(telegram_id)

    def normalize_symbol(self, symbol: str) -> str:
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

    async def execute_signal_for_all_users(self, signal: Dict) -> List[Dict]:
        """Ejecuta señal para TODOS los usuarios"""
        results = []

        for user_identifier, exchange in self.user_exchanges.items():
            logger.info(f"\n{'=' * 60}")
            logger.info(f"👤 Ejecutando para {user_identifier}")
            logger.info(f"{'=' * 60}")

            if signal["action"] == "open":
                result = await self.open_trade_for_user(signal, user_identifier)
            elif signal["action"] == "close":
                result = await self.close_trade_for_user(signal, user_identifier)
            else:
                result = {"success": False, "error": "Acción no reconocida", "user_identifier": user_identifier}

            result["user_identifier"] = user_identifier
            results.append(result)

        return results

    async def execute_signal_for_user(self, signal: Dict, user_identifier: str) -> Dict:
        """Ejecuta señal para un usuario específico"""
        if signal["action"] == "open":
            return await self.open_trade_for_user(signal, user_identifier)
        elif signal["action"] == "close":
            return await self.close_trade_for_user(signal, user_identifier)
        return {"success": False, "error": "Acción no reconocida"}

    async def open_trade_for_user(self, signal: Dict, user_identifier: str) -> Dict:
        """Abre trade para un usuario específico"""
        try:
            exchange = self.get_user_exchange(user_identifier)

            if not exchange:
                logger.error(f"❌ No hay exchange para {user_identifier}")
                return {"success": False, "error": f"Usuario {user_identifier} no configurado"}

            # Obtener configuración del usuario
            user_config = self.config.get_user_config(user_identifier)

            usdt_amount = user_config.get("usdt_margin_per_trade", 5.0)
            leverage = user_config.get("default_leverage", 10)
            min_balance = user_config.get("min_balance_required", 50)

            balance = exchange.get_balance()
            logger.info(f"💰 Balance de {user_identifier}: ${balance:.2f}")

            if balance < min_balance:
                return {"success": False, "error": f"Balance bajo: ${balance:.2f} (min: ${min_balance})"}

            symbol = self.normalize_symbol(signal["symbol"])
            side = signal["side"]

            logger.info(f"📊 {signal['symbol']} -> {symbol}")

            if self.check_existing_position(exchange, symbol):
                logger.warning(f"⚠️ {user_identifier} ya tiene posición en {symbol}")
                return {
                    "success": False,
                    "error": f"Ya hay posición abierta en {symbol}"
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
                user_config.get("tp1_percent", 2.0),
                user_config.get("tp2_percent", 3.5),
                user_config.get("tp3_percent", 5.0)
            ]

            tp_distribution = [
                user_config.get("tp1_distribution", 30),
                user_config.get("tp2_distribution", 30),
                user_config.get("tp3_distribution", 40)
            ]

            sl_percent = user_config.get("default_sl_percent", 1.8)
            trailing_stop = user_config.get("trailing_stop_percent", 2.0)

            logger.info(f"🚀 Abriendo {side} en {symbol} para {user_identifier}")
            logger.info(f"   💰 Margen: ${usdt_amount} | ⚡ Leverage: {leverage}x")

            result = exchange.open_position(
                symbol, side, usdt_amount, leverage,
                tp_percent, sl_percent, trailing_stop, tp_distribution
            )

            if result["success"]:
                self.active_positions[f"{user_identifier}_{symbol}"] = {
                    "order_id": result["order_id"],
                    "side": side,
                    "symbol": symbol,
                    "exchange": exchange.name,
                    "user_identifier": user_identifier,
                    "timestamp": datetime.now().isoformat()
                }

            return result
        except Exception as e:
            logger.error(f"❌ Error abriendo trade para {user_identifier}: {e}")
            return {"success": False, "error": str(e)}

    async def close_trade_for_user(self, signal: Dict, user_identifier: str) -> Dict:
        """Cierra trade para un usuario específico"""
        try:
            exchange = self.get_user_exchange(user_identifier)

            if not exchange:
                logger.error(f"❌ No hay exchange para {user_identifier}")
                return {"success": False, "error": f"Usuario {user_identifier} no configurado"}

            symbol = self.normalize_symbol(signal["symbol"])

            if not self.check_existing_position(exchange, symbol):
                logger.info(f"ℹ️ {user_identifier}: No hay posición abierta para {symbol}")
                return {
                    "success": False,
                    "error": "No hay posición abierta"
                }

            logger.info(f"🔴 Cerrando {symbol} para {user_identifier}")

            result = exchange.close_position(symbol)

            if result["success"]:
                key = f"{user_identifier}_{symbol}"
                if key in self.active_positions:
                    del self.active_positions[key]

            return result

        except Exception as e:
            logger.error(f"❌ Error cerrando para {user_identifier}: {e}")
            return {"success": False, "error": str(e)}


# Instancia global del bot
bot = TradingBot()


# ============================================================================
# COMANDOS
# ============================================================================

async def handle_command(event, sender_id: int):
    """Maneja comandos de Telegram"""
    try:
        message = event.message.text.strip()
        command = message.split()[0].lower()

        # Obtener user_identifier desde el Telegram ID
        user_identifier = bot.get_user_identifier_from_telegram_id(sender_id)

        if not user_identifier:
            await event.reply(
                f"❌ Tu cuenta de Telegram (ID: {sender_id}) no está configurada. Contacta al administrador.")
            return

        if command == "/balance":
            exchange = bot.get_user_exchange(user_identifier)
            if not exchange:
                await event.reply(f"❌ {user_identifier} no está configurado")
                return

            balance = exchange.get_balance()
            await event.reply(f"💰 Balance {user_identifier}: ${balance:.2f} USDT")

        elif command == "/positions":
            exchange = bot.get_user_exchange(user_identifier)
            if not exchange:
                await event.reply(f"❌ {user_identifier} no está configurado")
                return

            positions = exchange.get_open_positions()
            if not positions:
                await event.reply(f"📭 {user_identifier}: Sin posiciones abiertas")
                return

            msg = f"📊 Posiciones de {user_identifier}:\n\n"
            for pos in positions:
                symbol = pos.get("symbol", "?")
                side = pos.get("positionSide", "?")
                qty = pos.get("positionAmt", 0)
                pnl = pos.get("unrealizedProfit", 0)
                msg += f"• {symbol} {side}\n"
                msg += f"  Qty: {qty} | PnL: ${float(pnl):.2f}\n"

            await event.reply(msg)

        elif command == "/close":
            parts = message.split()
            if len(parts) < 2:
                await event.reply("❌ Uso: /close SYMBOL\nEjemplo: /close BTC")
                return

            symbol_input = parts[1]
            signal = {"action": "close", "symbol": symbol_input}

            # Solo cerrar para el usuario que ejecutó el comando
            result = await bot.close_trade_for_user(signal, user_identifier)

            if result["success"]:
                await event.reply(f"✅ Posición cerrada: {symbol_input} (solo tu cuenta)")
            else:
                await event.reply(f"❌ Error: {result.get('error')}")

        elif command == "/help":
            help_text = f"""
🤖 NeptuneBot - Comandos

👤 Tu cuenta: {user_identifier}

/balance - Ver tu balance
/positions - Ver tus posiciones
/close SYMBOL - Cerrar posición
/help - Este mensaje

Señales automáticas:
• BUY BTC - Abre LONG
• SELL ETH - Abre SHORT
• CLOSE BTC - Cierra posición
"""
            await event.reply(help_text)

    except Exception as e:
        logger.error(f"Error en comando: {e}")
        await event.reply(f"❌ Error: {str(e)}")


# ============================================================================
# TELETHON USERBOT - Lee mensajes de otros bots
# ============================================================================

async def main():
    """Función principal con Telethon"""

    # Credenciales de Telegram API
    api_id = int(os.getenv("TELEGRAM_API_ID"))
    api_hash = os.getenv("TELEGRAM_API_HASH")
    phone = os.getenv("TELEGRAM_PHONE")

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
            sender_id = None
            is_bot = False

            if sender:
                sender_name = sender.first_name or "Unknown"
                sender_id = sender.id
                is_bot = getattr(sender, 'bot', False)

            logger.info(f"📨 Mensaje de {'🤖 BOT' if is_bot else '👤'} {sender_name} (ID: {sender_id}): {message}")

            # Si es un comando (empieza con /)
            if message.startswith("/"):
                if sender_id:
                    await handle_command(event, sender_id)
                return

            # Parsear señal
            signal = bot.parse_signal(message)

            if not signal:
                logger.info("ℹ️ No es una señal válida")
                return

            # 🔒 SEGURIDAD: Solo aceptar señales de BOTS
            if not is_bot:
                logger.warning(f"⚠️ Señal ignorada: viene de usuario {sender_name} (ID: {sender_id}), no de un bot")
                logger.info("💡 Las señales automáticas solo pueden venir de bots de Telegram")
                return

            logger.info(f"🎯 SEÑAL DETECTADA (de BOT): {signal}")

            # Ejecutar señal para TODOS los usuarios
            results = await bot.execute_signal_for_all_users(signal)

            # Construir respuesta
            success_count = sum(1 for r in results if r.get("success"))
            total_count = len(results)

            if success_count == total_count:
                response = f"✅ {signal['action'].upper()} ejecutado en {success_count}/{total_count} cuentas: {signal['symbol']}\n"
                for r in results:
                    user_id = r.get('user_identifier', 'Unknown')
                    if signal["action"] == "open":
                        response += f"• {user_id}: ${r.get('margin_used', 0):.2f}\n"
                    else:
                        response += f"• {user_id}: ✓\n"
            else:
                response = f"⚠️ {signal['action'].upper()} ejecutado en {success_count}/{total_count} cuentas: {signal['symbol']}\n"
                for r in results:
                    user_id = r.get('user_identifier', 'Unknown')
                    if r.get("success"):
                        response += f"• {user_id}: ✅\n"
                    else:
                        response += f"• {user_id}: ❌ {r.get('error', 'Error')}\n"

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
    logger.info(f"👥 Usuarios: {list(bot.user_exchanges.keys())}")
    logger.info(f"💬 Escuchando en chat ID: {target_chat_id}")
    logger.info("📡 Escuchando TODOS los mensajes (incluidos bots)...")
    logger.info("=" * 60)

    # Mantener el cliente corriendo
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())