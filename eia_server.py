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
import asyncio
from datetime import datetime

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
    "User-Agent": "US-Energy-Info-Admin-MCP-Server/2.1 (contact@example.com)",
    "Accept": "application/json",
    "Content-Type": "application/json"
}

PORT = int(os.getenv("PORT", 8000))

# --- Mapeamento expandido de conceitos ---
CONCEPT_MAPPING = {
    "electricity": {
        "keywords": ["eletricidade", "energia elétrica", "consumo energia", "geração energia", "preço energia", "electricity", "power", "grid"],
        "routes": ["electricity", "electricity/retail-sales", "electricity/electric-power-operational-data", "electricity/rto", "electricity/facility-fuel"]
    },
    "petroleum": {
        "keywords": ["petróleo", "gasolina", "diesel", "crude oil", "combustível", "refino", "petroleum", "oil", "gasoline", "refineries"],
        "routes": ["petroleum", "petroleum/crd/crpdn", "petroleum/supply/weekly", "petroleum/supply/historical", "petroleum/pri/spt", "petroleum/sum/sndw"]
    },
    "natural-gas": {
        "keywords": ["gás natural", "gas natural", "lng", "pipeline", "natural gas", "methane"],
        "routes": ["natural-gas", "natural-gas/prod", "natural-gas/cons", "natural-gas/pri", "natural-gas/stor"]
    },
    "coal": {
        "keywords": ["carvão", "coal", "mineração carvão", "carbon", "mining"],
        "routes": ["coal", "coal/production", "coal/consumption", "coal/reserves"]
    },
    "renewable": {
        "keywords": ["renovável", "solar", "eólica", "hidráulica", "biomassa", "renewable", "wind", "hydro", "geothermal"],
        "routes": ["electricity/electric-power-operational-data", "renewable"]
    },
    "nuclear": {
        "keywords": ["nuclear", "uranium", "reactor", "nuclear power"],
        "routes": ["nuclear", "nuclear/fuel-cycle"]
    },
    "total-energy": {
        "keywords": ["energia total", "consumo total", "balanço energético", "total energy", "energy balance"],
        "routes": ["total-energy", "total-energy/data"]
    },
    "international": {
        "keywords": ["internacional", "world", "global", "countries", "export", "import"],
        "routes": ["international"]
    }
}

# --- Cache simples para metadados ---
metadata_cache = {}
cache_ttl = 3600  # 1 hora

# --- Inicialização do Servidor MCP ---
mcp = FastMCP(
    name="eia-energy-data-v2",
    host="0.0.0.0",
    port=PORT,
)

