import os
import sys
from typing import Any, Dict, List, Optional, Union
import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, Resource, GetPromptResult
from dotenv import load_dotenv
import logging
import json
from urllib.parse import urlencode

# Configurar logging para debug
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Carrega variáveis de ambiente
load_dotenv()

# --- Configurações da API da EIA ---
EIA_API_BASE_URL = "https://api.eia.gov/v2"
EIA_API_KEY = os.getenv("EIA_API_KEY")

if not EIA_API_KEY:
    logger.warning("EIA_API_KEY não definida. Algumas funcionalidades podem não funcionar.")

EIA_HEADERS = {
    "User-Agent": "Enhanced-EIA-MCP-Server/2.0 (mcp-enhanced@example.com)",
    "Accept": "application/json"
}

PORT = int(os.getenv("PORT", 8000))

# --- Inicialização do Servidor MCP ---
mcp = FastMCP(
    name="enhanced-eia-data-api",
    host="0.0.0.0",
    port=PORT,
)

# --- Funções Auxiliares Melhoradas ---
def build_facets_params(facets: Dict[str, Union[str, List[str]]]) -> Dict[str, Any]:
    """
    Constrói parâmetros de facets corretamente para a API EIA v2.
    A API espera: facets[key][] para arrays ou facets[key] para valores únicos.
    """
    params = {}
    for key, values in facets.items():
        if isinstance(values, list):
            # Based on EIA documentation "facets[stateid][]=CO", httpx should serialize
            # facets[key] = ["value1", "value2"] as facets[key]=value1&facets[key]=value2
            # For EIA v2, it seems to accept facets[key]=value1&facets[key]=value2 if the key is plain,
            # or facets[key][]=value1&facets[key][]=value2 if it has the brackets.
            # Sticking to `facets[key]` as httpx handles the list serialization correctly for multiple values
            # and it often works with APIs that accept repeated parameters.
            # If `facets[key][]` is strictly needed, it should be changed to f"facets[{key}]": values.
            # However, the example in the prompt is: "facets[stateid][)]=CO",
            # the `build_facets_params` should output a single key for httpx.
            params[f"facets[{key}]"] = values # This will be serialized as facets[key]=val1&facets[key]=val2...
        else:
            params[f"facets[{key}]"] = values # Single value
    return params

def build_sort_params(sort_column: str, sort_direction: str) -> Dict[str, str]:
    """
    Constrói parâmetros de ordenação para a API EIA v2.
    """
    return {
        f"sort[0][column]": sort_column,
        f"sort[0][direction]": sort_direction
    }

async def make_eia_api_request(
    route_path: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: float = 60.0
) -> Optional[Dict[str, Any]]:
    """
    Faz requisição à API da EIA com tratamento robusto de erros e logging detalhado.
    """
    if not EIA_API_KEY:
        logger.error("EIA_API_KEY não está definida")
        return {"error": "API_KEY_MISSING", "message": "Chave da API EIA não configurada"}

    # Limpa o caminho para evitar '//' duplicados
    clean_path = route_path.strip('/')
    full_url = f"{EIA_API_BASE_URL}/{clean_path}"

    if params is None:
        params = {}

    # Adiciona a API key
    params_with_key = {**params, 'api_key': EIA_API_KEY}

    # Log detalhado da requisição (sem expor a API key)
    # urlencode handles lists correctly if doseq=True, which is useful for debugging.
    # However, httpx's default param serialization handles EIA's expected format for lists.
    logger.info(f"Requisição EIA: {full_url}")
    # For logging, let's use urlencode to see the full query string without sensitive info
    safe_params_for_log = {k: v for k, v in params_with_key.items() if k != 'api_key'}
    query_string_for_log = urlencode(safe_params_for_log, doseq=True)
    logger.info(f"Parâmetros (sem API Key): {query_string_for_log}")

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                full_url,
                params=params_with_key,
                headers=EIA_HEADERS,
                timeout=timeout
            )

            logger.info(f"Status da resposta: {response.status_code}")

            # Log da resposta para debug
            if response.status_code != 200:
                logger.error(f"Resposta não-200: {response.text[:500]}")

            response.raise_for_status()

            json_response = response.json()
            logger.info(f"Estrutura da resposta: {list(json_response.keys()) if isinstance(json_response, dict) else type(json_response)}")

            return json_response

        except httpx.HTTPStatusError as e:
            error_details = {
                "status_code": e.response.status_code,
                "response_text": e.response.text[:1000],  # Limita para não logar demais
                "url": str(e.response.url).replace(EIA_API_KEY, '***') if EIA_API_KEY else str(e.response.url)
            }
            logger.error(f"Erro HTTP EIA API: {error_details}")

            try:
                error_json = e.response.json()
                return {
                    "error": f"HTTPStatusError: {e.response.status_code}",
                    "message": error_json.get('message', e.response.text),
                    "details": error_json
                }
            except Exception:
                return {
                    "error": f"HTTPStatusError: {e.response.status_code}",
                    "message": e.response.text[:500]
                }

        except httpx.RequestError as e:
            logger.error(f"Erro de requisição EIA API: {e}")
            return {"error": "RequestError", "message": str(e)}

        except Exception as e:
            logger.error(f"Erro inesperado EIA API: {e}")
            return {"error": "UnexpectedError", "message": str(e)}

