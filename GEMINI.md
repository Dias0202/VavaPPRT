# VavaPPRT - Bot de Telemetria Valorant

Projeto de bot para Discord que extrai e processa métricas avançadas de Valorant.

## Como rodar localmente
1. Instale as dependências: \pip install -r requirements.txt\ 
2. Crie um arquivo \.env\ baseado no \.env.template\ e insira seus tokens.
3. Execute o bot: \python src/main.py\ 

## Comandos
- \!registrar RiotID#Tag\: Vincula seu Discord ao seu ID do Valorant.
- \!stats\: Mostra estatísticas da sua última partida registrada.
- \!ping\: Verifica se o bot está online.

## Arquitetura
- **Python 3 / discord.py**: Lógica principal.
- **SQLite**: Cache e persistência local.
- **HenrikDev API**: Fonte de dados de partidas.
- **Valorant-API.com**: Assets visuais (ícones de agentes, mapas).