# --- Funções Auxiliares Melhoradas ---
def format_eia_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Formata parâmetros para o formato correto da API EIA v2.
    Melhora o tratamento de arrays e objetos aninhados.
    """
    formatted_params = {}
    
    for key, value in params.items():
        # Pular valores None, strings vazias ou listas/dicts vazios
        if value is None or value == "" or (isinstance(value, (list, dict)) and not value):
            continue
            
        if key == "facets" and isinstance(value, dict):
            # Formatação especial para facets: facets[stateid][]=TX&facets[stateid][]=CA
            for facet_key, facet_values in value.items():
                if isinstance(facet_values, list):
                    for facet_value in facet_values:
                        param_key = f"facets[{facet_key}][]"
                        if param_key not in formatted_params:
                            formatted_params[param_key] = []
                        formatted_params[param_key].append(facet_value)
                else:
                    formatted_params[f"facets[{facet_key}][]"] = [facet_values]
        elif key == "data" and isinstance(value, list):
            # data[0]=value&data[1]=price
            for i, item in enumerate(value):
                formatted_params[f"data[{i}]"] = item
        elif key == "sort" and isinstance(value, list):
            # sort[0][column]=period&sort[0][direction]=desc
            for i, sort_item in enumerate(value):
                if isinstance(sort_item, dict):
                    for sort_key, sort_value in sort_item.items():
                        formatted_params[f"sort[{i}][{sort_key}]"] = sort_value
        elif isinstance(value, list) and key not in ["facets", "data", "sort"]:
            # CORREÇÃO: Verificar se a lista não está vazia antes do join
            if value:  # Só fazer join se a lista não estiver vazia
                formatted_params[key] = ",".join(map(str, value))
        else:
            formatted_params[key] = value
    
    return formatted_params

async def make_eia_api_request(route_path: str, params: Optional[Dict[str, Any]] = None, use_cache: bool = True) -> Optional[Dict[str, Any]]:
    """Faz requisição à API da EIA com cache e tratamento robusto de erros."""
    if not EIA_API_KEY:
        logger.error("EIA_API_KEY não está definida")
        return {"error": "API_KEY_MISSING", "message": "Chave da API EIA não configurada"}
    
    # Normalizar route_path
    route_path = route_path.strip('/')
    full_url = f"{EIA_API_BASE_URL}/{route_path}"
    
    if params is None:
        params = {}
    
    # Cache key para metadados (sem api_key para segurança)
    cache_key = f"{route_path}_{json.dumps(sorted(params.items()), sort_keys=True)}"
    
    # Verificar cache para metadados (não para dados)
    if use_cache and not route_path.endswith('/data') and cache_key in metadata_cache:
        cache_entry = metadata_cache[cache_key]
        if (datetime.now().timestamp() - cache_entry['timestamp']) < cache_ttl:
            logger.info(f"Retornando do cache: {route_path}")
            return cache_entry['data']
    
    # Formatar parâmetros corretamente
    formatted_params = format_eia_params(params)
    formatted_params['api_key'] = EIA_API_KEY
    
    # Log detalhado para debug
    temp_params = {k: v for k, v in formatted_params.items() if k != 'api_key'}
    logger.info(f"URL: {full_url}")
    logger.info(f"Parâmetros formatados: {json.dumps(temp_params, indent=2)}")
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                full_url, 
                params=formatted_params, 
                headers=EIA_HEADERS, 
                timeout=90.0  # Timeout aumentado
            )
            
            # Log da URL final (sem api_key)
            url_without_key = str(response.url).replace(f"api_key={EIA_API_KEY}", "api_key=***")
            logger.info(f"URL final: {url_without_key}")
            
            response.raise_for_status()
            result = response.json()
            
            # Cache para metadados
            if use_cache and not route_path.endswith('/data'):
                metadata_cache[cache_key] = {
                    'data': result,
                    'timestamp': datetime.now().timestamp()
                }
            
            return result
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Erro HTTP EIA API: {e.response.status_code}")
            logger.error(f"Response text: {e.response.text}")
            try:
                error_response = e.response.json()
                logger.error(f"Error details: {json.dumps(error_response, indent=2)}")
                return error_response
            except Exception:
                return {
                    "error": f"HTTPStatusError: {e.response.status_code}", 
                    "message": e.response.text,
                    "url": str(e.response.url).replace(f"api_key={EIA_API_KEY}", "api_key=***")
                }
        except httpx.RequestError as e:
            logger.error(f"Erro de requisição EIA API: {e}")
            return {"error": "RequestError", "message": str(e)}
        except Exception as e:
            logger.error(f"Erro inesperado EIA API: {e}")
            return {"error": "UnexpectedError", "message": str(e)}

def find_relevant_routes(query: str) -> List[str]:
    """Encontra rotas relevantes baseadas na consulta do usuário com scoring."""
    query_lower = query.lower()
    route_scores = {}
    
    for concept, data in CONCEPT_MAPPING.items():
        score = 0
        for keyword in data["keywords"]:
            if keyword.lower() in query_lower:
                # Scoring baseado na especificidade e frequência
                score += len(keyword) * query_lower.count(keyword.lower())
        
        if score > 0:
            for route in data["routes"]:
                if route not in route_scores:
                    route_scores[route] = 0
                route_scores[route] += score
    
    # Retornar rotas ordenadas por score
    return [route for route, _ in sorted(route_scores.items(), key=lambda x: x[1], reverse=True)]

def format_data_table(data: List[Dict], max_rows: int = 50) -> List[str]:
    """Formata dados em tabela markdown com limite de linhas."""
    if not data:
        return ["Nenhum dado encontrado."]
    
    output_lines = []
    columns = list(data[0].keys())
    
    # Cabeçalho da tabela
    header_line = "| " + " | ".join(columns) + " |"
    separator_line = "|" + "---|".join(["---"] * len(columns)) + "|"
    output_lines.extend([header_line, separator_line])
    
    # Dados da tabela (limitado)
    display_data = data[:max_rows]
    for row in display_data:
        row_values = []
        for col in columns:
            value = row.get(col, 'N/A')
            if isinstance(value, str):
                try:
                    # Tenta converter para float se houver ponto decimal, senão para int
                    if '.' in value:
                        converted_value = float(value)
                    else:
                        converted_value = int(value)
                    # Se a conversão for bem-sucedida, use o valor convertido
                    value = converted_value
                except ValueError:
                    # Se não puder converter para número, mantém como string
                    pass
            # Formatação especial para números
            if isinstance(value, (int, float)):
                if abs(value) >= 1000:
                    formatted_value = f"{value:,.2f}" if isinstance(value, float) else f"{value:,}"
                else:
                    formatted_value = str(value) # Mantém números menores como string simples
            else:
                formatted_value = str(value) # Para valores não numéricos (como texto)

            row_values.append(formatted_value)
        output_lines.append("| " + " | ".join(row_values) + " |")
    
    if len(data) > max_rows:
        output_lines.append(f"\n*Mostrando {max_rows} de {len(data)} registros*")
    
    return output_lines

# --- Ferramentas Principais Melhoradas ---
@mcp.tool()
async def search_energy_data(
    query: str,
    specific_route: Optional[str] = None,
    data_elements: Optional[List[str]] = None,
    facets: Optional[Dict[str, Union[str, List[str]]]] = None,
    frequency: Optional[str] = None,
    start_period: Optional[str] = None,
    end_period: Optional[str] = None,
    limit: int = 100,
    sort_column: Optional[str] = "period",
    sort_direction: Optional[str] = "desc"
) -> CallToolResult:
    """
    Busca dados de energia da EIA de forma inteligente e otimizada.
    
    Esta ferramenta implementa um fluxo inteligente:
    1. Descoberta automática de rotas baseada na consulta
    2. Exploração de metadados quando necessário
    3. Recuperação de dados reais com parâmetros completos
    
    Args:
        query: Descrição natural do que você procura (ex: "consumo de eletricidade residencial no Texas em 2023")
        specific_route: Rota específica se conhecida (ex: "electricity/retail-sales")
        data_elements: Elementos de dados específicos (ex: ["value", "price"])
        facets: Filtros como dicionário (ex: {"stateid": ["TX"], "sectorid": ["RES"]})
        frequency: Frequência dos dados (ex: "monthly", "annual", "quarterly")
        start_period: Período inicial (ex: "2020", "2020-01")
        end_period: Período final (ex: "2023", "2023-12")
        limit: Número máximo de registros (padrão: 100, máximo: 5000)
        sort_column: Coluna para ordenação (padrão: "period")
        sort_direction: Direção da ordenação ("asc" ou "desc", padrão: "desc")
    """
    
    try:
        # Validação de entrada
        if limit > 5000:
            limit = 5000
        
        # Fase 1: Descoberta de rotas se não especificada
        if not specific_route:
            relevant_routes = find_relevant_routes(query)
            if not relevant_routes:
                # Listar categorias principais
                response = await make_eia_api_request("", {})
                if response and not response.get('error'):
                    routes_info = []
                    routes_data = response.get('response', {}).get('routes', [])
                    for route in routes_data:
                        route_id = route.get('id', 'N/A')
                        route_name = route.get('name', 'N/A')
                        route_desc = route.get('description', '')
                        routes_info.append(f"**{route_id}**: {route_name}")
                        if route_desc:
                            routes_info.append(f"  ↳ {route_desc}")
                    
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"""
🔍 **Busca por**: "{query}"

