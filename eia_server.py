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

# --- Cache simples para metadados ---
metadata_cache = {}

# --- Inicialização do Servidor MCP ---
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
        
        # URL para debug
        debug_params = {k: v for k, v in params.items()}
        debug_url = f"{EIA_API_BASE_URL}/{data_route}?" + urlencode(debug_params, doseq=True)
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
            'frequency': 'annual',  # Começar com anual
            'limit': 50  # Limite menor para primeiro teste
        }
        
        # Tentar detectar filtros da query
        query_lower = query.lower()
        
        # Estados
        state_mapping = {
            'texas': 'TX', 'california': 'CA', 'florida': 'FL', 'new york': 'NY',
            'illinois': 'IL', 'pennsylvania': 'PA', 'ohio': 'OH', 'georgia': 'GA',
            'north carolina': 'NC', 'michigan': 'MI'
        }
        
        for state_name, state_code in state_mapping.items():
            if state_name in query_lower:
                params['filters'] = {'stateid': [state_code]}
                break
        
        # Setores
        if 'residencial' in query_lower or 'residential' in query_lower:
            if 'filters' not in params:
                params['filters'] = {}
            params['filters']['sectorid'] = ['RES']
        elif 'industrial' in query_lower:
            if 'filters' not in params:
                params['filters'] = {}
            params['filters']['sectorid'] = ['IND']
        elif 'comercial' in query_lower or 'commercial' in query_lower:
            if 'filters' not in params:
                params['filters'] = {}
            params['filters']['sectorid'] = ['COM']
        
        # Períodos
        import re
        years = re.findall(r'\b20\d{2}\b', query)
        if years:
            params['start_period'] = years[0]
            if len(years) > 1:
                params['end_period'] = years[-1]
        
        # Obter dados
        data_result = await get_energy_data(**params)
        
        results.append(f"\n=== Dados de {route} ===")
        results.append(data_result.content[0].text)
        
        if data_result.is_error:
            # Se falhou, tentar versão simplificada
            simple_params = {
                'route': route,
                'data_elements': common_elements[:1],
                'limit': 20
            }
            simple_result = await get_energy_data(**simple_params)
            results.append(f"\n--- Tentativa simplificada ---")
            results.append(simple_result.content[0].text)
    
    if not results:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Não foi possível encontrar dados relevantes para: {query}")]
        )
    
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(results))]
    )

# --- Recursos e Prompts ---
# PARTE 1: Completar o conteúdo do guia (que está cortado no final)
@mcp.resource(uri="eia://guide", name="Guia do Servidor EIA Otimizado", description="Guia completo e otimizado")
async def get_eia_guide() -> Resource:
    content = """
# Guia do Servidor MCP da EIA - Versão Otimizada

## Ferramentas Principais

### 🎯 smart_energy_search() - RECOMENDADA
A ferramenta mais inteligente para buscar dados de energia:
```python
# Busca automatizada com drill-down
smart_energy_search(query="vendas de eletricidade no Texas em 2024")

# Apenas exploração
smart_energy_search(query="dados de petróleo", auto_drill_down=False)
```

### 🔍 explore_energy_routes()
Para descobrir rotas disponíveis:
```python
explore_energy_routes(category="electricity")
explore_energy_routes(query="solar energy")
```

### 📊 get_energy_data() - Para controle preciso
Quando você sabe exatamente o que quer:
```python
get_energy_data(
    route="electricity/retail-sales",
    data_elements=["sales"],
    frequency="annual",
    filters={"stateid": ["TX"], "sectorid": ["RES"]},
    start_period="2020",
    end_period="2024"
)
```

### 🏷️ get_facet_values()
Para descobrir valores de filtros:
```python
get_facet_values(route="electricity/retail-sales", facet_id="stateid")
```

### ℹ️ get_route_info()
Para metadados detalhados:
```python
get_route_info(route="electricity/retail-sales")
```

## Melhorias da Versão Otimizada

1. **Cache de Metadados**: Evita requisições desnecessárias
2. **Busca Inteligente**: Combina descoberta e obtenção de dados
3. **Detecção Automática**: Reconhece estados, setores e períodos na query
4. **Drill-down Automático**: Navega sub-rotas automaticamente
5. **Formatação Melhorada**: URLs de debug e orientações claras
6. **Tratamento Robusto**: Múltiplas tentativas e fallbacks

## Categorias Principais

- **electricity**: Dados de eletricidade (geração, vendas, preços)
- **petroleum**: Petróleo e derivados (produção, consumo, preços)
- **natural-gas**: Gás natural (produção, consumo, preços)
- **coal**: Carvão (produção, consumo)
- **renewable**: Energias renováveis
- **total-energy**: Balanço energético total

## Exemplos de Uso Prático

### Consumo Residencial de Eletricidade
```python
smart_energy_search("consumo residencial de eletricidade em 2023")
```

### Produção de Petróleo por Estado
```python
get_energy_data(
    route="petroleum/crd/crpdn",
    data_elements=["value"],
    filters={"duoarea": ["R10", "R20"]},
    frequency="monthly"
)
```

### Preços de Gasolina
```python
smart_energy_search("preços da gasolina nos últimos 2 anos")
```

## Dicas Importantes

1. **Use smart_energy_search primeiro** - É a ferramenta mais eficiente
2. **Especifique períodos** - A API tem muito dados históricos
3. **Use filtros por estado** - Para dados específicos de regiões
4. **Comece com annual** - Depois refine para monthly se necessário
5. **Verifique facets** - Nem todas as rotas têm os mesmos filtros
"""
    
    return Resource(
        uri="eia://guide",
        name="Guia do Servidor EIA Otimizado",
        mimeType="text/markdown",
        text=content
    )

