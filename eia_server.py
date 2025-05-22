import os
from typing import Any, Dict, List, Optional, Union
import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, Resource, GetPromptResult
from dotenv import load_dotenv

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
mcp = FastMCP(
    name="eia-data-api",
    host="0.0.0.0",
    port=8000,
)

# --- Funções Auxiliares para Interagir com a API da EIA ---
async def make_eia_api_request(route_path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    full_url = f"{EIA_API_BASE_URL}/{route_path.lstrip('/')}" # Garante que não haja barras duplas se route_path já começar com /

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
            # Retornar o corpo do erro da API EIA, se disponível e for JSON
            try:
                return e.response.json()
            except Exception:
                return {"error": f"HTTPStatusError: {e.response.status_code}", "message": e.response.text}
        except httpx.RequestError as e:
            print(f"Erro de requisição ao acessar EIA API: {e}")
            return {"error": "RequestError", "message": str(e)}
        except Exception as e:
            print(f"Erro inesperado ao acessar EIA API: {e}")
            return {"error": "UnexpectedError", "message": str(e)}

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

    full_path = route_path_with_data_segment.lstrip('/')

    params = {
        "length": length,
        "offset": offset
    }

    if data_elements:
        # A API espera múltiplos parâmetros 'data[]'
        # httpx lida com a conversão de listas para múltiplos parâmetros com o mesmo nome
        params["data"] = data_elements
    if frequency:
        params["frequency"] = frequency
    if start_period:
        params["start"] = start_period
    if end_period:
        params["end"] = end_period

    if sort_column and sort_direction:
        # A API espera sort[0][column]=... e sort[0][direction]=...
        # Precisamos formatar isso corretamente para httpx ou a API
        # httpx pode não lidar com essa aninhamento de array diretamente em `params`
        # Vamos construir a string de query para sort manualmente se necessário,
        # ou verificar como a API espera. Documentação pág 19 sugere:
        # sort[0][column]=period&sort[0][direction]=desc
        # httpx lida com dicts para isso: params['sort[0][column]'] = sort_column
        params[f"sort[0][column]"] = sort_column
        params[f"sort[0][direction]"] = sort_direction


    if facets:
        for facet_key, facet_values in facets.items():
            # Para facets[stateid][]=CO&facets[sectorid][]=RES
            # Se facet_values for uma lista: facets[key][]=value1&facets[key][]=value2
            # Se facet_values for uma string: facets[key][]=value
            # httpx deve lidar com isso: params[f"facets[{facet_key}][]"] = facet_values (se lista)
            # ou params[f"facets[{facet_key}][]"] = [facet_values] (se string, converte pra lista de um item)
            if isinstance(facet_values, list):
                 params[f"facets[{facet_key}][]"] = facet_values
            else:
                 params[f"facets[{facet_key}][]"] = [facet_values] # API espera array mesmo para valor único

    data_response = await make_eia_api_request(full_path, params)

    if not data_response or data_response.get("error"): # Verifica erro customizado de make_eia_api_request
        error_message = f"Falha ao recuperar dados para a rota {full_path}."
        if data_response and data_response.get("message"):
            error_message += f" Detalhe: {data_response['message']}"
        elif data_response and isinstance(data_response, dict) and data_response.get('response', {}).get('error'): # Erro da API EIA
             error_message += f" Erro da API EIA: {data_response['response']['error']}"
        return CallToolResult(is_error=True, content=[TextContent(type="text", text=error_message)])

    response_content = data_response.get('response', {})
    actual_data = response_content.get('data')

    if not actual_data and isinstance(actual_data, list): # API retorna lista vazia se não houver dados
        return CallToolResult(
            is_error=False, # Não é um erro da ferramenta, apenas não há dados
            content=[TextContent(type="text", text=f"Nenhum dado encontrado para a rota {full_path} com os critérios fornecidos. A API retornou uma lista vazia.")]
        )

    if not actual_data: # Se for None ou outro tipo
        warning_message = response_content.get('warnings', response_content.get('warning'))
        error_api_msg = response_content.get('error', 'Resposta inesperada da API.')
        error_message = f"Não foi possível recuperar dados para a rota {full_path}. "
        if warning_message:
             error_message += f"Aviso da API: {warning_message}. "
        error_message += f"Detalhe: {error_api_msg}. Resposta completa: {data_response}"
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=error_message)]
        )

    formatted_data_output = [f"Total de registros correspondentes (pode ser paginado): {response_content.get('total', len(actual_data))}"]
    
    if actual_data: #Deve ser uma lista de dicts
        columns = list(actual_data[0].keys()) if actual_data else []
        header_line = "| " + " | ".join(columns) + " |"
        separator_line = "|" + "---|".join(["---"] * len(columns)) + "|"
        formatted_data_output.extend([header_line, separator_line])
        for row in actual_data:
            row_values = [str(row.get(col, 'N/A')) for col in columns]
            formatted_data_output.append("| " + " | ".join(row_values) + " |")

    if not formatted_data_output or len(formatted_data_output) <= 1: # Apenas o total
        return CallToolResult(
            is_error=False,
            content=[TextContent(type="text", text=f"Nenhum dado tabular encontrado para a rota {full_path} com os critérios fornecidos. Verifique os parâmetros. Resposta da API: {data_response}")]
        )

    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(formatted_data_output))]
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
    route_path = f"seriesid/{series_id}" # Não precisa de /data/ aqui, a API v2/seriesid já retorna dados
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

    if not formatted_data_output or len(formatted_data_output) <=1:
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

    Args:
        segment_path: O caminho para explorar.
                      - Se omitido ou vazio: lista as rotas de nível superior (ex: "electricity", "petroleum").
                      - Se um caminho de rota (ex: "electricity/retail-sales"): lista sub-rotas e metadados dessa rota (facets, colunas de dados, frequências).
                      - Se um caminho para um facet (ex: "electricity/retail-sales/facet/sectorid"): lista os valores disponíveis para esse facet.
    """
    path_to_list = segment_path.strip('/') if segment_path and segment_path.strip() else ""
    
    raw_response = await make_eia_api_request(path_to_list, {})

    if not raw_response:
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=f"Falha ao fazer requisição à API da EIA para o caminho: '{path_to_list}'. Verifique os logs do servidor MCP.")]
        )

    # A API da EIA às vezes retorna o conteúdo diretamente, às vezes dentro de 'response'
    # Se 'request' estiver no nível raiz, é provável que os dados estejam em 'response'
    if 'request' in raw_response and 'response' in raw_response:
        response_obj = raw_response.get('response')
    else: # Caso contrário, o objeto raiz pode ser a própria resposta de metadados/facet
        response_obj = raw_response

    if response_obj is None or not isinstance(response_obj, dict):
         return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=f"Resposta da API para '{path_to_list}' não é um objeto JSON válido ou está vazia. Resposta: {raw_response}")]
        )

    print(f"DEBUG_EIA_API_LIST_ROUTES: Processando response_obj para '{path_to_list}': {response_obj}")
    formatted_info = []
    
    # Caso 1: A resposta é para VALORES DE UM FACET específico (contém 'totalFacets' e 'facets' como lista - ver pág 12 doc)
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
    
    # Caso 2: A resposta é uma LISTA DE SUB-ROTAS de um nó pai (contém 'routes' como lista - ver pág 8 doc)
    elif isinstance(response_obj.get('routes'), list) and response_obj.get('routes'):
        routes_list = response_obj.get('routes')
        parent_id = response_obj.get('id', path_to_list if path_to_list else "Nível Raiz")
        parent_name = response_obj.get('name', '')
        
        header_text = f"Rotas de Nível Superior Disponíveis (sob '{parent_id}')" if not segment_path or not segment_path.strip() else f"Sub-Rotas para '{parent_id}' ({parent_name})"
        formatted_info.append(header_text)
        
        if response_obj.get('description'): formatted_info.append(f"  Descrição do Pai: {response_obj.get('description')}")
            
        for route_item in routes_list:
            name_str = route_item.get('name', 'N/A')
            desc_str = f"    Descrição: {route_item.get('description', 'N/A')}" if route_item.get('description') else ""
            formatted_info.append(f"  - ID da Sub-rota: {route_item.get('id', 'N/A')}, Nome: {name_str}")
            if desc_str: formatted_info.append(desc_str)
    
    # Caso 3: A resposta são METADADOS DE UMA ROTA específica (contém 'id', 'name', 'facets', 'data', 'frequency' etc. - ver pág 10 doc)
    elif response_obj.get('id') or response_obj.get('name'): # Verifica se tem campos de metadados de rota
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

        # Colunas de dados disponíveis (para get_eia_v2_route_data 'data_elements')
        data_columns_meta = response_obj.get('data', {}) # 'data' é um dict na pág 10
        if isinstance(data_columns_meta, dict) and data_columns_meta:
            formatted_info.append("\n  Colunas de Dados Disponíveis (para parâmetro 'data_elements' em get_eia_v2_route_data):")
            for col_id, col_details in data_columns_meta.items(): # Iterar sobre chaves do dict 'data'
                if isinstance(col_details, dict):
                    name_val = col_details.get('name', col_details.get('alias', 'N/A')) # 'alias' é usado na doc
                    units_val = col_details.get('units', 'N/A')
                    formatted_info.append(f"    - ID da Coluna: {col_id}, Nome/Alias: {name_val}, Unidades: {units_val}")
                else: # Algumas APIs podem retornar uma lista de strings simples para 'data'
                    formatted_info.append(f"    - ID da Coluna: {col_id} (detalhes em formato inesperado: {col_details})")
        elif isinstance(data_columns_meta, list): # Tratamento alternativo se 'data' for uma lista
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
                # 'query' é o valor a ser usado na query, 'id' é o nome mais descritivo
                id_val = freq.get('id', 'N/A') # Ex: 'monthly'
                query_val = freq.get('query', id_val) # Ex: 'M'
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

    # Caso 4: Se nenhum dos formatos acima for correspondido ou a resposta for um erro da API EIA
    else:
        error_detail = f"Resposta da API da EIA para '{path_to_list}' não corresponde a um formato esperado de metadados, sub-rotas ou valores de facet. "
        # Tentar extrair erro específico da API EIA, se houver
        api_error_data = response_obj.get('error') # Erro no nível do response_obj
        if not api_error_data and isinstance(raw_response, dict): # Tenta no raw_response
            api_error_data = raw_response.get('error')
            if not api_error_data and 'response' in raw_response and isinstance(raw_response['response'], dict): # Tenta dentro de response no raw_response
                api_error_data = raw_response['response'].get('error')

        if api_error_data:
            error_detail += f"Erro explícito da API EIA: {api_error_data}. "
        
        error_detail += f"Resposta completa recebida: {raw_response}"
        
        # Verificar se há um campo 'request' no raw_response para ecoar o comando enviado
        if isinstance(raw_response, dict) and 'request' in raw_response:
            error_detail += f" Comando ecoado pela API: {raw_response.get('request')}"

        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=error_detail)]
        )

    if not formatted_info: # Se passou por todos os cases mas não formatou nada
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

    1.  **`list_eia_v2_routes(segment_path: Optional[str])`**:
        *   **Uso:** Para explorar a árvore de dados.
            *   Se `segment_path` for omitido ou vazio, lista as rotas de nível superior (ex: "electricity", "petroleum").
            *   Se `segment_path` for um caminho de rota (ex: "electricity/retail-sales"), lista as sub-rotas E os metadados dessa rota (tipos de facets, colunas de dados, frequências disponíveis).
            *   Se `segment_path` for um caminho para um facet específico (ex: "electricity/retail-sales/facet/sectorid"), lista os VALORES disponíveis para esse facet (ex: "RES" para residencial, "COM" para comercial).
        *   **Retorna:** Dependendo do `segment_path`:
            *   Lista de rotas de nível superior ou sub-rotas.
            *   Metadados detalhados de uma rota: nome, descrição, ID, tipos de facets (com instrução de como listar seus valores), colunas de dados (para `data_elements`), frequências (para parâmetro `frequency`), período de dados.
            *   Lista de valores para um facet específico (com seus IDs e Nomes).

    2.  **`get_eia_v2_route_data(...)`**:
        *   **Uso:** Para recuperar os dados reais de uma rota específica. É a ferramenta principal para obter dados tabulares.
        *   **Argumentos Chave:**
            *   `route_path_with_data_segment`: **Obrigatório.** O caminho completo da rota que **DEVE terminar em '/data/'** (ex: "electricity/retail-sales/data/").
            *   `data_elements`: **Opcional, mas frequentemente necessário.** Uma lista de IDs de colunas que você deseja (ex: `["price", "revenue"]`). Consulte `list_eia_v2_routes` na rota base para descobrir os IDs das colunas de dados disponíveis (procure por "ID da Coluna").
            *   `facets`: **Opcional.** Dicionário para filtrar dados (ex: `{"stateid": "CO", "sectorid": "RES"}`).
                1. Use `list_eia_v2_routes` na rota base (ex: "electricity/retail-sales") para descobrir os `ID do Facet` disponíveis.
                2. Para cada `ID do Facet` que deseja usar, chame `list_eia_v2_routes` com `segment_path="<rota_base>/facet/<ID_do_Facet>"` (ex: "electricity/retail-sales/facet/stateid") para descobrir os `ID (valor do facet)` válidos.
            *   `frequency`: **Opcional.** O `ID (para query)` da frequência desejada (ex: `"M"` para mensal, `"A"` para anual). Consulte `list_eia_v2_routes` na rota base para frequências disponíveis.
            *   `start_period`, `end_period`: Formato YYYY-MM-DD, YYYY-MM ou YYYY dependendo da frequência.
            *   `sort_column`, `sort_direction`, `length`, `offset`: Para refinar a consulta e paginar.

    3.  **`get_eia_v2_series_id_data(series_id: str, ...)`**:
        *   **Uso:** Para compatibilidade com Series IDs da APIv1 (ex: "ELEC.SALES.CO-RES.A"). Permite usar IDs antigos diretamente.
        *   **Argumentos:** `series_id` (obrigatório) e outros parâmetros como `data_elements`, `frequency`, `start_period`, `end_period`.
        *   **Importante:** Use esta ferramenta *apenas* se o usuário fornecer explicitamente um Series ID no formato da APIv1. **Não tente inventar ou construir um Series ID.**

    **Fluxo Recomendado para o LLM (Exemplo: "Consumo de petróleo no Brasil este ano"):**
    1.  **Explorar Rotas Principais:** Chame `list_eia_v2_routes()` para ver categorias como "petroleum".
    2.  **Aprofundar na Categoria:** Se "petroleum" for uma rota, chame `list_eia_v2_routes(segment_path="petroleum")` para ver sub-rotas (ex: "supply", "consumption", "prices").
    3.  **Identificar Rota de Dados Relevante:** Suponha que encontre uma rota como "petroleum/consumption/monthly" (ou similar). Chame `list_eia_v2_routes(segment_path="petroleum/consumption/monthly")` para ver seus metadados:
        *   Anote os `ID do Facet` relevantes (ex: `countryRegionId`, `productId`).
        *   Anote o `ID da Coluna` para "consumo" (ex: `value`).
        *   Anote o `ID (para query)` para "anual" (ex: `A`) ou "mensal" (`M`) se quiser dados mais recentes e agregar.
    4.  **Listar Valores de Facet:** Para "Brasil", você precisa do ID de país/região. Se o facet `countryRegionId` foi encontrado:
        *   Chame `list_eia_v2_routes(segment_path="petroleum/consumption/monthly/facet/countryRegionId")`.
        *   Procure "Brasil" na lista de nomes e anote seu `ID (valor do facet)` (ex: `BRA`).
    5.  **Construir e Chamar `get_eia_v2_route_data`:**
        *   `route_path_with_data_segment`: "petroleum/consumption/monthly/data/" (ou a rota de dados correta que você encontrou)
        *   `data_elements`: `["value"]` (ou o ID da coluna de consumo)
        *   `facets`: `{"countryRegionId": "BRA"}` (e possivelmente `productId` se precisar especificar o tipo de petróleo)
        *   `frequency`: `"A"` (se quiser anual) ou `"M"` (para mensal e pegar o último ano).
        *   `start_period`/`end_period`: Defina para o ano corrente ou o mais recente. Ex: `start_period="2023"`, `end_period="2023"` para anual.
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
            "content": TextContent(type="text", text="Gostaria de obter dados da EIA. Por favor, siga o fluxo recomendado no 'Guia Rápido da API EIA v2' para descobrir a rota, os elementos de dados, os facets e os valores de facet necessários. Depois, use a ferramenta `get_eia_v2_route_data`.")
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
    print("Iniciando o servidor MCP da EIA (SSE)...")
    mcp.run(transport="sse")