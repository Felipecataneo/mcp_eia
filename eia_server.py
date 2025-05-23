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
import re
from datetime import datetime
import asyncio

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

# --- Mapeamento expandido de conceitos ---
CONCEPT_MAPPING = {
    "electricity": {
        "keywords": ["eletricidade", "energia elétrica", "consumo energia", "geração energia", "preço energia", "vendas energia"],
        "routes": ["electricity", "electricity/retail-sales", "electricity/electric-power-operational-data"],
        "common_facets": ["stateid", "sectorid", "fueltypeid"]
    },
    "petroleum": {
        "keywords": ["petróleo", "gasolina", "diesel", "crude oil", "combustível", "refino"],
        "routes": ["petroleum", "petroleum/crd/crpdn", "petroleum/supply/weekly", "petroleum/supply/historical"],
        "common_facets": ["area", "product", "duoarea"]
    },
    "natural-gas": {
        "keywords": ["gás natural", "gas natural", "lng", "pipeline"],
        "routes": ["natural-gas", "natural-gas/prod", "natural-gas/cons"],
        "common_facets": ["stateid", "product"]
    },
    "coal": {
        "keywords": ["carvão", "coal", "mineração carvão"],
        "routes": ["coal", "coal/production", "coal/consumption"],
        "common_facets": ["stateid", "rank"]
    },
    "renewable": {
        "keywords": ["renovável", "solar", "eólica", "hidráulica", "biomassa", "renewable"],
        "routes": ["electricity/electric-power-operational-data"],
        "common_facets": ["stateid", "fueltypeid"]
    },
    "total-energy": {
        "keywords": ["energia total", "consumo total", "balanço energético"],
        "routes": ["total-energy"],
        "common_facets": ["stateid", "msn"]
    }
}

# --- Mapeamento de estados ---
STATE_MAPPING = {
    'alabama': 'AL', 'alaska': 'AK', 'arizona': 'AZ', 'arkansas': 'AR', 'california': 'CA',
    'colorado': 'CO', 'connecticut': 'CT', 'delaware': 'DE', 'florida': 'FL', 'georgia': 'GA',
    'hawaii': 'HI', 'idaho': 'ID', 'illinois': 'IL', 'indiana': 'IN', 'iowa': 'IA',
    'kansas': 'KS', 'kentucky': 'KY', 'louisiana': 'LA', 'maine': 'ME', 'maryland': 'MD',
    'massachusetts': 'MA', 'michigan': 'MI', 'minnesota': 'MN', 'mississippi': 'MS', 'missouri': 'MO',
    'montana': 'MT', 'nebraska': 'NE', 'nevada': 'NV', 'new hampshire': 'NH', 'new jersey': 'NJ',
    'new mexico': 'NM', 'new york': 'NY', 'north carolina': 'NC', 'north dakota': 'ND', 'ohio': 'OH',
    'oklahoma': 'OK', 'oregon': 'OR', 'pennsylvania': 'PA', 'rhode island': 'RI', 'south carolina': 'SC',
    'south dakota': 'SD', 'tennessee': 'TN', 'texas': 'TX', 'utah': 'UT', 'vermont': 'VT',
    'virginia': 'VA', 'washington': 'WA', 'west virginia': 'WV', 'wisconsin': 'WI', 'wyoming': 'WY'
}

# --- Cache simples para metadados ---
metadata_cache = {}

# --- Inicialização do Servidor MCP ---
# Esta é a instância da sua aplicação FastMCP. O Uvicorn a carregará.
mcp = FastMCP(
    name="eia-energy-data",
    host="0.0.0.0",
    port=PORT,
)