# PARTE 2: Adicionar prompt template para consultas
@mcp.prompt(
    name="eia-query-helper",
    description="Template para ajudar na construção de consultas à API EIA"
)
async def eia_query_helper(
    topic: str = "electricity",
    region: Optional[str] = None,
    time_period: Optional[str] = None,
    sector: Optional[str] = None
) -> GetPromptResult:
    """
    Gera template para consultas otimizadas à API EIA.
    
    Args:
        topic: Tópico energético (electricity, petroleum, natural-gas, coal, renewable)
        region: Região/Estado (opcional)
        time_period: Período de interesse (opcional)
        sector: Setor específico (opcional)
    """
    
    prompt_content = f"""
# Consulta EIA - {topic.upper()}

## Contexto
Você está consultando dados de energia da U.S. Energy Information Administration (EIA).

## Parâmetros da Consulta
- **Tópico**: {topic}
"""
    
    if region:
        prompt_content += f"- **Região**: {region}\n"
    if time_period:
        prompt_content += f"- **Período**: {time_period}\n"
    if sector:
        prompt_content += f"- **Setor**: {sector}\n"
    
    prompt_content += f"""
## Sugestões de Ferramentas

### Para exploração inicial:
```python
smart_energy_search(query="{topic}"""
    
    if region:
        prompt_content += f" {region}"
    if time_period:
        prompt_content += f" {time_period}"
    if sector:
        prompt_content += f" {sector}"
    
    prompt_content += f"""")
```

### Para dados específicos:
```python
explore_energy_routes(category="{topic}")
```

## Filtros Comuns para {topic.upper()}

"""
    
    # Adicionar filtros específicos por categoria
    if topic == "electricity":
        prompt_content += """
- **stateid**: Código do estado (ex: "TX", "CA", "US")
- **sectorid**: Setor (RES=Residencial, COM=Comercial, IND=Industrial, TRA=Transporte)
- **fueltypeid**: Tipo de combustível para geração
"""
    elif topic == "petroleum":
        prompt_content += """
- **area**: Área geográfica
- **product**: Produto petrolífero (gasolina, diesel, etc.)
- **duoarea**: Área PADD (Petroleum Administration for Defense Districts)
"""
    elif topic == "natural-gas":
        prompt_content += """
- **stateid**: Código do estado
- **product**: Tipo de gás natural
- **area**: Área geográfica
"""
    
    prompt_content += """
## Frequências Disponíveis
- **annual**: Dados anuais (recomendado para início)
- **monthly**: Dados mensais
- **weekly**: Dados semanais (quando disponível)
- **daily**: Dados diários (limitado)

## Elementos de Dados Comuns
- **value**: Valor principal do indicador
- **sales**: Vendas (quando aplicável)
- **price**: Preços
- **generation**: Geração (para eletricidade)
- **consumption**: Consumo
"""
    
    return GetPromptResult(
        description="Template otimizado para consultas EIA",
        messages=[
            {
                "role": "system",
                "content": {
                    "type": "text",
                    "text": prompt_content
                }
            }
        ]
    )

