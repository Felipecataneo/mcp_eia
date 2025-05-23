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

# Configurar logging
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
    "User-Agent": "US-Energy-Info-Admin-MCP-Server/2.0 (contact@example.com)"
}

PORT = int(os.getenv("PORT", 8000))

# --- Mapeamento de conceitos para facilitar busca ---
CONCEPT_MAPPING = {
    "electricity": {
        "keywords": ["eletricidade", "energia elétrica", "consumo energia", "geração energia", "preço energia"],
        "routes": ["electricity", "electricity/retail-sales", "electricity/electric-power-operational-data"]
    },
    "petroleum": {
        "keywords": ["petróleo", "gasolina", "diesel", "crude oil", "combustível", "refino"],
        "routes": ["petroleum", "petroleum/crd/crpdn", "petroleum/supply/weekly", "petroleum/supply/historical"]
    },
    "natural-gas": {
        "keywords": ["gás natural", "gas natural", "lng", "pipeline"],
        "routes": ["natural-gas", "natural-gas/prod", "natural-gas/cons"]
    },
    "coal": {
        "keywords": ["carvão", "coal", "mineração carvão"],
        "routes": ["coal", "coal/production", "coal/consumption"]
    },
    "renewable": {
        "keywords": ["renovável", "solar", "eólica", "hidráulica", "biomassa", "renewable"],
        "routes": ["electricity/electric-power-operational-data"]
    },
    "total-energy": {
        "keywords": ["energia total", "consumo total", "balanço energético"],
        "routes": ["total-energy"]
    }
}

# --- Inicialização do Servidor MCP ---
mcp = FastMCP(
    name="eia-energy-data",
    host="0.0.0.0",
    port=PORT,
)

# --- Funções Auxiliares ---
def format_eia_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Formata parâmetros para o formato correto da API EIA.
    Converte listas em parâmetros indexados como data[0]=value, data[1]=price
    """
    formatted_params = {}
    
    for key, value in params.items():
        if isinstance(value, list):
            # Converter listas para formato indexado
            for i, item in enumerate(value):
                formatted_params[f"{key}[{i}]"] = item
        elif isinstance(value, dict):
            # Para objetos aninhados como sort[0][column]=period
            for sub_key, sub_value in value.items():
                if isinstance(sub_value, list):
                    for i, item in enumerate(sub_value):
                        formatted_params[f"{key}[{i}][{sub_key}]"] = item
                else:
                    formatted_params[f"{key}[{sub_key}]"] = sub_value
        else:
            formatted_params[key] = value
    
    return formatted_params

async def make_eia_api_request(route_path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Faz requisição à API da EIA com tratamento robusto de erros e formatação correta."""
    if not EIA_API_KEY:
        logger.error("EIA_API_KEY não está definida")
        return {"error": "API_KEY_MISSING", "message": "Chave da API EIA não configurada"}
    
    full_url = f"{EIA_API_BASE_URL}/{route_path.lstrip('/')}"
    
    if params is None:
        params = {}
    
    # Formatar parâmetros corretamente
    formatted_params = format_eia_params(params)
    formatted_params['api_key'] = EIA_API_KEY
    
    # Log detalhado para debug
    temp_params = {k: v for k, v in formatted_params.items() if k != 'api_key'}
    logger.info(f"URL: {full_url}")
    logger.info(f"Parâmetros formatados: {temp_params}")
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                full_url, 
                params=formatted_params, 
                headers=EIA_HEADERS, 
                timeout=60.0  # Aumentado timeout
            )
            
            # Log da URL final para debug
            logger.info(f"URL final: {response.url}")
            
            response.raise_for_status()
            return response.json()
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Erro HTTP EIA API: {e.response.status_code}")
            logger.error(f"Response text: {e.response.text}")
            try:
                error_response = e.response.json()
                logger.error(f"Error details: {error_response}")
                return error_response
            except Exception:
                return {"error": f"HTTPStatusError: {e.response.status_code}", "message": e.response.text}
        except httpx.RequestError as e:
            logger.error(f"Erro de requisição EIA API: {e}")
            return {"error": "RequestError", "message": str(e)}
        except Exception as e:
            logger.error(f"Erro inesperado EIA API: {e}")
            return {"error": "UnexpectedError", "message": str(e)}

