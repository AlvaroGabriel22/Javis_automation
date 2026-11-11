# Jarvis pessoal

O Jarvis conversa em português pelo WhatsApp com o número pessoal do proprietário. O painel local mostra o QR de pareamento, verifica a posse do número autorizado e exibe os estados da fila de conversa. O modelo inicial é o `qwen2.5:3b` no Ollama da própria máquina. A conversa também aceita Groq e OpenAI por configuração. O Jarvis seleciona ferramentas de agenda, recados, finanças, memória, cálculo, pesquisa e visão conforme o pedido. [Ferramentas, exemplos e diagnóstico](docs/sprints/S04.md)

Em **Finanças e planilhas**, você confere despesas por mês, corrige um valor, define a reserva mensal e baixa o Excel. O WhatsApp recebe fotos de cupom para confirmação. Em uma conversa descontraída, o Jarvis pode responder e mandar uma figurinha do catálogo, se combinar com o assunto. Diga “sem figurinhas por enquanto” para pausar e “pode voltar a mandar figurinhas” para retomar. [Como usar a S05 e conectar o Google Sheets](docs/sprints/S05.md)

Em **Meu perfil**, o tom **Próximo** permite que o Jarvis use expressões como “caramba” e “isso aí!” quando combinarem com a conversa. Os bons-dias e boas-noites também variam. Se preferir saudações simples e orientações financeiras sem brincadeiras, escolha **Neutro**.

Envie um áudio ou texto como “Jarvis, daqui a dois minutos envie para mãe: Oi mãe, tô indo dormir. Boa viagem!”. O Jarvis identifica o destinatário, grava o horário e devolve o código que aparece na **Agenda de envios**. O destinatário vê a identificação “Jarvis, a pedido de Álvaro” e, abaixo, as palavras do recado conforme a transcrição. Para enviar ao seu próprio WhatsApp, diga “envie para mim”.

Quando faltar o horário, responda “agora”, “daqui a dois minutos” ou “mais tarde”. Neste último caso, o Jarvis pergunta o dia e o horário. O pedido fica pendente até você completá-lo, cancelá-lo ou iniciar outro pedido de envio. Se faltar apenas o texto, dite o recado na mensagem seguinte. O prazo relativo conta do recebimento da mensagem que especificou o horário. O fuso é o de Manaus. O aviso com o resultado da tentativa de envio usa o mesmo código do agendamento.

Se o contato está salvo como Gabriela, diga “Chame Gabriela de Gabi” uma vez para cadastrar o apelido. Quando houver duas correspondências, o Jarvis guarda o recado e lista os números e suas origens. Responda com o número da opção, o telefone completo ou, se houver uma única opção dessa origem, “o da agenda do WhatsApp” ou “o do painel”. O painel também permite criar, remarcar ou cancelar envios. Para usar uma transcrição como rascunho, escolha **Agenda de envios → Agendar mensagem → Usar última transcrição**.

Em **Meu perfil**, salve o nome que o Jarvis deve usar, seu gênero e a forma de tratamento. O nome identifica o dono nos recados. O gênero e o tratamento são usados na conversa privada. Quando o cadastro antigo tem apenas o nome “Proprietário”, o nome inicial desta instalação é Álvaro.

Em **Contatos**, use **Excluir contato** ao lado do telefone e confirme. A exclusão remove o contato da lista do Jarvis. A agenda do celular e o histórico de envios são preservados. Para interromper uma mensagem já programada, cancele-a em **Agenda de envios**.

## Agenda pessoal

Em **Agenda pessoal**, consulte dia, semana ou mês, registre compromissos com avisos e configure lembretes, aniversário e saudações. O horário é o de Manaus. No WhatsApp, experimente “me lembre de pagar a conta amanhã às nove” e “mostre minha agenda desta semana”. Um lembrete avulso funciona mesmo sem conta Google. O envio pelo WhatsApp depende do computador acordado e do supervisor do chat em execução.

Para atualizar uma instalação anterior, pare o supervisor com Ctrl+C, rode `make install` na pasta atualizada e inicie o mesmo perfil com `make demo-chat` ou `make chat`. A inicialização aplica a migração. Os exemplos, as regras de atraso e o roteiro para testar uma mudança feita no celular estão em [S03](docs/sprints/S03.md).

## Google Agenda

