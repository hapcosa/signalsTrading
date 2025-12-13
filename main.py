#!/usr/bin/env python3
"""
Bot de Trading Automatizado para BingX - FUTUROS USDT ISOLATED
Multi-usuario con Monitor de Posiciones
"""

import os
import json
import logging
from typing import Dict, Optional, List
from datetime import datetime
from dotenv import load_dotenv
from telethon import TelegramClient, events
import telethon
from telethon.errors import TypeNotFoundError
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

        default_config = users.get("default", {})
        merged_config = {**default_config, **user_config}

        # Distribuciones de TP (ahora dejamos 15% para trailing stop)
        if "tp1_distribution" not in merged_config:
            merged_config["tp1_distribution"] = 30
        if "tp2_distribution" not in merged_config:
            merged_config["tp2_distribution"] = 35
        if "tp3_distribution" not in merged_config:
            merged_config["tp3_distribution"] = 20
        # 15% restante lo gestiona el trailing stop

        # Trailing stop callback rate (cuánto retrocede antes de cerrar)
        if "trailing_stop_callback" not in merged_config:
            merged_config["trailing_stop_callback"] = 1.0  # 1% de retroceso

        # Trailing stop activation (a qué % de ganancia se activa)
        if "trailing_stop_activation_percent" not in merged_config:
            merged_config["trailing_stop_activation_percent"] = 2.5  # Se activa al +2.5%

        logger.info(
            f"📋 Config {username}: margen=${merged_config.get('usdt_margin_per_trade')}, "
            f"leverage={merged_config.get('default_leverage')}x, "
            f"TP: {merged_config.get('tp1_distribution')}/{merged_config.get('tp2_distribution')}/{merged_config.get('tp3_distribution')}% "
            f"en +{merged_config.get('tp1_percent')}/{merged_config.get('tp2_percent')}/{merged_config.get('tp3_percent')}%, "
            f"trailing: +{merged_config.get('trailing_stop_activation_percent')}%, callback={merged_config.get('trailing_stop_callback')}%")
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