def find_relevant_routes(query: str) -> List[str]:
    """Encontra rotas relevantes baseadas na consulta do usuário."""
    query_lower = query.lower()
    relevant_routes = []
    
    for concept, data in CONCEPT_MAPPING.items():
        for keyword in data["keywords"]:
            if keyword.lower() in query_lower:
                relevant_routes.extend(data["routes"])
                break
    
    # Remove duplicatas mantendo ordem
    return list(dict.fromkeys(relevant_routes))

# --- Ferramentas Principais ---
@mcp.tool()
async def search_energy_data(
    query: str,
    specific_route: Optional[str] = None,
    data_elements: Optional[List[str]] = None,
    filters: Optional[Dict[str, Union[str, List[str]]]] = None,
    frequency: Optional[str] = None,
    start_period: Optional[str] = None,
    end_period: Optional[str] = None,
    limit: int = 100,
    sort_column: Optional[str] = "period",
    sort_direction: Optional[str] = "desc"
) -> CallToolResult:
    """
    Busca dados de energia da EIA de forma inteligente. 
    
    Esta ferramenta pode:
    1. Descobrir automaticamente rotas relevantes baseadas na consulta
    2. Explorar metadados de uma rota específica
    3. Recuperar dados reais quando todos os parâmetros estão disponíveis
    
    Args:
        query: Descrição do que você está procurando (ex: "consumo de eletricidade residencial no Texas")
        specific_route: Rota específica se conhecida (ex: "electricity/retail-sales")
        data_elements: Elementos de dados específicos (ex: ["value", "price"])
        filters: Filtros/facets (ex: {"stateid": ["TX"], "sectorid": ["RES"]})
        frequency: Frequência dos dados (ex: "monthly", "annual")
        start_period: Período inicial (ex: "2020")
        end_period: Período final (ex: "2023")
        limit: Limite de registros retornados
        sort_column: Coluna para ordenação (padrão: "period")
        sort_direction: Direção da ordenação (padrão: "desc")
    """
    
    # Fase 1: Descoberta de rotas se não especificada
    if not specific_route:
        relevant_routes = find_relevant_routes(query)
        if not relevant_routes:
            # Listar rotas principais se não encontrou nada específico
            response = await make_eia_api_request("", {})
            if response and response.get('response', {}).get('routes'):
                routes_info = []
                for route in response['response']['routes']:
                    routes_info.append(f"- {route.get('id', 'N/A')}: {route.get('name', 'N/A')}")
                
                return CallToolResult(
                    content=[TextContent(type="text", text=f"""
Não encontrei rotas específicas para "{query}". Aqui estão as categorias principais disponíveis:

{chr(10).join(routes_info)}

Para continuar, especifique uma categoria usando o parâmetro 'specific_route' ou reformule sua consulta com termos mais específicos como:
- "eletricidade", "petróleo", "gás natural", "carvão", "renovável"
                    """)]
                )
        
        # Se encontrou rotas, explorar a primeira
        specific_route = relevant_routes[0]
        logger.info(f"Usando rota descoberta automaticamente: {specific_route}")
    
    # Fase 2: Exploração de metadados da rota
    metadata_response = await make_eia_api_request(specific_route, {})
    
    if not metadata_response or metadata_response.get("error"):
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=f"Erro ao acessar rota '{specific_route}': {metadata_response.get('message', 'Erro desconhecido')}")]
        )
    
    response_content = metadata_response.get('response', metadata_response)
    
    # Se temos sub-rotas, listar elas
    if response_content.get('routes'):
        subroutes_info = []
        for subroute in response_content['routes']:
            subroutes_info.append(f"- {subroute.get('id', 'N/A')}: {subroute.get('name', 'N/A')}")
            if subroute.get('description'):
                subroutes_info.append(f"  → {subroute['description']}")
        
        return CallToolResult(
            content=[TextContent(type="text", text=f"""
Rota '{specific_route}' contém as seguintes sub-rotas:

{chr(10).join(subroutes_info)}

Para obter dados, especifique uma sub-rota mais específica no parâmetro 'specific_route'.
            """)]
        )
    
    # Fase 3: Se não temos parâmetros suficientes, mostrar metadados para ajudar
    if not data_elements:
        metadata_info = [f"Metadados para '{specific_route}':"]
        
        if response_content.get('name'):
            metadata_info.append(f"Nome: {response_content['name']}")
        if response_content.get('description'):
            metadata_info.append(f"Descrição: {response_content['description']}")
        
        # Mostrar elementos de dados disponíveis
        data_meta = response_content.get('data', {})
        if data_meta:
            metadata_info.append("\nElementos de dados disponíveis:")
            for col_id, col_info in data_meta.items():
                if isinstance(col_info, dict):
                    name = col_info.get('name', col_info.get('alias', col_id))
                    units = col_info.get('units', 'N/A')
                    metadata_info.append(f"  - {col_id}: {name} ({units})")
        
        # Mostrar facets/filtros disponíveis
        facets_meta = response_content.get('facets', [])
        if facets_meta:
            metadata_info.append("\nFiltros disponíveis:")
            for facet in facets_meta:
                facet_id = facet.get('id', 'N/A')
                facet_name = facet.get('name', 'N/A')
                metadata_info.append(f"  - {facet_id}: {facet_name}")
        
        # Mostrar frequências disponíveis
        frequencies = response_content.get('frequency', [])
        if frequencies:
            metadata_info.append("\nFrequências disponíveis:")
            for freq in frequencies:
                freq_id = freq.get('id', freq.get('query', 'N/A'))
                freq_desc = freq.get('description', 'N/A')
                metadata_info.append(f"  - {freq_id}: {freq_desc}")
        
        metadata_info.append(f"\nPara obter dados reais, chame novamente especificando:")
        metadata_info.append(f"- data_elements: lista dos elementos que deseja (ex: ['value'])")
        metadata_info.append(f"- filters: dicionário com os filtros se necessário")
        metadata_info.append(f"- frequency: frequência desejada se aplicável")
        
        return CallToolResult(
            content=[TextContent(type="text", text="\n".join(metadata_info))]
        )
    
    # Fase 4: Recuperar dados reais
    data_route = f"{specific_route.rstrip('/')}/data/"
    params = {
        "length": limit,
        "offset": 0
    }
    
    # Formatação correta dos parâmetros
    if data_elements:
        params["data"] = data_elements  # Será convertido para data[0]=value, data[1]=price, etc.
    
    if frequency:
        params["frequency"] = frequency
    if start_period:
        params["start"] = start_period
    if end_period:
        params["end"] = end_period
    
    # Adicionar ordenação padrão
    if sort_column:
        params["sort"] = [{"column": sort_column, "direction": sort_direction}]
    
    # Formatação correta dos filtros/facets
    if filters:
        for facet_key, facet_values in filters.items():
            if isinstance(facet_values, list):
                params[f"facets[{facet_key}][]"] = facet_values
            else:
                params[f"facets[{facet_key}][]"] = [facet_values]
    
    logger.info(f"Fazendo requisição de dados para: {data_route}")
    logger.info(f"Parâmetros antes da formatação: {params}")
    
    data_response = await make_eia_api_request(data_route, params)
    
    if not data_response:
        return CallToolResult(
            is_error=True, 
            content=[TextContent(type="text", text=f"Falha na requisição para '{data_route}' - sem resposta")]
        )
    
    if data_response.get("error"):
        error_msg = f"Erro ao recuperar dados de '{data_route}'"
        if data_response.get("message"):
            error_msg += f": {data_response['message']}"
        if data_response.get("data"):
            error_msg += f"\nDetalhes: {data_response.get('data')}"
        return CallToolResult(is_error=True, content=[TextContent(type="text", text=error_msg)])
    
    response_data = data_response.get('response', {})
    actual_data = response_data.get('data', [])
    # Adicionado: Captura a mensagem de warning da API
    warning_message = response_data.get('warning') 

    # Se não há dados retornados (e não é um erro fatal da API), informa ao LLM
    if not actual_data and not response_data.get("error"):
        output_message = f"Nenhum dado encontrado para os critérios especificados em '{data_route}'."
        if warning_message:
            output_message += f"\n\nAVISO DA API EIA: {warning_message}"
        # Adiciona a URL completa de debug para o LLM, para que ele possa inspecionar se quiser
        debug_params = {k: v for k, v in params.items() if k != 'api_key'}
        full_url_with_params = f"{full_url.split('?')[0]}?{urlencode(debug_params)}"
        output_message += f"\n\nURL da API (sem API Key): {full_url_with_params}"

        return CallToolResult(
            content=[TextContent(type="text", text=output_message)]
        )
    
    # Formatação melhorada dos dados
    total_records = response_data.get('total', len(actual_data))
    output_lines = [
        f"Dados de Energia - {response_content.get('name', specific_route)}",
        f"Total de registros: {total_records} (mostrando {len(actual_data)})",
        f"Consulta: {query}",
        ""
    ]
    
    # Adicionado: Inclui o warning da API mesmo se houver dados
    if warning_message: 
        output_lines.append(f"AVISO DA API EIA: {warning_message}")
        output_lines.append("") # Linha em branco para melhor formatação

    if actual_data:
        # Adicionado: Heurística para orientar o LLM sobre dados desagregados
        first_row_keys = actual_data[0].keys()
        is_disaggregated_by_state_sector = 'stateid' in first_row_keys and 'sectorid' in first_row_keys

        # Verifica se 'US' ou 'ALL' para stateid foi explicitamente solicitado
        stateid_filter_values = filters.get('stateid', []) if filters else []
        requested_national_aggregate = False
        if isinstance(stateid_filter_values, list):
            if 'US' in stateid_filter_values or 'ALL' in stateid_filter_values:
                requested_national_aggregate = True
        elif isinstance(stateid_filter_values, str):
            if stateid_filter_values == 'US' or stateid_filter_values == 'ALL':
                requested_national_aggregate = True

        # Se os dados parecem desagregados e um total nacional não foi explicitamente pedido
        if is_disaggregated_by_state_sector and not requested_national_aggregate:
            output_lines.append(
                "Os dados retornados são desagregados por estado e setor. "
                "Para obter um total nacional, você pode precisar somar os valores relevantes "
                "(ex: 'sales' para 'sectorid: ALL') de cada estado, "
                "ou refinar a busca especificando 'filters={{'stateid': ['US']}}' para obter o agregado nacional (se disponível)."
            )
            output_lines.append("") # Linha em branco para melhor formatação

        # Cabeçalho da tabela
        columns = list(actual_data[0].keys())
        header_line = "| " + " | ".join(columns) + " |"
        separator_line = "|" + "---|".join(["---"] * len(columns)) + "|"
        output_lines.extend([header_line, separator_line])
        
        # Dados da tabela (limitado a 50 linhas para evitar overflow)
        display_data = actual_data[:50]
        for row in display_data:
            row_values = [str(row.get(col, 'N/A')) for col in columns]
            output_lines.append("| " + " | ".join(row_values) + " |")
        
        if len(actual_data) > 50:
            output_lines.append(f"... e mais {len(actual_data) - 50} registros")
        
        # Informações adicionais
        output_lines.append("")
        output_lines.append("Informações sobre os dados:")
        if response_content.get('description'):
            output_lines.append(f"- Descrição: {response_content['description']}")
        if response_data.get('total', 0) > len(actual_data):
            output_lines.append(f"- Dados paginados: apenas {len(actual_data)} de {response_data['total']} registros mostrados")
    
    # Adicionado: Adiciona a URL da API (sem API Key) para que o LLM possa debuggar ou re-testar se necessário
    debug_params = {k: v for k, v in params.items() if k != 'api_key'}
    full_url_with_params = f"{full_url.split('?')[0]}?{urlencode(debug_params)}"
    output_lines.append(f"URL da API (sem API Key): {full_url_with_params}")

    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(output_lines))]
    )