1. No painel local, entre em **Agenda pessoal → Conectar Google Agenda**. Se aparecer **Credencial pronta para uso**, vá direto para **Autorizar no Google**.
2. No primeiro uso, abra **Como obter o arquivo no Google Cloud?**. Os links levam às telas de configuração do projeto. Ative a **Google Calendar API**, configure o consentimento e, para um Gmail pessoal em modo de teste, cadastre seu endereço em **Público → Usuários de teste**. Em **Clientes → Criar cliente**, escolha **Aplicativo para computador**, crie o cliente e baixe o JSON.
3. De volta ao Jarvis, em **Importar arquivo JSON**, escolha o arquivo baixado. Clique em **Autorizar no Google** e permita o acesso na conta que deseja usar. Se o navegador bloquear a nova aba, use **Abrir autorização no Google**. Você tem cinco minutos para concluir essa tentativa de conexão.
4. Volte à aba do Jarvis e confira **Conta vinculada** com o identificador da sua agenda. Clique em **Voltar à agenda**. Em **Agendas e rotinas**, aguarde a primeira sincronização, escolha as agendas de leitura e onde salvar compromissos e clique em **Salvar agendas**. O sincronizador roda junto com `make demo-chat` ou `make chat`. O painel informa quando ele está desligado.

A primeira conexão seleciona a agenda principal para leitura e, quando há permissão, para gravação. No formulário do painel, você pode escolher **Somente neste computador** para um compromisso. Os itens que já existiam na agenda local permanecem nela.