Não encontrei rotas específicas para sua consulta. Aqui estão as categorias principais disponíveis:

{chr(10).join(routes_info)}

💡 **Dicas para refinar sua busca:**
- Use termos específicos como: "eletricidade", "petróleo", "gás natural", "carvão", "solar"
- Especifique localização: "Texas", "Califórnia", "região sudeste"
- Mencione tipo de dados: "consumo", "produção", "preços"
- Indique período: "2023", "últimos 5 anos"

**Exemplo**: "consumo de eletricidade residencial no Texas em 2023"
                        """)]
                    )
            
            # Usar a rota com melhor score
            specific_route = relevant_routes[0]
            logger.info(f"Rota descoberta automaticamente: {specific_route} (de {len(relevant_routes)} opções)")
        
        # Fase 2: Exploração de metadados
        metadata_response = await make_eia_api_request(specific_route, {})
        
        if not metadata_response or metadata_response.get("error"):
            error_msg = metadata_response.get('message', 'Erro desconhecido') if metadata_response else 'Sem resposta'
            return CallToolResult(
                is_error=True,
                content=[TextContent(type="text", text=f"❌ Erro ao acessar rota '{specific_route}': {error_msg}")]
            )
        
        response_content = metadata_response.get('response', metadata_response)
        
        # Se há sub-rotas, listá-las
        if response_content.get('routes'):
            subroutes_info = []
            for subroute in response_content['routes'][:20]:  # Limitar para não sobrecarregar
                subroute_id = subroute.get('id', 'N/A')
                subroute_name = subroute.get('name', 'N/A')
                subroute_desc = subroute.get('description', '')
                
                subroutes_info.append(f"**{subroute_id}**: {subroute_name}")
                if subroute_desc:
                    subroutes_info.append(f"  ↳ {subroute_desc}")
            
            total_subroutes = len(response_content['routes'])
            if total_subroutes > 20:
                subroutes_info.append(f"\n*... e mais {total_subroutes - 20} sub-rotas*")
            
            return CallToolResult(
                content=[TextContent(type="text", text=f"""
📂 **Rota**: `{specific_route}`
📊 **Sub-rotas disponíveis** ({total_subroutes} total):

{chr(10).join(subroutes_info)}

