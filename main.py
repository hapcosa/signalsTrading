#!/usr/bin/env python3
"""
Bot de Trading Automatizado para BingX y Bybit - FUTUROS USDT
Recibe señales de TradingView vía Telegram y ejecuta trades automáticamente
Formato simple: BUY BTC, SELL ETH, CLOSE BTC

NUEVA FUNCIONALIDAD:
- Cierra automáticamente posiciones contrarias (SHORT si llega LONG, y viceversa)
- Verifica posiciones existentes antes de operar
"""

import os
import json
import logging
from typing import Dict, Optional, List
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler
import hmac
import hashlib
import time
import requests
import re

# Cargar variables de entorno
load_dotenv()

# Configurar logging
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
    """API para BingX - Futuros USDT (Solo modo LIVE, no tiene testnet por API)"""

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
        """Obtiene posiciones abiertas (NUEVA FUNCIÓN)"""
        try:
            endpoint = "/openApi/swap/v2/user/positions"
            timestamp = int(time.time() * 1000)
            params = {"timestamp": timestamp}

            if symbol:
                params["symbol"] = symbol

            response = self._make_request("GET", endpoint, params)

            if response and response.get("code") == 0:
                positions = response.get("data", [])
                # Filtrar solo posiciones con cantidad > 0
                active_positions = [
                    pos for pos in positions
                    if float(pos.get("positionAmt", 0)) != 0
                ]
                return active_positions
            return []
        except Exception as e:
            logger.error(f"Error obteniendo posiciones BingX: {e}")
            return []

    def calculate_position_size(self, symbol: str, usdt_amount: float,
                                leverage: int, current_price: float) -> float:
        """Calcula el tamaño de posición basado en USDT"""
        try:
            # Cantidad = (USDT * Leverage) / Precio
            quantity = (usdt_amount * leverage) / current_price

            # Redondear según el símbolo
            if "BTC" in symbol:
                return round(quantity, 3)
            elif "ETH" in symbol:
                return round(quantity, 2)
            else:
                return round(quantity, 1)
        except Exception as e:
            logger.error(f"Error calculando tamaño: {e}")
            return 0.0

    def open_position(self, symbol: str, side: str, usdt_amount: float,
                      leverage: int, tp_percent: List[float], sl_percent: float,
                      trailing_stop_percent: float) -> Dict:
        """Abre posición en futuros USDT"""
        try:
            # 1. Obtener precio actual
            current_price = self.get_current_price(symbol)
            if current_price == 0:
                return {"success": False, "error": "No se pudo obtener precio"}

            # 2. Calcular tamaño de posición
            quantity = self.calculate_position_size(symbol, usdt_amount, leverage, current_price)
            if quantity == 0:
                return {"success": False, "error": "Cantidad calculada es 0"}

            # 3. Configurar apalancamiento
            self._set_leverage(symbol, leverage)

            # 4. Calcular precios TP y SL
            if side == "BUY":
                tp_prices = [current_price * (1 + tp / 100) for tp in tp_percent]
                sl_price = current_price * (1 - sl_percent / 100)
            else:  # SELL
                tp_prices = [current_price * (1 - tp / 100) for tp in tp_percent]
                sl_price = current_price * (1 + sl_percent / 100)

            # 5. Abrir posición principal
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

                logger.info(f"✅ Posición abierta en BingX: {order_id}")
                logger.info(f"   Symbol: {symbol} | Side: {side} | Qty: {quantity}")
                logger.info(f"   Precio: ${current_price:.2f} | Margen: ${usdt_amount} USDT")

                # 6. Configurar Stop Loss
                self._set_stop_loss(symbol, side, sl_price, quantity)
                logger.info(f"   Stop Loss: ${sl_price:.2f}")

                # 7. Configurar Take Profits (30%, 30%, 30%)
                tp_levels = [
                    {"price": tp_prices[0], "percentage": 30},
                    {"price": tp_prices[1], "percentage": 30},
                    {"price": tp_prices[2], "percentage": 30}
                ]
                self._set_take_profits(symbol, side, tp_levels, quantity)
                logger.info(f"   TP1: ${tp_prices[0]:.2f} (30%)")
                logger.info(f"   TP2: ${tp_prices[1]:.2f} (30%)")
                logger.info(f"   TP3: ${tp_prices[2]:.2f} (30%)")

                # 8. Configurar Trailing Stop (10% restante)
                self._set_trailing_stop(symbol, side, trailing_stop_percent)
                logger.info(f"   Trailing Stop: {trailing_stop_percent}% (10% restante)")

                return {
                    "success": True,
                    "order_id": order_id,
                    "quantity": quantity,
                    "price": current_price,
                    "margin_used": usdt_amount,
                    "leverage": leverage,
                    "exchange": "BingX"
                }

            return {"success": False, "error": response}

        except Exception as e:
            logger.error(f"❌ Error abriendo posición en BingX: {e}")
            return {"success": False, "error": str(e)}

    def _set_leverage(self, symbol: str, leverage: int):
        """Configura el apalancamiento"""
        endpoint = "/openApi/swap/v2/trade/leverage"

        # Configurar para LONG
        params = {
            "symbol": symbol,
            "side": "LONG",
            "leverage": leverage,
            "timestamp": int(time.time() * 1000)
        }
        self._make_request("POST", endpoint, params)

        # Configurar para SHORT
        params["side"] = "SHORT"
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
        """Configura múltiples niveles de Take Profit"""
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
        """Cierra una posición completamente"""
        try:
            endpoint = "/openApi/swap/v2/trade/closeAllPositions"
            params = {
                "symbol": symbol,
                "timestamp": int(time.time() * 1000)
            }
            response = self._make_request("POST", endpoint, params)
            logger.info(f"✅ Posición cerrada en BingX: {symbol}")
            return {"success": True, "response": response, "exchange": "BingX"}
        except Exception as e:
            logger.error(f"❌ Error cerrando posición en BingX: {e}")
            return {"success": False, "error": str(e)}

    def _make_request(self, method: str, endpoint: str, params: Dict) -> Dict:
        """Realiza request a la API"""
        try:
            query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
            signature = self._generate_signature(query_string, self.api_secret)

            headers = {
                "X-BX-APIKEY": self.api_key
            }

            url = f"{self.base_url}{endpoint}?{query_string}&signature={signature}"

            if method == "POST":
                response = requests.post(url, headers=headers, timeout=10)
            else:
                response = requests.get(url, headers=headers, timeout=10)

            return response.json()
        except Exception as e:
            logger.error(f"Error en request a BingX: {e}")
            return {"code": -1, "msg": str(e)}