# --- Tools Melhoradas ---
@mcp.tool()
async def health_check() -> CallToolResult:
    """
    Endpoint de health check para verificar se o servidor está funcionando.
    """
    status_info = [
        "🟢 Servidor MCP EIA Enhanced está funcionando!",
        f"📡 URL Base da API: {EIA_API_BASE_URL}",
        f"🔑 API Key configurada: {'✅ Sim' if EIA_API_KEY else '❌ Não'}",
        f"🚀 Porta: {PORT}"
    ]

    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(status_info))]
    )

@mcp.tool()
async def test_eia_connection() -> CallToolResult:
    """
    Testa a conexão com a API da EIA fazendo uma requisição simples.
    """
    if not EIA_API_KEY:
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text="❌ EIA_API_KEY não configurada. Configure a variável de ambiente primeiro.")]
        )

    logger.info("Testando conexão com a API da EIA...")
    response = await make_eia_api_request("")

    if not response:
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text="❌ Falha ao conectar com a API da EIA")]
        )

    if response.get("error"):
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=f"❌ Erro na API da EIA: {response.get('message', response.get('error'))}")]
        )

    # Extrai informações básicas da resposta
    if isinstance(response, dict):
        if 'response' in response:
            api_response = response['response']
            routes_count = len(api_response.get('routes', [])) if isinstance(api_response.get('routes'), list) else 0

            success_info = [
                "✅ Conexão com API da EIA bem-sucedida!",
                f"📊 Rotas principais disponíveis: {routes_count}",
                f"🔢 Versão da API: {response.get('apiVersion', 'N/A')}",
                f"📝 ID da requisição: {response.get('request', {}).get('command', 'N/A')}"
            ]

            if routes_count > 0:
                route_names = [route.get('id', route.get('name', 'N/A')) for route in api_response.get('routes', [])[:5]]
                success_info.append(f"🗂️ Primeiras rotas: {', '.join(route_names)}")

            return CallToolResult(
                content=[TextContent(type="text", text="\n".join(success_info))]
            )

    return CallToolResult(
        content=[TextContent(type="text", text="✅ Conexão estabelecida, mas resposta em formato inesperado")]
    )

@mcp.tool()
async def get_eia_v2_route_data_enhanced(
    route_path_with_data_segment: str,
    data_elements: Optional[List[str]] = None,
    facets: Optional[Dict[str, Union[str, List[str]]]] = None,
    frequency: Optional[str] = None,
    start_period: Optional[str] = None,
    end_period: Optional[str] = None,
    sort_column: Optional[str] = None,
    sort_direction: Optional[str] = None,
    length: int = 5000,
    offset: int = 0,
    debug_mode: bool = False
) -> CallToolResult:
    """
    Versão melhorada para recuperar dados de uma rota específica da API v2 da EIA.
    Inclui melhor tratamento de erros, logging detalhado e validações.
    """

    # Validação de entrada
    if not route_path_with_data_segment.endswith('/data') and not route_path_with_data_segment.endswith('/data/'):
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=f"❌ O parâmetro 'route_path_with_data_segment' DEVE terminar em '/data' ou '/data/'. Caminho fornecido: {route_path_with_data_segment}")]
        )

    # Normaliza o caminho
    clean_path = route_path_with_data_segment.rstrip('/').rstrip('/data') + '/data'

    # Constrói parâmetros
    params = {
        "length": min(length, 5000),  # Limita ao máximo da API
        "offset": max(offset, 0)
    }

    if data_elements and isinstance(data_elements, list):
        params["data"] = data_elements

    if frequency:
        params["frequency"] = frequency

    if start_period:
        params["start"] = start_period

    if end_period:
        params["end"] = end_period

    # Adiciona facets usando função helper
    if facets and isinstance(facets, dict):
        facet_params = build_facets_params(facets)
        params.update(facet_params)

    # Adiciona ordenação
    if sort_column and sort_direction:
        sort_params = build_sort_params(sort_column, sort_direction)
        params.update(sort_params)

    # Faz a requisição
    if debug_mode:
        logger.info(f"Parâmetros construídos: {json.dumps(params, indent=2)}")

    data_response = await make_eia_api_request(clean_path, params)

    if not data_response:
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=f"❌ Falha na requisição para a rota {clean_path}")]
        )

    # Verifica erros na resposta
    if data_response.get("error"):
        error_details = [
            f"❌ Erro ao recuperar dados para a rota {clean_path}",
            f"🔍 Erro: {data_response.get('error')}",
            f"💬 Mensagem: {data_response.get('message', 'N/A')}"
        ]

        if data_response.get('details'):
            error_details.append(f"📋 Detalhes: {json.dumps(data_response['details'], indent=2)}")

        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text="\n".join(error_details))]
        )

    # Extrai dados da resposta
    response_content = data_response.get('response', data_response)
    actual_data = response_content.get('data')

    # Verifica se há dados
    if not actual_data:
        warning_message = response_content.get('warnings') or response_content.get('warning')
        no_data_info = [
            f"⚠️ Nenhum dado encontrado para a rota {clean_path}",
            f"📊 Total de registros: {response_content.get('total', 0)}",
        ]

        if warning_message:
            no_data_info.append(f"⚠️ Aviso da API: {warning_message}")

        if debug_mode:
            no_data_info.append(f"🔍 Resposta completa: {json.dumps(data_response, indent=2)[:1000]}...")

        return CallToolResult(
            is_error=False,
            content=[TextContent(type="text", text="\n".join(no_data_info))]
        )

    if isinstance(actual_data, list) and len(actual_data) == 0:
        return CallToolResult(
            is_error=False,
            content=[TextContent(type="text", text=f"📊 Consulta executada com sucesso, mas retornou 0 registros para a rota {clean_path}")]
        )

    # Formata os dados para exibição
    formatted_output = []

    # Cabeçalho com informações da consulta
    total_records = response_content.get('total', len(actual_data))
    returned_records = len(actual_data) if isinstance(actual_data, list) else 1

    header_info = [
        f"✅ Dados recuperados com sucesso da rota: {clean_path}",
        f"📊 Total de registros disponíveis: {total_records:,}",
        f"📥 Registros retornados nesta página: {returned_records:,}",
        f"📄 Offset: {offset:,} | Limite: {length:,}"
    ]

    if returned_records < total_records:
        remaining = total_records - (offset + returned_records)
        header_info.append(f"➡️ Registros restantes: {remaining:,}")

    formatted_output.extend(header_info)
    formatted_output.append("")  # Linha em branco

    # Tabela de dados
    if isinstance(actual_data, list) and actual_data:
        columns = list(actual_data[0].keys())

        # Cabeçalho da tabela
        header_line = "| " + " | ".join(columns) + " |"
        separator_line = "|" + "---|" * len(columns)

        formatted_output.extend([header_line, separator_line])

        # Linhas de dados (limita exibição para evitar output muito longo)
        display_limit = min(100, len(actual_data))
        for i, row in enumerate(actual_data[:display_limit]):
            row_values = [str(row.get(col, 'N/A')) for col in columns]
            formatted_output.append("| " + " | ".join(row_values) + " |")

        if len(actual_data) > display_limit:
            formatted_output.append(f"... (mostrando primeiras {display_limit} linhas de {len(actual_data)})")

    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(formatted_output))]
    )

