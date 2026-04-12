import sys
import os

# Adiciona o diretório src ao path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from dotenv import load_dotenv
load_dotenv()

from database import init_db, set_config, get_config

def validate():
    print("🔍 Validando Banco de Dados (PostgreSQL)...")
    
    # 1. Testa inicialização
    try:
        init_db()
        print("✅ Conexão e inicialização ok.")
    except Exception as e:
        print(f"❌ Erro ao conectar/inicializar: {e}")
        print("\n💡 Dica: Verifique se a DATABASE_URL no seu .env ou no Render está correta.")
        print("A URL deve ser algo como: postgresql://usuario:senha@host:porta/database")
        return

    # 2. Testa Configurações
    try:
        set_config("test_key", "test_value")
        val = get_config("test_key")
        if val == "test_value":
            print("✅ Leitura/Escrita de config ok.")
        else:
            print(f"❌ Erro na config: esperado 'test_value', recebeu '{val}'")
    except Exception as e:
        print(f"❌ Erro durante teste de leitura/escrita: {e}")

    print("\n🚀 Validação concluída!")

if __name__ == '__main__':
    validate()
