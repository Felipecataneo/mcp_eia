import os
from typing import Any, Dict, List, Optional, Union
import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, Resource, GetPromptResult
from dotenv import load_dotenv

# Importações específicas para SSE/HTTP
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import JSONResponse
# CORREÇÃO: Usar SseServerTransport explicitamente
from mcp.server.sse import SseServerTransport 
import uvicorn
import asyncio # Necessário para rodar funções assíncronas no setup da Starlette

# Carrega variáveis de ambiente do .env
load_dotenv()

# --- Configurações da API da EIA ---
EIA_API_BASE_URL = "https://api.eia.gov/v2" 
EIA_API_KEY = os.getenv("EIA_API_KEY") 
if not EIA_API_KEY:
    raise ValueError("A variável de ambiente EIA_API_KEY não está definida.")

EIA_HEADERS = {
    "User-Agent": "US-Energy-Info-Admin-MCP-Server/1.0 (contact@example.com)"
}

# --- Inicialização do Servidor MCP ---
mcp = FastMCP("eia-data-api")

# --- Funções Auxiliares para Interagir com a API da EIA ---
async def make_eia_api_request(route_path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    full_url = f"{EIA_API_BASE_URL}/{route_path}"
    
    if params is None:
        params = {}
    
    params_with_key = {**params, 'api_key': EIA_API_KEY} 

    temp_request = httpx.Request("GET", full_url, params=params_with_key)
    print(f"DEBUG_EIA_API: Enviando requisição para: {temp_request.url}")
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(full_url, params=params_with_key, headers=EIA_HEADERS, timeout=30.0)
            response.raise_for_status() 
            return response.json()
        except httpx.HTTPStatusError as e:
            print(f"Erro HTTP ao acessar EIA API: {e.response.status_code} - {e.response.text}")
            return None
        except httpx.RequestError as e:
            print(f"Erro de requisição ao acessar EIA API: {e}")
            return None
        except Exception as e:
            print(f"Erro inesperado ao acessar EIA API: {e}")
            return None

# --- Ferramentas (Tools) da EIA ---
@mcp.tool()
async def get_eia_v2_route_data(
    route_path_with_data_segment: str,
    data_elements: Optional[List[str]] = None,
    facets: Optional[Dict[str, Union[str, List[str]]]] = None,
    frequency: Optional[str] = None,
    start_period: Optional[str] = None,
    end_period: Optional[str] = None,
    sort_column: Optional[str] = None,
    sort_direction: Optional[str] = None,
    length: int = 5000,
    offset: int = 0
) -> CallToolResult:
    if not route_path_with_data_segment.endswith('/data/'):
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=f"O parâmetro 'route_path_with_data_segment' DEVE terminar em '/data/' para consultas de dados. Caminho fornecido: {route_path_with_data_segment}")]
        )

    full_path = route_path_with_data_segment
    
    params = {
        "length": length,
        "offset": offset
    }

    if data_elements:
        params["data"] = data_elements
    if frequency:
        params["frequency"] = frequency
    if start_period:
        params["start"] = start_period
    if end_period:
        params["end"] = end_period
    
    if sort_column and sort_direction:
        params["sort"] = [{"column": sort_column, "direction": sort_direction}]

    if facets:
        for facet_key, facet_values in facets.items():
            if isinstance(facet_values, list):
                params[f"facets[{facet_key}]"] = facet_values 
            else:
                params[f"facets[{facet_key}]"] = facet_values 
    
    data = await make_eia_api_request(full_path, params)

    if not data or not data.get('response', {}).get('data'):
        error_message = f"Não foi possível recuperar dados para a rota {full_path}. "
        if data and data.get('response', {}).get('errors'):
            error_message += f" Erro da API: {data['response']['errors']}"
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=error_message)]
        )
    
    response_data = data['response']['data']
    formatted_data = []
    
    if response_data:
        columns = list(response_data[0].keys()) if response_data else []
        header_line = "| " + " | ".join(columns) + " |"
        separator_line = "|" + "---|".join(["---"] * len(columns)) + "|"
        formatted_data.extend([header_line, separator_line])
        for row in response_data:
            row_values = [str(row.get(col, 'N/A')) for col in columns]
            formatted_data.append("| " + " | ".join(row_values) + " |")

    if not formatted_data:
        return CallToolResult(
            is_error=False,
            content=[TextContent(type="text", text=f"Nenhum dado encontrado para a rota {full_path} com os critérios fornecidos.")]
        )

    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(formatted_data))]
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
        params["sort"] = [{"column": sort_column, "direction": sort_direction}]

    data = await make_eia_api_request(route_path, params)

    if not data or not data.get('response', {}).get('data'):
        error_message = f"Não foi possível recuperar dados para o Series ID {series_id}. Verifique o ID ou os parâmetros. "
        if data and data.get('response', {}).get('errors'):
            error_message += f" Erro da API: {data['response']['errors']}"
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=error_message)]
        )
    
    response_data = data['response']['data']
    formatted_data = []
    
    if response_data:
        columns = list(response_data[0].keys()) if response_data else []
        header_line = "| " + " | ".join(columns) + " |"
        separator_line = "|" + "---|".join(["---"] * len(columns)) + "|"
        formatted_data.extend([header_line, separator_line])
        for row in response_data:
            row_values = [str(row.get(col, 'N/A')) for col in columns]
            formatted_data.append("| " + " | ".join(row_values) + " |")

    if not formatted_data:
        return CallToolResult(
            is_error=False,
            content=[TextContent(type="text", text=f"Nenhum dado encontrado para o Series ID {series_id} com os critérios fornecidos.")]
        )

    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(formatted_data))]
    )


