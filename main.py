#!/usr/bin/env python3
"""
Bot de Trading Automatizado para BingX - FUTUROS USDT ISOLATED
Multi-usuario con Monitor de Posiciones + Web Log Viewer
"""
import aiohttp
from aiohttp import web
import aiohttp_cors
from collections import deque
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

# =============================================================================
# SERVIDOR WEB DE LOGS - DEFINIR ANTES DE CONFIGURAR LOGGING
# =============================================================================

log_buffer = deque(maxlen=1000)
websocket_connections = set()


class WebLogHandler(logging.Handler):
    """Handler que captura logs y los envía a WebSocket"""

    def emit(self, record):
        try:
            log_entry = {
                'id': int(datetime.now().timestamp() * 1000),
                'timestamp': datetime.fromtimestamp(record.created).isoformat(),
                'level': record.levelname,
                'message': self.format(record),
                'user': getattr(record, 'user', 'system')
            }

            log_buffer.append(log_entry)

            # Crear tarea de forma segura
            try:
                asyncio.create_task(broadcast_log(log_entry))
            except RuntimeError:
                # Si no hay event loop activo, ignorar
                pass

        except Exception as e:
            print(f"Error en WebLogHandler: {e}")


async def broadcast_log(log_entry):
    """Envía un log a todos los WebSockets conectados"""
    if websocket_connections:
        message = json.dumps({
            'type': 'new_log',
            'data': log_entry
        })

        dead_connections = set()
        for ws in websocket_connections:
            try:
                await ws.send_str(message)
            except Exception:
                dead_connections.add(ws)

        websocket_connections.difference_update(dead_connections)