class BingXAPI:
    """API para BingX - Futuros USDT ISOLATED"""

    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://open-api.bingx.com"
        self.name = "BingX"

    def is_available(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _generate_signature(self, params: str, secret: str) -> str:
        return hmac.new(
            secret.encode('utf-8'),
            params.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

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
            logger.error(f"Error obteniendo balance: {e}")
            return 0.0

    def get_current_price(self, symbol: str) -> float:
        """Obtiene precio actual"""
        try:
            endpoint = "/openApi/swap/v2/quote/ticker"
            params = {"symbol": symbol}
            response = self._make_request("GET", endpoint, params)
            if response and response.get("code") == 0:
                return float(response["data"]["lastPrice"])
            return 0.0
        except Exception as e:
            logger.error(f"Error obteniendo precio: {e}")
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
                return [pos for pos in positions if float(pos.get("positionAmt", 0)) != 0]
            return []
        except Exception as e:
            logger.error(f"Error obteniendo posiciones: {e}")
            return []

    def get_open_orders(self, symbol: str) -> List[Dict]:
        """Obtiene órdenes abiertas (TP, SL, trailing)"""
        try:
            endpoint = "/openApi/swap/v2/trade/openOrders"
            timestamp = int(time.time() * 1000)
            params = {"symbol": symbol, "timestamp": timestamp}

            response = self._make_request("GET", endpoint, params)
            if response and response.get("code") == 0:
                return response.get("data", {}).get("orders", [])
            return []
        except Exception as e:
            logger.error(f"Error obteniendo órdenes: {e}")
            return []

    def set_margin_mode(self, symbol: str, margin_type: str = "ISOLATED"):
        """Configura margin mode"""
        try:
            endpoint = "/openApi/swap/v2/trade/marginType"
            timestamp = int(time.time() * 1000)
            params = {"symbol": symbol, "marginType": margin_type, "timestamp": timestamp}
            response = self._make_request("POST", endpoint, params)

            if response and (response.get("code") == 0 or response.get("code") == 100412):
                logger.info(f"✅ Margin mode: {margin_type} para {symbol}")
                return True
            return False
        except Exception as e:
            logger.error(f"Error configurando margin: {e}")
            return False

    def get_contract_info(self, symbol: str) -> Dict:
        """Obtiene info del contrato"""
        try:
            endpoint = "/openApi/swap/v2/quote/contracts"
            params = {"symbol": symbol}
            response = self._make_request("GET", endpoint, params)

            if response and response.get("code") == 0:
                for contract in response.get("data", []):
                    if contract.get("symbol") == symbol:
                        return contract
            return {}
        except Exception as e:
            logger.error(f"Error obteniendo contrato: {e}")
            return {}

    def calculate_tp_quantity_from_usdt(self, total_quantity: float, entry_price: float,
                                        tp_price: float, usdt_target: float, leverage: int) -> float:
        """Calcula la cantidad para un TP basado en valor USDT objetivo

        Args:
            total_quantity: Cantidad total de la posición
            entry_price: Precio de entrada
            tp_price: Precio del take profit
            usdt_target: Valor en USDT que quieres ganar con este TP
            leverage: Apalancamiento usado

        Returns:
            Cantidad de monedas para el TP
        """
        try:
            # Ganancia por unidad = diferencia de precio
            profit_per_unit = abs(tp_price - entry_price)

            # Cantidad necesaria para alcanzar el objetivo USDT
            # usdt_target = quantity * profit_per_unit
            quantity_needed = usdt_target / profit_per_unit

            # No puede exceder la cantidad total
            if quantity_needed > total_quantity:
                quantity_needed = total_quantity

            return quantity_needed

        except Exception as e:
            logger.error(f"Error calculando TP quantity desde USDT: {e}")
            return 0.0

    def calculate_position_size(self, symbol: str, usdt_amount: float, leverage: int, current_price: float) -> float:
        """Calcula tamaño de posición"""
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
                logger.error(f"❌ Cantidad {quantity} < mínimo {min_qty}")
                return 0.0

            logger.info(f"📊 Tamaño: {quantity} {symbol}")
            return quantity
        except Exception as e:
            logger.error(f"Error calculando tamaño: {e}")
            return 0.0

    def _set_leverage(self, symbol: str, leverage: int):
        """Configura leverage"""
        endpoint = "/openApi/swap/v2/trade/leverage"
        for side in ["LONG", "SHORT"]:
            params = {
                "symbol": symbol,
                "side": side,
                "leverage": leverage,
                "timestamp": int(time.time() * 1000)
            }
            self._make_request("POST", endpoint, params)

    def set_stop_loss(self, symbol: str, side: str, price: float, quantity: float) -> bool:
        """Configura Stop Loss"""
        try:
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
            response = self._make_request("POST", endpoint, params)
            if response and response.get("code") == 0:
                logger.info(f"✅ SL configurado: ${price:.4f}")
                return True
            logger.warning(f"⚠️ Error en SL: {response}")
            return False
        except Exception as e:
            logger.error(f"Error configurando SL: {e}")
            return False

    def set_take_profit(self, symbol: str, side: str, price: float, quantity: float, tp_num: int) -> bool:
        """Configura un Take Profit usando orden LIMIT (sin reduceOnly en Hedge Mode)"""
        try:
            endpoint = "/openApi/swap/v2/trade/order"
            params = {
                "symbol": symbol,
                "side": "SELL" if side == "BUY" else "BUY",
                "positionSide": "LONG" if side == "BUY" else "SHORT",
                "type": "LIMIT",
                "price": price,
                "quantity": quantity,
                # NO usar reduceOnly en Hedge Mode - BingX lo rechaza
                "timestamp": int(time.time() * 1000)
            }
            response = self._make_request("POST", endpoint, params)
            if response and response.get("code") == 0:
                logger.info(f"✅ TP{tp_num} configurado: ${price:.4f} qty={quantity}")
                return True
            logger.warning(f"⚠️ Error en TP{tp_num}: {response}")
            return False
        except Exception as e:
            logger.error(f"Error configurando TP{tp_num}: {e}")
            return False

    def set_trailing_stop(self, symbol: str, side: str, callback_rate: float, activation_price: float,
                          position_quantity: float) -> bool:
        """Configura Trailing Stop con precio de activación y callback rate

        IMPORTANTE: callback_rate debe estar en formato decimal
        Ejemplo: 1.2% debe pasarse como 1.2, y se convierte a 0.012 internamente
        """
        try:
            endpoint = "/openApi/swap/v2/trade/order"

            # BingX requiere priceRate en formato decimal (1.2% = 0.012, no 1.2)
            price_rate_decimal = callback_rate / 100

            params = {
                "symbol": symbol,
                "side": "SELL" if side == "BUY" else "BUY",
                "positionSide": "LONG" if side == "BUY" else "SHORT",
                "type": "TRAILING_STOP_MARKET",
                "stopPrice": activation_price,  # Precio donde se activa el trailing
                "priceRate": price_rate_decimal,  # % de retroceso en formato decimal (debe ser ≤ 1)
                "quantity": position_quantity,  # Cantidad total restante
                "timestamp": int(time.time() * 1000)
            }

            response = self._make_request("POST", endpoint, params)
            if response and response.get("code") == 0:
                logger.info(
                    f"✅ Trailing Stop: activa en ${activation_price:.4f}, callback {callback_rate}% ({price_rate_decimal}), qty={position_quantity}")
                return True
            logger.warning(f"⚠️ Error en Trailing: {response}")
            return False
        except Exception as e:
            logger.error(f"Error configurando Trailing: {e}")
            return False

    def open_position(self, symbol: str, side: str, usdt_amount: float, leverage: int,
                      tp_percent: List[float], sl_percent: float,
                      trailing_activation_percent: float, trailing_callback: float,
                      tp_distribution: List[int]) -> Dict:
        """Abre posición con TP parciales y trailing stop

        Los TPs se configuran basándose en el valor USDT calculado desde los porcentajes.
        Esto evita problemas con cantidades mínimas del contrato.
        """
        try:
            self.set_margin_mode(symbol, "ISOLATED")

            current_price = self.get_current_price(symbol)
            if current_price == 0:
                return {"success": False, "error": "No se pudo obtener precio"}

            quantity = self.calculate_position_size(symbol, usdt_amount, leverage, current_price)
            if quantity == 0:
                return {"success": False, "error": "Cantidad = 0"}

            self._set_leverage(symbol, leverage)

            # Calcular precios de TP, SL y Trailing
            if side == "BUY":
                tp_prices = [current_price * (1 + tp / 100) for tp in tp_percent]
                sl_price = current_price * (1 - sl_percent / 100)
                trailing_activation_price = current_price * (1 + trailing_activation_percent / 100)
            else:
                tp_prices = [current_price * (1 - tp / 100) for tp in tp_percent]
                sl_price = current_price * (1 + sl_percent / 100)
                trailing_activation_price = current_price * (1 - trailing_activation_percent / 100)

            # Abrir posición
            endpoint = "/openApi/swap/v2/trade/order"
            params = {
                "symbol": symbol,
                "side": side,
                "positionSide": "LONG" if side == "BUY" else "SHORT",
                "type": "MARKET",
                "quantity": quantity,
                "timestamp": int(time.time() * 1000)
            }

            response = self._make_request("POST", endpoint, params)

            if not response or response.get("code") != 0:
                error_msg = response.get("msg", "Error desconocido") if response else "Sin respuesta"
                return {"success": False, "error": f"BingX: {error_msg}"}

            order_data = response.get("data", {}).get("order", {})
            order_id = order_data.get("orderId", "unknown")

            logger.info(f"✅ Posición abierta: {order_id}")
            logger.info(f"   {symbol} | {side} | Qty: {quantity}")
            logger.info(f"   Precio: ${current_price:.4f} | Margen: ${usdt_amount}")

            # Esperar un poco para que se registre la posición
            time.sleep(1)

            # Configurar SL
            sl_success = self.set_stop_loss(symbol, side, sl_price, quantity)
            if not sl_success:
                logger.warning("⚠️ SL no se pudo configurar, se reintentará en el monitor")

            # Obtener info del contrato
            contract_info = self.get_contract_info(symbol)
            min_qty = float(contract_info.get("minQty", 0))
            qty_precision = int(contract_info.get("quantityPrecision", 0))

            # Calcular valor de posición total en USDT
            position_value_usdt = usdt_amount * leverage

            # NO usar qty_precision si es 0, usar 8 decimales (estándar crypto)
            precision_to_use = max(qty_precision, 8) if qty_precision > 0 else 8

            logger.info(
                f"   💰 Valor posición: ${position_value_usdt:.2f} USDT (precision: {precision_to_use} decimales)")

            # Configurar TPs basados en porcentajes → convertir a valores USDT
            tp_success_count = 0
            total_tp_quantity = 0

            logger.info(f"   📊 TPs: {tp_distribution[0]}% (≈${position_value_usdt * tp_distribution[0] / 100:.2f}), "
                        f"{tp_distribution[1]}% (≈${position_value_usdt * tp_distribution[1] / 100:.2f}), "
                        f"{tp_distribution[2]}% (≈${position_value_usdt * tp_distribution[2] / 100:.2f})")

            for i, (tp_price, distribution, tp_pct) in enumerate(zip(tp_prices, tp_distribution, tp_percent), 1):
                # Calcular cuánto USDT queremos ganar con este TP (basado en el % de distribución)
                # Por ejemplo: 30% de la posición con 2% de ganancia
                tp_position_value = position_value_usdt * (distribution / 100)
                tp_profit_usdt = tp_position_value * (tp_pct / 100)

                # Calcular cantidad necesaria para alcanzar ese profit en USDT
                tp_quantity = self.calculate_tp_quantity_from_usdt(
                    quantity - total_tp_quantity,
                    current_price,
                    tp_price,
                    tp_profit_usdt,
                    leverage
                )

                tp_quantity = round(tp_quantity, precision_to_use)

                # Verificar mínimo (usar un mínimo razonable si min_qty es 0)
                effective_min_qty = min_qty if min_qty > 0 else 0.0001
                if tp_quantity < effective_min_qty:
                    logger.warning(f"⚠️ TP{i} qty={tp_quantity} < min={effective_min_qty}, ajustando")
                    tp_quantity = effective_min_qty

                # Verificar que no exceda lo disponible
                remaining_qty = quantity - total_tp_quantity
                if tp_quantity > remaining_qty:
                    tp_quantity = remaining_qty

                if tp_quantity >= effective_min_qty and tp_quantity > 0:
                    if self.set_take_profit(symbol, side, tp_price, tp_quantity, i):
                        # Calcular ganancia real en USDT
                        profit_per_unit = abs(tp_price - current_price)
                        actual_profit_usdt = profit_per_unit * tp_quantity

                        tp_success_count += 1
                        total_tp_quantity += tp_quantity
                        logger.info(f"   💵 TP{i}: {tp_quantity} unidades → ${actual_profit_usdt:.2f} USDT de ganancia")
                    time.sleep(0.5)
                else:
                    logger.warning(f"⚠️ TP{i} omitido: qty={tp_quantity} inválida")

            logger.info(f"   TPs configurados: {tp_success_count}/3 (total qty: {total_tp_quantity}/{quantity})")

            # Calcular cantidad restante para trailing stop
            trailing_quantity = round(quantity - total_tp_quantity, precision_to_use)

            # Calcular valor USDT objetivo para el trailing (basado en el % restante, típicamente 15%)
            trailing_distribution_pct = 100 - sum(tp_distribution)
            trailing_position_value = position_value_usdt * (trailing_distribution_pct / 100)
            trailing_profit_usdt = trailing_position_value * (trailing_activation_percent / 100)

            logger.info(f"   🎯 Trailing: {trailing_distribution_pct}% restante "
                        f"(≈${trailing_position_value:.2f}) → objetivo ${trailing_profit_usdt:.2f} USDT")

            effective_min_qty = min_qty if min_qty > 0 else 0.0001
            if trailing_quantity < effective_min_qty:
                logger.warning(f"⚠️ Trailing qty={trailing_quantity} < min={effective_min_qty}, ajustando")
                trailing_quantity = effective_min_qty

            # Configurar Trailing Stop
            trailing_success = False
            if trailing_quantity > 0 and trailing_quantity <= (quantity - total_tp_quantity + 0.01):
                trailing_success = self.set_trailing_stop(
                    symbol, side, trailing_callback, trailing_activation_price, trailing_quantity
                )
                if trailing_success:
                    # Calcular ganancia potencial del trailing
                    profit_per_unit_trailing = abs(trailing_activation_price - current_price)
                    potential_trailing_profit = profit_per_unit_trailing * trailing_quantity
                    logger.info(f"   💰 Trailing potencial: ${potential_trailing_profit:.2f} USDT al activarse")
                else:
                    logger.warning("⚠️ Trailing no se pudo configurar, se reintentará en el monitor")
            else:
                logger.warning("⚠️ No queda cantidad válida para trailing stop")

            # Calcular ganancia total potencial
            total_potential_profit = sum([
                abs(tp_price - current_price) * round(quantity * (dist / 100), precision_to_use)
                for tp_price, dist in zip(tp_prices[:tp_success_count], tp_distribution[:tp_success_count])
            ])

            logger.info(f"   💎 Ganancia total potencial TPs: ${total_potential_profit:.2f} USDT")
            logger.info(f"   📈 ROI potencial: {(total_potential_profit / usdt_amount) * 100:.1f}%")

            return {
                "success": True,
                "order_id": order_id,
                "quantity": quantity,
                "price": current_price,
                "margin_used": usdt_amount,
                "leverage": leverage,
                "sl_set": sl_success,
                "tp_count": tp_success_count,
                "trailing_set": trailing_success,
                "trailing_activation": trailing_activation_price,
                "trailing_quantity": trailing_quantity,
                "potential_profit_usdt": total_potential_profit,
                "exchange": "BingX"
            }

        except Exception as e:
            logger.error(f"❌ Error abriendo posición: {e}")
            return {"success": False, "error": str(e)}

    def close_position(self, symbol: str) -> Dict:
        """Cierra posición"""
        try:
            endpoint = "/openApi/swap/v2/trade/closeAllPositions"
            params = {"symbol": symbol, "timestamp": int(time.time() * 1000)}
            response = self._make_request("POST", endpoint, params)
            logger.info(f"✅ Posición cerrada: {symbol}")
            return {"success": True, "response": response}
        except Exception as e:
            logger.error(f"❌ Error cerrando: {e}")
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


class PositionMonitor:
    """Monitor de posiciones que verifica y corrige TP/SL/Trailing"""

    def __init__(self, bot):
        self.bot = bot
        self.is_running = False
        self.check_interval = 30  # segundos
        self.failed_positions = {}  # Guarda posiciones que dan error para no reintentarlas constantemente

    async def start(self):
        """Inicia el monitor"""
        self.is_running = True
        logger.info("🔍 Monitor de posiciones iniciado")

        while self.is_running:
            try:
                await self.check_all_positions()
                await asyncio.sleep(self.check_interval)
            except Exception as e:
                logger.error(f"Error en monitor: {e}")
                await asyncio.sleep(self.check_interval)

    async def check_all_positions(self):
        """Verifica todas las posiciones de todos los usuarios"""
        for user_id, exchange in self.bot.user_exchanges.items():
            try:
                positions = exchange.get_open_positions()

                if positions:
                    logger.info(f"🔍 {user_id}: Detectadas {len(positions)} posición(es)")
                    for pos in positions:
                        symbol = pos.get("symbol", "?")
                        side = pos.get("positionSide", "?")
                        qty = pos.get("positionAmt", 0)
                        logger.info(f"   📍 {symbol} {side} qty={qty}")
                else:
                    logger.debug(f"🔍 {user_id}: Sin posiciones abiertas")

                for pos in positions:
                    await self.verify_position_orders(user_id, exchange, pos)

            except Exception as e:
                logger.error(f"Error verificando posiciones de {user_id}: {e}")

    async def verify_position_orders(self, user_id: str, exchange: BingXAPI, position: Dict):
        """Verifica que una posición tenga todos sus TP/SL/Trailing"""
        try:
            symbol = position.get("symbol")
            side = position.get("positionSide")  # LONG o SHORT
            quantity = float(position.get("positionAmt", 0))
            entry_price = float(position.get("avgPrice", 0))

            if quantity == 0 or entry_price == 0:
                return

            # Crear ID único para la posición
            position_id = f"{user_id}_{symbol}_{side}"

            # Si esta posición ha fallado 3+ veces, saltarla
            if position_id in self.failed_positions and self.failed_positions[position_id] >= 3:
                logger.debug(f"⏭️ Saltando {position_id} (demasiados errores previos)")
                return

            # Obtener órdenes abiertas
            orders = exchange.get_open_orders(symbol)

            # Clasificar órdenes
            has_sl = False
            tp_count = 0
            has_trailing = False
            total_tp_quantity = 0

            for order in orders:
                order_type = order.get("type", "")
                order_side = order.get("side", "")
                position_side = order.get("positionSide", "")

                if order_type == "STOP_MARKET":
                    has_sl = True
                elif order_type == "LIMIT":
                    # Las órdenes LIMIT que cierran la posición son nuestros TPs
                    # En LONG: TP es SELL, en SHORT: TP es BUY
                    is_closing_order = (
                            (side == "LONG" and order_side == "SELL" and position_side == "LONG") or
                            (side == "SHORT" and order_side == "BUY" and position_side == "SHORT")
                    )
                    if is_closing_order:
                        tp_count += 1
                        total_tp_quantity += abs(float(order.get("quantity", 0)))
                elif order_type == "TRAILING_STOP_MARKET":
                    has_trailing = True

            # Obtener configuración del usuario
            user_config = self.bot.config.get_user_config(user_id)

            # Obtener info del contrato
            contract_info = exchange.get_contract_info(symbol)
            min_qty = float(contract_info.get("minQty", 0))
            qty_precision = int(contract_info.get("quantityPrecision", 0))

            # Verificar y corregir SL
            if not has_sl:
                logger.warning(f"⚠️ {user_id} - {symbol}: Falta SL, configurando...")
                sl_percent = user_config.get("default_sl_percent", 1.8)

                if side == "LONG":
                    sl_price = entry_price * (1 - sl_percent / 100)
                    order_side = "SELL"  # Para cerrar posición LONG
                else:
                    sl_price = entry_price * (1 + sl_percent / 100)
                    order_side = "BUY"  # Para cerrar posición SHORT

                exchange.set_stop_loss(symbol, order_side, sl_price, abs(quantity))

            # Verificar y corregir TPs
            if tp_count < 3:
                logger.warning(f"⚠️ {user_id} - {symbol}: Solo {tp_count}/3 TPs, configurando faltantes...")

                tp_percents = [
                    user_config.get("tp1_percent", 2.0),
                    user_config.get("tp2_percent", 3.5),
                    user_config.get("tp3_percent", 5.0)
                ]

                tp_distributions = [
                    user_config.get("tp1_distribution", 30),
                    user_config.get("tp2_distribution", 35),
                    user_config.get("tp3_distribution", 20)
                ]

                if side == "LONG":
                    tp_prices = [entry_price * (1 + tp / 100) for tp in tp_percents]
                    order_side = "SELL"  # Para cerrar posición LONG
                else:
                    tp_prices = [entry_price * (1 - tp / 100) for tp in tp_percents]
                    order_side = "BUY"  # Para cerrar posición SHORT

                # Calcular valor de posición en USDT para usar el mismo sistema que open_position
                usdt_margin = user_config.get("usdt_margin_per_trade", 5.0)
                leverage = user_config.get("default_leverage", 10)
                position_value_usdt = usdt_margin * leverage

                remaining_for_tps = abs(quantity) - total_tp_quantity

                logger.info(
                    f"   💡 Configurando TPs faltantes: entry=${entry_price:.4f}, qty={abs(quantity)}, remaining={remaining_for_tps}")
                logger.info(f"   💰 Valor posición estimado: ${position_value_usdt:.2f} USDT")

                for i in range(tp_count, 3):
                    # Calcular usando el mismo sistema que open_position (basado en USDT)
                    tp_position_value = position_value_usdt * (tp_distributions[i] / 100)
                    tp_profit_usdt = tp_position_value * (tp_percents[i] / 100)

                    # Calcular cantidad necesaria para alcanzar ese profit en USDT
                    profit_per_unit = abs(tp_prices[i] - entry_price)

                    if profit_per_unit > 0:
                        tp_qty = tp_profit_usdt / profit_per_unit
                    else:
                        tp_qty = 0

                    # NO usar qty_precision del contrato ya que puede ser 0
                    # Usar máximo 8 decimales (estándar en crypto) o el del contrato si es mayor
                    precision_to_use = max(qty_precision, 8) if qty_precision > 0 else 8
                    tp_qty = round(tp_qty, precision_to_use)

                    logger.info(
                        f"   🔍 TP{i + 1}: qty={tp_qty} (precision={precision_to_use}) → ${tp_profit_usdt:.2f} USDT")

                    # Verificar mínimo
                    if min_qty > 0 and tp_qty < min_qty:
                        logger.warning(f"⚠️ {user_id} - {symbol}: TP{i + 1} qty={tp_qty} < min={min_qty}, ajustando")
                        tp_qty = min_qty

                    # Verificar que no exceda la cantidad restante
                    if tp_qty > remaining_for_tps:
                        tp_qty = remaining_for_tps

                    # Verificar que sea una cantidad válida (usar un mínimo razonable)
                    min_valid_qty = min_qty if min_qty > 0 else 0.0001

                    if tp_qty >= min_valid_qty and tp_qty > 0 and remaining_for_tps > 0:
                        success = exchange.set_take_profit(symbol, order_side, tp_prices[i], tp_qty, i + 1)
                        if success:
                            total_tp_quantity += tp_qty
                            remaining_for_tps -= tp_qty
                            logger.info(f"   ✅ TP{i + 1} configurado exitosamente")
                            # Reset contador de errores si tuvo éxito
                            if position_id in self.failed_positions:
                                del self.failed_positions[position_id]
                        else:
                            # Incrementar contador de errores
                            self.failed_positions[position_id] = self.failed_positions.get(position_id, 0) + 1
                            if self.failed_positions[position_id] >= 3:
                                logger.warning(
                                    f"⚠️ {position_id}: Demasiados errores, será ignorada en próximas verificaciones")
                        await asyncio.sleep(0.5)
                    else:
                        logger.warning(
                            f"⚠️ {user_id} - {symbol}: TP{i + 1} omitido - qty={tp_qty}, min_valid={min_valid_qty}, remaining={remaining_for_tps}")

            # Verificar y corregir Trailing Stop
            if not has_trailing:
                logger.warning(f"⚠️ {user_id} - {symbol}: Falta Trailing Stop, configurando...")

                trailing_callback = user_config.get("trailing_stop_callback", 1.0)
                trailing_activation_percent = user_config.get("trailing_stop_activation_percent", 2.5)

                # Calcular precio de activación
                if side == "LONG":
                    trailing_activation_price = entry_price * (1 + trailing_activation_percent / 100)
                    order_side = "SELL"  # Para cerrar posición LONG
                else:
                    trailing_activation_price = entry_price * (1 - trailing_activation_percent / 100)
                    order_side = "BUY"  # Para cerrar posición SHORT

                # Calcular cantidad restante para trailing
                trailing_quantity = round(abs(quantity) - total_tp_quantity, qty_precision)

                if trailing_quantity < min_qty:
                    logger.warning(
                        f"⚠️ {user_id} - {symbol}: Trailing qty={trailing_quantity} < min={min_qty}, ajustando")
                    trailing_quantity = min_qty

                if trailing_quantity > 0 and trailing_quantity <= abs(quantity):
                    exchange.set_trailing_stop(symbol, order_side, trailing_callback, trailing_activation_price,
                                               trailing_quantity)
                else:
                    logger.warning(
                        f"⚠️ {user_id} - {symbol}: No hay cantidad válida para trailing ({trailing_quantity})")

        except Exception as e:
            logger.error(f"Error verificando órdenes de {symbol}: {e}")

    def stop(self):
        """Detiene el monitor"""
        self.is_running = False
        logger.info("🛑 Monitor de posiciones detenido")


class TradingBot:
    """Bot principal"""

    def __init__(self, config_path: str = "config.json"):
        self.config = ConfigManager(config_path)
        self.active_positions = {}
        self.user_exchanges = {}
        self.user_id_to_name = {}
        self.monitor = PositionMonitor(self)

        # Configurar usuarios
        self._setup_users()

        if not self.user_exchanges:
            logger.error("❌ No hay exchanges configurados")

        logger.info(f"👥 Usuarios: {list(self.user_exchanges.keys())}")

    def _setup_users(self):
        """Configura usuarios desde .env"""
        users_config = [
            {
                "username": os.getenv("TELEGRAM_USERNAME", "").strip().strip("'\""),
                "telegram_id": os.getenv("TELEGRAM_USER_ID"),
                "api_key": os.getenv("BINGX_API_KEY"),
                "api_secret": os.getenv("BINGX_SECRET_KEY")
            },
            {
                "username": os.getenv("TELEGRAM_USERNAME2", "").strip().strip("'\""),
                "telegram_id": os.getenv("TELEGRAM_USER_ID2"),
                "api_key": os.getenv("BINGX2_API_KEY"),
                "api_secret": os.getenv("BINGX2_SECRET_KEY")
            }
        ]

        for user in users_config:
            if user["username"] and user["api_key"] and user["api_secret"]:
                exchange = BingXAPI(user["api_key"], user["api_secret"])
                exchange.name = f"BingX-{user['username']}"

                if exchange.is_available():
                    self.user_exchanges[user["username"]] = exchange
                    if user["telegram_id"]:
                        self.user_id_to_name[int(user["telegram_id"])] = user["username"]
                    logger.info(f"✅ {user['username']} inicializado")

    def get_user_exchange(self, user_id: str) -> Optional[BingXAPI]:
        return self.user_exchanges.get(user_id)

    def get_user_identifier_from_telegram_id(self, telegram_id: int) -> Optional[str]:
        return self.user_id_to_name.get(telegram_id)

    def normalize_symbol(self, symbol: str) -> str:
        """BTC -> BTC-USDT"""
        symbol = symbol.upper().strip()
        if ":" in symbol:
            symbol = symbol.split(":")[1]
        if "-USDT" in symbol:
            return symbol
        if symbol.endswith("USDT"):
            symbol = symbol[:-4]
        return f"{symbol}-USDT"

    def parse_signal(self, message: str) -> Optional[Dict]:
        """Parsea señales"""
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
                return {"action": "open", "side": action, "symbol": symbol}
            elif action == "CLOSE":
                return {"action": "close", "symbol": symbol}

            return None
        except Exception as e:
            logger.error(f"Error parseando: {e}")
            return None

    async def execute_signal_for_all_users(self, signal: Dict) -> List[Dict]:
        """Ejecuta señal para todos"""
        results = []

        for user_id in self.user_exchanges.keys():
            logger.info(f"\n{'=' * 60}")
            logger.info(f"👤 Ejecutando para {user_id}")
            logger.info(f"{'=' * 60}")

            if signal["action"] == "open":
                result = await self.open_trade_for_user(signal, user_id)
            elif signal["action"] == "close":
                result = await self.close_trade_for_user(signal, user_id)
            else:
                result = {"success": False, "error": "Acción inválida"}

            result["user_identifier"] = user_id
            results.append(result)

        return results

    async def open_trade_for_user(self, signal: Dict, user_id: str) -> Dict:
        """Abre trade para un usuario"""
        try:
            exchange = self.get_user_exchange(user_id)
            if not exchange:
                return {"success": False, "error": "Usuario no configurado"}

            user_config = self.config.get_user_config(user_id)

            usdt_amount = user_config.get("usdt_margin_per_trade", 5.0)
            leverage = user_config.get("default_leverage", 10)
            min_balance = user_config.get("min_balance_required", 50)

            balance = exchange.get_balance()
            logger.info(f"💰 Balance: ${balance:.2f}")

            if balance < min_balance:
                return {"success": False, "error": f"Balance bajo: ${balance:.2f}"}

            symbol = self.normalize_symbol(signal["symbol"])
            side = signal["side"]

            # Verificar posición existente
            positions = exchange.get_open_positions(symbol)
            if positions:
                # Verificar si la posición es en la MISMA dirección o CONTRARIA
                existing_pos = positions[0]
                existing_side = existing_pos.get("positionSide")  # LONG o SHORT
                new_side = "LONG" if side == "BUY" else "SHORT"

                if existing_side == new_side:
                    # Misma dirección - no abrir otra
                    logger.warning(f"⚠️ Ya hay posición {existing_side} en {symbol}")
                    return {"success": False, "error": f"Posición {existing_side} existente"}
                else:
                    # Dirección CONTRARIA - cerrar la actual y abrir la nueva
                    logger.warning(
                        f"🔄 Posición {existing_side} detectada en {symbol}, cerrando para abrir {new_side}...")

                    # Cerrar posición actual
                    close_result = exchange.close_position(symbol)
                    if close_result["success"]:
                        logger.info(f"✅ Posición {existing_side} cerrada exitosamente")
                        # Esperar un momento para que BingX procese el cierre
                        await asyncio.sleep(2)
                    else:
                        logger.error(f"❌ Error cerrando posición {existing_side}: {close_result.get('error')}")
                        return {"success": False, "error": f"No se pudo cerrar posición {existing_side}"}

            # Configuración TP/SL desde config
            tp_percent = [
                user_config.get("tp1_percent", 2.0),
                user_config.get("tp2_percent", 3.5),
                user_config.get("tp3_percent", 5.0)
            ]

            tp_distribution = [
                user_config.get("tp1_distribution", 30),
                user_config.get("tp2_distribution", 35),
                user_config.get("tp3_distribution", 20)
            ]

            sl_percent = user_config.get("default_sl_percent", 1.8)
            trailing_activation_percent = user_config.get("trailing_stop_activation_percent", 2.5)
            trailing_callback = user_config.get("trailing_stop_callback", 1.0)

            logger.info(f"🚀 Abriendo {side} en {symbol}")
            logger.info(f"   💰 Margen: ${usdt_amount} | ⚡ Leverage: {leverage}x")
            logger.info(f"   📊 TPs en: +{tp_percent[0]}%, +{tp_percent[1]}%, +{tp_percent[2]}%")
            logger.info(f"   📈 Trailing activa: +{trailing_activation_percent}%, callback: {trailing_callback}%")

            # El cálculo de USDT se hace automáticamente dentro de open_position
            result = exchange.open_position(
                symbol, side, usdt_amount, leverage,
                tp_percent, sl_percent, trailing_activation_percent, trailing_callback, tp_distribution
            )

            if result["success"]:
                self.active_positions[f"{user_id}_{symbol}"] = {
                    "order_id": result["order_id"],
                    "side": side,
                    "symbol": symbol,
                    "user_identifier": user_id,
                    "timestamp": datetime.now().isoformat()
                }

            return result

        except Exception as e:
            logger.error(f"❌ Error: {e}")
            return {"success": False, "error": str(e)}

    async def close_trade_for_user(self, signal: Dict, user_id: str) -> Dict:
        """Cierra trade"""
        try:
            exchange = self.get_user_exchange(user_id)
            if not exchange:
                return {"success": False, "error": "Usuario no configurado"}

            symbol = self.normalize_symbol(signal["symbol"])

            positions = exchange.get_open_positions(symbol)
            if not positions:
                return {"success": False, "error": "No hay posición"}

            logger.info(f"🔴 Cerrando {symbol}")
            result = exchange.close_position(symbol)

            if result["success"]:
                key = f"{user_id}_{symbol}"
                if key in self.active_positions:
                    del self.active_positions[key]

            return result

        except Exception as e:
            logger.error(f"❌ Error: {e}")
            return {"success": False, "error": str(e)}


# Bot global
bot = TradingBot()


async def handle_command(event, sender_id: int):
    """Maneja comandos"""
    try:
        message = event.message.text.strip()
        parts = message.split()
        command = parts[0].lower()

        user_id = bot.get_user_identifier_from_telegram_id(sender_id)
        if not user_id:
            await event.reply(f"❌ Tu ID {sender_id} no está configurado")
            return

        # Verificar si es admin (primer usuario configurado en config.json)
        user_list = list(bot.config.config.get("users", {}).keys())
        # Remover 'default' de la lista
        user_list = [u for u in user_list if u != "default"]
        # El primer usuario real es el admin
        admin_users = [user_list[0]] if user_list else []
        is_admin = user_id in admin_users

        # ===== COMANDOS DE ADMIN =====
        if command == "/admin" and is_admin:
            if len(parts) < 2:
                admin_help = """
👑 Comandos de Admin:

/admin positions - Ver TODAS las posiciones
/admin close USER SYMBOL - Cerrar posición de un usuario
/admin balance USER - Ver balance de un usuario
/admin status - Estado general del bot

Ejemplo:
/admin positions
/admin close "Vera Marco Marco Vera" SUI
/admin balance "Hernan Paredes"
"""
                await event.reply(admin_help)
                return

            sub_command = parts[1].lower()

            if sub_command == "positions":
                # Ver todas las posiciones de todos los usuarios
                msg = "👑 TODAS LAS POSICIONES:\n\n"
                total_positions = 0

                for uid, exchange in bot.user_exchanges.items():
                    positions = exchange.get_open_positions()
                    if positions:
                        msg += f"👤 {uid}:\n"
                        for pos in positions:
                            symbol = pos.get("symbol", "?")
                            side = pos.get("positionSide", "?")
                            qty = pos.get("positionAmt", 0)
                            entry = pos.get("avgPrice", 0)
                            pnl = pos.get("unrealizedProfit", 0)
                            msg += f"  • {symbol} {side}\n"
                            msg += f"    Entry: ${float(entry):.4f} | Qty: {qty}\n"
                            msg += f"    PnL: ${float(pnl):.2f}\n"
                            total_positions += 1
                        msg += "\n"
                    else:
                        msg += f"👤 {uid}: Sin posiciones\n\n"

                msg += f"📊 Total: {total_positions} posición(es)"
                await event.reply(msg)

            elif sub_command == "close":
                if len(parts) < 4:
                    await event.reply('❌ Uso: /admin close "USER" SYMBOL')
                    return

                target_user = parts[2].strip('"')
                symbol_input = parts[3]

                if target_user not in bot.user_exchanges:
                    await event.reply(f"❌ Usuario '{target_user}' no encontrado")
                    return

                signal = {"action": "close", "symbol": symbol_input}
                result = await bot.close_trade_for_user(signal, target_user)

                if result["success"]:
                    await event.reply(f"✅ Admin: Cerrado {symbol_input} de {target_user}")
                else:
                    await event.reply(f"❌ Error: {result.get('error')}")

            elif sub_command == "balance":
                if len(parts) < 3:
                    await event.reply('❌ Uso: /admin balance "USER"')
                    return

                target_user = parts[2].strip('"')

                if target_user not in bot.user_exchanges:
                    await event.reply(f"❌ Usuario '{target_user}' no encontrado")
                    return

                exchange = bot.user_exchanges[target_user]
                balance = exchange.get_balance()
                await event.reply(f"💰 Balance de {target_user}: ${balance:.2f} USDT")

            elif sub_command == "status":
                msg = "🤖 Estado del Bot:\n\n"
                msg += f"👥 Usuarios: {len(bot.user_exchanges)}\n"
                msg += f"📊 Posiciones activas: {len(bot.active_positions)}\n"
                msg += f"🔍 Monitor: {'✅ Activo' if bot.monitor.is_running else '❌ Inactivo'}\n\n"

                for uid in bot.user_exchanges.keys():
                    msg += f"• {uid}\n"

                await event.reply(msg)

        # ===== COMANDOS DE CONFIGURACIÓN =====
        elif command == "/config":
            if len(parts) < 2:
                config_help = f"""
⚙️ Configuración de {user_id}:

/config leverage <valor>
/config margin <valor>
/config tp1 <porcentaje>
/config tp2 <porcentaje>
/config tp3 <porcentaje>
/config tp1-dist <porcentaje>
/config tp2-dist <porcentaje>
/config tp3-dist <porcentaje>
/config sl <porcentaje>
/config trailing-activation <porcentaje>
/config trailing-callback <porcentaje>
/config show - Ver configuración actual

Ejemplos:
/config leverage 15
/config margin 10
/config tp1 2.5
/config trailing-activation 3.0
"""
                await event.reply(config_help)
                return

            param = parts[1].lower()

            if param == "show":
                # Mostrar configuración actual
                user_config = bot.config.get_user_config(user_id)
                msg = f"⚙️ Configuración de {user_id}:\n\n"
                msg += f"💰 Margen por trade: ${user_config.get('usdt_margin_per_trade')} USDT\n"
                msg += f"⚡ Leverage: {user_config.get('default_leverage')}x\n\n"
                msg += f"📈 Take Profits:\n"
                msg += f"  TP1: +{user_config.get('tp1_percent')}% ({user_config.get('tp1_distribution')}% de posición)\n"
                msg += f"  TP2: +{user_config.get('tp2_percent')}% ({user_config.get('tp2_distribution')}% de posición)\n"
                msg += f"  TP3: +{user_config.get('tp3_percent')}% ({user_config.get('tp3_distribution')}% de posición)\n\n"
                msg += f"🛑 Stop Loss: -{user_config.get('default_sl_percent')}%\n\n"
                msg += f"📊 Trailing Stop:\n"
                msg += f"  Activación: +{user_config.get('trailing_stop_activation_percent')}%\n"
                msg += f"  Callback: {user_config.get('trailing_stop_callback')}%\n"
                await event.reply(msg)
                return

            if len(parts) < 3:
                await event.reply("❌ Falta el valor. Ejemplo: /config leverage 15")
                return

            try:
                value = float(parts[2])
            except ValueError:
                await event.reply("❌ Valor inválido. Debe ser un número.")
                return

            # Mapeo de parámetros a claves del config
            param_map = {
                "leverage": "default_leverage",
                "margin": "usdt_margin_per_trade",
                "tp1": "tp1_percent",
                "tp2": "tp2_percent",
                "tp3": "tp3_percent",
                "tp1-dist": "tp1_distribution",
                "tp2-dist": "tp2_distribution",
                "tp3-dist": "tp3_distribution",
                "sl": "default_sl_percent",
                "trailing-activation": "trailing_stop_activation_percent",
                "trailing-callback": "trailing_stop_callback"
            }

            if param not in param_map:
                await event.reply(f"❌ Parámetro '{param}' no reconocido. Usa /config para ver opciones.")
                return

            config_key = param_map[param]

            # Actualizar en memoria
            if user_id not in bot.config.config["users"]:
                bot.config.config["users"][user_id] = {}

            bot.config.config["users"][user_id][config_key] = value if param != "leverage" else int(value)

            # Guardar en archivo
            try:
                with open("config.json", 'w') as f:
                    json.dump(bot.config.config, f, indent=2)

                await event.reply(f"✅ {param} actualizado a {value}\n\nUsa /config show para ver toda tu configuración")
                logger.info(f"⚙️ {user_id} actualizó {param} = {value}")
            except Exception as e:
                await event.reply(f"❌ Error guardando configuración: {e}")

        # ===== COMANDOS REGULARES =====
        elif command == "/balance":
            exchange = bot.get_user_exchange(user_id)
            if not exchange:
                await event.reply("❌ No configurado")
                return
            balance = exchange.get_balance()
            await event.reply(f"💰 Balance: ${balance:.2f} USDT")

        elif command == "/positions":
            exchange = bot.get_user_exchange(user_id)
            if not exchange:
                await event.reply("❌ No configurado")
                return

            positions = exchange.get_open_positions()
            if not positions:
                await event.reply("🔭 Sin posiciones")
                return

            msg = f"📊 Tus Posiciones:\n\n"
            for pos in positions:
                symbol = pos.get("symbol", "?")
                side = pos.get("positionSide", "?")
                qty = pos.get("positionAmt", 0)
                entry = pos.get("avgPrice", 0)
                pnl = pos.get("unrealizedProfit", 0)
                msg += f"• {symbol} {side}\n"
                msg += f"  Entry: ${float(entry):.4f} | Qty: {qty}\n"
                msg += f"  PnL: ${float(pnl):.2f}\n\n"

            await event.reply(msg)

        elif command == "/close":
            if len(parts) < 2:
                await event.reply("❌ Uso: /close SYMBOL")
                return

            symbol_input = parts[1]
            signal = {"action": "close", "symbol": symbol_input}
            result = await bot.close_trade_for_user(signal, user_id)

            if result["success"]:
                await event.reply(f"✅ Cerrado: {symbol_input}")
            else:
                await event.reply(f"❌ Error: {result.get('error')}")

        elif command == "/help":
            help_text = f"""
🤖 NeptuneBot

👤 Tu cuenta: {user_id}
{"👑 Admin" if is_admin else ""}

📋 Comandos Básicos:
/balance - Ver tu balance
/positions - Ver tus posiciones
/close SYMBOL - Cerrar posición
/config - Configuración personal
/help - Esta ayuda

⚙️ Configuración:
/config show - Ver tu config
/config leverage 15 - Cambiar leverage
/config margin 10 - Cambiar margen
/config tp1 2.5 - Cambiar TP1
"""
            if is_admin:
                help_text += """
👑 Comandos de Admin:
/admin positions - Ver todas
/admin close USER SYMBOL
/admin balance USER


 - Estado del bot
"""

            help_text += """
📡 Señales Automáticas:
• BUY BTC - Abre LONG
• SELL ETH - Abre SHORT
• CLOSE BTC - Cierra posición

🔍 Monitor: Activo cada 30s
"""
            await event.reply(help_text)

        else:
            await event.reply(f"❌ Comando desconocido: {command}\nUsa /help")

    except Exception as e:
        logger.error(f"Error comando: {e}")
        await event.reply(f"❌ Error: {str(e)}")

    except Exception as e:
        logger.error(f"Error comando: {e}")
        await event.reply(f"❌ Error: {str(e)}")


async def main():
    """Función principal"""

    api_id = int(os.getenv("TELEGRAM_API_ID"))
    api_hash = os.getenv("TELEGRAM_API_HASH")
    phone = os.getenv("TELEGRAM_PHONE")
    target_chat_id = int(os.getenv("TELEGRAM_CHAT_ID"))

    max_retries = 5
    retry_delay = 5

    # Iniciar monitor en segundo plano
    monitor_task = asyncio.create_task(bot.monitor.start())

    for attempt in range(max_retries):
        client = None
        try:
            client = TelegramClient(
                'trading_session',
                api_id,
                api_hash,
                connection_retries=5,
                retry_delay=retry_delay
            )

            await client.start(phone=phone)
            logger.info("✅ Telethon conectado")

            me = await client.get_me()
            logger.info(f"👤 Conectado: {me.first_name}")

            @client.on(events.NewMessage(chats=target_chat_id))
            async def handler(event):
                try:
                    message = event.message.text
                    if not message:
                        return

                    sender = await event.get_sender()
                    sender_name = "Unknown"
                    sender_id = None
                    is_bot = False

                    if sender:
                        sender_name = sender.first_name or "Unknown"
                        sender_id = sender.id
                        is_bot = getattr(sender, 'bot', False)

                    logger.info(f"📨 {'🤖' if is_bot else '👤'} {sender_name}: {message}")

                    if message.startswith("/"):
                        if sender_id:
                            await handle_command(event, sender_id)
                        return

                    signal = bot.parse_signal(message)
                    if not signal:
                        return

                    if not is_bot:
                        logger.warning("⚠️ Ignorando: no es bot")
                        return

                    logger.info(f"🎯 SEÑAL: {signal}")

                    results = await bot.execute_signal_for_all_users(signal)

                    success_count = sum(1 for r in results if r.get("success"))
                    total = len(results)

                    if success_count == total:
                        response = f"✅ {signal['action'].upper()} en {success_count}/{total} cuentas: {signal['symbol']}\n"
                        for r in results:
                            uid = r.get('user_identifier', '?')
                            if signal["action"] == "open":
                                response += f"• {uid}: ${r.get('margin_used', 0):.2f}\n"
                            else:
                                response += f"• {uid}: ✓\n"
                    else:
                        response = f"⚠️ {signal['action'].upper()} en {success_count}/{total}: {signal['symbol']}\n"
                        for r in results:
                            uid = r.get('user_identifier', '?')
                            if r.get("success"):
                                response += f"• {uid}: ✅\n"
                            else:
                                response += f"• {uid}: ❌ {r.get('error')}\n"

                    logger.info(response)

                    try:
                        await event.reply(response)
                    except:
                        pass

                except Exception as e:
                    logger.error(f"❌ Error handler: {e}")

            logger.info("=" * 60)
            logger.info("🤖 NEPTUNEBOT ACTIVO")
            logger.info("=" * 60)
            logger.info(f"👥 Usuarios: {list(bot.user_exchanges.keys())}")
            logger.info(f"💬 Chat: {target_chat_id}")
            logger.info("🔍 Monitor: ACTIVO")
            logger.info("=" * 60)

            await client.run_until_disconnected()

        except TypeNotFoundError as e:
            logger.error(f"❌ Error Telethon ({attempt + 1}/{max_retries}): {e}")
            await asyncio.sleep(retry_delay)
            if attempt < max_retries - 1:
                continue
            raise

        except Exception as e:
            logger.error(f"❌ Error ({attempt + 1}/{max_retries}): {e}")
            await asyncio.sleep(retry_delay)
            if attempt < max_retries - 1:
                continue
            raise

        finally:
            if client:
                try:
                    await client.disconnect()
                except:
                    pass

    # Detener monitor
    bot.monitor.stop()
    await monitor_task


if __name__ == "__main__":
    while True:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            logger.info("👋 Bot detenido")
            break
        except Exception as e:
            logger.error(f"❌ Error fatal: {e}")
            logger.info("🔄 Reiniciando en 10s...")
            time.sleep(10)