@mcp.tool()
async def list_eia_v2_routes(
    segment_path: Optional[str] = None
) -> CallToolResult:
    """
    Lista as rotas (segmentos de URL) disponíveis na API v2 da EIA e seus metadados.
    Use para explorar a árvore de dados da EIA.

    Args:
        segment_path: O caminho de um segmento para listar suas sub-rotas/metadados.
                      Se omitido, lista as rotas de nível superior (ex: "electricity", "petroleum").
    """
    path_to_list = segment_path if segment_path else "" 
    
    data = await make_eia_api_request(path_to_list, {})

    print(f"DEBUG_EIA_API_LIST_ROUTES: Resposta da API para '{path_to_list}': {data}")

    formatted_info = []
    
    response_obj = data.get('response', data) 
    
    routes_list = response_obj.get('routes', []) 
    
    if routes_list:
        if not segment_path: 
            formatted_info.append("Rotas de Nível Superior Disponíveis:")
        else: 
            formatted_info.append(f"Sub-Rotas para '{segment_path}':")
            if response_obj.get('id'): formatted_info.append(f"  ID da Rota: {response_obj.get('id')}")
            if response_obj.get('name'): formatted_info.append(f"  Nome da Rota: {response_obj.get('name')}")
            if response_obj.get('description'): formatted_info.append(f"  Descrição: {response_obj.get('description')}")
            
        for route_item in routes_list:
            formatted_info.append(f"  - ID: {route_item.get('id', 'N/A')}, Nome: {route_item.get('name', 'N/A')}")
            if route_item.get('description'):
                formatted_info.append(f"    Descrição: {route_item.get('description', 'N/A')}")
    else:
        if response_obj.get('id') or response_obj.get('name') or response_obj.get('description'):
            formatted_info.append(f"Metadados da Rota '{segment_path}':")
            if response_obj.get('id'): formatted_info.append(f"  ID: {response_obj.get('id')}")
            if response_obj.get('name'): formatted_info.append(f"  Nome: {response_obj.get('name')}")
            if response_obj.get('description'): formatted_info.append(f"  Descrição: {response_obj.get('description')}")
            
            facets = response_obj.get('facets', [])
            if facets:
                formatted_info.append("\nFacets (Filtros de Dimensão):")
                for facet in facets:
                    formatted_info.append(f"  - ID: {facet.get('id', 'N/A')}, Nome: {facet.get('name', 'N/A')}, Descrição: {facet.get('description', 'N/A')}")

            data_columns_meta = response_obj.get('data', [])
            if data_columns_meta:
                formatted_info.append("\nColunas de Dados Disponíveis:")
                for col in data_columns_meta:
                    formatted_info.append(f"  - ID: {col.get('id', 'N/A')}, Nome: {col.get('name', 'N/A')}, Unidades: {col.get('units', 'N/A')}")
            
            frequencies = response_obj.get('frequency', [])
            if frequencies:
                formatted_info.append("\nFrequências Disponíveis:")
                for freq in frequencies:
                    formatted_info.append(f"  - ID: {freq.get('id', 'N/A')}, Descrição: {freq.get('description', 'N/A')}, Formato: {freq.get('format', 'N/A')}")
        else:
            return CallToolResult(
                is_error=True,
                content=[TextContent(type="text", text=f"Resposta da API da EIA para '{path_to_list}' não contém informações de rota ou sub-rotas esperadas.")]
            )
    
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(formatted_info))]
    )