@mcp.tool()
async def get_facet_values(route: str, facet_id: str) -> CallToolResult:
    """
    Obtém os valores disponíveis para um facet específico em uma rota.
    
    Args:
        route: Rota da EIA (ex: "electricity/retail-sales")
        facet_id: ID do facet (ex: "stateid", "sectorid")
    """
    facet_route = f"{route.rstrip('/')}/facet/{facet_id}"
    
    response = await make_eia_api_request(facet_route, {})
    
    if not response or response.get("error"):
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=f"Erro ao obter valores do facet '{facet_id}' na rota '{route}': {response.get('message', 'Erro desconhecido')}")]
        )
    
    response_content = response.get('response', response)
    facet_values = response_content.get('facets', [])
    
    if not facet_values:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Nenhum valor encontrado para o facet '{facet_id}' na rota '{route}'.")]
        )
    
    output_lines = [
        f"Valores disponíveis para o facet '{facet_id}' na rota '{route}':",
        f"Total: {response_content.get('totalFacets', len(facet_values))}",
        ""
    ]
    
    for value in facet_values:
        value_id = value.get('id', 'N/A')
        value_name = value.get('name', 'N/A')
        alias = value.get('alias', '')
        
        line = f"- {value_id}: {value_name}"
        if alias and alias != value_name:
            line += f" ({alias})"
        output_lines.append(line)
    
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(output_lines))]
    )