async def websocket_handler(request):
    """Maneja conexiones WebSocket"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    websocket_connections.add(ws)
    print(f"✅ Nueva conexión web. Total: {len(websocket_connections)}")

    try:
        if log_buffer:
            await ws.send_str(json.dumps({
                'type': 'initial_logs',
                'data': list(log_buffer)
            }))

        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                if msg.data == 'ping':
                    await ws.send_str('pong')
            elif msg.type == aiohttp.WSMsgType.ERROR:
                print(f'WebSocket error: {ws.exception()}')
    finally:
        websocket_connections.remove(ws)

    return ws


async def index(request):
    """Página principal HTML"""
    html = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🤖 NeptuneBot - Log Viewer</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        .pulse { animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite; }
        .custom-scrollbar::-webkit-scrollbar { width: 8px; }
        .custom-scrollbar::-webkit-scrollbar-track { background: #1e293b; }
        .custom-scrollbar::-webkit-scrollbar-thumb { background: #475569; border-radius: 4px; }
        .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: #64748b; }
    </style>
</head>
<body class="bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900">
    <div id="root" class="min-h-screen p-6"></div>

    <script>
        let ws = null;
        let logs = [];
        let filter = 'all';
        let searchTerm = '';
        let autoScroll = true;
        let isConnected = false;

        function connectWebSocket() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(`${protocol}//${window.location.host}/ws`);

            ws.onopen = () => {
                console.log('✅ WebSocket conectado');
                isConnected = true;
                updateUI();
            };

            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (data.type === 'initial_logs') {
                    logs = data.data;
                } else if (data.type === 'new_log') {
                    logs.push(data.data);
                    if (logs.length > 1000) logs.shift();
                } else if (data.type === 'logs_cleared') {
                    logs = [];
                }
                updateUI();
            };

            ws.onclose = () => {
                console.log('❌ WebSocket desconectado');
                isConnected = false;
                updateUI();
                setTimeout(connectWebSocket, 3000);
            };
        }

        function getLevelColor(level) {
            const colors = {
                'INFO': 'text-blue-400 bg-blue-500/10',
                'WARNING': 'text-yellow-400 bg-yellow-500/10',
                'ERROR': 'text-red-400 bg-red-500/10',
                'SUCCESS': 'text-green-400 bg-green-500/10'
            };
            return colors[level] || 'text-gray-400 bg-gray-500/10';
        }

        function getLevelIcon(level) {
            const icons = { 'INFO': '📘', 'WARNING': '⚠️', 'ERROR': '❌', 'SUCCESS': '✅' };
            return icons[level] || '📋';
        }

        function filterLogs() {
            return logs.filter(log => {
                const matchesFilter = filter === 'all' || log.level === filter;
                const matchesSearch = log.message.toLowerCase().includes(searchTerm.toLowerCase()) ||
                                     log.user.toLowerCase().includes(searchTerm.toLowerCase());
                return matchesFilter && matchesSearch;
            });
        }

        function updateUI() {
            const filtered = filterLogs();
            const stats = {
                total: logs.length,
                info: logs.filter(l => l.level === 'INFO').length,
                warning: logs.filter(l => l.level === 'WARNING').length,
                error: logs.filter(l => l.level === 'ERROR').length
            };

            document.getElementById('root').innerHTML = `
                <div class="max-w-7xl mx-auto">
                    <div class="mb-6">
                        <div class="flex items-center justify-between mb-4">
                            <div>
                                <h1 class="text-3xl font-bold text-white flex items-center gap-3">
                                    🤖 NeptuneBot Log Viewer
                                </h1>
                                <p class="text-gray-400 mt-1">Monitoreo en tiempo real de logs del bot</p>
                            </div>
                            <div class="flex items-center gap-2">
                                <div class="px-3 py-1 rounded-full text-sm font-medium flex items-center gap-2 ${
                                    isConnected ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                                }">
                                    <div class="w-2 h-2 rounded-full ${isConnected ? 'bg-green-400 pulse' : 'bg-red-400'}"></div>
                                    ${isConnected ? 'Conectado' : 'Desconectado'}
                                </div>
                            </div>
                        </div>

                        <div class="grid grid-cols-4 gap-4 mb-6">
                            <div class="bg-slate-800/50 border border-slate-700 rounded-lg p-4">
                                <div class="text-sm text-gray-400">Total Logs</div>
                                <div class="text-2xl font-bold text-white">${stats.total}</div>
                            </div>
                            <div class="bg-blue-500/10 border border-blue-500/30 rounded-lg p-4">
                                <div class="text-sm text-blue-300">Info</div>
                                <div class="text-2xl font-bold text-blue-400">${stats.info}</div>
                            </div>
                            <div class="bg-yellow-500/10 border border-yellow-500/30 rounded-lg p-4">
                                <div class="text-sm text-yellow-300">Warnings</div>
                                <div class="text-2xl font-bold text-yellow-400">${stats.warning}</div>
                            </div>
                            <div class="bg-red-500/10 border border-red-500/30 rounded-lg p-4">
                                <div class="text-sm text-red-300">Errors</div>
                                <div class="text-2xl font-bold text-red-400">${stats.error}</div>
                            </div>
                        </div>

                        <div class="flex flex-wrap gap-4 items-center bg-slate-800/50 border border-slate-700 rounded-lg p-4">
                            <div class="flex-1 min-w-[200px]">
                                <input
                                    type="text"
                                    placeholder="🔍 Buscar en logs..."
                                    value="${searchTerm}"
                                    oninput="searchTerm = this.value; updateUI();"
                                    class="w-full px-4 py-2 bg-slate-900 border border-slate-600 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
                                />
                            </div>

                            <div class="flex gap-2">
                                <button onclick="filter = 'all'; updateUI();" class="px-4 py-2 rounded-lg font-medium transition-colors ${filter === 'all' ? 'bg-blue-500 text-white' : 'bg-slate-700 text-gray-300 hover:bg-slate-600'}">Todos</button>
                                <button onclick="filter = 'INFO'; updateUI();" class="px-4 py-2 rounded-lg font-medium transition-colors ${filter === 'INFO' ? 'bg-blue-500 text-white' : 'bg-slate-700 text-gray-300 hover:bg-slate-600'}">Info</button>
                                <button onclick="filter = 'WARNING'; updateUI();" class="px-4 py-2 rounded-lg font-medium transition-colors ${filter === 'WARNING' ? 'bg-yellow-500 text-white' : 'bg-slate-700 text-gray-300 hover:bg-slate-600'}">Warnings</button>
                                <button onclick="filter = 'ERROR'; updateUI();" class="px-4 py-2 rounded-lg font-medium transition-colors ${filter === 'ERROR' ? 'bg-red-500 text-white' : 'bg-slate-700 text-gray-300 hover:bg-slate-600'}">Errors</button>
                            </div>

                            <div class="flex gap-2 ml-auto">
                                <button onclick="autoScroll = !autoScroll; updateUI();" class="p-2 rounded-lg transition-colors ${autoScroll ? 'bg-blue-500 text-white' : 'bg-slate-700 text-gray-300 hover:bg-slate-600'}" title="Auto-scroll">🔄</button>
                                <button onclick="exportLogs()" class="p-2 bg-slate-700 text-gray-300 rounded-lg hover:bg-slate-600 transition-colors" title="Exportar logs">💾</button>
                                <button onclick="clearLogs()" class="p-2 bg-red-500/20 text-red-400 rounded-lg hover:bg-red-500/30 transition-colors" title="Limpiar logs">🗑️</button>
                            </div>
                        </div>
                    </div>

                    <div id="logs-container" class="bg-slate-900 border border-slate-700 rounded-lg p-4 h-[600px] overflow-y-auto font-mono text-sm custom-scrollbar">
                        ${filtered.length === 0 ? `
                            <div class="flex flex-col items-center justify-center h-full text-gray-500">
                                <div class="text-4xl mb-2 opacity-50">📋</div>
                                <p>No hay logs para mostrar</p>
                                <p class="text-xs mt-2">Esperando logs del bot...</p>
                            </div>
                        ` : filtered.map(log => `
                            <div class="mb-2 p-3 bg-slate-800/50 border border-slate-700 rounded hover:bg-slate-800 transition-colors">
                                <div class="flex items-start gap-3">
                                    <div class="flex items-center gap-2 px-2 py-1 rounded ${getLevelColor(log.level)}">
                                        <span>${getLevelIcon(log.level)}</span>
                                        <span class="font-bold text-xs">${log.level}</span>
                                    </div>
                                    <div class="flex-1 min-w-0">
                                        <div class="flex items-center gap-2 mb-1">
                                            <span class="text-gray-400 text-xs">${new Date(log.timestamp).toLocaleTimeString()}</span>
                                            <span class="text-blue-400 text-xs bg-blue-500/10 px-2 py-0.5 rounded">${log.user}</span>
                                        </div>
                                        <div class="text-gray-200 break-words">${log.message}</div>
                                    </div>
                                </div>
                            </div>
                        `).join('')}
                    </div>

                    <div class="mt-4 text-center text-gray-500 text-sm">
                        Mostrando ${filtered.length} de ${logs.length} logs
                    </div>
                </div>
            `;

            if (autoScroll) {
                const container = document.getElementById('logs-container');
                if (container) container.scrollTop = container.scrollHeight;
            }
        }

        function exportLogs() {
            const filtered = filterLogs();
            const text = filtered.map(log => 
                `${log.timestamp} [${log.level}] [${log.user}] ${log.message}`
            ).join('\\n');

            const blob = new Blob([text], { type: 'text/plain' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `neptune-logs-${new Date().toISOString().split('T')[0]}.txt`;
            a.click();
            URL.revokeObjectURL(url);
        }

        async function clearLogs() {
            if (confirm('¿Estás seguro de que quieres limpiar todos los logs?')) {
                await fetch('/api/clear', { method: 'POST' });
            }
        }

        connectWebSocket();
        updateUI();
    </script>
</body>
</html>"""
    return web.Response(text=html, content_type='text/html')