# --- Recursos (Resources) da EIA ---
@mcp.resource(uri="eia://metadata/api-guide", name="Guia Rápido da API EIA v2", description="Um guia sobre como usar as ferramentas para acessar dados da API v2 da EIA.")
async def get_eia_api_guide_resource() -> Resource:
    content_text = """
    Bem-vindo ao Guia Rápido da API EIA v2. A API v2 organiza os dados em uma hierarquia de "rotas" (URL paths).

    **Ferramentas Disponíveis:**

    1.  **`list_eia_v2_routes(segment_path: Optional[str])`**:
        *   **Uso:** Para explorar a árvore de dados. Se `segment_path` for omitido, lista as rotas de nível superior (ex: "electricity", "petróleo"). Se for um caminho (ex: "electricity"), lista as sub-rotas e metadados dessa rota.
        *   **Retorna:** Nome, descrição, ID da rota, sub-rotas, facets (filtros de dimensão) e colunas de dados disponíveis.

    2.  **`get_eia_v2_route_data(...)`**:
        *   **Uso:** Para recuperar os dados reais de uma rota específica. É a ferramenta principal para obter dados tabulares.
        *   **Argumentos Chave:**
            *   `route_path_with_data_segment`: **Obrigatório.** O caminho completo da rota que **DEVE terminar em '/data/'** (ex: "electricity/retail-sales/data/").
            *   `data_elements`: **Opcional.** Uma lista de colunas que você deseja (ex: `["value", "net_generation"]`). Se omitido, a API pode retornar um conjunto padrão ou exigir este parâmetro.
            *   `facets`: **Opcional.** Dicionário para filtrar dados (ex: `{"stateid": ["CA", "TX"]}`). Consulte `list_eia_v2_routes` para descobrir facets disponíveis.
            *   `frequency`, `start_period`, `end_period`, `sort_column`, `sort_direction`, `length`, `offset`: Para refinar a consulta e paginar.

    3.  **`get_eia_v2_series_id_data(series_id: str, ...)`**:
        *   **Uso:** Para compatibilidade com Series IDs da APIv1 (ex: "ELEC.SALES.CO-RES.A"). Permite usar IDs antigos diretamente.
        *   **Argumentos:** `series_id` (obrigatório) e outros parâmetros como `data_elements`, `frequency`, `start_period`, `end_period`, `sort_column`, `sort_direction`.

    **Fluxo Recomendado para o LLM:**
    1.  **Explorar Dados:** Comece usando `list_eia_v2_routes()` sem argumentos.
    2.  **Aprofundar:** Se você encontrar uma rota interessante (ex: "electricity"), use `list_eia_v2_routes(segment_path="electricity")` para ver mais detalhes.
    3.  **Identificar Rota de Dados:** Continue até encontrar uma rota que termine em `/data/` ou que pareça conter os dados que você precisa.
    4.  **Obter Dados:** Use `get_eia_v2_route_data` com a rota de dados completa, especificando `data_elements` e quaisquer `facets` ou `frequency` que você descobriu.
    5.  **Series ID (Alternativa):** Se o usuário fornecer um Series ID da APIv1, use `get_eia_v2_series_id_data`.

    **Exemplos de Perguntas:**
    *   "Quais são as principais categorias de dados de energia na EIA?" (Chama `list_eia_v2_routes()`)
    *   "Mostre-me os detalhes da rota 'electricity/retail-sales'." (Chama `list_eia_v2_routes(segment_path="electricity/retail-sales")`)
    *   "Obtenha os dados de preços anuais de eletricidade para vendas a varejo, na Califórnia (CA) e Texas (TX), usando a rota 'electricity/retail-sales/data/'. Eu preciso do 'price' como elemento de dado. Comece em 2020." (Chama `get_eia_v2_route_data`)
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
    Prompt para guiar o usuário a fazer uma pergunta sobre dados da EIA v2, usando rotas, elementos de dados e filtros.
    """
    description = "Ajuda a encontrar dados da API v2 da EIA, especificando o caminho da rota, elementos de dados desejados e filtros (facets)."
    messages = [
        {
            "role": "user",
            "content": TextContent(type="text", text="Gostaria de obter dados da EIA. Por favor, forneça o caminho da rota (ex: 'electricity/retail-sales/data/'), quais elementos de dados você precisa (ex: ['value', 'net_generation']), e quaisquer filtros (facets) ou frequência (ex: {'stateid': ['CA', 'TX']}, 'annual').")
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
            "content": TextContent(type="text", text="Gostaria de explorar as rotas da API v2 da EIA. Qual segmento de caminho você gostaria de investigar (ex: 'electricity' ou deixe em branco para o nível superior)?")
        }
    ]
    return GetPromptResult(description=description, messages=messages)

# --- Função Principal para Rodar o Servidor ---
# ALTERAÇÃO: Para Streamable HTTP (SSE)
# CORREÇÃO: sse_transport é instanciado e conectado ao mcp.
# A Starlette precisa do método de tratamento de requisições do sse_transport.
sse_transport = SseServerTransport(path="/mcp") # Endpoint para comunicação MCP

app = Starlette(routes=[
    Route("/mcp", endpoint=sse_transport.handle_request), # Usar o método handle_request do sse_transport
])

# Conecta a instância FastMCP ao sse_transport
# Isso precisa ser feito APÓS a criação do sse_transport
async def connect_mcp_to_transport():
    await mcp.connect(sse_transport)

# Garante que a conexão do MCP ao transporte seja feita na inicialização do Uvicorn
# Uvicorn não tem um hook direto para o "startup" da Starlette, mas podemos usar um wrapper.
# Ou, de forma mais simples e robusta, criar um `on_startup` hook na Starlette:
@app.on_event("startup")
async def startup_event():
    print("Starlette app starting up, connecting MCP to SSE transport...")
    await connect_mcp_to_transport()
    print("MCP connected to SSE transport.")

# Para rodar com uvicorn, o nome do arquivo deve ser `eia_server.py` e o objeto da aplicação `app`.
# Ex: uvicorn eia_server:app --host 0.0.0.0 --port 8000
# Já está configurado no Dockerfile.