# --- Funções Auxiliares Melhoradas ---
def format_eia_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Formata parâmetros para o formato correto da API EIA.
    """
    formatted_params = {}
    
    for key, value in params.items():
        if key == "facets" and isinstance(value, dict):
            # Formatação especial para facets
            for facet_key, facet_values in value.items():
                if isinstance(facet_values, list):
                    formatted_params[f"facets[{facet_key}][]"] = facet_values
                else:
                    formatted_params[f"facets[{facet_key}][]"] = [facet_values]
        elif isinstance(value, list):
            # Arrays indexados
            for i, item in enumerate(value):
                formatted_params[f"{key}[{i}]"] = item
        elif isinstance(value, dict):
            # Objetos aninhados (como sort)
            for sub_key, sub_value in value.items():
                if isinstance(sub_value, list):
                    for i, item in enumerate(sub_value):
                        if isinstance(item, dict):
                            for item_key, item_value in item.items():
                                formatted_params[f"{key}[{i}][{item_key}]"] = item_value
                        else:
                            formatted_params[f"{key}[{i}][{sub_key}]"] = item
                else:
                    formatted_params[f"{key}[{sub_key}]"] = sub_value
        else:
            formatted_params[key] = value
    
    return formatted_params

async def make_eia_api_request(route_path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Faz requisição à API da EIA com tratamento robusto de erros."""
    if not EIA_API_KEY:
        logger.error("EIA_API_KEY não está definida")
        return {"error": "API_KEY_MISSING", "message": "Chave da API EIA não configurada"}
    
    full_url = f"{EIA_API_BASE_URL}/{route_path.lstrip('/')}"
    
    if params is None:
        params = {}
    
    formatted_params = format_eia_params(params)
    formatted_params['api_key'] = EIA_API_KEY
    
    # Log detalhado
    temp_params = {k: v for k, v in formatted_params.items() if k != 'api_key'}
    logger.info(f"URL: {full_url}")
    logger.info(f"Parâmetros: {temp_params}")
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                full_url, 
                params=formatted_params, 
                headers=EIA_HEADERS, 
                timeout=90.0
            )
            
            logger.info(f"Status: {response.status_code}")
            logger.info(f"URL final: {response.url}")
            
            response.raise_for_status()
            return response.json()
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Erro HTTP EIA API: {e.response.status_code}")
            logger.error(f"Response text: {e.response.text}")
            try:
                error_response = e.response.json()
                return error_response
            except Exception:
                return {"error": f"HTTPStatusError: {e.response.status_code}", "message": e.response.text}
        except Exception as e:
            logger.error(f"Erro inesperado: {e}")
            return {"error": "UnexpectedError", "message": str(e)}

def find_relevant_routes(query: str) -> List[str]:
    """Encontra rotas relevantes baseadas na consulta."""
    query_lower = query.lower()
    relevant_routes = []
    
    for concept, data in CONCEPT_MAPPING.items():
        for keyword in data["keywords"]:
            if keyword.lower() in query_lower:
                relevant_routes.extend(data["routes"])
                break
    
    return list(dict.fromkeys(relevant_routes))

async def get_route_metadata(route: str) -> Dict[str, Any]:
    """Obtém e cacheia metadados de uma rota."""
    if route in metadata_cache:
        return metadata_cache[route]
    
    metadata = await make_eia_api_request(route, {})
    if metadata and not metadata.get("error"):
        metadata_cache[route] = metadata
    
    return metadata or {}

