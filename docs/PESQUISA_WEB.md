# Pesquisa pela máquina do Jarvis

Com o Docker Compose disponível, execute na pasta do projeto:

```bash
python3 infra/search/start.py
```

O comando inicia o SearXNG em `127.0.0.1:8888`, gera o segredo privado da instância no perfil local e espera a verificação de saúde. O contêiner requer acesso da máquina à internet para baixar a imagem e consultar os motores de pesquisa. Para usar uma versão de imagem já homologada na sua instalação, defina `SEARXNG_IMAGE` com a imagem e seu digest antes de iniciar.

Pelo WhatsApp, diga “Jarvis, pesquisa na internet como funciona o CalDAV” ou “Procure por documentação do PostgreSQL em português”. A pesquisa envia o termo ao buscador e devolve trechos acompanhados pelos endereços originais. O SearXNG consulta os motores configurados na instância. Se um serviço ficar fora do ar ou atingir o limite de consultas, o Jarvis informa o problema.

Para usar outra porta de SearXNG no próprio computador, configure `JARVIS_SEARXNG_URL=http://127.0.0.1:PORTA` no `.env`. Habilite o formato `json` na seção `search.formats` dessa instância. [API de pesquisa](https://docs.searxng.org/dev/search_api.html) · [Instalação do contêiner](https://docs.searxng.org/admin/installation-docker.html)

Para usar o Brave Search diretamente, defina as duas variáveis no `.env` do projeto e preencha a chave localmente. [Brave Web Search API](https://api-dashboard.search.brave.com/app/documentation/web-search)

```dotenv
JARVIS_SEARCH_PROVIDER=brave
BRAVE_SEARCH_API_KEY=
```