async def get_logs(request):
    """API endpoint para obtener logs"""
    return web.json_response({'logs': list(log_buffer), 'total': len(log_buffer)})


async def clear_logs(request):
    """API endpoint para limpiar logs"""
    log_buffer.clear()
    message = json.dumps({'type': 'logs_cleared'})
    for ws in websocket_connections:
        try:
            await ws.send_str(message)
        except Exception:
            pass
    return web.json_response({'success': True})


def create_web_app():
    """Crea la aplicación web"""
    app = web.Application()

    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
        )
    })

    app.router.add_get('/', index)
    app.router.add_get('/ws', websocket_handler)
    app.router.add_get('/api/logs', get_logs)
    app.router.add_post('/api/clear', clear_logs)

    for route in list(app.router.routes()):
        cors.add(route)

    return app


async def start_web_server():
    """Inicia el servidor web en segundo plano"""
    app = create_web_app()
    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, 'localhost', 8765)
    await site.start()

    print("=" * 60)
    print("🌐 SERVIDOR DE LOGS INICIADO")
    print("=" * 60)
    print("📊 URL: http://localhost:8765")
    print("=" * 60)


# =============================================================================
# CONFIGURAR LOGGING - AHORA QUE WebLogHandler ESTÁ DEFINIDO
# =============================================================================

log_level = os.getenv("LOG_LEVEL", "INFO")

# Crear el logger
logger = logging.getLogger(__name__)
logger.setLevel(getattr(logging, log_level))

# Handler para consola
console_handler = logging.StreamHandler()
console_handler.setLevel(getattr(logging, log_level))
console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(console_formatter)

# Handler para web
web_handler = WebLogHandler()
web_handler.setLevel(getattr(logging, log_level))
web_formatter = logging.Formatter('%(message)s')
web_handler.setFormatter(web_formatter)

# Configurar el logger raíz
root_logger = logging.getLogger('')
root_logger.setLevel(getattr(logging, log_level))
root_logger.addHandler(console_handler)
root_logger.addHandler(web_handler)


# =============================================================================
# RESTO DEL CÓDIGO DEL BOT (Sin cambios)
# =============================================================================