@mcp.tool()
async def list_eia_v2_routes_enhanced(
    segment_path: Optional[str] = None,
    show_examples: bool = True
) -> CallToolResult:
    """
    Versão melhorada para listar rotas, metadados e valores de facets da API v2 da EIA.
    Inclui exemplos práticos de uso.
    """
    path_to_list = segment_path.strip('/') if segment_path and segment_path.strip() else ""

    logger.info(f"Listando rotas para: '{path_to_list}'")

    raw_response = await make_eia_api_request(path_to_list, {})

    if not raw_response:
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=f"❌ Falha na requisição para o caminho: '{path_to_list}'")]
        )

    if raw_response.get("error"):
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=f"❌ Erro da API: {raw_response.get('message', raw_response.get('error'))}")]
        )

    # Processa a resposta
    response_obj = raw_response.get('response', raw_response)

    if not isinstance(response_obj, dict):
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=f"❌ Resposta em formato inválido para '{path_to_list}'")]
        )

    formatted_info = []

    # Caso 1: Valores de facet
    if 'totalFacets' in response_obj and isinstance(response_obj.get('facets'), list):
        formatted_info.extend([
            f"🏷️ Valores disponíveis para o facet: {path_to_list}",
            f"📊 Total de valores: {response_obj.get('totalFacets')}",
            ""
        ])

        facet_values = response_obj.get('facets', [])
        for facet_value in facet_values[:20]:  # Limita exibição
            name = facet_value.get('name', 'N/A')
            alias = facet_value.get('alias', '')
            value_id = facet_value.get('id', 'N/A')

            line = f"  🔹 {value_id}: {name}"
            if alias and alias != name:
                line += f" ({alias})"
            formatted_info.append(line)

        if len(facet_values) > 20:
            formatted_info.append(f"  ... e mais {len(facet_values) - 20} valores")

    # Caso 2: Lista de sub-rotas
    elif isinstance(response_obj.get('routes'), list) and response_obj.get('routes'):
        routes_list = response_obj.get('routes')
        parent_info = response_obj.get('name', response_obj.get('id', 'Nível Superior'))

        formatted_info.extend([
            f"📁 Rotas disponíveis em: {parent_info}",
            f"📊 Total de sub-rotas: {len(routes_list)}",
            ""
        ])

        if response_obj.get('description'):
            formatted_info.extend([
                f"📝 Descrição: {response_obj.get('description')}",
                ""
            ])

        for route in routes_list:
            route_id = route.get('id', 'N/A')
            route_name = route.get('name', 'N/A')
            route_desc = route.get('description', '')

            formatted_info.append(f"  📂 {route_id}: {route_name}")
            if route_desc:
                formatted_info.append(f"     💡 {route_desc}")

        if show_examples and routes_list:
            formatted_info.extend([
                "",
                "💡 Exemplos de uso:",
                f"   Para explorar '{routes_list[0].get('id', 'primeira-rota')}':",
                f"   list_eia_v2_routes_enhanced(segment_path='{path_to_list + '/' + routes_list[0].get('id', '') if path_to_list else routes_list[0].get('id', '')}')"
            ])

    # Caso 3: Metadados de rota específica
    elif response_obj.get('id') or response_obj.get('name'):
        route_id = response_obj.get('id', path_to_list)
        route_name = response_obj.get('name', 'N/A')

        formatted_info.extend([
            f"🔍 Metadados da rota: {route_id}",
            f"📝 Nome: {route_name}",
            ""
        ])

        if response_obj.get('description'):
            formatted_info.extend([
                f"📄 Descrição: {response_obj.get('description')}",
                ""
            ])

        # Facets disponíveis
        facets_metadata = response_obj.get('facets', [])
        if facets_metadata and isinstance(facets_metadata, list):
            formatted_info.extend([
                "🏷️ Facets disponíveis (filtros):",
                ""
            ])

            for facet in facets_metadata:
                facet_id = facet.get('id', 'N/A')
                facet_name = facet.get('name', 'N/A')
                facet_desc = facet.get('description', '')

                formatted_info.append(f"  🔹 {facet_id}: {facet_name}")
                if facet_desc:
                    formatted_info.append(f"     💡 {facet_desc}")

                # Exemplo de como listar valores deste facet
                facet_path = f"{path_to_list}/facet/{facet_id}" if path_to_list else f"facet/{facet_id}"
                formatted_info.append(f"     🔍 Ver valores: list_eia_v2_routes_enhanced(segment_path='{facet_path}')")
                formatted_info.append("")

        # Colunas de dados
        data_columns = response_obj.get('data', {})
        if data_columns and isinstance(data_columns, dict):
            formatted_info.extend([
                "📊 Colunas de dados disponíveis:",
                ""
            ])

            for col_id, col_info in data_columns.items():
                if isinstance(col_info, dict):
                    col_name = col_info.get('name', col_info.get('alias', col_id))
                    col_units = col_info.get('units', 'N/A')
                    formatted_info.append(f"  📈 {col_id}: {col_name} ({col_units})")
                else:
                    formatted_info.append(f"  📈 {col_id}: {col_info}")

        # Frequências disponíveis
        frequencies = response_obj.get('frequency', [])
        if frequencies and isinstance(frequencies, list):
            formatted_info.extend([
                "",
                "📅 Frequências disponíveis:",
                ""
            ])

            for freq in frequencies:
                freq_id = freq.get('id', 'N/A')
                freq_query = freq.get('query', freq_id)
                freq_desc = freq.get('description', 'N/A')
                freq_format = freq.get('format', 'N/A')

                formatted_info.append(f"  🕐 {freq_query}: {freq_desc} (Formato: {freq_format})")

        # Período de dados
        if response_obj.get('startPeriod') or response_obj.get('endPeriod'):
            formatted_info.extend([
                "",
                "📆 Período de dados disponível:",
                f"  📅 Início: {response_obj.get('startPeriod', 'N/A')}",
                f"  📅 Fim: {response_obj.get('endPeriod', 'N/A')}"
            ])

        # Exemplos práticos
        if show_examples:
            data_path = f"{path_to_list}/data" if path_to_list else "data"
            formatted_info.extend([
                "",
                "💡 Exemplo de uso para obter dados:",
                f"   get_eia_v2_route_data_enhanced(",
                f"       route_path_with_data_segment='{data_path}',",
                f"       data_elements=['value'],  # ou outras colunas disponíveis",
                f"       frequency='monthly',  # ou outra frequência disponível",
                f"       start_period='2023-01',",
                f"       end_period='2024-01'",
                f"   )"
            ])

    else:
        # Formato não reconhecido
        formatted_info.extend([
            f"❓ Resposta em formato não reconhecido para: {path_to_list}",
            "",
            "🔍 Estrutura da resposta:"
        ])

        if isinstance(response_obj, dict):
            for key in response_obj.keys():
                formatted_info.append(f"  - {key}: {type(response_obj[key])}")

        # Inclui parte da resposta para debug
        formatted_info.extend([
            "",
            "📋 Amostra da resposta:",
            json.dumps(response_obj, indent=2)[:500] + "..." if len(str(response_obj)) > 500 else json.dumps(response_obj, indent=2)
        ])

    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(formatted_info))]
    )