class BybitAPI(ExchangeAPI):
    """API para Bybit - Futuros USDT (Soporta testnet)"""

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        super().__init__(api_key, api_secret)
        self.base_url = "https://api.bybit.com" if not testnet else "https://api-testnet.bybit.com"
        self.name = "Bybit" + (" Testnet" if testnet else "")
        self.testnet = testnet

    def is_available(self) -> bool:
        """Verifica si el exchange está disponible"""
        return bool(self.api_key and self.api_secret)

    def get_balance(self) -> float:
        """Obtiene balance USDT disponible"""
        try:
            endpoint = "/v5/account/wallet-balance"
            timestamp = str(int(time.time() * 1000))
            params = {
                "accountType": "UNIFIED",
                "timestamp": timestamp
            }

            response = self._make_request("GET", endpoint, params)
            if response and response.get("retCode") == 0:
                coins = response.get("result", {}).get("list", [{}])[0].get("coin", [])
                for coin in coins:
                    if coin.get("coin") == "USDT":
                        return float(coin.get("availableToWithdraw", 0))
            return 0.0
        except Exception as e:
            logger.error(f"Error obteniendo balance Bybit: {e}")
            return 0.0

    def get_current_price(self, symbol: str) -> float:
        """Obtiene precio actual del símbolo"""
        try:
            endpoint = "/v5/market/tickers"
            params = {
                "category": "linear",
                "symbol": symbol
            }
            response = self._make_request("GET", endpoint, params)

            if response and response.get("retCode") == 0:
                tickers = response.get("result", {}).get("list", [])
                if tickers:
                    return float(tickers[0].get("lastPrice", 0))
            return 0.0
        except Exception as e:
            logger.error(f"Error obteniendo precio Bybit: {e}")
            return 0.0

    def get_open_positions(self, symbol: Optional[str] = None) -> List[Dict]:
        """Obtiene posiciones abiertas (NUEVA FUNCIÓN)"""
        return []  # Por implementar

    # Implementación pendiente - Por ahora retorna error
    def open_position(self, *args, **kwargs) -> Dict:
        return {"success": False, "error": "Bybit no implementado aún"}

    def close_position(self, *args, **kwargs) -> Dict:
        return {"success": False, "error": "Bybit no implementado aún"}

    def _make_request(self, method: str, endpoint: str, params: Dict) -> Dict:
        """Realiza request a la API"""
        try:
            timestamp = params.get("timestamp", str(int(time.time() * 1000)))

            if method == "POST":
                params_str = json.dumps(params)
                sign_str = f"{timestamp}{self.api_key}5000{params_str}"
            else:
                params_str = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
                sign_str = f"{timestamp}{self.api_key}5000{params_str}"

            signature = self._generate_signature(sign_str, self.api_secret)

            headers = {
                "X-BAPI-API-KEY": self.api_key,
                "X-BAPI-SIGN": signature,
                "X-BAPI-TIMESTAMP": timestamp,
                "X-BAPI-RECV-WINDOW": "5000",
                "Content-Type": "application/json"
            }

            url = f"{self.base_url}{endpoint}"

            if method == "POST":
                response = requests.post(url, headers=headers, json=params, timeout=10)
            else:
                response = requests.get(url, headers=headers, params=params, timeout=10)

            return response.json()
        except Exception as e:
            logger.error(f"Error en request a Bybit: {e}")
            return {"retCode": -1, "retMsg": str(e)}


