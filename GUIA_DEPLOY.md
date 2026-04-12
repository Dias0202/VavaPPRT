# Guia de Deploy 100% Gratuito (Render + Supabase + UptimeRobot)

Siga este guia para colocar seu bot de Valorant online 24/7 sem custos.

## 1. Configurar o Banco de Dados (Supabase)
O Render apaga arquivos locais toda vez que o bot reinicia. Por isso, usamos o Supabase para salvar seus dados (usuários, histórico, etc).

1.  Acesse [supabase.com](https://supabase.com/) e crie uma conta gratuita.
2.  Crie um novo projeto (ex: `vavapprt-db`).
3.  Defina uma senha forte para o banco e **guarde-a**.
4.  Aguarde o provisionamento. Vá em **Project Settings** (engrenagem) > **Database**.
5.  Role até **Connection String** e escolha a aba **URI**.
6.  Copie a URL. Ela será algo como: `postgresql://postgres.[SEU-ID]:[SUA-SENHA]@aws-0-sa-east-1.pooler.supabase.com:6543/postgres`
    *   **Dica:** Se a senha tiver caracteres especiais (como `@`), você deve trocá-la por uma simples ou encodá-la.
    *   **Dica 2:** Adicione `?sslmode=require` ao final da URL se tiver problemas de conexão.

## 2. Configurar o Bot no Render
O Render vai hospedar o código do seu bot.

1.  Suba seu código para um repositório no **GitHub** (lembre-se de manter o `.env` no `.gitignore`).
2.  Acesse [render.com](https://render.com/) e crie uma conta.
3.  Clique em **New +** > **Web Service**.
4.  Conecte seu repositório do GitHub.
5.  Configurações:
    *   **Name:** `vavapprt`
    *   **Runtime:** `Python 3`
    *   **Build Command:** `pip install -r requirements.txt`
    *   **Start Command:** `python src/main.py` (ou deixe em branco se o Render detectar o `Procfile`)
    *   **Plan:** `Free`
6.  Clique em **Advanced** > **Add Environment Variable**:
    *   `DISCORD_TOKEN`: (Seu token do bot do Discord)
    *   `DATABASE_URL`: (A URL que você copiou do Supabase no passo anterior)
7.  Clique em **Create Web Service**.

## 3. Manter Online (UptimeRobot)
O plano gratuito do Render "dorme" após 15 minutos sem atividade web. Como nosso bot agora tem um mini-servidor web interno, podemos usar o UptimeRobot para dar um "cutucão" nele a cada 5 minutos.

1.  Aguarde o Render terminar o deploy. Ele vai te dar uma URL (ex: `https://vavapprt.onrender.com`).
2.  Acesse [uptimerobot.com](https://uptimerobot.com/) e crie uma conta gratuita.
3.  Clique em **Add New Monitor**.
4.  Configurações:
    *   **Monitor Type:** `HTTP(s)`
    *   **Friendly Name:** `VavaPPRT Bot`
    *   **URL (or IP):** Cole a URL do seu app no Render.
    *   **Monitoring Interval:** `5 minutes`
5.  Clique em **Create Monitor**.

---

### Verificação
*   Se o Render mostrar "Live" e o log disser `Servidor Web rodando na porta 8080` e `Bot logado como ...`, deu tudo certo!
*   Se você acessar a URL do Render no navegador, deverá ver a mensagem: `Bot is alive and monitoring!`.