@mcp.tool()
async def find_wti_oil_data() -> CallToolResult:
    """
    Ferramenta especializada para encontrar dados de preço spot do petróleo WTI.
    Resolve o problema específico mencionado no exemplo.
    """

    steps_info = [
        "🛢️ Procurando dados de preço spot do petróleo WTI...",
        ""
    ]

    # Passo 1: Verificar rota petroleum
    logger.info("Passo 1: Verificando rota petroleum")
    petroleum_response = await make_eia_api_request("petroleum")

    if not petroleum_response or petroleum_response.get("error"):
        steps_info.append(f"❌ Erro ao acessar rota 'petroleum': {petroleum_response.get('message', 'N/A')}")
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text="\n".join(steps_info))]
        )

    steps_info.append("✅ Passo 1: Rota 'petroleum' acessada com sucesso")

    # Passo 2: Verificar petroleum/pri (prices)
    logger.info("Passo 2: Verificando rota petroleum/pri")
    pri_response = await make_eia_api_request("petroleum/pri")

    if not pri_response or pri_response.get("error"):
        steps_info.append(f"❌ Erro ao acessar rota 'petroleum/pri': {pri_response.get('message', 'N/A')}")
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text="\n".join(steps_info))]
        )

    steps_info.append("✅ Passo 2: Rota 'petroleum/pri' (preços) acessada com sucesso")

    # Passo 3: Verificar petroleum/pri/spt (spot prices)
    logger.info("Passo 3: Verificando rota petroleum/pri/spt")
    spt_response = await make_eia_api_request("petroleum/pri/spt")

    if not spt_response or spt_response.get("error"):
        steps_info.append(f"❌ Erro ao acessar rota 'petroleum/pri/spt': {spt_response.get('message', 'N/A')}")
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text="\n".join(steps_info))]
        )

    steps_info.append("✅ Passo 3: Rota 'petroleum/pri/spt' (preços spot) acessada com sucesso")

    # Passo 4: Analisar metadados da rota spot para encontrar o facet 'product'
    spt_data = spt_response.get('response', spt_response)

    # Verificar facets disponíveis
    facets_metadata = spt_data.get('facets', [])
    product_facet_found = False
    for facet in facets_metadata:
        if facet.get('id') == 'product':
            product_facet_found = True
            break

    if not product_facet_found:
        steps_info.append("❌ Facet 'product' não encontrado na rota spot 'petroleum/pri/spt'.")
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text="\n".join(steps_info))]
        )

    steps_info.append("✅ Passo 4: Facet 'product' encontrado nos metadados da rota spot.")

    # Passo 5: Verificar valores do facet 'product' para encontrar WTI
    logger.info("Passo 5: Verificando valores do facet product para WTI")
    product_values_response = await make_eia_api_request("petroleum/pri/spt/facet/product")

    if not product_values_response or product_values_response.get("error"):
        steps_info.append(f"❌ Erro ao acessar valores do facet 'product': {product_values_response.get('message', 'N/A')}")
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text="\n".join(steps_info))]
        )

    product_values_data = product_values_response.get('response', product_values_response)
    product_facet_list = product_values_data.get('facets', [])
    wti_product_id = None

    for product_value in product_facet_list:
        p_id = product_value.get('id', '').upper()
        p_name = product_value.get('name', '').upper()
        p_alias = product_value.get('alias', '').upper()

        if "WTI" in p_id or "WTI" in p_name or "WTI" in p_alias or \
           "WEST TEXAS INTERMEDIATE" in p_name or "WEST TEXAS INTERMEDIATE" in p_alias:
            wti_product_id = product_value.get('id')
            break

    if not wti_product_id:
        steps_info.append("❌ Valor de facet para 'WTI' não encontrado na lista de produtos.")
        steps_info.append("   Valores de produto disponíveis (primeiros 10):")
        for val in product_facet_list[:10]:
            steps_info.append(f"     - ID: {val.get('id')}, Nome: {val.get('name')}")
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text="\n".join(steps_info))]
        )

    steps_info.append(f"✅ Passo 5: ID do produto WTI encontrado: '{wti_product_id}'")

    # Passo 6: Recuperar dados de preço spot do WTI
    logger.info("Passo 6: Recuperando dados de preço spot do WTI")
    wti_data_path = "petroleum/pri/spt/data"
    
    # Identify common data elements. Looking at the documentation, 'value' is typical for prices.
    # The 'spt_data' (metadata for petroleum/pri/spt) would have 'data' key with available columns.
    data_columns_meta = spt_data.get('data', {})
    price_element = None
    # Prioritize columns that seem to represent price, e.g., 'value', 'price'
    for col_id, col_info in data_columns_meta.items():
        if isinstance(col_info, dict) and 'price' in col_id.lower() or 'value' in col_id.lower():
            price_element = col_id
            break
    if not price_element:
        # Fallback if no specific price element found, though 'value' is a common default for many EIA series.
        price_element = 'value' 
        steps_info.append(f"⚠️ Não foi possível identificar explicitamente a coluna de preço. Assumindo a coluna '{price_element}'.")

    data_elements_to_request = [price_element] if price_element else []
    
    if not data_elements_to_request:
        steps_info.append("❌ Nenhuma coluna de dados para preço identificada para WTI.")
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text="\n".join(steps_info))]
        )

    # Calling the enhanced data retrieval tool for better formatting and error handling
    result_call = await get_eia_v2_route_data_enhanced(
        route_path_with_data_segment=wti_data_path,
        data_elements=data_elements_to_request,
        facets={"product": wti_product_id},
        frequency="daily", # Request daily data for WTI spot prices
        sort_column="period", # Sort by period
        sort_direction="desc", # Most recent first
        length=50 # Get the 50 most recent daily prices
    )
    
    if result_call.is_error:
        steps_info.append(f"❌ Erro ao recuperar dados de preço WTI: {result_call.content[0].text}")
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text="\n".join(steps_info))]
        )

    steps_info.append("✅ Passo 6: Dados de preço spot do WTI recuperados com sucesso.")
    steps_info.append("")
    steps_info.append("--- Resultados do Preço Spot WTI (Top 50 Diários) ---")
    
    # Append the content from the get_eia_v2_route_data_enhanced tool
    steps_info.extend([c.text for c in result_call.content])

    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(steps_info))]
    )