@mcp.tool()
async def get_series_data(series_id: str, start: Optional[str] = None, end: Optional[str] = None) -> CallToolResult:
    """
    Obtém dados usando Series ID da API v1 (compatibilidade reversa).
    
    Args:
        series_id: ID da série (ex: "ELEC.SALES.US-ALL.A")
        start: Data de início (ex: "2020")
        end: Data de fim (ex: "2023")
    """
    route_path = f"seriesid/{series_id}"
    params = {}
    
    if start:
        params["start"] = start
    if end:
        params["end"] = end
    
    response = await make_eia_api_request(route_path, params)
    
    if not response or response.get("error"):
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=f"Erro ao obter dados da série '{series_id}': {response.get('message', 'Erro desconhecido')}")]
        )
    
    response_content = response.get('response', {})
    data = response_content.get('data', [])
    
    if not data:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Nenhum dado encontrado para a série '{series_id}'.")]
        )
    
    output_lines = [
        f"Dados da Série: {series_id}",
        f"Total de registros: {len(data)}",
        ""
    ]
    
    # Formatação dos dados da série
    columns = list(data[0].keys()) if data else []
    if columns:
        header_line = "| " + " | ".join(columns) + " |"
        separator_line = "|" + "---|".join(["---"] * len(columns)) + "|"
        output_lines.extend([header_line, separator_line])
        
        for row in data:
            row_values = [str(row.get(col, 'N/A')) for col in columns]
            output_lines.append("| " + " | ".join(row_values) + " |")
    
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(output_lines))]
    )

