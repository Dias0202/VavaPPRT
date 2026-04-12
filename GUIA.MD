# ==============================================================================
# PROJETO: Bot de Telemetria Valorant para Discord
# ==============================================================================
# OBJETIVO:
# Desenvolver um sistema de extração, processamento heurístico e apresentação de 
# métricas avançadas (K/D, Taxa de Vitórias, ACS, Clutches retroativos) focado 
# em um grupo fechado de até 10 usuários. O artefato contorna a inacessibilidade 
# da API oficial implementando consumo vetorial da API HenrikDev.
#
# ARQUITETURA E ENGENHARIA DE DADOS:
# - Infraestrutura: Node de processamento assíncrono em Python 3.
# - Bibliotecas Base: discord.py (event loop) e aiohttp (requisições de rede).
# - Persistência/Cache: SQLite local, utilizado para controle de limite de taxa 
#   (Rate Limit de 30 req/min) aplicando a heurística de Time-To-Live (TTL).
# - Segurança: Gestão rigorosa de credenciais via dotenv, atendendo aos padrões 
#   necessários para evitar vazamentos de tokens e restrições da Riot Games.
#
# FLUXO DE USO NA CLI:
# Este Makefile providencia a automação do ciclo de vida da aplicação.
# O processo padrão de inicialização deve seguir a ordem:
# 1. make setup
# 2. Preenchimento manual do arquivo .env gerado
# 3. make db-init
# 4. make run
# ==============================================================================

# Variáveis de ambiente de compilação
VENV_DIR = venv
PYTHON = $(VENV_DIR)/bin/python
PIP = $(VENV_DIR)/bin/pip
MAIN_FILE = src/main.py
DB_FILE = data/bot_database.db

# Declaração de alvos que não representam arquivos físicos
.PHONY: help setup db-init run clean

# Alvo padrão executado quando o usuário digita apenas 'make'
help:
	@echo "Orquestrador de Processos - Bot Valorant"
	@echo "-----------------------------------------------------------------"
	@echo "Comandos disponíveis na CLI:"
	@echo "  make setup    - Instancia o Virtual Environment (venv) e instala dependências."
	@echo "  make db-init  - Estrutura o banco SQLite com as tabelas de cache e usuários."
	@echo "  make run      - Levanta o processo assíncrono do bot em modo de produção."
	@echo "  make clean    - Expulsa artefatos de compilação e exclui o ambiente virtual."
	@echo "-----------------------------------------------------------------"

# Setup inicial do ambiente isolado e injeção do arquivo de variáveis de ambiente
setup:
	@echo "[INFO] Iniciando provisionamento do ambiente isolado de Python..."
	python3 -m venv $(VENV_DIR)
	$(PIP) install --upgrade pip
	$(PIP) install discord.py python-dotenv aiohttp
	@echo "[INFO] Gerando template de segurança para variáveis de ambiente..."
	@if [ ! -f .env ]; then \
		echo "DISCORD_TOKEN=inserir_token_discord_aqui" > .env; \
		echo "HENRIKDEV_API_KEY=inserir_chave_basica_aqui" >> .env; \
		echo "[ALERTA] Arquivo .env criado na raiz. Interrompa a execução e preencha as chaves criptográficas."; \
	else \
		echo "[INFO] Arquivo .env já existe. Nenhuma ação sobrescrita."; \
	fi

# Criação relacional dos esquemas de banco de dados para o controle de cache
db-init:
	@echo "[INFO] Configurando motor local SQLite..."
	@mkdir -p data
	@mkdir -p src
	@$(PYTHON) -c "import sqlite3; \
	conn = sqlite3.connect('$(DB_FILE)'); \
	cursor = conn.cursor(); \
	cursor.execute('''CREATE TABLE IF NOT EXISTS users ( \
		discord_id TEXT PRIMARY KEY, \
		riot_id TEXT NOT NULL, \
		tagline TEXT NOT NULL, \
		allow_tracking INTEGER DEFAULT 1 \
	)'''); \
	cursor.execute('''CREATE TABLE IF NOT EXISTS cache_telemetry ( \
		riot_id TEXT PRIMARY KEY, \
		last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP, \
		payload_json TEXT NOT NULL \
	)'''); \
	conn.commit(); \
	conn.close();"
	@echo "[INFO] Estruturas relacionais persistidas em $(DB_FILE)."
	@if [ ! -f $(MAIN_FILE) ]; then \
		echo "import discord, os\nfrom dotenv import load_dotenv\n\nload_dotenv()\nprint('Bot em execução inicial.')" > $(MAIN_FILE); \
		echo "[INFO] Script base $(MAIN_FILE) gerado."; \
	fi

# Executa o loop de eventos principal
run:
	@echo "[INFO] Submetendo script $(MAIN_FILE) para o interpretador local..."
	$(PYTHON) $(MAIN_FILE)

# Limpeza da malha de diretórios (Cuidado: não exclui o SQLite por segurança)
clean:
	@echo "[INFO] Desalocando binários e arquivos temporários..."
	rm -rf $(VENV_DIR)
	rm -rf __pycache__
	rm -rf src/__pycache__
	@echo "[INFO] Limpeza do ecossistema concluída com sucesso."