class ConfigManager:
    """Gestor de configuración JSON por usuario - Multi-cuenta"""

    def __init__(self, config_path: str = "config.json"):
        self.config_path = config_path
        with open(config_path, 'r') as f:
            self.config = json.load(f)

    def save(self):
        """Guarda la configuración en el archivo"""
        with open(self.config_path, 'w') as f:
            json.dump(self.config, f, indent=2)

    def get_user_accounts(self, username: str) -> Dict:
        """Obtiene todas las cuentas de un usuario"""
        users = self.config.get("users", {})
        user_data = users.get(username, {})
        return user_data.get("accounts", {})

    def get_account_config(self, username: str, account_name: str) -> Dict:
        """Obtiene configuración de una cuenta específica de un usuario"""
        users = self.config.get("users", {})
        default_config = users.get("default", {})
        user_data = users.get(username, {})

        # Si el usuario tiene estructura de accounts (multi-cuenta)
        if "accounts" in user_data:
            account_config = user_data.get("accounts", {}).get(account_name, {})
            # Merge: default -> account_config
            merged_config = {**default_config, **account_config}
        else:
            # Compatibilidad con estructura antigua (single account)
            merged_config = {**default_config, **user_data}

        # Asegurar valores por defecto
        defaults = {
            "tp1_distribution": 30,
            "tp2_distribution": 35,
            "tp3_distribution": 20,
            "trailing_stop_callback": 1.0,
            "trailing_stop_activation_percent": 2.5
        }

        for key, value in defaults.items():
            if key not in merged_config:
                merged_config[key] = value

        return merged_config

    def get_user_config(self, username: str, account_name: str = None) -> Dict:
        """Obtiene configuración de un usuario (compatible con código existente)"""
        if account_name:
            return self.get_account_config(username, account_name)

        # Si no se especifica cuenta, usar la primera cuenta disponible o config legacy
        users = self.config.get("users", {})
        user_data = users.get(username, {})

        if "accounts" in user_data:
            # Retornar config de la primera cuenta habilitada
            for acc_name, acc_config in user_data["accounts"].items():
                if acc_config.get("enabled", True):
                    return self.get_account_config(username, acc_name)

        # Fallback a estructura legacy
        default_config = users.get("default", {})
        merged_config = {**default_config, **user_data}

        defaults = {
            "tp1_distribution": 30,
            "tp2_distribution": 35,
            "tp3_distribution": 20,
            "trailing_stop_callback": 1.0,
            "trailing_stop_activation_percent": 2.5
        }

        for key, value in defaults.items():
            if key not in merged_config:
                merged_config[key] = value

        logger.info(
            f"📋 Config {username}: margen=${merged_config.get('usdt_margin_per_trade')}, "
            f"leverage={merged_config.get('default_leverage')}x, "
            f"TP: {merged_config.get('tp1_distribution')}/{merged_config.get('tp2_distribution')}/{merged_config.get('tp3_distribution')}% "
            f"en +{merged_config.get('tp1_percent')}/{merged_config.get('tp2_percent')}/{merged_config.get('tp3_percent')}%, "
            f"trailing: +{merged_config.get('trailing_stop_activation_percent')}%, callback={merged_config.get('trailing_stop_callback')}%")
        return merged_config

    def add_account(self, username: str, account_name: str, env_prefix: str, config: Dict = None) -> bool:
        """Añade una nueva cuenta a un usuario"""
        if username not in self.config["users"]:
            self.config["users"][username] = {"accounts": {}}

        if "accounts" not in self.config["users"][username]:
            self.config["users"][username]["accounts"] = {}

        default_config = self.config["users"].get("default", {})
        account_config = {
            "env_prefix": env_prefix,
            "enabled": True,
            **default_config,
            **(config or {})
        }

        self.config["users"][username]["accounts"][account_name] = account_config
        self.save()
        return True

    def remove_account(self, username: str, account_name: str) -> bool:
        """Elimina una cuenta de un usuario"""
        if username in self.config["users"]:
            if "accounts" in self.config["users"][username]:
                if account_name in self.config["users"][username]["accounts"]:
                    del self.config["users"][username]["accounts"][account_name]
                    self.save()
                    return True
        return False

    def toggle_account(self, username: str, account_name: str, enabled: bool) -> bool:
        """Habilita/deshabilita una cuenta"""
        if username in self.config["users"]:
            if "accounts" in self.config["users"][username]:
                if account_name in self.config["users"][username]["accounts"]:
                    self.config["users"][username]["accounts"][account_name]["enabled"] = enabled
                    self.save()
                    return True
        return False

    def update_account_config(self, username: str, account_name: str, key: str, value) -> bool:
        """Actualiza un parámetro de configuración de una cuenta"""
        if username in self.config["users"]:
            if "accounts" in self.config["users"][username]:
                if account_name in self.config["users"][username]["accounts"]:
                    self.config["users"][username]["accounts"][account_name][key] = value
                    self.save()
                    return True
        return False

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
        """Verifica todas las posiciones de todas las cuentas de todos los usuarios"""
        for user_id, accounts in self.bot.user_accounts.items():
            for account_name, exchange in accounts.items():
                try:
                    positions = exchange.get_open_positions()

                    if positions:
                        logger.info(f"🔍 {user_id}/{account_name}: Detectadas {len(positions)} posición(es)")
                        for pos in positions:
                            symbol = pos.get("symbol", "?")
                            side = pos.get("positionSide", "?")
                            qty = pos.get("positionAmt", 0)
                            logger.info(f"   📍 {symbol} {side} qty={qty}")
                    else:
                        logger.debug(f"🔍 {user_id}/{account_name}: Sin posiciones abiertas")

                    for pos in positions:
                        await self.verify_position_orders(user_id, account_name, exchange, pos)

                except Exception as e:
                    logger.error(f"Error verificando posiciones de {user_id}/{account_name}: {e}")

    async def verify_position_orders(self, user_id: str, account_name: str, exchange: BingXAPI, position: Dict):
        """Verifica que una posición tenga todos sus TP/SL/Trailing"""
        try:
            symbol = position.get("symbol")
            side = position.get("positionSide")  # LONG o SHORT
            quantity = float(position.get("positionAmt", 0))
            entry_price = float(position.get("avgPrice", 0))

            if quantity == 0 or entry_price == 0:
                return

            # Crear ID único para la posición
            position_id = f"{user_id}_{account_name}_{symbol}_{side}"

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

            # Obtener configuración del usuario/cuenta
            user_config = self.bot.config.get_account_config(user_id, account_name)

            # Obtener info del contrato
            contract_info = exchange.get_contract_info(symbol)
            min_qty = float(contract_info.get("minQty", 0))
            qty_precision = int(contract_info.get("quantityPrecision", 0))

            # Verificar y corregir SL
            if not has_sl:
                logger.warning(f"⚠️ {user_id}/{account_name} - {symbol}: Falta SL, configurando...")
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
                logger.warning(
                    f"⚠️ {user_id}/{account_name} - {symbol}: Solo {tp_count}/3 TPs, configurando faltantes...")

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
                        logger.warning(
                            f"⚠️ {user_id}/{account_name} - {symbol}: TP{i + 1} qty={tp_qty} < min={min_qty}, ajustando")
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
                            f"⚠️ {user_id}/{account_name} - {symbol}: TP{i + 1} omitido - qty={tp_qty}, min_valid={min_valid_qty}, remaining={remaining_for_tps}")

            # Verificar y corregir Trailing Stop
            if not has_trailing:
                logger.warning(f"⚠️ {user_id}/{account_name} - {symbol}: Falta Trailing Stop, configurando...")

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
                        f"⚠️ {user_id}/{account_name} - {symbol}: Trailing qty={trailing_quantity} < min={min_qty}, ajustando")
                    trailing_quantity = min_qty

                if trailing_quantity > 0 and trailing_quantity <= abs(quantity):
                    exchange.set_trailing_stop(symbol, order_side, trailing_callback, trailing_activation_price,
                                               trailing_quantity)
                else:
                    logger.warning(
                        f"⚠️ {user_id}/{account_name} - {symbol}: No hay cantidad válida para trailing ({trailing_quantity})")

        except Exception as e:
            logger.error(f"Error verificando órdenes de {symbol}: {e}")

    def stop(self):
        """Detiene el monitor"""
        self.is_running = False
        logger.info("🛑 Monitor de posiciones detenido")