# --- Ferramenta para Teste Direto ---
@mcp.tool()
async def test_direct_api_call(url_path: str, params_dict: Optional[Dict[str, Any]] = None) -> CallToolResult:
    """
    Ferramenta para teste direto de chamadas da API EIA.
    Use para debug e testes específicos.
    
    Args:
        url_path: Caminho da URL (ex: "petroleum/crd/crpdn/data/")
        params_dict: Parâmetros como dicionário
    """
    if params_dict is None:
        params_dict = {}
    
    logger.info(f"Teste direto - URL: {url_path}")
    logger.info(f"Teste direto - Parâmetros: {params_dict}")
    
    response = await make_eia_api_request(url_path, params_dict)
    
    if not response:
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text="Falha na requisição - sem resposta")]
        )
    
    # Retornar resposta bruta para análise
    response_str = json.dumps(response, indent=2, ensure_ascii=False)
    
    return CallToolResult(
        content=[TextContent(type="text", text=f"Resposta da API:\n\n{response_str}")]
    )

# --- Recursos ---
@mcp.resource(uri="eia://guide", name="Guia do Servidor EIA", description="Guia completo para usar o servidor MCP da EIA")
async def get_eia_guide() -> Resource:
    content = """
# Guia do Servidor MCP da EIA

## Visão Geral
Este servidor fornece acesso simplificado aos dados energéticos da EIA (Energy Information Administration) dos EUA.

## Ferramentas Disponíveis

### 1. search_energy_data()
A ferramenta principal e mais inteligente. Use esta para:
- Buscar qualquer tipo de dado energético
- Descobrir automaticamente rotas relevantes
- Explorar metadados e opções disponíveis
- Recuperar dados reais

**Exemplos de uso:**
- `search_energy_data(query="consumo de eletricidade residencial")`
- `search_energy_data(query="preços do petróleo", specific_route="petroleum")`
- `search_energy_data(query="geração solar", specific_route="electricity/electric-power-operational-data", data_elements=["generation"], filters={"fueltypeid": ["SUN"]})`

### 2. get_facet_values()
Para obter valores específicos de filtros:
- `get_facet_values(route="electricity/retail-sales", facet_id="stateid")` - Estados disponíveis
- `get_facet_values(route="electricity/retail-sales", facet_id="sectorid")` - Setores disponíveis

### 3. get_series_data()
Para compatibilidade com Series IDs da API v1:
- `get_series_data(series_id="ELEC.SALES.US-ALL.A")`

### 4. test_direct_api_call()
Para testes e debug diretos:
- `test_direct_api_call(url_path="petroleum/crd/crpdn/data/", params_dict={"frequency": "monthly", "data": ["value"]})`

## Correções Implementadas

1. **Formatação de Parâmetros**: Arrays agora são formatados corretamente como `data[0]=value`
2. **Timeout Aumentado**: Para requisições mais longas
3. **Logging Melhorado**: Para debug detalhado
4. **Tratamento de Erros**: Mais robusto e informativo
5. **Ordenação Padrão**: Incluída automaticamente
6. **Retorno de Avisos da API EIA**: O MCP agora repassa mensagens de 'warning' da API.
7. **Orientação para Agregação Nacional**: Se os dados são desagregados e um total nacional é implicado, o MCP orienta o LLM.
8. **URL de Debug**: A URL completa da API (sem a chave) é incluída para depuração.

## Fluxo Recomendado

1. **Início Exploratório**: Use `search_energy_data()` com uma consulta geral
2. **Refinamento**: Use as informações retornadas para especificar rotas e parâmetros
3. **Obtenção de Dados**: Chame novamente com parâmetros específicos para dados reais
4. **Debug**: Use `test_direct_api_call()` se algo não funcionar

## Categorias Principais de Dados

- **Eletricidade**: Geração, consumo, preços, capacidade
- **Petróleo**: Produção, refino, preços, estoques (incluindo petroleum/crd/crpdn)
- **Gás Natural**: Produção, consumo, preços, capacidade
- **Carvão**: Produção, consumo, preços
- **Energia Renovável**: Solar, eólica, hidráulica
- **Energia Total**: Balanços energéticos, estatísticas consolidadas

## Dicas

- Use termos em português ou inglês nas consultas
- Seja específico sobre localização (estado, região) quando relevante
- Especifique período de tempo quando necessário
- Use filtros para refinar resultados (por estado, setor, tipo de combustível, etc.)
- Para debug, use a ferramenta `test_direct_api_call()`
"""
    
    return Resource(
        uri="eia://guide",
        name="Guia do Servidor EIA",
        mime_type="text/markdown",
        text=content
    )