class TradingBot:
    """Bot principal de trading"""

    def __init__(self, config_path: str = "config.json"):
        self.config = ConfigManager(config_path)
        self.active_positions = {}
        self.exchanges = []

        # Cargar modo de trading
        trading_mode = os.getenv("TRADING_MODE", "paper")

        # Inicializar BingX (solo modo live, no tiene testnet)
        bingx_key = os.getenv("BINGX_API_KEY")
        bingx_secret = os.getenv("BINGX_SECRET_KEY")

        # MODIFICACIÓN: Permitir BingX en cualquier modo si las credenciales existen
        if bingx_key and bingx_secret:
            self.bingx = BingXAPI(bingx_key, bingx_secret)
            if self.bingx.is_available():
                self.exchanges.append(self.bingx)
                logger.info(f"✅ BingX inicializado (MODO: {trading_mode.upper()})")
                if trading_mode == "paper":
                    logger.warning("⚠️  BingX no tiene testnet - Usarás cuenta REAL")
        else:
            self.bingx = None
            logger.warning("⚠️  BingX no configurado - Verifica BINGX_API_KEY y BINGX_SECRET_KEY")

        # Inicializar Bybit (OPCIONAL)
        bybit_enabled = os.getenv("BYBIT_ENABLED", "true").lower() == "true"

        if bybit_enabled:
            if trading_mode == "paper":
                bybit_key = os.getenv("BYBIT_DEMO_API_KEY")
                bybit_secret = os.getenv("BYBIT_DEMO_SECRET_KEY")
                testnet = True
            else:
                bybit_key = os.getenv("BYBIT_API_KEY")
                bybit_secret = os.getenv("BYBIT_SECRET_KEY")
                testnet = False

            if bybit_key and bybit_secret:
                self.bybit = BybitAPI(bybit_key, bybit_secret, testnet)
                if self.bybit.is_available():
                    self.exchanges.append(self.bybit)
                    logger.info(f"✅ Bybit inicializado ({'TESTNET' if testnet else 'LIVE'})")
            else:
                self.bybit = None
                logger.warning("⚠️  Bybit no configurado")
        else:
            self.bybit = None
            logger.info("ℹ️  Bybit deshabilitado (BYBIT_ENABLED=false)")

        # Validar que al menos un exchange esté disponible
        if not self.exchanges:
            logger.error("❌ No hay exchanges configurados. Verifica tu .env")

        # Chat ID autorizado
        self.authorized_chat_id = os.getenv("TELEGRAM_CHAT_ID")

    def get_available_exchange(self, min_balance: float) -> Optional[ExchangeAPI]:
        """Obtiene el exchange disponible con balance suficiente"""
        priority_exchange = self.config.get("general", "priority_exchange", default="bingx")

        # Priorizar exchange configurado
        for exchange in self.exchanges:
            if priority_exchange.lower() in exchange.name.lower():
                balance = exchange.get_balance()
                if balance >= min_balance:
                    logger.info(f"📊 Exchange seleccionado: {exchange.name} (Balance: ${balance:.2f})")
                    return exchange
                else:
                    logger.warning(f"⚠️  {exchange.name} sin balance suficiente: ${balance:.2f}")

        # Si no hay balance en prioritario, buscar en otros
        for exchange in self.exchanges:
            balance = exchange.get_balance()
            if balance >= min_balance:
                logger.info(f"📊 Exchange seleccionado: {exchange.name} (Balance: ${balance:.2f})")
                return exchange

        logger.error("❌ No hay exchanges con balance suficiente")
        return None

    def normalize_symbol(self, symbol: str, exchange: ExchangeAPI) -> str:
        """Normaliza el símbolo según el exchange"""
        symbol = symbol.upper()

        if "bingx" in exchange.name.lower():
            # BingX usa formato: BTC-USDT
            if "-" not in symbol:
                return f"{symbol}-USDT"
        elif "bybit" in exchange.name.lower():
            # Bybit usa formato: BTCUSDT
            if "-" in symbol:
                symbol = symbol.replace("-", "")
            if not symbol.endswith("USDT"):
                return f"{symbol}USDT"

        return symbol

    def parse_signal(self, message: str) -> Optional[Dict]:
        """Parsea señales: BUY BTC, SELL ETH, CLOSE BTC"""
        try:
            message = message.strip().upper()

            # Patrón: ACCION SIMBOLO
            pattern = r'^(BUY|SELL|CLOSE)\s+([A-Z0-9]+)$'
            match = re.match(pattern, message)

            if not match:
                return None

            action = match.group(1)
            symbol = match.group(2)

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

    def check_opposite_position(self, exchange: ExchangeAPI, symbol: str, new_side: str) -> Optional[Dict]:
        """
        NUEVA FUNCIÓN: Verifica si existe una posición contraria
        Retorna información de la posición contraria si existe
        """
        try:
            positions = exchange.get_open_positions(symbol)

            for pos in positions:
                pos_side = pos.get("positionSide", "")

                # Detectar posición contraria
                if new_side == "BUY" and pos_side == "SHORT":
                    return {"exists": True, "position": pos, "side": "SHORT"}
                elif new_side == "SELL" and pos_side == "LONG":
                    return {"exists": True, "position": pos, "side": "LONG"}

            return None
        except Exception as e:
            logger.error(f"Error verificando posición contraria: {e}")
            return None

    def execute_signal(self, signal: Dict) -> Dict:
        """Ejecuta la señal"""
        if signal["action"] == "open":
            return self.open_trade(signal)
        elif signal["action"] == "close":
            return self.close_trade(signal)
        return {"success": False, "error": "Acción no reconocida"}

    def open_trade(self, signal: Dict) -> Dict:
        """
        Abre un trade
        MODIFICADO: Ahora cierra automáticamente posiciones contrarias
        """
        try:
            # Obtener parámetros de config
            usdt_amount = self.config.get("trading", "usdt_margin_per_trade", default=100)
            leverage = self.config.get("trading", "default_leverage", default=10)
            min_balance = self.config.get("risk_management", "min_balance_required", default=50)

            # Seleccionar exchange con balance
            exchange = self.get_available_exchange(min_balance)
            if not exchange:
                return {"success": False, "error": "No hay exchanges disponibles con balance"}

            # Normalizar símbolo
            symbol = self.normalize_symbol(signal["symbol"], exchange)
            side = signal["side"]

            # ⚡ NUEVA LÓGICA: Verificar si existe posición contraria
            opposite_pos = self.check_opposite_position(exchange, symbol, side)

            if opposite_pos and opposite_pos.get("exists"):
                logger.warning(f"⚠️  Detectada posición contraria: {opposite_pos['side']} en {symbol}")
                logger.info(f"🔄 Cerrando posición {opposite_pos['side']} antes de abrir {side}")

                # Cerrar posición contraria
                close_result = exchange.close_position(symbol)

                if not close_result.get("success"):
                    return {
                        "success": False,
                        "error": f"No se pudo cerrar posición contraria: {close_result.get('error')}"
                    }

                logger.info(f"✅ Posición contraria cerrada exitosamente")

                # Eliminar de posiciones activas
                key_to_remove = f"{exchange.name}_{symbol}"
                if key_to_remove in self.active_positions:
                    del self.active_positions[key_to_remove]

                # Esperar un poco para que se procese el cierre
                time.sleep(1)

            # Take Profits
            tp_percent = [
                self.config.get("take_profit", "tp1_percent", default=2.0),
                self.config.get("take_profit", "tp2_percent", default=4.0),
                self.config.get("take_profit", "tp3_percent", default=6.0)
            ]

            sl_percent = self.config.get("risk_management", "default_sl_percent", default=2.0)
            trailing_stop = self.config.get("trading", "trailing_stop_percent", default=1.5)

            logger.info(f"🚀 Abriendo {side} en {symbol} ({exchange.name})")

            # Ejecutar en el exchange seleccionado
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

    def close_trade(self, signal: Dict) -> Dict:
        """Cierra un trade"""
        try:
            symbol_raw = signal["symbol"]

            # Buscar en qué exchange está la posición
            for key, pos in self.active_positions.items():
                if symbol_raw in pos["symbol"]:
                    exchange = next((ex for ex in self.exchanges if ex.name == pos["exchange"]), None)
                    if exchange:
                        symbol = self.normalize_symbol(symbol_raw, exchange)
                        logger.info(f"🔴 Cerrando posición en {symbol} ({exchange.name})")

                        result = exchange.close_position(symbol)

                        if result["success"]:
                            del self.active_positions[key]

                        return result

            # Si no está en posiciones activas, intentar cerrar en exchange prioritario
            exchange = self.exchanges[0] if self.exchanges else None
            if exchange:
                symbol = self.normalize_symbol(symbol_raw, exchange)
                return exchange.close_position(symbol)

            return {"success": False, "error": "No se encontró la posición"}
        except Exception as e:
            logger.error(f"❌ Error cerrando trade: {e}")
            return {"success": False, "error": str(e)}