🎯 **Para obter dados**, escolha uma sub-rota específica e chame novamente:
specific_route: "rota-escolhida"
                """)]
            )
        
        # --- INÍCIO DA LÓGICA DE TRATAMENTO DE ELEMENTOS DE DADOS ---

        # Obter os elementos de dados disponíveis para esta rota a partir dos metadados
        available_data_elements_meta = response_content.get('data', {})
        
        # Variável para armazenar os elementos de dados que realmente serão buscados
        elements_to_fetch = data_elements # Começa com o que o usuário forneceu (pode ser None)
        
        # Flag para indicar se 'value' foi assumido por padrão
        assumed_value_default = False

        # Cenário: Usuário NÃO especificou 'data_elements'
        if not elements_to_fetch:
            if available_data_elements_meta: # Se os metadados listam elementos de dados explicitamente
                # Sub-cenário A: Há elementos explícitos, mas o usuário não escolheu.
                # Exibe os metadados e pede para o usuário especificar.
                metadata_info = [f"📋 **Metadados para**: `{specific_route}`\n"]
                
                if response_content.get('name'):
                    metadata_info.append(f"**Nome**: {response_content['name']}")
                if response_content.get('description'):
                    metadata_info.append(f"**Descrição**: {response_content['description']}")
                
                # Elementos de dados disponíveis (populados a partir de available_data_elements_meta)
                if available_data_elements_meta:
                    metadata_info.append("\n📊 **Elementos de dados disponíveis**:")
                    for col_id, col_info in list(available_data_elements_meta.items())[:10]:  # Limitar
                        if isinstance(col_info, dict):
                            name = col_info.get('name', col_info.get('alias', col_id))
                            units = col_info.get('units', 'N/A')
                            metadata_info.append(f"  • `{col_id}`: {name} ({units})")
                    
                    if len(available_data_elements_meta) > 10:
                        metadata_info.append(f"  *... e mais {len(available_data_elements_meta) - 10} elementos*")
                
                # Filtros/facets disponíveis
                facets_meta = response_content.get('facets', [])
                if facets_meta:
                    metadata_info.append("\n🔍 **Filtros disponíveis**:")
                    for facet in facets_meta[:8]:  # Limitar
                        facet_id = facet.get('id', 'N/A')
                        facet_name = facet.get('name', 'N/A')
                        metadata_info.append(f"  • `{facet_id}`: {facet_name}")
                    
                    if len(facets_meta) > 8:
                        metadata_info.append(f"  *... e mais {len(facets_meta) - 8} filtros*")
                
                # Frequências disponíveis
                frequencies = response_content.get('frequency', [])
                if frequencies:
                    freq_list = []
                    for freq in frequencies:
                        freq_id = freq.get('id', freq.get('query', 'N/A'))
                        freq_desc = freq.get('description', freq.get('name', ''))
                        freq_list.append(f"`{freq_id}`" + (f" ({freq_desc})" if freq_desc else ""))
                    metadata_info.append(f"\n📅 **Frequências**: {', '.join(freq_list)}")
                
                metadata_info.append(f"""
