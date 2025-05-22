# Usa uma imagem Python oficial como base
FROM python:3.10-slim-buster

# Define o diretório de trabalho dentro do contêiner
WORKDIR /app/server

# Copia os arquivos de requisitos e os instala
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código da aplicação
COPY eia_server.py .

# Define a variável de ambiente para a API Key. 
# EM PRODUÇÃO NO RENDER, VOCÊ CONFIGURARÁ ISSO DIRETAMENTE LÁ!
# Isso é apenas para testar localmente com Docker Compose sem usar .env global
# ENV EIA_API_KEY="SUA_CHAVE_AQUI" 

# Expõe a porta que o Uvicorn (interno ao FastMCP) vai escutar
EXPOSE 8000

# Comando para rodar o servidor FastMCP com transporte SSE
# Ele usará o host e a porta definidos no construtor do FastMCP
# e executará o servidor web (uvicorn) internamente.
CMD ["python", "eia_server.py"]