class TradingBot:
    """Bot principal - Multi-cuenta por usuario"""

    def __init__(self, config_path: str = "config.json"):
        self.config = ConfigManager(config_path)
        self.active_positions = {}
        # Nueva estructura: {username: {account_name: exchange}}
        self.user_accounts = {}
        self.user_id_to_name = {}
        self.monitor = PositionMonitor(self)

        # Configurar usuarios
        self._setup_users()

        if not self.user_accounts:
            logger.error("❌ No hay exchanges configurados")

        # Log de usuarios y cuentas
        for user, accounts in self.user_accounts.items():
            acc_names = list(accounts.keys())
            logger.info(f"👥 {user}: {len(acc_names)} cuenta(s) - {acc_names}")

    def _setup_users(self):
        """Configura usuarios desde .env y config.json"""
        # Mapeo de usuarios de Telegram
        telegram_users = [
            {
                "username": os.getenv("TELEGRAM_USERNAME", "").strip().strip("'\""),
                "telegram_id": os.getenv("TELEGRAM_USER_ID")
            },
            {
                "username": os.getenv("TELEGRAM_USERNAME2", "").strip().strip("'\""),
                "telegram_id": os.getenv("TELEGRAM_USER_ID2")
            }
        ]

        # Registrar mapeo telegram_id -> username
        for user in telegram_users:
            if user["username"] and user["telegram_id"]:
                self.user_id_to_name[int(user["telegram_id"])] = user["username"]
                self.user_accounts[user["username"]] = {}

        # Configurar cuentas desde config.json
        for username in self.user_accounts.keys():
            accounts = self.config.get_user_accounts(username)

            if accounts:
                # Nueva estructura multi-cuenta
                for account_name, account_config in accounts.items():
                    if not account_config.get("enabled", True):
                        logger.info(f"⏸️ {username}/{account_name} deshabilitada")
                        continue

                    env_prefix = account_config.get("env_prefix", "BINGX")
                    api_key = os.getenv(f"{env_prefix}_API_KEY")
                    api_secret = os.getenv(f"{env_prefix}_SECRET_KEY")

                    if api_key and api_secret:
                        exchange = BingXAPI(api_key, api_secret)
                        exchange.name = f"BingX-{username}-{account_name}"
                        exchange.account_name = account_name

                        if exchange.is_available():
                            self.user_accounts[username][account_name] = exchange
                            logger.info(f"✅ {username}/{account_name} ({env_prefix}) inicializado")
                        else:
                            logger.warning(f"⚠️ {username}/{account_name} no disponible")
                    else:
                        logger.warning(f"⚠️ {username}/{account_name}: Faltan credenciales {env_prefix}")
            else:
                # Compatibilidad: estructura legacy (un usuario = una cuenta)
                # Mapeo por defecto basado en posición
                if username == os.getenv("TELEGRAM_USERNAME", "").strip().strip("'\""):
                    api_key = os.getenv("BINGX_API_KEY")
                    api_secret = os.getenv("BINGX_SECRET_KEY")
                    env_prefix = "BINGX"
                else:
                    api_key = os.getenv("BINGX2_API_KEY")
                    api_secret = os.getenv("BINGX2_SECRET_KEY")
                    env_prefix = "BINGX2"

                if api_key and api_secret:
                    exchange = BingXAPI(api_key, api_secret)
                    exchange.name = f"BingX-{username}"
                    exchange.account_name = "Principal"

                    if exchange.is_available():
                        self.user_accounts[username]["Principal"] = exchange
                        logger.info(f"✅ {username}/Principal (legacy) inicializado")

    # Propiedades de compatibilidad con código existente
    @property
    def user_exchanges(self):
        """Compatibilidad: retorna el primer exchange de cada usuario"""
        result = {}
        for username, accounts in self.user_accounts.items():
            if accounts:
                result[username] = next(iter(accounts.values()))
        return result

    def get_user_exchange(self, user_id: str, account_name: str = None) -> Optional[BingXAPI]:
        """Obtiene exchange de un usuario (opcionalmente una cuenta específica)"""
        if user_id not in self.user_accounts:
            return None

        accounts = self.user_accounts[user_id]
        if not accounts:
            return None

        if account_name:
            return accounts.get(account_name)

        # Retornar el primero disponible
        return next(iter(accounts.values()), None)

    def get_user_all_exchanges(self, user_id: str) -> Dict[str, BingXAPI]:
        """Obtiene todos los exchanges de un usuario"""
        return self.user_accounts.get(user_id, {})

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
        """Ejecuta señal para todas las cuentas de todos los usuarios"""
        results = []

        for user_id, accounts in self.user_accounts.items():
            for account_name, exchange in accounts.items():
                logger.info(f"\n{'=' * 60}")
                logger.info(f"👤 Ejecutando para {user_id}/{account_name}")
                logger.info(f"{'=' * 60}")

                if signal["action"] == "open":
                    result = await self.open_trade_for_user(signal, user_id, account_name)
                elif signal["action"] == "close":
                    result = await self.close_trade_for_user(signal, user_id, account_name)
                else:
                    result = {"success": False, "error": "Acción inválida"}

                result["user_identifier"] = f"{user_id}/{account_name}"
                result["username"] = user_id
                result["account_name"] = account_name
                results.append(result)

        return results

    async def open_trade_for_user(self, signal: Dict, user_id: str, account_name: str = None) -> Dict:
        """Abre trade para un usuario/cuenta"""
        try:
            exchange = self.get_user_exchange(user_id, account_name)
            if not exchange:
                return {"success": False, "error": "Usuario/cuenta no configurado"}

            user_config = self.config.get_user_config(user_id, account_name)

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

    async def close_trade_for_user(self, signal: Dict, user_id: str, account_name: str = None) -> Dict:
        """Cierra trade para una cuenta específica"""
        try:
            exchange = self.get_user_exchange(user_id, account_name)
            if not exchange:
                return {"success": False, "error": "Usuario/cuenta no configurado"}

            symbol = self.normalize_symbol(signal["symbol"])

            positions = exchange.get_open_positions(symbol)
            if not positions:
                return {"success": False, "error": "No hay posición"}

            logger.info(f"🔴 Cerrando {symbol}")
            result = exchange.close_position(symbol)

            if result["success"]:
                key = f"{user_id}_{account_name}_{symbol}"
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

