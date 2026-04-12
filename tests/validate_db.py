import sys
import os
import sqlite3

# Adiciona o diretório src ao path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from database import init_db, set_config, get_config, DB_FILE

def validate():
    print("🔍 Validando Banco de Dados...")
    
    # 1. Testa inicialização
    init_db()
    if os.path.exists(DB_FILE):
        print("✅ Arquivo de banco de dados existe.")
    else:
        print("❌ Erro: Banco de dados não foi criado.")
        return

    # 2. Testa integridade das tabelas
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    tables = ["users", "matches", "player_match_stats", "bot_config", "cache_telemetry"]
    for table in tables:
        cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}';")
        if cursor.fetchone():
            print(f"✅ Tabela '{table}' ok.")
        else:
            print(f"❌ Tabela '{table}' faltando!")
    
    # 3. Testa Configurações
    set_config("test_key", "test_value")
    val = get_config("test_key")
    if val == "test_value":
        print("✅ Leitura/Escrita de config ok.")
    else:
        print(f"❌ Erro na config: esperado 'test_value', recebeu '{val}'")

    conn.close()
    print("\n🚀 Validação concluída com sucesso!")

if __name__ == '__main__':
    validate()