@mcp.tool()
async def get_eia_v2_series_id_data(
    series_id: str,
    data_elements: Optional[List[str]] = None,
    frequency: Optional[str] = None,
    start_period: Optional[str] = None,
    end_period: Optional[str] = None,
    sort_column: Optional[str] = None,
    sort_direction: Optional[str] = None
) -> CallToolResult:
    """
    Recupera dados usando Series ID da API v1 (compatibilidade reversa).
    """
    route_path = f"seriesid/{series_id}"
    params = {}

    if data_elements:
        params["data"] = data_elements
    if frequency:
        params["frequency"] = frequency
    if start_period:
        params["start"] = start_period
    if end_period:
        params["end"] = end_period
    if sort_column and sort_direction:
        # Corrigido o formato do sort para a API EIA v2
        params[f"sort[0][column]"] = sort_column
        params[f"sort[0][direction]"] = sort_direction

    data_response = await make_eia_api_request(route_path, params)

    if not data_response or data_response.get("error"):
        error_message = f"Falha ao recuperar dados para o Series ID {series_id}."
        if data_response and data_response.get("message"):
            error_message += f" Detalhe: {data_response['message']}"
        elif data_response and isinstance(data_response, dict) and data_response.get('response', {}).get('error'):
            error_message += f" Erro da API EIA: {data_response['response']['error']}"
        return CallToolResult(is_error=True, content=[TextContent(type="text", text=error_message)])

    response_content = data_response.get('response', {})
    actual_data = response_content.get('data')

    if not actual_data and isinstance(actual_data, list):
        return CallToolResult(
            is_error=False,
            content=[TextContent(type="text", text=f"Nenhum dado encontrado para o Series ID {series_id}. A API retornou uma lista vazia.")]
        )

    if not actual_data:
        warning_message = response_content.get('warnings', response_content.get('warning'))
        error_api_msg = response_content.get('error', 'Resposta inesperada da API.')
        error_message = f"Não foi possível recuperar dados para o Series ID {series_id}. "
        if warning_message:
            error_message += f"Aviso da API: {warning_message}. "
        error_message += f"Detalhe: {error_api_msg}. Resposta completa: {data_response}"
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=error_message)]
        )

    formatted_data_output = [f"Total de registros correspondentes (pode ser paginado): {response_content.get('total', len(actual_data))}"]

    if actual_data:
        columns = list(actual_data[0].keys()) if actual_data else []
        header_line = "| " + " | ".join(columns) + " |"
        separator_line = "|" + "---|".join(["---"] * len(columns)) + "|"
        formatted_data_output.extend([header_line, separator_line])
        for row in actual_data:
            row_values = [str(row.get(col, 'N/A')) for col in columns]
            formatted_data_output.append("| " + " | ".join(row_values) + " |")

    if not formatted_data_output or len(formatted_data_output) <= 1:
        return CallToolResult(
            is_error=False,
            content=[TextContent(type="text", text=f"Nenhum dado tabular encontrado para Series ID {series_id}. Resposta da API: {data_response}")]
        )

    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(formatted_data_output))]
    )