# --- Prompts ---
@mcp.prompt()
async def energy_data_assistant() -> GetPromptResult:
    """Assistente especializado em dados energéticos da EIA."""
    return GetPromptResult(
        description="Assistente para análise de dados energéticos dos EUA usando a API da EIA",
        messages=[
            {
                "role": "system", 
                "content": TextContent(
                    type="text", 
                    text="""Você é um assistente especializado em dados energéticos dos EUA, com acesso à API da EIA através do servidor MCP.

Use a ferramenta search_energy_data() como ponto de partida para qualquer consulta. Esta ferramenta é inteligente e pode:
1. Descobrir automaticamente rotas relevantes
2. Explorar metadados quando necessário
3. Recuperar dados reais quando os parâmetros estão completos

Sempre comece com consultas gerais e vá refinando conforme necessário. Seja proativo em sugerir filtros e parâmetros úteis baseados no contexto da pergunta do usuário.

Se a API retornar um aviso (AVISO DA API EIA), informe o usuário sobre ele.

Quando dados desagregados (por estado/setor) forem retornados, e a pergunta implicar um total nacional, lembre o usuário de que os dados precisam ser agregados ou que ele pode tentar especificar 'stateid': 'US' para um total direto, se a API o suportar para essa rota.

Se algo não funcionar, use a ferramenta test_direct_api_call() para debug."""
                )
            }
        ]
    )

# --- Função Principal ---
if __name__ == "__main__":
    logger.info("Iniciando servidor MCP da EIA melhorado...")
    
    try:
        mcp.run(transport="sse")
    except Exception as e:
        logger.error(f"Erro ao iniciar servidor: {e}")
        sys.exit(1)