🎯 **Para obter dados reais**, chame novamente especificando:
data_elements: ["value"] # ou outros elementos disponíveis
facets: {{"filtro": ["valor"]}} # opcional
frequency: "monthly" # opcional
start_period: "2020" # opcional
end_period: "2023" # opcional
                """)
                
                return CallToolResult(
                    content=[TextContent(type="text", text="\n".join(metadata_info))]
                )
            
            else: # Sub-cenário B: Metadados 'data' está vazio (como em petroleum/crd/crpdn) E usuário não especificou.
                  # Assume 'value' e prossegue.
                elements_to_fetch = ["value"]
                assumed_value_default = True # Seta a flag para adicionar nota no final
        
        # Cenário: Usuário ESPECIFICOU 'data_elements' (ou elements_to_fetch foi setado para ['value'] por padrão)
        # Se elements_to_fetch foi fornecido pelo usuário E os metadados NÃO estavam vazios, então validamos
        elif data_elements and available_data_elements_meta:
            for de in data_elements:
                if de not in available_data_elements_meta:
                    return CallToolResult(
                        is_error=True,
                        content=[TextContent(type="text", text=f"❌ O elemento de dados '{de}' não está disponível para a rota '{specific_route}'. Elementos disponíveis: {', '.join(available_data_elements_meta.keys())}.")]
                    )
        # Se elements_to_fetch foi fornecido pelo usuário E os metadados estavam vazios,
        # simplesmente prosseguimos sem validação estrita, pois a API da EIA pode ter campos implícitos.

        # --- FIM DA LÓGICA DE TRATAMENTO DE ELEMENTOS DE DADOS ---

        # Fase 4: Recuperar dados reais
        data_route = f"{specific_route.rstrip('/')}/data"
        params = {
            "length": min(limit, 5000),
            "offset": 0
        }
        
        # Adicionar parâmetros
        if elements_to_fetch: # Usa os elementos determinados, seja pelo usuário ou por padrão
            params["data"] = elements_to_fetch
        if frequency:
            params["frequency"] = frequency
        if start_period:
            params["start"] = start_period
        if end_period:
            params["end"] = end_period
        if facets and any(facets.values()):
            params["facets"] = facets
        if sort_column:
            params["sort"] = [{"column": sort_column, "direction": sort_direction}]
        
        logger.info(f"Requisitando dados de: {data_route}")
        data_response = await make_eia_api_request(data_route, params, use_cache=False)
        
        if not data_response:
            return CallToolResult(
                is_error=True, 
                content=[TextContent(type="text", text=f"❌ Falha na requisição para '{data_route}' - sem resposta")]
            )
        
        if data_response.get("error"):
            error_details = []
            error_details.append(f"❌ **Erro ao recuperar dados**: {data_response.get('message', 'Erro desconhecido')}")
            
            if data_response.get("data"):
                error_details.append(f"**Detalhes**: {data_response.get('data')}")
            
            # Sugestões baseadas no erro
            error_msg = str(data_response.get('message', '')).lower()
            if 'facet' in error_msg:
                error_details.append("\n💡 **Dica**: Verifique os filtros (facets) disponíveis usando a ferramenta `get_facet_values()`")
            elif 'frequency' in error_msg:
                error_details.append("\n💡 **Dica**: Verifique as frequências disponíveis nos metadados")
            elif 'data' in error_msg:
                error_details.append("\n💡 **Dica**: Verifique os elementos de dados disponíveis nos metadados (se houver, chame `search_energy_data` sem `data_elements`)")
            elif 'cannot specify' in error_msg and 'with' in error_msg:
                 error_details.append("\n💡 **Dica**: Este erro incomum pode indicar que o elemento de dados solicitado não é compatível, ou que o formato da sua requisição tem um problema sutil não aparente. Verifique a documentação oficial da EIA para esta rota.")
            
            return CallToolResult(
                is_error=True, 
                content=[TextContent(type="text", text="\n".join(error_details))]
            )
        
        response_data = data_response.get('response', {})
        actual_data = response_data.get('data', [])
        
        if not actual_data:
            suggestion_text = f"""
❌ **Nenhum dado encontrado** para os critérios especificados.

**Parâmetros utilizados**:
- Rota: `{data_route}`
- Elementos: `{elements_to_fetch}`
- Filtros: `{facets}`
- Frequência: `{frequency}`
- Período: `{start_period}` até `{end_period}`