@mcp.tool()
async def list_eia_v2_routes(
    segment_path: Optional[str] = None
) -> CallToolResult:
    """
    Lista as rotas (segmentos de URL) disponíveis na API v2 da EIA,
    metadados de uma rota específica, OU os valores de um facet dentro de uma rota.
    Use para explorar a árvore de dados da EIA.
    """
    path_to_list = segment_path.strip('/') if segment_path and segment_path.strip() else ""

    raw_response = await make_eia_api_request(path_to_list, {})

    if not raw_response:
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=f"Falha ao fazer requisição à API da EIA para o caminho: '{path_to_list}'. Verifique os logs do servidor MCP.")]
        )

    # A API da EIA às vezes retorna o conteúdo diretamente, às vezes dentro de 'response'
    if 'request' in raw_response and 'response' in raw_response:
        response_obj = raw_response.get('response')
    else:
        response_obj = raw_response

    if response_obj is None or not isinstance(response_obj, dict):
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=f"Resposta da API para '{path_to_list}' não é um objeto JSON válido ou está vazia. Resposta: {raw_response}")]
        )

    logger.info(f"Processando response_obj para '{path_to_list}': {type(response_obj)}")
    formatted_info = []

    # Caso 1: A resposta é para VALORES DE UM FACET específico
    is_facet_values_response = 'totalFacets' in response_obj and isinstance(response_obj.get('facets'), list)

    if is_facet_values_response:
        formatted_info.append(f"Valores disponíveis para o facet em '{path_to_list}':")
        formatted_info.append(f"  Total de Valores: {response_obj.get('totalFacets')}")
        facet_values_list = response_obj.get('facets', [])
        if not facet_values_list:
            formatted_info.append("  Nenhum valor de facet retornado.")
        for facet_value in facet_values_list:
            name_str = facet_value.get('name', 'N/A')
            alias_str = f", Alias: {facet_value.get('alias')}" if facet_value.get('alias') else ""
            formatted_info.append(f"  - ID (valor do facet): {facet_value.get('id', 'N/A')}, Nome: {name_str}{alias_str}")

    # Caso 2: A resposta é uma LISTA DE SUB-ROTAS
    elif isinstance(response_obj.get('routes'), list) and response_obj.get('routes'):
        routes_list = response_obj.get('routes')
        parent_id = response_obj.get('id', path_to_list if path_to_list else "Nível Raiz")
        parent_name = response_obj.get('name', '')

        header_text = f"Rotas de Nível Superior Disponíveis (sob '{parent_id}')" if not segment_path or not segment_path.strip() else f"Sub-Rotas para '{parent_id}' ({parent_name})"
        formatted_info.append(header_text)

        if response_obj.get('description'):
            formatted_info.append(f"  Descrição do Pai: {response_obj.get('description')}")

        for route_item in routes_list:
            name_str = route_item.get('name', 'N/A')
            desc_str = f"    Descrição: {route_item.get('description', 'N/A')}" if route_item.get('description') else ""
            formatted_info.append(f"  - ID da Sub-rota: {route_item.get('id', 'N/A')}, Nome: {name_str}")
            if desc_str: formatted_info.append(desc_str)

    # Caso 3: A resposta são METADADOS DE UMA ROTA específica
    elif response_obj.get('id') or response_obj.get('name'):
        route_id = response_obj.get('id', path_to_list)
        formatted_info.append(f"Metadados da Rota '{route_id}':")
        if response_obj.get('name'): formatted_info.append(f"  Nome: {response_obj.get('name')}")
        if response_obj.get('description'): formatted_info.append(f"  Descrição: {response_obj.get('description')}")

        # Facets disponíveis para esta rota
        facets_metadata = response_obj.get('facets', [])
        if facets_metadata and isinstance(facets_metadata, list):
            formatted_info.append("\n  Facets Disponíveis (filtros de dimensão):")
            for facet_meta in facets_metadata:
                facet_id_val = facet_meta.get('id', 'N/A')
                current_base_path = path_to_list.rstrip('/')
                explore_facet_path = f"{current_base_path}/facet/{facet_id_val}" if current_base_path else f"facet/{facet_id_val}"
                name_str = facet_meta.get('name', 'N/A')
                desc_str = facet_meta.get('description', 'N/A')
                formatted_info.append(f"    - ID do Facet: {facet_id_val}, Nome: {name_str}, Descrição: {desc_str}")
                formatted_info.append(f"      (Para listar valores, use: list_eia_v2_routes com segment_path='{explore_facet_path}')")

        # Colunas de dados disponíveis
        data_columns_meta = response_obj.get('data', {})
        if isinstance(data_columns_meta, dict) and data_columns_meta:
            formatted_info.append("\n  Colunas de Dados Disponíveis (para parâmetro 'data_elements' em get_eia_v2_route_data):")
            for col_id, col_details in data_columns_meta.items():
                if isinstance(col_details, dict):
                    name_val = col_details.get('name', col_details.get('alias', 'N/A'))
                    units_val = col_details.get('units', 'N/A')
                    formatted_info.append(f"    - ID da Coluna: {col_id}, Nome/Alias: {name_val}, Unidades: {units_val}")
                else:
                    formatted_info.append(f"    - ID da Coluna: {col_id} (detalhes em formato inesperado: {col_details})")
        elif isinstance(data_columns_meta, list):
            formatted_info.append("\n  Colunas de Dados Disponíveis (para parâmetro 'data_elements' em get_eia_v2_route_data):")
            for item in data_columns_meta:
                if isinstance(item, dict) and 'id' in item:
                    formatted_info.append(f"    - ID da Coluna: {item.get('id')}, Nome: {item.get('name', 'N/A')}, Unidades: {item.get('units', 'N/A')}")
                else:
                    formatted_info.append(f"    - Coluna: {item}")

        # Frequências disponíveis
        frequencies = response_obj.get('frequency', [])
        if frequencies and isinstance(frequencies, list):
            formatted_info.append("\n  Frequências Disponíveis (para parâmetro 'frequency'):")
            for freq in frequencies:
                id_val = freq.get('id', 'N/A')
                query_val = freq.get('query', id_val)
                desc_str = freq.get('description', 'N/A')
                format_str = freq.get('format', 'N/A')
                formatted_info.append(f"    - ID (para query): {query_val}, Nome: {id_val}, Descrição: {desc_str}, Formato do Período: {format_str}")
        
        # Informações adicionais de metadados
        if response_obj.get('startPeriod') or response_obj.get('endPeriod'):
            formatted_info.append("\n  Período de Dados Disponível (aproximado):")
            if response_obj.get('startPeriod'): formatted_info.append(f"    Início: {response_obj.get('startPeriod')}")
            if response_obj.get('endPeriod'): formatted_info.append(f"    Fim: {response_obj.get('endPeriod')}")
        if response_obj.get('defaultDateFormat'): formatted_info.append(f"  Formato de Data Padrão: {response_obj.get('defaultDateFormat')}")
        if response_obj.get('defaultFrequency'): formatted_info.append(f"  Frequência Padrão: {response_obj.get('defaultFrequency')}")

    # Caso 4: Formato não reconhecido
    else:
        error_detail = f"Resposta da API da EIA para '{path_to_list}' não corresponde a um formato esperado de metadados, sub-rotas ou valores de facet. "
        api_error_data = response_obj.get('error')
        if not api_error_data and isinstance(raw_response, dict):
            api_error_data = raw_response.get('error')
            if not api_error_data and 'response' in raw_response and isinstance(raw_response['response'], dict):
                api_error_data = raw_response['response'].get('error')

        if api_error_data:
            error_detail += f"Erro explícito da API EIA: {api_error_data}. "
        
        error_detail += f"Resposta completa recebida: {raw_response}"
        
        if isinstance(raw_response, dict) and 'request' in raw_response:
            error_detail += f" Comando ecoado pela API: {raw_response.get('request')}"

        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=error_detail)]
        )

    if not formatted_info:
        formatted_info.append(f"Nenhuma informação formatável encontrada para '{path_to_list}', mas a API respondeu. Resposta completa: {raw_response}")

    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(formatted_info))]
    )