# Instancia global del bot
bot = TradingBot()


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja mensajes de Telegram"""
    try:
        # Verificar que venga del chat autorizado
        chat_id = str(update.message.chat_id)
        authorized_chat = bot.authorized_chat_id

        if authorized_chat and chat_id != authorized_chat:
            logger.warning(f"⚠️  Mensaje de chat no autorizado: {chat_id}")
            return

        message = update.message.text
        logger.info(f"📨 Señal recibida: {message}")

        # Parsear señal
        signal = bot.parse_signal(message)
        if not signal:
            await update.message.reply_text(
                "❌ Formato inválido\n\n"
                "Usa:\n"
                "• BUY BTC\n"
                "• SELL ETH\n"
                "• CLOSE BTC"
            )
            return

        # Ejecutar señal
        result = bot.execute_signal(signal)

        if result["success"]:
            if signal["action"] == "open":
                await update.message.reply_text(
                    f"✅ Posición abierta\n\n"
                    f"📊 {signal['symbol']}\n"
                    f"📈 {signal['side']}\n"
                    f"🦅 Exchange: {result.get('exchange', 'N/A')}\n"
                    f"💰 Margen: ${result.get('margin_used', 0):.2f} USDT\n"
                    f"📢 Cantidad: {result.get('quantity', 0)}\n"
                    f"💵 Precio: ${result.get('price', 0):.2f}\n"
                    f"⚡ Apalancamiento: {result.get('leverage', 0)}x\n\n"
                    f"🎯 3 Take Profits (30%-30%-30%)\n"
                    f"🛑 Stop Loss y Trailing Stop activos"
                )
            else:
                await update.message.reply_text(
                    f"✅ Posición cerrada\n\n"
                    f"📊 {signal['symbol']}\n"
                    f"🦅 {result.get('exchange', 'N/A')}"
                )
        else:
            await update.message.reply_text(
                f"❌ Error\n\n"
                f"{result.get('error', 'Desconocido')}"
            )
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start"""
    exchanges_info = "\n".join([f"• {ex.name}" for ex in bot.exchanges])
    if not exchanges_info:
        exchanges_info = "• Ninguno configurado"

    await update.message.reply_text(
        f"🤖 Bot de Trading Futuros USDT\n\n"
        f"📝 Comandos:\n"
        f"• BUY BTC - Long\n"
        f"• SELL ETH - Short\n"
        f"• CLOSE BTC - Cerrar\n\n"
        f"🦅 Exchanges:\n{exchanges_info}\n\n"
        f"📊 3 TP (30%-30%-30%)\n"
        f"🛑 SL y Trailing Stop activos\n\n"
        f"⚡ NUEVO: Cierre automático de posiciones contrarias"
    )


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /balance"""
    try:
        balances = []
        total = 0.0

        for exchange in bot.exchanges:
            balance = exchange.get_balance()
            balances.append(f"{exchange.name}: ${balance:.2f}")
            total += balance

        msg = "💰 Balances USDT:\n\n" + "\n".join(balances) + f"\n\nTotal: ${total:.2f}"
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /positions - MEJORADO"""
    try:
        if not bot.active_positions:
            # Sincronizar con posiciones reales del exchange
            msg = "🔄 Sincronizando posiciones con exchanges...\n\n"

            for exchange in bot.exchanges:
                positions = exchange.get_open_positions()
                if positions:
                    msg += f"🦅 {exchange.name}:\n"
                    for pos in positions:
                        symbol = pos.get("symbol", "N/A")
                        side = pos.get("positionSide", "N/A")
                        qty = pos.get("positionAmt", 0)
                        msg += f"  • {symbol} {side} (Qty: {qty})\n"
                    msg += "\n"

            if msg == "🔄 Sincronizando posiciones con exchanges...\n\n":
                msg = "📭 No hay posiciones activas"

            await update.message.reply_text(msg)
            return

        msg = "📊 Posiciones activas:\n\n"
        for key, pos in bot.active_positions.items():
            msg += f"• {pos['symbol']} {pos['side']}\n"
            msg += f"  Exchange: {pos['exchange']}\n"
            msg += f"  ID: {pos['order_id']}\n"
            msg += f"  Desde: {pos['timestamp'][:19]}\n\n"

        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


def main():
    """Función principal"""
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not telegram_token:
        logger.error("❌ TELEGRAM_BOT_TOKEN no configurado")
        return

    if not bot.exchanges:
        logger.error("❌ No hay exchanges configurados")
        return

    # Crear aplicación
    application = Application.builder().token(telegram_token).build()

    # Agregar handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("balance", balance_command))
    application.add_handler(CommandHandler("positions", positions_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("✅ Bot iniciado. Esperando señales...")
    logger.info(f"📊 Exchanges configurados: {[ex.name for ex in bot.exchanges]}")
    logger.info(f"💬 Chat autorizado: {bot.authorized_chat_id}")
    logger.info("⚡ Función de cierre automático de posiciones contrarias: ACTIVA")

    # Iniciar bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()