/config show [cuenta] - Ver configuración
/config <param> <valor> [cuenta]

Parámetros:
• leverage, margin
• tp1, tp2, tp3
• tp1-dist, tp2-dist, tp3-dist
• sl, trailing-activation, trailing-callback

Ejemplos:
/config show
/config show Principal
/config leverage 15
/config margin 10 Secundaria
"""
                await event.reply(config_help)
                return

            param = parts[1].lower()

            if param == "show":
                # Mostrar configuración actual
                account_name = parts[2] if len(parts) > 2 else None
                accounts = bot.get_user_all_exchanges(user_id)

                if account_name:
                    # Config de cuenta específica
                    if account_name not in accounts:
                        await event.reply(f"❌ Cuenta '{account_name}' no encontrada")
                        return

                    user_config = bot.config.get_account_config(user_id, account_name)
                    msg = f"⚙️ Config de {account_name}:\n\n"
                    msg += f"💰 Margen: ${user_config.get('usdt_margin_per_trade')} USDT\n"
                    msg += f"⚡ Leverage: {user_config.get('default_leverage')}x\n\n"
                    msg += f"📈 Take Profits:\n"
                    msg += f"  TP1: +{user_config.get('tp1_percent')}% ({user_config.get('tp1_distribution')}%)\n"
                    msg += f"  TP2: +{user_config.get('tp2_percent')}% ({user_config.get('tp2_distribution')}%)\n"
                    msg += f"  TP3: +{user_config.get('tp3_percent')}% ({user_config.get('tp3_distribution')}%)\n\n"
                    msg += f"🛑 Stop Loss: -{user_config.get('default_sl_percent')}%\n"
                    msg += f"📊 Trailing: +{user_config.get('trailing_stop_activation_percent')}% / {user_config.get('trailing_stop_callback')}%\n"
                else:
                    # Config de todas las cuentas
                    msg = f"⚙️ Configuración de todas las cuentas:\n"
                    for acc_name in accounts.keys():
                        user_config = bot.config.get_account_config(user_id, acc_name)
                        msg += f"\n🏦 {acc_name}:\n"
                        msg += f"  💰 ${user_config.get('usdt_margin_per_trade')} | ⚡ {user_config.get('default_leverage')}x\n"
                        msg += f"  📈 TP: +{user_config.get('tp1_percent')}/{user_config.get('tp2_percent')}/{user_config.get('tp3_percent')}%\n"
                        msg += f"  🛑 SL: -{user_config.get('default_sl_percent')}%\n"

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

            # Determinar si hay cuenta específica
            account_name = parts[3] if len(parts) > 3 else None

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
            final_value = int(value) if param == "leverage" else value

            # Guardar configuración
            try:
                if account_name:
                    # Actualizar cuenta específica
                    success = bot.config.update_account_config(user_id, account_name, config_key, final_value)
                    if success:
                        await event.reply(f"✅ {account_name}: {param} = {value}")
                    else:
                        await event.reply(f"❌ Cuenta '{account_name}' no encontrada")
                else:
                    # Actualizar todas las cuentas del usuario
                    accounts = bot.get_user_all_exchanges(user_id)
                    for acc_name in accounts.keys():
                        bot.config.update_account_config(user_id, acc_name, config_key, final_value)

                    await event.reply(f"✅ {param} = {value} en todas las cuentas")

                logger.info(f"⚙️ {user_id} actualizó {param} = {value}")
            except Exception as e:
                await event.reply(f"❌ Error guardando: {e}")

        # ===== COMANDOS REGULARES =====
        elif command == "/balance":
            account_name = parts[1] if len(parts) > 1 else None

            if account_name:
                # Balance de cuenta específica
                exchange = bot.get_user_exchange(user_id, account_name)
                if not exchange:
                    await event.reply(f"❌ Cuenta '{account_name}' no encontrada")
                    return
                balance = exchange.get_balance()
                await event.reply(f"💰 {account_name}: ${balance:.2f} USDT")
            else:
                # Balance de todas las cuentas
                accounts = bot.get_user_all_exchanges(user_id)
                if not accounts:
                    await event.reply("❌ No tienes cuentas configuradas")
                    return

                total = 0
                msg = "💰 Balance por cuenta:\n\n"
                for acc_name, exchange in accounts.items():
                    balance = exchange.get_balance()
                    total += balance
                    msg += f"• {acc_name}: ${balance:.2f}\n"

                msg += f"\n📊 Total: ${total:.2f} USDT"
                await event.reply(msg)

        elif command == "/positions":
            account_name = parts[1] if len(parts) > 1 else None

            if account_name:
                # Posiciones de cuenta específica
                exchange = bot.get_user_exchange(user_id, account_name)
                if not exchange:
                    await event.reply(f"❌ Cuenta '{account_name}' no encontrada")
                    return

                positions = exchange.get_open_positions()
                if not positions:
                    await event.reply(f"🔭 {account_name}: Sin posiciones")
                    return

                msg = f"📊 Posiciones de {account_name}:\n\n"
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
            else:
                # Posiciones de todas las cuentas
                accounts = bot.get_user_all_exchanges(user_id)
                if not accounts:
                    await event.reply("❌ No tienes cuentas configuradas")
                    return

                msg = "📊 Todas tus posiciones:\n"
                total_pnl = 0
                has_positions = False

                for acc_name, exchange in accounts.items():
                    positions = exchange.get_open_positions()
                    if positions:
                        has_positions = True
                        msg += f"\n🏦 {acc_name}:\n"
                        for pos in positions:
                            symbol = pos.get("symbol", "?")
                            side = pos.get("positionSide", "?")
                            qty = pos.get("positionAmt", 0)
                            entry = pos.get("avgPrice", 0)
                            pnl = float(pos.get("unrealizedProfit", 0))
                            total_pnl += pnl
                            msg += f"  • {symbol} {side}: ${pnl:.2f}\n"

                if not has_positions:
                    await event.reply("🔭 Sin posiciones en ninguna cuenta")
                else:
                    msg += f"\n💵 PnL Total: ${total_pnl:.2f}"
                    await event.reply(msg)

        elif command == "/close":
            if len(parts) < 2:
                await event.reply("❌ Uso: /close SYMBOL [cuenta]")
                return

            symbol_input = parts[1]
            account_name = parts[2] if len(parts) > 2 else None

            if account_name:
                # Cerrar en cuenta específica
                signal = {"action": "close", "symbol": symbol_input}
                result = await bot.close_trade_for_user(signal, user_id, account_name)
                if result["success"]:
                    await event.reply(f"✅ Cerrado {symbol_input} en {account_name}")
                else:
                    await event.reply(f"❌ Error: {result.get('error')}")
            else:
                # Cerrar en todas las cuentas del usuario
                accounts = bot.get_user_all_exchanges(user_id)
                results = []
                for acc_name in accounts.keys():
                    signal = {"action": "close", "symbol": symbol_input}
                    result = await bot.close_trade_for_user(signal, user_id, acc_name)
                    results.append(f"• {acc_name}: {'✅' if result['success'] else '❌ ' + result.get('error', '')}")

                await event.reply(f"📊 Cerrando {symbol_input}:\n" + "\n".join(results))

        # ===== COMANDOS DE CUENTAS =====
        elif command == "/accounts":
            accounts = bot.get_user_all_exchanges(user_id)
            if not accounts:
                await event.reply("❌ No tienes cuentas configuradas")
                return

            msg = f"📊 Tus Cuentas ({len(accounts)}):\n\n"
            for acc_name, exchange in accounts.items():
                balance = exchange.get_balance()
                config = bot.config.get_account_config(user_id, acc_name)
                enabled = config.get("enabled", True)
                env_prefix = config.get("env_prefix", "?")
                msg += f"{'✅' if enabled else '⏸️'} **{acc_name}** ({env_prefix})\n"
                msg += f"   💰 Balance: ${balance:.2f}\n"
                msg += f"   ⚡ Leverage: {config.get('default_leverage', 10)}x\n"
                msg += f"   💵 Margen: ${config.get('usdt_margin_per_trade', 5)}\n\n"

            await event.reply(msg)

        elif command == "/account":
            if len(parts) < 2:
                account_help = f"""