# --- Recursos (Resources) da EIA ---
@mcp.resource(uri="eia://metadata/api-guide", name="Guia Rápido da API EIA v2", description="Um guia sobre como usar as ferramentas para acessar dados da API v2 da EIA.")
async def get_eia_api_guide_resource() -> Resource:
    content_text = """
    Bem-vindo ao Guia Rápido da API EIA v2. A API v2 organiza os dados em uma hierarquia de "rotas" (URL paths).

    **Ferramentas Disponíveis:**

    1.  **`health_check()`**:
        *   **Uso:** Verifica se o servidor está funcionando corretamente.

    2.  **`test_eia_connection()`**:
        *   **Uso:** Testa se a conexão com a API da EIA está funcionando e se a API Key está configurada.

    3.  **`list_eia_v2_routes_enhanced(segment_path: Optional[str], show_examples: bool = True)`**:
        *   **Uso:** Para explorar a árvore de dados e metadados.
            *   Se `segment_path` for omitido ou vazio, lista as rotas de nível superior (ex: "electricity", "petroleum").
            *   Se `segment_path` for um caminho de rota (ex: "electricity/retail-sales"), lista as sub-rotas E os metadados dessa rota (tipos de facets, colunas de dados, frequências disponíveis).
            *   Se `segment_path` for um caminho para um facet específico (ex: "electricity/retail-sales/facet/sectorid"), lista os VALORES disponíveis para esse facet (ex: "RES" para residencial, "COM" para comercial).

    4.  **`get_eia_v2_route_data_enhanced(...)`**:
        *   **Uso:** Para recuperar os dados reais de uma rota específica. É a ferramenta principal para obter dados tabulares.
        *   **Argumentos Chave:**
            *   `route_path_with_data_segment`: **Obrigatório.** O caminho completo da rota que **DEVE terminar em '/data/'** (ex: "electricity/retail-sales/data/").
            *   `data_elements`: **Opcional, mas frequentemente necessário.** Uma lista de IDs de colunas que você deseja (ex: `["price", "revenue"]`).
            *   `facets`: **Opcional.** Dicionário de facets para filtrar (ex: `{"stateid": "CO", "sectorid": "RES"}`).
            *   `frequency`: **Opcional.** Define a periodicidade dos dados (ex: "monthly", "daily", "annual").
            *   `start_period`, `end_period`: **Opcional.** Intervalo de datas (ex: "2023-01", "2024-01-31").
            *   `sort_column`, `sort_direction`: **Opcional.** Para ordenar os resultados (ex: `sort_column="period", sort_direction="desc"`).
            *   `length`, `offset`: **Opcional.** Para paginação.

    5.  **`get_eia_v2_series_id_data(series_id: str, ...)`**:
        *   **Uso:** Para compatibilidade com Series IDs da APIv1 (ex: "ELEC.SALES.CO-RES.A"). Permite usar os mesmos filtros de data e ordenação.

    6.  **`find_wti_oil_data()`**:
        *   **Uso:** Uma ferramenta especializada para encontrar e retornar os dados de preço spot do petróleo WTI. Demonstra o uso de outras ferramentas de forma encadeada.

    **Fluxo Recomendado:**
    1.  **Explorar Rotas Principais:** Chame `list_eia_v2_routes_enhanced()` para ver categorias como "petroleum".
    2.  **Aprofundar na Categoria:** Se "petroleum" for uma rota, chame `list_eia_v2_routes_enhanced(segment_path="petroleum")`.
    3.  **Identificar Rota de Dados Relevante:** Continue explorando até encontrar a rota desejada (ex: "petroleum/pri/spt" para preços spot).
    4.  **Analisar Metadados da Rota:** Use `list_eia_v2_routes_enhanced(segment_path="sua/rota/aqui")` para ver os facets, colunas de dados e frequências disponíveis.
    5.  **Obter Valores de Facets:** Se necessário, use `list_eia_v2_routes_enhanced(segment_path="sua/rota/aqui/facet/seu_facet_id")` para ver os valores possíveis para um filtro específico.
    6.  **Construir e Chamar `get_eia_v2_route_data_enhanced`:** Com os parâmetros corretos derivados dos passos anteriores.
    """
    return Resource(
        uri="eia://metadata/api-guide",
        name="Guia Rápido da API EIA v2",
        mime_type="text/plain",
        text=content_text
    )

