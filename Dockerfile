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

# Expõe a porta que o Uvicorn vai escutar
EXPOSE 8000

# Comando para rodar o servidor Uvicorn
# --host 0.0.0.0 para escutar em todas as interfaces, acessível de fora do contêiner
# --port 8000 para a porta exposta
# app:app se refere à instância 'app' dentro do módulo 'app.py' (aqui 'eia_server:app')
CMD ["uvicorn", "eia_server:app", "--host", "0.0.0.0", "--port", "8000"]