📊 Gestión de Cuentas

/accounts - Ver todas tus cuentas
/account add <nombre> <ENV_PREFIX>
/account remove <nombre>
/account enable <nombre>
/account disable <nombre>
/account config <nombre> <param> <valor>

Ejemplos:
/account add Trading3 BINGX3
/account disable Principal
/account config Secundaria leverage 15
"""
                await event.reply(account_help)
                return

            subcommand = parts[1].lower()

            if subcommand == "add":
                if len(parts) < 4:
                    await event.reply("❌ Uso: /account add <nombre> <ENV_PREFIX>\nEj: /account add MiCuenta BINGX3")
                    return

                acc_name = parts[2]
                env_prefix = parts[3].upper()

                # Verificar que existan las credenciales
                api_key = os.getenv(f"{env_prefix}_API_KEY")
                api_secret = os.getenv(f"{env_prefix}_SECRET_KEY")

                if not api_key or not api_secret:
                    await event.reply(
                        f"❌ No se encontraron credenciales para {env_prefix}\nAsegúrate de tener {env_prefix}_API_KEY y {env_prefix}_SECRET_KEY en .env")
                    return

                # Añadir cuenta
                success = bot.config.add_account(user_id, acc_name, env_prefix)
                if success:
                    # Crear exchange y añadir al bot
                    exchange = BingXAPI(api_key, api_secret)
                    exchange.name = f"BingX-{user_id}-{acc_name}"
                    exchange.account_name = acc_name

                    if user_id not in bot.user_accounts:
                        bot.user_accounts[user_id] = {}
                    bot.user_accounts[user_id][acc_name] = exchange

                    await event.reply(
                        f"✅ Cuenta '{acc_name}' añadida con {env_prefix}\n\nUsa /accounts para ver tus cuentas")
                else:
                    await event.reply(f"❌ Error añadiendo cuenta")

            elif subcommand == "remove":
                if len(parts) < 3:
                    await event.reply("❌ Uso: /account remove <nombre>")
                    return

                acc_name = parts[2]
                success = bot.config.remove_account(user_id, acc_name)
                if success:
                    if user_id in bot.user_accounts and acc_name in bot.user_accounts[user_id]:
                        del bot.user_accounts[user_id][acc_name]
                    await event.reply(f"✅ Cuenta '{acc_name}' eliminada")
                else:
                    await event.reply(f"❌ Cuenta '{acc_name}' no encontrada")

            elif subcommand in ["enable", "disable"]:
                if len(parts) < 3:
                    await event.reply(f"❌ Uso: /account {subcommand} <nombre>")
                    return

                acc_name = parts[2]
                enabled = subcommand == "enable"
                success = bot.config.toggle_account(user_id, acc_name, enabled)
                if success:
                    status = "habilitada" if enabled else "deshabilitada"
                    await event.reply(f"✅ Cuenta '{acc_name}' {status}")
                else:
                    await event.reply(f"❌ Cuenta '{acc_name}' no encontrada")

            elif subcommand == "config":
                if len(parts) < 5:
                    await event.reply(
                        "❌ Uso: /account config <cuenta> <param> <valor>\nEj: /account config Principal leverage 15")
                    return

                acc_name = parts[2]
                param = parts[3].lower()
                try:
                    value = float(parts[4])
                except ValueError:
                    await event.reply("❌ El valor debe ser numérico")
                    return

                param_map = {
                    "leverage": "default_leverage",
                    "margin": "usdt_margin_per_trade",
                    "tp1": "tp1_percent",
                    "tp2": "tp2_percent",
                    "tp3": "tp3_percent",
                    "sl": "default_sl_percent",
                }

                if param not in param_map:
                    await event.reply(f"❌ Parámetro '{param}' no válido. Usa: leverage, margin, tp1, tp2, tp3, sl")
                    return

                config_key = param_map[param]
                if param == "leverage":
                    value = int(value)

                success = bot.config.update_account_config(user_id, acc_name, config_key, value)
                if success:
                    await event.reply(f"✅ {acc_name}: {param} = {value}")
                else:
                    await event.reply(f"❌ Error actualizando configuración")

            else:
                await event.reply("❌ Subcomando no reconocido. Usa /account para ver opciones")

        elif command == "/help":
            # Obtener cuentas del usuario
            accounts = bot.get_user_all_exchanges(user_id)
            acc_count = len(accounts)
            acc_names = ", ".join(accounts.keys()) if accounts else "ninguna"

            help_text = f"""
🤖 NeptuneBot Multi-Cuenta

👤 Usuario: {user_id}
📊 Cuentas: {acc_count} ({acc_names})
{"👑 Admin" if is_admin else ""}

📋 Comandos Básicos:
/accounts - Ver todas tus cuentas
/balance [cuenta] - Ver balance
/positions [cuenta] - Ver posiciones
/close SYMBOL [cuenta] - Cerrar posición
/help - Esta ayuda

📊 Gestión de Cuentas:
/account add <nombre> <PREFIX>
/account remove <nombre>
/account enable/disable <nombre>
/account config <cuenta> <param> <valor>

⚙️ Configuración:
/config show [cuenta]
/config <param> <valor> [cuenta]
"""
            if is_admin:
                help_text += """
👑 Admin:
/admin positions
/admin status
"""

            help_text += """
📡 Señales Automáticas:
• BUY BTC - Abre LONG en todas las cuentas
• SELL ETH - Abre SHORT en todas las cuentas
• CLOSE BTC - Cierra en todas las cuentas
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
    web_server_task = asyncio.create_task(start_web_server())
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