💡 **Sugestões**:
1. Tente ampliar o período de tempo
2. Remova alguns filtros específicos
3. Verifique se os valores dos filtros estão corretos
4. Use a ferramenta `get_facet_values()` para ver opções válidas
            """
            return CallToolResult(
                content=[TextContent(type="text", text=suggestion_text)]
            )
        
        # Formatação aprimorada dos resultados
        total_records = response_data.get('total')
        # Adicione este bloco para garantir que total_records é um int
        if total_records is not None:
            try:
                total_records = int(total_records)
            except ValueError:
                logger.warning(f"Total records received as non-integer: {total_records}. Falling back to len(actual_data).")
                total_records = len(actual_data)
        else:
            total_records = len(actual_data)
        
        output_lines = [
            f"📊 **Dados de Energia**: {response_content.get('name', specific_route)}",
            f"🔍 **Consulta**: {query}",
            f"📈 **Total de registros**: {total_records:,} (mostrando {len(actual_data):,})",
        ]
        
        # Adicionar nota se 'value' foi assumido por padrão
        if assumed_value_default:
            output_lines.insert(2, f"💡 **Nota**: `data_elements` não foi especificado, e os metadados não listam elementos de dados explícitos. Assumindo `data_elements=['value']` por padrão.")

        # Adicionar informações sobre parâmetros usados
        if facets:
            facet_info = []
            for k, v in facets.items():
                if isinstance(v, list):
                    facet_info.append(f"{k}: {', '.join(map(str, v))}")
                else:
                    facet_info.append(f"{k}: {v}")
            output_lines.append(f"🔍 **Filtros aplicados**: {'; '.join(facet_info)}")
        
        if frequency:
            output_lines.append(f"📅 **Frequência**: {frequency}")
        if start_period or end_period:
            period_info = f"{start_period or 'início'} até {end_period or 'fim'}"
            output_lines.append(f"📆 **Período**: {period_info}")
        
        output_lines.append("")  # Linha em branco
        
        # Tabela de dados
        output_lines.extend(format_data_table(actual_data, max_rows=50))
        
        # Informações adicionais
        if response_content.get('description'):
            output_lines.append(f"\n📝 **Sobre os dados**: {response_content['description']}")
        
        if total_records > len(actual_data):
            output_lines.append(f"\n⚠️ **Dados paginados**: Use `limit` maior ou implemente paginação para ver todos os {total_records:,} registros")
        
        return CallToolResult(
            content=[TextContent(type="text", text="\n".join(output_lines))]
        )
    
    except Exception as e:
        logger.error(f"Erro inesperado em search_energy_data: {e}")
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=f"❌ Erro inesperado: {str(e)}")]
        )

@mcp.tool()
async def get_facet_values(route: str, facet_id: str, limit: int = 100) -> CallToolResult:
    """
    Obtém os valores disponíveis para um filtro específico.
    
    Args:
        route: Rota da EIA (ex: "electricity/retail-sales")
        facet_id: ID do filtro (ex: "stateid", "sectorid")
        limit: Limite de valores retornados (padrão: 100)
    """
    try:
        facet_route = f"{route.rstrip('/')}/facet/{facet_id}"
        
        response = await make_eia_api_request(facet_route, {"length": limit})
        
        if not response or response.get("error"):
            error_msg = response.get('message', 'Erro desconhecido') if response else 'Sem resposta'
            return CallToolResult(
                is_error=True,
                content=[TextContent(type="text", text=f"❌ Erro ao obter valores do filtro '{facet_id}' na rota '{route}': {error_msg}")]
            )
        
        response_content = response.get('response', response)
        facet_values = response_content.get('facets', [])
        
        if not facet_values:
            return CallToolResult(
                content=[TextContent(type="text", text=f"❌ Nenhum valor encontrado para o filtro '{facet_id}' na rota '{route}'.")]
            )
        
        total_facets = response_content.get('totalFacets', len(facet_values))
        
        output_lines = [
            f"🔍 **Valores disponíveis para o filtro `{facet_id}`**",
            f"📂 **Rota**: `{route}`",
            f"📊 **Total**: {total_facets:,} valores (mostrando {len(facet_values):,})",
            ""
        ]
        
        # Agrupar valores por categoria se possível
        values_info = []
        for value in facet_values:
            value_id = value.get('id', 'N/A')
            value_name = value.get('name', 'N/A')
            alias = value.get('alias', '')
            
            line = f"• **{value_id}**: {value_name}"
            if alias and alias != value_name and alias != value_id:
                line += f" _{alias}_"
            values_info.append(line)
        
        output_lines.extend(values_info)
        
        if total_facets > len(facet_values):
            output_lines.append(f"\n⚠️ **Mostrando apenas {len(facet_values)} de {total_facets} valores**. Use `limit` maior para ver mais.")
        
        # Exemplo de uso
        output_lines.append(f"""