# --- Prompts da EIA ---
@mcp.prompt()
async def get_eia_data_by_route_prompt() -> GetPromptResult:
    """
    Prompt para guiar o usuário a fazer uma pergunta sobre dados da EIA v2.
    """
    description = "Ajuda a encontrar dados da API v2 da EIA, especificando o caminho da rota, elementos de dados desejados e filtros (facets)."
    messages = [
        {
            "role": "user",
            "content": TextContent(type="text", text="Gostaria de obter dados da EIA. Por favor, siga o fluxo recomendado no 'Guia Rápido da API EIA v2' para descobrir a rota, os elementos de dados, os facets e os valores de facet necessários. Depois, use a ferramenta `get_eia_v2_route_data_enhanced`.")
        }
    ]
    return GetPromptResult(description=description, messages=messages)

@mcp.prompt()
async def explore_eia_v2_routes_prompt() -> GetPromptResult:
    """
    Prompt para guiar o usuário a explorar as rotas disponíveis na API v2 da EIA.
    """
    description = "Ajuda a explorar a hierarquia de dados da API v2 da EIA para descobrir rotas e seus metadados."
    messages = [
        {
            "role": "user",
            "content": TextContent(type="text", text="Gostaria de explorar as rotas da API v2 da EIA. Qual segmento de caminho você gostaria de investigar (ex: 'electricity', 'petroleum/supply/historical/facet/regionId', ou deixe em branco para o nível superior)? Consulte o 'Guia Rápido da API EIA v2' para exemplos.")
        }
    ]
    return GetPromptResult(description=description, messages=messages)

# --- Função Principal para Rodar o Servidor ---
if __name__ == "__main__":
    logger.info("Iniciando o servidor MCP da EIA (SSE)...")

    try:
        mcp.run(transport="sse")
    except Exception as e:
        logger.error(f"Erro ao iniciar servidor: {e}")
        sys.exit(1)