# PARTE 3: Função de inicialização e limpeza
def setup_error_handlers():
    """Configura handlers de erro globais."""
    
    async def handle_startup():
        """Executado na inicialização do servidor."""
        logger.info("🚀 Servidor EIA MCP iniciado")
        logger.info(f"📡 Porta: {PORT}")
        logger.info(f"🔑 API Key configurada: {'Sim' if EIA_API_KEY else 'Não'}")
        
        # Teste básico da API
        if EIA_API_KEY:
            test_response = await make_eia_api_request("")
            if test_response and not test_response.get("error"):
                logger.info("✅ Conexão com API EIA verificada")
            else:
                logger.warning("⚠️ Problema na conexão com API EIA")
    
    async def handle_shutdown():
        """Executado no desligamento do servidor."""
        logger.info("🛑 Servidor EIA MCP desligando...")
        # Limpar cache se necessário
        metadata_cache.clear()
        logger.info("🏁 Servidor desligado com sucesso")
    
    # Registrar handlers se o FastMCP suportar
    if hasattr(mcp, 'on_startup'):
        mcp.on_startup(handle_startup)
    if hasattr(mcp, 'on_shutdown'):
        mcp.on_shutdown(handle_shutdown)

# PARTE 4: Função principal para execução
async def main():
    """Função principal para executar o servidor."""
    setup_error_handlers()
    
    try:
        logger.info(f"🌟 Iniciando servidor EIA MCP na porta {PORT}")
        await mcp.run()
    except KeyboardInterrupt:
        logger.info("👋 Servidor interrompido pelo usuário")
    except Exception as e:
        logger.error(f"💥 Erro fatal no servidor: {e}")
        sys.exit(1)

# PARTE 5: Ponto de entrada
if __name__ == "__main__":
    import asyncio
    
    # Verificar dependências críticas
    if not EIA_API_KEY:
        print("❌ ERRO: EIA_API_KEY não configurada!")
        print("Configure a variável de ambiente EIA_API_KEY com sua chave da API EIA")
        print("Obtenha uma chave gratuita em: https://www.eia.gov/opendata/register.php")
        sys.exit(1)
    
    # Executar servidor
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Tchau!")
    except Exception as e:
        print(f"💥 Erro fatal: {e}")
        sys.exit(1)

# PARTE 6: Utilitários adicionais (opcional)
class EIADataProcessor:
    """Classe auxiliar para processamento de dados da EIA."""
    
    @staticmethod
    def aggregate_by_state(data: List[Dict], value_column: str = "value") -> Dict[str, float]:
        """Agrega dados por estado."""
        aggregated = {}
        for row in data:
            state = row.get('stateid', 'Unknown')
            value = row.get(value_column, 0)
            if isinstance(value, (int, float)):
                aggregated[state] = aggregated.get(state, 0) + value
        return aggregated
    
    @staticmethod
    def aggregate_by_period(data: List[Dict], value_column: str = "value") -> Dict[str, float]:
        """Agrega dados por período."""
        aggregated = {}
        for row in data:
            period = row.get('period', 'Unknown')
            value = row.get(value_column, 0)
            if isinstance(value, (int, float)):
                aggregated[period] = aggregated.get(period, 0) + value
        return aggregated
    
    @staticmethod
    def format_large_numbers(value: Union[int, float]) -> str:
        """Formata números grandes de forma legível."""
        if not isinstance(value, (int, float)):
            return str(value)
        
        if abs(value) >= 1_000_000_000:
            return f"{value/1_000_000_000:.2f}B"
        elif abs(value) >= 1_000_000:
            return f"{value/1_000_000:.2f}M"
        elif abs(value) >= 1_000:
            return f"{value/1_000:.2f}K"
        else:
            return f"{value:.2f}"

# PARTE 7: Configurações adicionais de logging
def setup_detailed_logging():
    """Configura logging mais detalhado para debug."""
    
    # Formatter personalizado
    formatter = logging.Formatter(
        fmt='%(asctime)s | %(levelname)8s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Handler para arquivo (opcional)
    if os.getenv("EIA_LOG_FILE"):
        file_handler = logging.FileHandler(os.getenv("EIA_LOG_FILE"))
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    # Handler para console com cores (se disponível)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    # Configurar nível baseado em variável de ambiente
    log_level = os.getenv("EIA_LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, log_level, logging.INFO))

# Executar configuração de logging
setup_detailed_logging()