💡 **Exemplo de uso**:
```
facets: {{"{facet_id}": ["{facet_values[0].get('id', 'VALUE')}"]}}
```
        """)
        
        return CallToolResult(
            content=[TextContent(type="text", text="\n".join(output_lines))]
        )
    
    except Exception as e:
        logger.error(f"Erro em get_facet_values: {e}")
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=f"❌ Erro inesperado: {str(e)}")]
        )

@mcp.tool()
async def get_series_data(series_id: str, start: Optional[str] = None, end: Optional[str] = None, limit: int = 1000) -> CallToolResult:
    """
    Obtém dados de uma série específica da EIA usando o ID da série.
    
    Args:
        series_id: ID da série (ex: "ELEC.GEN.ALL-US-99.M")
        start: Data de início (ex: "2020-01", "2020")
        end: Data de fim (ex: "2023-12", "2023")
        limit: Número máximo de registros (padrão: 1000)
    """
    try:
        series_route = f"seriesid/{series_id}"
        params = {"length": min(limit, 5000)}
        
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        
        response = await make_eia_api_request(series_route, params, use_cache=False)
        
        if not response or response.get("error"):
            error_msg = response.get('message', 'Erro desconhecido') if response else 'Sem resposta'
            return CallToolResult(
                is_error=True,
                content=[TextContent(type="text", text=f"❌ Erro ao obter dados da série '{series_id}': {error_msg}")]
            )
        
        response_content = response.get('response', response)
        series_data = response_content.get('data', [])
        
        if not series_data:
            return CallToolResult(
                content=[TextContent(type="text", text=f"❌ Nenhum dado encontrado para a série '{series_id}' no período especificado.")]
            )
        
        # Obter metadados da série
        series_info = series_data[0] if series_data else {}
        series_name = series_info.get('name', series_id)
        series_description = series_info.get('description', '')
        series_units = series_info.get('units', 'N/A')
        data_points = series_info.get('data', [])
        
        output_lines = [
            f"📈 **Série**: {series_name}",
            f"🆔 **ID**: `{series_id}`",
            f"📊 **Pontos de dados**: {len(data_points):,}",
            f"📏 **Unidade**: {series_units}",
        ]
        
        if series_description:
            output_lines.append(f"📝 **Descrição**: {series_description}")
        
        if start or end:
            period_info = f"{start or 'início'} até {end or 'fim'}"
            output_lines.append(f"📆 **Período**: {period_info}")
        
        output_lines.append("")  # Linha em branco
        
        if data_points:
            # Formatar dados como tabela
            formatted_data = []
            for point in data_points:
                if len(point) >= 2:
                    period = point[0]
                    value = point[1]
                    # Formatação especial para valores numéricos
                    if isinstance(value, (int, float)) and abs(value) >= 1000:
                        formatted_value = f"{value:,.2f}" if isinstance(value, float) else f"{value:,}"
                    else:
                        formatted_value = str(value)
                    formatted_data.append({"Período": period, "Valor": formatted_value, "Unidade": series_units})
            
            # Mostrar tabela
            if formatted_data:
                output_lines.extend(format_data_table(formatted_data[:50]))
                
                if len(data_points) > 50:
                    output_lines.append(f"\n*Mostrando 50 de {len(data_points)} registros*")
        
        return CallToolResult(
            content=[TextContent(type="text", text="\n".join(output_lines))]
        )
    
    except Exception as e:
        logger.error(f"Erro em get_series_data: {e}")
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=f"❌ Erro inesperado: {str(e)}")]
        )

@mcp.tool()
async def discover_energy_routes(category: Optional[str] = None) -> CallToolResult:
    """
    Descobre e lista todas as rotas disponíveis na API da EIA, opcionalmente filtradas por categoria.
    
    Args:
        category: Categoria para filtrar (ex: "electricity", "petroleum", "natural-gas")
    """
    try:
        response = await make_eia_api_request("", {})
        
        if not response or response.get("error"):
            error_msg = response.get('message', 'Erro desconhecido') if response else 'Sem resposta'
            return CallToolResult(
                is_error=True,
                content=[TextContent(type="text", text=f"❌ Erro ao descobrir rotas: {error_msg}")]
            )
        
        routes_data = response.get('response', {}).get('routes', [])
        
        if not routes_data:
            return CallToolResult(
                content=[TextContent(type="text", text="❌ Nenhuma rota encontrada.")]
            )
        
        # Filtrar por categoria se especificada
        if category:
            filtered_routes = [r for r in routes_data if category.lower() in r.get('id', '').lower()]
            if not filtered_routes:
                available_categories = list(set([r.get('id', '').split('/')[0] for r in routes_data if '/' not in r.get('id', '')]))
                return CallToolResult(
                    content=[TextContent(type="text", text=f"❌ Categoria '{category}' não encontrada.\n\n📂 **Categorias disponíveis**: {', '.join(sorted(available_categories))}")]
                )
            routes_data = filtered_routes
        
        output_lines = [
            f"🗂️ **Rotas da API EIA v2**" + (f" - Categoria: {category}" if category else ""),
            f"📊 **Total**: {len(routes_data)} rotas",
            ""
        ]
        
        # Agrupar rotas por categoria principal
        categories = {}
        for route in routes_data:
            route_id = route.get('id', 'N/A')
            route_name = route.get('name', 'N/A')
            route_desc = route.get('description', '')
            
            # Determinar categoria principal
            main_category = route_id.split('/')[0] if '/' in route_id else route_id
            
            if main_category not in categories:
                categories[main_category] = []
            
            route_info = f"  • **{route_id}**: {route_name}"
            if route_desc:
                route_info += f"\n    ↳ _{route_desc}_"
            
            categories[main_category].append(route_info)
        
        # Mostrar categorias organizadas
        for cat_name, cat_routes in sorted(categories.items()):
            output_lines.append(f"## 📁 {cat_name.upper()}")
            output_lines.extend(cat_routes[:10])  # Limitar para não sobrecarregar
            
            if len(cat_routes) > 10:
                output_lines.append(f"    *... e mais {len(cat_routes) - 10} rotas*")
            
            output_lines.append("")  # Linha em branco entre categorias
        
        output_lines.append("""
💡 **Próximos passos**:
1. Use `search_energy_data()` com uma rota específica
2. Use `get_facet_values()` para ver filtros disponíveis
3. Use `get_series_data()` se tiver um ID de série específico

**Exemplo**: `search_energy_data(specific_route="electricity/retail-sales")`
        """)
        
        return CallToolResult(
            content=[TextContent(type="text", text="\n".join(output_lines))]
        )
    
    except Exception as e:
        logger.error(f"Erro em discover_energy_routes: {e}")
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=f"❌ Erro inesperado: {str(e)}")]
        )

# --- Recursos (Resources) ---
@mcp.resource("eia://energy-concepts")
async def get_energy_concepts() -> Resource:
    """Retorna informações sobre conceitos energéticos e mapeamento de palavras-chave."""
    concepts_text = """# Conceitos Energéticos - EIA

