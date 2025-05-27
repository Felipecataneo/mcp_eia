
# EIA Energy Data MCP Server

Este projeto implementa um servidor baseado no protocolo **Model Context Protocol (MCP)** para integração com a API da **U.S. Energy Information Administration (EIA)**. Ele permite buscas inteligentes de dados energéticos dos Estados Unidos usando linguagem natural.

## ✨ Funcionalidades

- Integração com a API pública da EIA v2
- Mapeamento inteligente de palavras-chave para rotas da API
- Cache local para metadados com TTL configurável
- Formatação de parâmetros complexos da API (ex: facets, sort, data)
- Interface compatível com agentes MCP
- Retorno formatado como tabela Markdown
- Logging detalhado para depuração

## 🔧 Instalação

1. Clone o repositório:

```bash
git clone https://github.com/seu-usuario/eia-energy-mcp.git
cd eia-energy-mcp
```

2. Instale as dependências:

```bash
pip install -r requirements.txt
```

3. Crie um arquivo `.env` com sua chave da API EIA:

```env
EIA_API_KEY=your_api_key_here
PORT=8000
```

## 🚀 Executando

Execute o servidor MCP com:

```bash
python seu_arquivo.py
```

O servidor estará disponível em `http://localhost:8000`.

## 🧠 Como funciona

O agente `search_energy_data`:

- Analisa a consulta em linguagem natural
- Mapeia para as rotas da EIA mais prováveis
- Busca metadados e dados reais
- Apresenta os resultados em tabela formatada

Exemplo de uso com MCP Agent:

```python
await search_energy_data(
    query="consumo de eletricidade residencial no Texas em 2023",
    facets={"stateid": ["TX"], "sectorid": ["RES"]},
    frequency="monthly",
    start_period="2023-01",
    end_period="2023-12"
)
```

## 🧩 Estrutura do Projeto

- `FastMCP`: Servidor MCP para interação com LLMs
- `make_eia_api_request`: Função assíncrona para chamada da API EIA
- `format_eia_params`: Conversor de parâmetros complexos para formato esperado pela API
- `find_relevant_routes`: Mapeia a consulta para as rotas corretas da EIA

## 📦 Dependências principais

- `httpx`
- `python-dotenv`
- `mcp` (Model Context Protocol)
- `asyncio`
- `logging`

## 📄 Licença

Este projeto está sob a licença MIT.

---

Desenvolvido com 💡 para facilitar o acesso a dados energéticos da EIA via agentes MCP.