A credencial importada fica no diretório privado do perfil. O Jarvis reconhece o JSON previamente configurado em `~/.config/jarvis/google-client.json` ou `JARVIS_GOOGLE_CLIENT_FILE`. Também é possível autorizar pelo terminal com `make demo-google-connect` (SQLite) ou `make google-connect` (PostgreSQL). Após revogação, clique em **Configurar Google Agenda → Autorizar novamente** usando a mesma conta. [Guia oficial do Google](https://developers.google.com/workspace/calendar/api/quickstart/python)

## Iniciar a conversa pelo WhatsApp

No Ubuntu, prepare Python 3.12 com `venv`, Node 24, npm, Make, FFmpeg, a biblioteca de sistema libmagic e Ollama. Use uma conta de WhatsApp dedicada ao Jarvis e um número pessoal separado para dar os comandos. O projeto mantém a própria sessão Neonize. O banco de sessão do `whats-manha` pertence ao projeto antigo.

1. Na pasta `jarvis/`, execute `make install` para atualizar as dependências e o frontend, inclusive se já estiver usando a S00.
2. Para habilitar a transcrição numa instalação nova, execute `make voice`. O comando prepara um ambiente Python separado e baixa os pesos do Whisper base uma vez. Execute `ollama list`. O serviço precisa estar escutando no loopback. Se estiver parado, inicie-o. Se a tag estiver ausente, execute `ollama pull qwen2.5:3b`.
3. Encerre a instância anterior do painel na porta 8787. Execute `make demo-chat` para iniciar o painel, o worker de IA e o gateway no mesmo perfil SQLite. O comando aplica a migração do banco. Um agendamento salvo nas versões anteriores fica em **Salva · precisa ativação** até você escolher **Ativar envio** e confirmar um horário futuro. Para um banco PostgreSQL já preparado, use `make chat`.
4. Em outro terminal na mesma pasta, gere o código com `make demo-code` (ou `make login-code` para PostgreSQL). Entre em **http://127.0.0.1:8787** e abra **WhatsApp → Conectar aparelho**.
5. No WhatsApp **da conta do Jarvis**, abra **Dispositivos conectados**, escolha conectar um aparelho e leia o QR do painel.
6. No painel, informe **seu número pessoal** com país e DDD, por exemplo no formato `+5592…`. Clique em **Gerar código de vínculo**. Envie a frase do seu WhatsApp pessoal em uma conversa privada com o número do Jarvis, em até cinco minutos. Acompanhe **Seu número autorizado** no painel. Quando aparecer **Vínculo confirmado**, mande uma mensagem de texto ao Jarvis.

Enquanto o painel mostrar **Aguardando a frase**, confira se o destinatário da conversa é a conta exibida em **Aparelho do Jarvis**. Se o painel exibir um remetente diferente, confira o número que o WhatsApp identificou e gere outro código para ele somente se for o seu. Cada código novo substitui o anterior. Reiniciar os serviços também exige gerar um novo código para um vínculo pendente.

Para interromper o conjunto, use `Ctrl+C` no terminal dos serviços. Ao reabrir com o mesmo comando, uma sessão válida é retomada; WhatsApp pode solicitar novo pareamento se o aparelho for desvinculado. Você pode remover o número autorizado na mesma tela. Para desautorizar o próprio aparelho, use a lista de dispositivos no WhatsApp da conta dedicada.

### Como interpretar o estado do painel

- **Gateway desligado**: o painel respondeu, mas o processo de WhatsApp não está acessível. Confirme se iniciou `make demo-chat` ou `make chat` no mesmo perfil.
- **Falhas de IA**: confira se a tag configurada existe e se o Ollama responde na máquina. A falha da inferência produz aviso de indisponibilidade ao dono quando o canal puder transmiti-lo.
- **Resposta aceita**: o WhatsApp retornou um ID da mensagem. A leitura pelo destinatário é um evento diferente.
- **Envio incerto**: o canal pode ter transmitido antes da queda. O Jarvis preserva a tentativa e evita uma repetição automática.

O QR e o desafio ficam em memória, e o painel usa sessão autenticada e CSRF nas operações de pareamento. A base da sessão e o vínculo confirmado ficam em `~/.local/share/jarvis/whatsapp/` em operação PostgreSQL ou `~/.local/share/jarvis/demo/whatsapp/` em demonstração (o diretório respeita `XDG_DATA_HOME`). O banco do Jarvis guarda as mensagens de conversa e as tentativas de resposta.

## Usar somente o painel, sem iniciar a conversa

Requisitos: Python 3.12 com suporte a `venv`, Node 24 com npm e Make. O perfil PostgreSQL usa também Docker com Compose. Se estiver usando o pacote ZIP, extraia a pasta `jarvis/` em `~/Documentos/` e abra um terminal nessa pasta. Se estiver usando a pasta de desenvolvimento criada pelo Codex, os mesmos comandos valem na raiz dela.

```bash
make install
make demo
```

O primeiro comando cria uma virtualenv em `backend/.venv`, instala as dependências dos arquivos de lock e compila o painel. O segundo prepara o banco de demonstração e abre o serviço exclusivamente em **http://127.0.0.1:8787**. Em um **segundo terminal**, na mesma pasta, gere o código para entrar:

```bash
make demo-code
```

Abra o endereço no navegador, cole o código e clique em **Entrar no painel**. O código é de uso único e vale por dez minutos. No primeiro uso, **Agendar mensagem** abre o cadastro do destinatário e continua direto para a composição. A hora exibida e digitada é a de Manaus, inclusive quando o navegador está em outro fuso.

Para parar o serviço, use `Ctrl+C` no primeiro terminal. Volte com `make demo`. O banco SQLite de demonstração fica em `~/.local/share/jarvis/demo/` ou em `$XDG_DATA_HOME/jarvis/demo/` quando a variável estiver configurada. `make demo-doctor` verifica esse banco e a revisão da migração.

## Usar PostgreSQL

Na raiz de um checkout com `make install` já concluído:

```bash
make setup
make db
make migrate
make doctor
make run
```

Em outro terminal, rode `make login-code`. Acesse o mesmo endereço local. `make setup` cria `.env` com uma senha aleatória para o PostgreSQL e permissões restritas. Uma configuração existente é preservada. O Compose publica o banco apenas no loopback, porta 55432 por padrão. O volume do Docker guarda os dados entre reinícios; o banco de demonstração e o PostgreSQL têm usuários, códigos e agendamentos independentes. Os dois perfis usam a mesma porta do painel; encerre um antes de iniciar o outro.

## Estrutura e verificação

| Local | Responsabilidade |
|---|---|
| `backend/src/jarvis/` | API FastAPI, autenticação local, contatos, serviços de agendamento e persistência. |
| `backend/migrations/` | Revisões Alembic do schema. |
| `backend/tests/` | Contratos HTTP, segurança da sessão e integridade dos dados. |
| `frontend/src/` | Painel React em TypeScript, cliente HTTP, formulários e conversão de horário. |
| `frontend/tests/`, `frontend/e2e/` | Testes de horário e jornada Playwright com dados sintéticos em banco temporário. |
| `infra/`, `scripts/` | Banco PostgreSQL, criação de configuração privada e inicialização local. |
| `docs/` | Arquitetura aprovada e andamento das sprints. |

```bash
make check
make test
make build
frontend/node_modules/.bin/playwright install chromium
make browser-test
```

A suíte de backend usa um banco SQLite temporário por teste. Para validá-la também em PostgreSQL, configure `JARVIS_TEST_DATABASE_URL` com um banco de **testes** em que o usuário possa criar schemas. O workflow de CI prepara PostgreSQL 16 e executa a jornada Chromium com o painel compilado e a API real.

Para desenvolver o frontend, mantenha a API em `127.0.0.1:8787` e rode `npm --prefix frontend run dev`. Acesse `http://127.0.0.1:5173`. O Vite encaminha `/api` para a API local. O contrato da API está em `/openapi.json`.

## Comportamento em falhas

O servidor confirma o agendamento após gravar a transação. Uma repetição com a mesma chave de idempotência e o mesmo conteúdo devolve o registro existente. A mesma chave com conteúdo diferente gera conflito. O frontend guarda essa chave durante novas tentativas do mesmo formulário. A API protege mutações autenticadas com token CSRF e restringe o painel ao acesso local.

Um erro de banco é apresentado como indisponibilidade, separado de credencial inválida, validação da mensagem e contato inexistente. Se o computador voltar mais de cinco minutos depois do horário marcado, o envio vai para **Revisar**. Se o WhatsApp perder a confirmação de uma tentativa, o resultado fica **incerto** e o envio não se repete automaticamente. As chaves de IA devem ser configuradas localmente quando os adaptadores estiverem disponíveis.

Consulte o [plano de arquitetura](docs/PLANO_ARQUITETURA_JARVIS.md), o [registro da S00](docs/sprints/S00.md), o [roteiro de homologação da S01](docs/sprints/S01.md), o [roteiro de voz e envios da S02](docs/sprints/S02.md) e a [agenda pessoal da S03](docs/sprints/S03.md).

## Calendário visual e iCloud

No painel, **Agenda pessoal** abre no mês atual. Clique no número de um dia para ver a grade de horários ou no espaço de uma célula para criar um item. Na semana e no dia, clique em um horário para preencher o início. Compromissos simultâneos ocupam colunas lado a lado. Clique no compromisso para ver a origem, o estado da sincronização e as opções de edição. O relógio do painel usa Manaus.

Em **Conectar iCloud**, informe o e-mail da Conta Apple e a senha específica gerada para o Jarvis em **Início de sessão e segurança → Senhas específicas de apps**. Sua conta deve ter autenticação de dois fatores e calendários no iCloud. [Conta Apple](https://account.apple.com/) · [Ajuda da Apple](https://support.apple.com/pt-br/102654)

Em **Agendas e rotinas**, marque as agendas que o Jarvis deve consultar. Em **Destinos do WhatsApp**, escolha uma agenda editável do Google e outra do iCloud e clique em **Salvar agendas**. Um pedido por texto ou áudio será gravado nas duas. Se houver uma única agenda editável selecionada no serviço, o Jarvis usa essa agenda. Se houver várias sem preferência, ele guarda o pedido e pergunta o destino pelo WhatsApp. Responda com o nome ou número. Para salvar a preferência, diga “sempre” e o número mostrado. Você também pode pedir expressamente “no iCloud” ou “no Google”. No formulário de criação do painel, **Agenda de destino** vale para aquele item. Os lembretes pessoais ficam no computador.

“Marque dentista dia 20 de novembro às 14h30” cria um compromisso de uma hora. “Marque reunião amanhã das 9h às 10h30” usa o intervalo informado. “Coloque a entrega do projeto na minha agenda dia 25 de novembro” reserva o dia inteiro. A data e a hora usam o fuso de Manaus. A primeira resposta identifica os destinos pendentes. O Jarvis confirma pelo WhatsApp depois de receber o aceite dos destinos solicitados. Mantenha o supervisor do chat em execução para transmitir as alterações.

A legenda usa 🟢 para Google e 🔵 para iCloud. Um compromisso gravado nas duas agendas mostra os dois marcadores. Uma consulta “só no iCloud” usa apenas essa origem. “Quais são meus compromissos amanhã?”, “Minha agenda da semana de 15/11” e “Mostre meu calendário de dezembro” atualizam os calendários de leitura antes de montar a resposta. Compromissos criados diretamente no celular entram nessa consulta. Se um serviço estiver indisponível, a resposta identifica a leitura parcial. As duas cópias correspondentes aparecem juntas na grade e na conversa. Se alguém editar uma das cópias diretamente no aplicativo, o Jarvis exibe as versões divergentes com suas origens.

A revisão `0008_icloud` identifica as agendas existentes como Google e mantém as preferências e os compromissos. O iCloud usa descoberta CalDAV, verificação de permissões e ETags para conciliar edições locais e remotas. A senha específica fica cifrada no banco com chave privada do perfil. Use **Configurar iCloud** para renovar a senha ou desconectar a conta.