## Mapeamento de Conceitos

### Eletricidade
- **Palavras-chave**: eletricidade, energia elétrica, consumo energia, geração energia, preço energia, electricity, power, grid
- **Rotas principais**: electricity, electricity/retail-sales, electricity/electric-power-operational-data

### Petróleo
- **Palavras-chave**: petróleo, gasolina, diesel, crude oil, combustível, refino, petroleum, oil, gasoline, refineries
- **Rotas principais**: petroleum, petroleum/crd/crpdn, petroleum/supply/weekly

### Gás Natural
- **Palavras-chave**: gás natural, gas natural, lng, pipeline, natural gas, methane
- **Rotas principais**: natural-gas, natural-gas/prod, natural-gas/cons

### Carvão
- **Palavras-chave**: carvão, coal, mineração carvão, carbon, mining
- **Rotas principais**: coal, coal/production, coal/consumption

### Energias Renováveis
- **Palavras-chave**: renovável, solar, eólica, hidráulica, biomassa, renewable, wind, hydro, geothermal
- **Rotas principais**: electricity/electric-power-operational-data, renewable

### Nuclear
- **Palavras-chave**: nuclear, uranium, reactor, nuclear power
- **Rotas principais**: nuclear, nuclear/fuel-cycle

### Energia Total
- **Palavras-chave**: energia total, consumo total, balanço energético, total energy, energy balance
- **Rotas principais**: total-energy, total-energy/data

### Internacional
- **Palavras-chave**: internacional, world, global, countries, export, import
- **Rotas principais**: international
"""
    
    return Resource(
        uri="eia://energy-concepts",
        name="Conceitos Energéticos EIA",
        description="Mapeamento de conceitos energéticos e palavras-chave para descoberta automática de rotas",
        mimeType="text/markdown",
        text=concepts_text
    )

# --- Prompts ---
@mcp.prompt()
async def energy_analysis_prompt(
    topic: str,
    geographic_scope: str = "US",
    time_period: str = "recent",
    analysis_type: str = "overview"
) -> GetPromptResult:
    """
    Gera um prompt estruturado para análise de dados energéticos.
    
    Args:
        topic: Tópico energético (ex: "electricity consumption", "oil prices")
        geographic_scope: Escopo geográfico (ex: "US", "Texas", "California", "regional")
        time_period: Período temporal (ex: "recent", "2020-2023", "historical")
        analysis_type: Tipo de análise (ex: "overview", "trends", "comparison", "forecast")
    """
    
    prompt_text = f"""# Análise de Dados Energéticos - {topic.title()}

## Contexto da Análise
- **Tópico**: {topic}
- **Escopo Geográfico**: {geographic_scope}
- **Período**: {time_period}
- **Tipo de Análise**: {analysis_type}

## Objetivos da Análise
1. Identificar tendências principais nos dados de {topic}
2. Analisar padrões sazonais ou cíclicos
3. Comparar diferentes regiões/setores quando aplicável
4. Identificar fatores que influenciam as variações
5. Fornecer insights acionáveis

## Passos Recomendados
1. **Descoberta de Dados**: Use `search_energy_data()` para encontrar dados relevantes sobre {topic}
2. **Exploração**: Examine metadados e filtros disponíveis
3. **Coleta**: Obtenha dados específicos com parâmetros adequados
4. **Análise**: Identifique padrões, tendências e anomalias
5. **Interpretação**: Contextualize os resultados com fatores externos

## Considerações Especiais
- Atenção a unidades de medida e conversões
- Verificação de dados sazonalmente ajustados vs. não ajustados
- Comparação com benchmarks históricos
- Impacto de eventos externos (crises, políticas, clima)

## Formato de Resultado Esperado
- Resumo executivo dos principais achados
- Visualizações ou tabelas dos dados chave
- Análise de tendências com explicações
- Recomendações ou insights para tomada de decisão
"""
    
    return GetPromptResult(
        name=f"energy_analysis_{topic.replace(' ', '_')}",
        description=f"Prompt estruturado para análise de {topic}",
        messages=[
            {"role": "user", "content": {"type": "text", "text": prompt_text}}
        ]
    )

# --- Execução do Servidor ---
if __name__ == "__main__":
    logger.info(f"🚀 Iniciando EIA Energy Data MCP Server v2.1 na porta {PORT}")
    logger.info(f"🔑 API Key configurada: {'✅' if EIA_API_KEY else '❌'}")
    logger.info(f"📊 Conceitos mapeados: {len(CONCEPT_MAPPING)}")
    
    try:
        mcp.run(transport="sse")
    except KeyboardInterrupt:
        logger.info("🛑 Servidor interrompido pelo usuário")
    except Exception as e:
        logger.error(f"❌ Erro fatal: {e}")
        sys.exit(1)