def extract_available_facets(metadata: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extrai facets disponíveis dos metadados."""
    response_content = metadata.get('response', metadata)
    facets = response_content.get('facets', [])
    
    return [
        {
            'id': facet.get('id', ''),
            'name': facet.get('name', ''),
            'description': facet.get('description', '')
        }
        for facet in facets
    ]

def extract_data_elements(metadata: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extrai elementos de dados disponíveis."""
    response_content = metadata.get('response', metadata)
    data_meta = response_content.get('data', {})
    
    elements = []
    for col_id, col_info in data_meta.items():
        if isinstance(col_info, dict):
            elements.append({
                'id': col_id,
                'name': col_info.get('name', col_info.get('alias', col_id)),
                'units': col_info.get('units', ''),
                'description': col_info.get('description', '')
            })
    
    return elements

def extract_frequencies(metadata: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extrai frequências disponíveis."""
    response_content = metadata.get('response', metadata)
    frequencies = response_content.get('frequency', [])
    
    return [
        {
            'id': freq.get('id', freq.get('query', '')),
            'description': freq.get('description', ''),
            'alias': freq.get('alias', '')
        }
        for freq in frequencies
    ]

def parse_query_filters(query: str) -> Dict[str, Any]:
    """Analisa a query e extrai filtros automaticamente."""
    query_lower = query.lower()
    filters = {}
    
    # Estados
    for state_name, state_code in STATE_MAPPING.items():
        if state_name in query_lower:
            filters['stateid'] = [state_code]
            break
    
    # Setores
    if 'residencial' in query_lower or 'residential' in query_lower:
        filters['sectorid'] = ['RES']
    elif 'industrial' in query_lower:
        filters['sectorid'] = ['IND']
    elif 'comercial' in query_lower or 'commercial' in query_lower:
        filters['sectorid'] = ['COM']
    elif 'transporte' in query_lower or 'transportation' in query_lower:
        filters['sectorid'] = ['TRA']
    
    # Combustíveis para energia elétrica
    if 'solar' in query_lower:
        filters['fueltypeid'] = ['SUN']
    elif 'eólica' in query_lower or 'wind' in query_lower:
        filters['fueltypeid'] = ['WND']
    elif 'nuclear' in query_lower:
        filters['fueltypeid'] = ['NUC']
    elif 'carvão' in query_lower or 'coal' in query_lower:
        filters['fueltypeid'] = ['COL']
    elif 'gás natural' in query_lower or 'natural gas' in query_lower:
        filters['fueltypeid'] = ['NG']
    
    return filters

def parse_query_periods(query: str) -> Dict[str, str]:
    """Extrai períodos da query."""
    periods = {}
    
    # Buscar anos (formato 2XXX)
    years = re.findall(r'\b20\d{2}\b', query)
    if years:
        periods['start'] = years[0]
        if len(years) > 1:
            periods['end'] = years[-1]
    
    # Buscar meses (formato YYYY-MM)
    months = re.findall(r'\b20\d{2}-\d{2}\b', query)
    if months:
        periods['start'] = months[0]
        if len(months) > 1:
            periods['end'] = months[-1]
    
    return periods

def determine_frequency(query: str) -> str:
    """Determina a frequência baseada na query."""
    query_lower = query.lower()
    
    if any(word in query_lower for word in ['mensal', 'monthly', 'mês', 'month']):
        return 'monthly'
    elif any(word in query_lower for word in ['trimestral', 'quarterly', 'trimestre', 'quarter']):
        return 'quarterly'
    elif any(word in query_lower for word in ['anual', 'annual', 'ano', 'year']):
        return 'annual'
    else:
        return 'annual'  # Default

# --- Ferramentas Otimizadas ---
@mcp.tool()
async def explore_energy_routes(
    query: Optional[str] = None,
    category: Optional[str] = None
) -> CallToolResult:
    """
    Explora e lista rotas disponíveis da API EIA.
    
    Args:
        query: Consulta de busca (opcional)
        category: Categoria específica (electricity, petroleum, natural-gas, coal, renewable, total-energy)
    """
    if category and category in CONCEPT_MAPPING:
        routes = CONCEPT_MAPPING[category]["routes"]
        info_lines = [f"Rotas para categoria '{category}':"]
        
        for route in routes:
            metadata = await get_route_metadata(route)
            if metadata and not metadata.get("error"):
                response_content = metadata.get('response', metadata)
                name = response_content.get('name', route)
                description = response_content.get('description', '')
                info_lines.append(f"- {route}: {name}")
                if description:
                    info_lines.append(f"  → {description}")
        
        return CallToolResult(
            content=[TextContent(type="text", text="\n".join(info_lines))]
        )
    
    # Listar rotas principais
    response = await make_eia_api_request("", {})
    if not response or response.get('error'):
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text="Erro ao obter rotas principais")]
        )
    
    routes_info = ["Categorias principais da API EIA:"]
    main_routes = response.get('response', {}).get('routes', [])
    
    for route in main_routes:
        route_id = route.get('id', '')
        route_name = route.get('name', '')
        route_desc = route.get('description', '')
        
        if query and query.lower() not in f"{route_id} {route_name} {route_desc}".lower():
            continue
            
        routes_info.append(f"- {route_id}: {route_name}")
        if route_desc:
            routes_info.append(f"  → {route_desc}")
    
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(routes_info))]
    )

@mcp.tool()
async def get_route_info(route: str) -> CallToolResult:
    """
    Obtém informações detalhadas sobre uma rota específica.
    
    Args:
        route: Rota da EIA (ex: "electricity/retail-sales")
    """
    metadata = await get_route_metadata(route)
    
    if not metadata or metadata.get("error"):
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=f"Erro ao obter informações da rota '{route}': {metadata.get('message', 'Erro desconhecido')}")]
        )
    
    response_content = metadata.get('response', metadata)
    
    # Se há sub-rotas, listar
    if response_content.get('routes'):
        subroutes_info = [f"Sub-rotas disponíveis em '{route}':"]
        for subroute in response_content['routes']:
            subroute_id = subroute.get('id', '')
            subroute_name = subroute.get('name', '')
            subroute_desc = subroute.get('description', '')
            
            subroutes_info.append(f"- {subroute_id}: {subroute_name}")
            if subroute_desc:
                subroutes_info.append(f"  → {subroute_desc}")
        
        return CallToolResult(
            content=[TextContent(type="text", text="\n".join(subroutes_info))]
        )
    
    # Informações da rota
    info_lines = [f"Informações da rota: {route}"]
    
    if response_content.get('name'):
        info_lines.append(f"Nome: {response_content['name']}")
    if response_content.get('description'):
        info_lines.append(f"Descrição: {response_content['description']}")
    
    # Elementos de dados
    data_elements = extract_data_elements(metadata)
    if data_elements:
        info_lines.append("\nElementos de dados disponíveis:")
        for element in data_elements:
            line = f"  - {element['id']}: {element['name']}"
            if element['units']:
                line += f" ({element['units']})"
            info_lines.append(line)
    
    # Filtros/Facets
    facets = extract_available_facets(metadata)
    if facets:
        info_lines.append("\nFiltros disponíveis:")
        for facet in facets:
            info_lines.append(f"  - {facet['id']}: {facet['name']}")
    
    # Frequências
    frequencies = extract_frequencies(metadata)
    if frequencies:
        info_lines.append("\nFrequências disponíveis:")
        for freq in frequencies:
            line = f"  - {freq['id']}"
            if freq['description']:
                line += f": {freq['description']}"
            info_lines.append(line)
    
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(info_lines))]
    )

@mcp.tool()
async def get_energy_data(
    route: str,
    data_elements: List[str],
    frequency: Optional[str] = None,
    start_period: Optional[str] = None,
    end_period: Optional[str] = None,
    filters: Optional[Dict[str, Union[str, List[str]]]] = None,
    limit: int = 5000,
    sort_column: Optional[str] = "period",
    sort_direction: Optional[str] = "desc"
) -> CallToolResult:
    """
    Obtém dados de energia de uma rota específica.
    
    Args:
        route: Rota da EIA (ex: "electricity/retail-sales")
        data_elements: Elementos de dados a retornar (ex: ["sales", "price"])
        frequency: Frequência (ex: "annual", "monthly")
        start_period: Período inicial (ex: "2020")
        end_period: Período final (ex: "2024")
        filters: Filtros como dicionário (ex: {"stateid": ["US"], "sectorid": ["RES"]})
        limit: Limite de registros
        sort_column: Coluna para ordenação
        sort_direction: Direção da ordenação (asc/desc)
    """
    data_route = f"{route.rstrip('/')}/data/"
    
    params = {
        "length": limit,
        "offset": 0,
        "data": data_elements
    }
    
    if frequency:
        params["frequency"] = frequency
    if start_period:
        params["start"] = start_period
    if end_period:
        params["end"] = end_period
    
    # Ordenação
    if sort_column:
        params["sort"] = [{"column": sort_column, "direction": sort_direction}]
    
    # Filtros
    if filters:
        params["facets"] = filters
    
    response = await make_eia_api_request(data_route, params)
    
    if not response:
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text="Falha na requisição - sem resposta")]
        )
    
    if response.get("error"):
        error_msg = f"Erro ao obter dados: {response.get('message', 'Erro desconhecido')}"
        if response.get("data"):
            error_msg += f"\nDetalhes: {response.get('data')}"
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=error_msg)]
        )
    
    response_data = response.get('response', {})
    actual_data = response_data.get('data', [])
    warning_message = response_data.get('warning')
    
    if not actual_data:
        msg = f"Nenhum dado encontrado para os critérios especificados."
        if warning_message:
            msg += f"\n\nAVISO DA API EIA: {warning_message}"
        
        # Gerar URL para debug manualmente para garantir formato correto
        debug_params = format_eia_params({k: v for k, v in params.items() if k != 'api_key'})
        query_string = urlencode(debug_params, doseq=True)
        debug_url = f"{EIA_API_BASE_URL}/{data_route.lstrip('/')}?{query_string}"
        
        msg += f"\n\nURL de debug (sem API key): {debug_url}"
        
        return CallToolResult(
            content=[TextContent(type="text", text=msg)]
        )
    
    # Formatação dos resultados
    total_records = response_data.get('total', len(actual_data))
    output_lines = [
        f"Dados de Energia - Rota: {route}",
        f"Total de registros: {total_records} (retornados: {len(actual_data)})",
        ""
    ]
    
    if warning_message:
        output_lines.append(f"AVISO DA API EIA: {warning_message}")
        output_lines.append("")
    
    # Análise dos dados para orientação
    if actual_data:
        first_row = actual_data[0]
        has_state_data = 'stateid' in first_row
        has_sector_data = 'sectorid' in first_row
        
        # Orientação sobre agregação
        if has_state_data and has_sector_data:
            state_filter = filters.get('stateid', []) if filters else []
            national_requested = ('US' in state_filter) if isinstance(state_filter, list) else (state_filter == 'US')
            
            if not national_requested:
                output_lines.append("📊 NOTA: Os dados são desagregados por estado e setor.")
                output_lines.append("Para total nacional, use filtro: {'stateid': ['US']} ou aggregate manualmente.")
                output_lines.append("")
        
        # Tabela de dados
        columns = list(first_row.keys())
        header = "| " + " | ".join(columns) + " |"
        separator = "|" + "---|".join(["---"] * len(columns)) + "|"
        output_lines.extend([header, separator])
        
        # Limitar exibição para evitar overflow
        display_limit = min(100, len(actual_data))
        for row in actual_data[:display_limit]:
            row_values = [str(row.get(col, 'N/A')) for col in columns]
            output_lines.append("| " + " | ".join(row_values) + " |")
        
        if len(actual_data) > display_limit:
            output_lines.append(f"... e mais {len(actual_data) - display_limit} registros")
    
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(output_lines))]
    )

@mcp.tool()
async def get_facet_values(route: str, facet_id: str, limit: int = 100) -> CallToolResult:
    """
    Obtém os valores disponíveis para um facet específico.
    
    Args:
        route: Rota da EIA (ex: "electricity/retail-sales")
        facet_id: ID do facet (ex: "stateid", "sectorid")
        limit: Limite de valores retornados
    """
    facet_route = f"{route.rstrip('/')}/facet/{facet_id}"
    params = {"length": limit}
    
    response = await make_eia_api_request(facet_route, params)
    
    if not response or response.get("error"):
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=f"Erro ao obter valores do facet '{facet_id}': {response.get('message', 'Erro desconhecido')}")]
        )
    
    response_content = response.get('response', response)
    facet_values = response_content.get('facets', [])
    
    if not facet_values:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Nenhum valor encontrado para o facet '{facet_id}' na rota '{route}'.")]
        )
    
    output_lines = [
        f"Valores do facet '{facet_id}' na rota '{route}':",
        f"Total disponível: {response_content.get('totalFacets', len(facet_values))}",
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
async def smart_energy_search(
    query: str,
    auto_drill_down: bool = True,
    include_metadata: bool = False
) -> CallToolResult:
    """
    Busca inteligente de dados de energia que combina descoberta de rotas e obtenção de dados.
    
    Args:
        query: Consulta em linguagem natural (ex: "vendas de eletricidade no Texas em 2024")
        auto_drill_down: Se deve automaticamente tentar obter dados quando possível
        include_metadata: Se deve incluir informações de metadados na resposta
    """
    # Descobrir rotas relevantes
    relevant_routes = find_relevant_routes(query)
    
    if not relevant_routes:
        return await explore_energy_routes(query=query)
    
    results = []
    
    for route in relevant_routes[:2]:  # Limitar para evitar muitas requisições
        # Obter metadados da rota
        metadata = await get_route_metadata(route)
        if not metadata or metadata.get("error"):
            continue
        
        response_content = metadata.get('response', metadata)
        
        # Se tem sub-rotas, pular para a primeira viável
        if response_content.get('routes'):
            subroutes = response_content['routes']
            # Escolher sub-rota mais específica baseada na query
            best_subroute = None
            for subroute in subroutes:
                subroute_name = subroute.get('name', '').lower()
                if any(keyword in subroute_name for keyword in ['sales', 'data', 'retail']):
                    best_subroute = subroute.get('id', '')
                    break
            
            if best_subroute:
                route = best_subroute
                metadata = await get_route_metadata(route)
                response_content = metadata.get('response', metadata)
        
        if not auto_drill_down:
            route_info = await get_route_info(route)
            results.append(f"Rota encontrada: {route}")
            results.append(route_info.content[0].text)
            continue
        
        # Tentar obter dados automaticamente
        data_elements = extract_data_elements(metadata)
        if not data_elements:
            continue
        
        # Selecionar elementos mais comuns
        common_elements = []
        for element in data_elements:
            element_id = element['id'].lower()
            if any(common in element_id for common in ['sales', 'value', 'generation', 'consumption', 'price']):
                common_elements.append(element['id'])
        
        if not common_elements:
            common_elements = [data_elements[0]['id']]  # Pegar o primeiro se não encontrar comum
        
        # Definir parâmetros básicos
        params = {
            'route': route,
            'data_elements': common_elements[:2],  # Máximo 2 elementos
            'frequency': determine_frequency(query),
            'limit': 50  # Limite menor para primeiro teste
        }
        
        # Aplicar filtros automáticos
        auto_filters = parse_query_filters(query)
        if auto_filters: # <-- Linha corrigida/completada
            params['filters'] = auto_filters
        
        # Aplicar períodos automáticos
        auto_periods = parse_query_periods(query)
        if auto_periods.get('start'): # <-- Linha corrigida/completada
            params['start_period'] = auto_periods['start']
        if auto_periods.get('end'): # <-- Linha corrigida/completada
            params['end_period'] = auto_periods['end']
            
        # Call get_energy_data
        data_result = await get_energy_data(**params)
        
        results.append(f"--- Resultados para Rota: {route} ---")
        results.append(data_result.content[0].text)
        
        if include_metadata:
            route_info = await get_route_info(route)
            results.append(f"--- Metadados para Rota: {route} ---")
            results.append(route_info.content[0].text)
            
    if not results:
        return CallToolResult(
            content=[TextContent(type="text", text="Nenhum dado ou rota relevante encontrado para sua consulta.")]
        )
    
    return CallToolResult(
        content=[TextContent(type="text", text="\n\n".join(results))]
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

### 1. explore_energy_routes()
Lista as categorias principais de dados e as rotas associadas. Útil para entender o escopo da API.
**Exemplos de uso:**
- `explore_energy_routes()`
- `explore_energy_routes(category="electricity")`

### 2. get_route_info()
Obtém metadados detalhados de uma rota específica, incluindo elementos de dados, filtros (facets) e frequências disponíveis. Essencial para planejar sua consulta de dados.
**Exemplos de uso:**
- `get_route_info(route="electricity/retail-sales")`

### 3. get_energy_data()
A ferramenta principal para recuperar dados energéticos. Requer uma rota específica e os elementos de dados desejados. Permite filtrar por frequência, período e outros facets.
**Exemplos de uso:**
- `get_energy_data(route="electricity/retail-sales", data_elements=["sales"], frequency="annual", start_period="2020", end_period="2022", filters={"stateid": ["TX"], "sectorid": ["RES"]})`

### 4. get_facet_values()
Para obter todos os valores possíveis para um filtro (facet) específico em uma rota. Ajuda a construir filtros precisos.
**Exemplos de uso:**
- `get_facet_values(route="electricity/retail-sales", facet_id="stateid")`
- `get_facet_values(route="petroleum/supply/weekly", facet_id="duoarea")`

### 5. smart_energy_search()
A ferramenta mais inteligente e versátil. Ela tenta entender sua consulta em linguagem natural para:
- Descobrir rotas relevantes automaticamente.
- Extrair elementos de dados, filtros e períodos da sua query.
- Tentar obter os dados diretamente, se possível.
Use esta ferramenta como ponto de partida para a maioria das perguntas.

**Exemplos de uso:**
- `smart_energy_search(query="vendas de eletricidade residencial no Texas em 2023")`
- `smart_energy_search(query="geração eólica na Califórnia nos últimos 5 anos")`
- `smart_energy_search(query="preço da gasolina nos EUA em 2024")`

### 6. get_series_data()
Para compatibilidade com Series IDs da API v1. Use se você tiver um ID de série específico da versão anterior da API EIA.
**Exemplos de uso:**
- `get_series_data(series_id="ELEC.SALES.US-ALL.A")`

### 7. test_direct_api_call()
Ferramenta para debug e testes diretos de chamadas da API EIA. Retorna a resposta bruta da API.
**Exemplos de uso:**
- `test_direct_api_call(url_path="electricity/retail-sales/data/", params_dict={"data": ["sales"], "frequency": "annual", "start": "2020"})`

## Correções Implementadas (Versão Atual)

- **Formatação de Parâmetros Aprimorada**: Arrays e objetos aninhados são formatados corretamente para a API EIA (ex: `data[0]=value`, `facets[stateid][]=TX`).
- **Timeout Aumentado**: Para requisições mais longas, evitando timeouts prematuros.
- **Logging Detalhado**: Melhora a capacidade de depuração e monitoramento.
- **Tratamento Robusto de Erros**: Mensagens de erro da API são passadas de forma mais clara, e erros inesperados são tratados.
- **Ordenação Padrão**: Dados são ordenados por padrão (coluna 'period', decrescente) para consistência.
- **Retorno de Avisos da API EIA**: Mensagens de 'warning' da API são explicitamente incluídas na resposta.
- **Orientação para Agregação Nacional**: Sugere ao usuário como obter totais nacionais ou interpretar dados desagregados.
- **URL de Debug Explícita**: Inclui a URL completa da requisição da API (sem a chave secreta) para depuração.
- **Melhoria do `smart_energy_search`**: Mais inteligente na extração de parâmetros e na condução da busca.

## Fluxo Recomendado para Interação

1. **Início Exploratório**: Comece com `smart_energy_search(query="sua pergunta")` para a maioria das consultas, pois ele tenta automatizar a descoberta e recuperação.
2. **Refinamento e Detalhes**: Se `smart_energy_search` não for suficiente ou precisar de mais clareza, use `get_route_info()` para entender as opções de uma rota, e `get_facet_values()` para ver valores de filtros.
3. **Obtenção de Dados Direta**: Uma vez que você tenha a rota e os parâmetros exatos, use `get_energy_data()` para uma consulta mais controlada.
4. **Debug**: Em caso de problemas, utilize `test_direct_api_call()` com a `url_path` e `params_dict` relevantes para inspecionar a resposta bruta da API.

## Categorias Principais de Dados

- **Eletricidade**: Geração, consumo, preços, capacidade (rotas como `electricity/retail-sales`, `electricity/electric-power-operational-data`)
- **Petróleo**: Produção, refino, preços, estoques (rotas como `petroleum/crd/crpdn`, `petroleum/supply/weekly`)
- **Gás Natural**: Produção, consumo, preços, capacidade (rotas como `natural-gas/prod`, `natural-gas/cons`)
- **Carvão**: Produção, consumo, preços (rotas como `coal/production`, `coal/consumption`)
- **Energia Renovável**: Solar, eólica, hidráulica (geralmente sob rotas de eletricidade)
- **Energia Total**: Balanços energéticos, estatísticas consolidadas (rota `total-energy`)

## Dicas

- Use termos em português ou inglês nas consultas para `smart_energy_search`.
- Seja específico sobre localização (estado, região) e período de tempo (ano, mês, intervalo) quando relevante.
- Utilize os filtros (facets) para refinar seus resultados (por estado, setor, tipo de combustível, etc.).
- A API EIA é vasta; se uma consulta inicial não for específica o suficiente, o assistente o guiará para refinar.
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

Sempre comece com a ferramenta `smart_energy_search(query="...")` para qualquer consulta. Esta ferramenta é a mais inteligente e tentará resolver a consulta do usuário de forma autônoma, descobrindo rotas, extraindo parâmetros e obtendo dados.

Se `smart_energy_search` não for suficiente ou precisar de mais clareza:
- Use `get_route_info(route="...")` para entender os elementos de dados, filtros e frequências de uma rota específica.
- Use `get_facet_values(route="...", facet_id="...")` para listar valores possíveis para um filtro.
- Em seguida, use `get_energy_data(route="...", data_elements=["..."], filters={...}, ...)` para obter os dados com os parâmetros corretos.

Se a API retornar um aviso (AVISO DA API EIA), informe o usuário sobre ele.

Quando dados desagregados (por exemplo, por estado ou setor) forem retornados, e a pergunta puder implicar um total nacional, lembre o usuário de que os dados podem precisar ser agregados manualmente, ou sugira que ele tente especificar `'stateid': ['US']` (ou o equivalente para outros filtros) para obter o agregado nacional, se a API o suportar para aquela rota.

Para qualquer problema ou depuração profunda, utilize a ferramenta `test_direct_api_call(url_path="...", params_dict={...})`."""
                )
            }
        ]
    )

