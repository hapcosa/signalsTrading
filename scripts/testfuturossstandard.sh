#!/bin/bash

API_KEY="XTrEthh0O9bqFVdSPH4Web9zgd3rSIwlh10Ukvb9u0VVBFdeGXBFAItu341vTiPUmBSBNaeUZHospvbPavg"
SECRET_KEY="TYixG9oOZnL6HPmn7aGXVm2lc2kbTe8Wjew7G4NWplZn6YVPQ8imeNOSoV8SG0BkKRxplniSMdUSfQ8EfIA"
TIMESTAMP=$(date +%s)000

# Parámetros ordenados alfabéticamente
PARAMS="timestamp=${TIMESTAMP}"

# Generar HMAC SHA256
SIGNATURE=$(echo -n "${PARAMS}" | openssl dgst -sha256 -hmac "${SECRET_KEY}" | awk '{print $2}')

echo "=========================================="
echo "🔍 PRUEBA 1: Contratos de Futuros Estándar"
echo "=========================================="
curl -X GET "https://open-api.bingx.com/openApi/contract/v1/allContracts"
echo -e "\n\n"

echo "=========================================="
echo "💰 PRUEBA 2: Balance Futuros Estándar"
echo "=========================================="
curl -X GET "https://open-api.bingx.com/openApi/contract/v1/balance?${PARAMS}&signature=${SIGNATURE}" \
  -H "X-BX-APIKEY: ${API_KEY}"
echo -e "\n\n"

echo "=========================================="
echo "📊 PRUEBA 3: Posiciones Futuros Estándar"
echo "=========================================="
curl -X GET "https://open-api.bingx.com/openApi/contract/v1/allPosition?${PARAMS}&signature=${SIGNATURE}" \
  -H "X-BX-APIKEY: ${API_KEY}"
echo -e "\n\n"

echo "=========================================="
echo "🔎 PRUEBA 4: Precio contrato BTC"
echo "=========================================="
curl -X GET "https://open-api.bingx.com/openApi/contract/v1/ticker/price?symbol=BTC-USDT-250328"
echo -e "\n\n"

echo "=========================================="
echo "📋 PRUEBA 5: Info contratos (alternativo)"
echo "=========================================="
curl -X GET "https://open-api.bingx.com/openApi/future/v1/market/contracts"
echo -e "\n\n"