# Jarvis pessoal • Arquitetura e plano de implementação

**Destino proposto** · `/home/alvaro/Documentos/jarvis` no Ubuntu.

**Primeira entrega utilizável** · Álvaro pode agendar por voz ou texto uma mensagem de assunto livre para qualquer contato do WhatsApp, na data e no horário escolhidos. O Jarvis registra destinatário, conteúdo e horário, preserva o agendamento após reinício e informa o resultado do envio.

**Escopo deste documento** · Projeto técnico e roteiro de implantação. Os comandos de preparação e os contratos abaixo são para implementação futura.

## 1. Decisões de arquitetura

| Decisão | Escolha inicial | Motivo |
|---|---|---|
| Organização | Um repositório, frontend independente e backend Python em módulos de negócio | A API, os processos em segundo plano e o WhatsApp usam as mesmas regras. A interface pode evoluir separadamente. |
| Canal | Neonize em processo próprio, um escritor por sessão, pareamento por QR | Aproveita o protocolo já testado na máquina. O frontend e a API podem reiniciar sem derrubar o WhatsApp. |
| Dados | PostgreSQL para os dados do Jarvis e arquivo de sessão exclusivo do Neonize | O banco sustenta histórico, transações, fila e recuperação. A sessão pertence ao conector. |
| Execução | API FastAPI, processo de trabalho e processo de WhatsApp. Um único código de backend com três pontos de entrada | O modelo pode estar ocupado com áudio ou visão enquanto o WhatsApp despacha uma mensagem que já está agendada. |
| Inteligência | Ollama com `qwen2.5:3b` para conversa e intenção, `qwen3-vl:2b` para visão, faster-whisper para voz. Groq e OpenAI opcionais por capacidade | A troca de provedor preserva dados e agendamentos. Áudio, imagem e texto podem ter custos e destinos diferentes. |
| Interface | React + TypeScript + Vite. Painel privado, em português, servido em localhost | WhatsApp é a interface cotidiana. O painel facilita consultar a agenda, revisar memória e diagnosticar um envio. |
| Integrações iniciais | Google Agenda, arquivo Excel dedicado e Gmail via OAuth. Google Sheets como segundo adaptador de planilha | Escolhas propostas para dar um caminho implementável. Adaptadores permitem usar outro calendário ou e-mail se forem os do usuário. |

### O que foi verificado no `whats-manha`

| Código de referência em `/home/alvaro/Documentos/whats-manha` | Comportamento observado | Decisão para o Jarvis |
|---|---|---|
| `bot.py`, linhas 75–95, e `whatsapp_runtime.py`, 285–305 | `NewClient("sessao_whatsapp.db")`, registro de eventos de conexão, desconexão e mensagem, seguido de `connect()`. A instalação de referência usa Neonize 0.10.0. | Reutilizar o padrão e a versão como ponto de partida. Criar sessão própria e registrar o callback de QR antes de conectar. |
| `whatsapp_runtime.py`, 61–159 e 202–275 | Identificação por chat, remetente e mensagem, ordenação por conversa e deduplicação em RAM. O histórico fica limitado às últimas 20 entradas. O envio distingue falha anterior à transmissão de resultado incerto. | Preservar a ordenação e essa distinção. Persistir entradas, comandos e tentativas. Acrescentar envio a um contato sem depender do evento ao qual o bot está respondendo. |
| `whatsapp_runtime.py`, 102–115 | O chat “Você” tem tratamento restrito a comandos da coleção de figurinhas. Mensagens próprias em geral são filtradas. | Usar como primeiro modo uma conta dedicada ao Jarvis com o número de Álvaro autorizado. O modo de conversa consigo mesmo exigiria distinguir comandos de ecos emitidos pelo Jarvis. |
| `bot.py`, 75–83, `ollama_client.py`, 22–66 e 117–156, `groq_client.py`, 47–118 | O bot compõe personalidade, histórico e entrada. O cliente seleciona Ollama/Groq e pode pedir JSON validado para texto ou figurinha. | Manter a conversa em português e a validação. Separar interpretação de pedido, execução de ferramenta e redação da resposta. |
| `audios.py`, 18–48 e 85–128, `figurinhas.py` e `visao_figurinhas.py` | Há transcrição local em CPU com int8, controle de tamanho/duração e tratamento de WebP. O código de visão observado se concentra em figurinhas. | Reaproveitar ideias e testes. Criar entrada específica para foto sem legenda, OCR de recibo e agendamento por voz. |

No disco da máquina há manifestos e camadas de `qwen2.5:3b` e `qwen3-vl:2b`. A indicação “qwen.b:” foi interpretada a partir desses arquivos e dos padrões do bot. A disponibilidade do serviço e a qualidade de inferência ficam para o teste de inicialização. O projeto novo começa com banco e sessão próprios. RAG de arquivos, OMDb e respectivas dependências ficam fora da implementação.

## 2. Componentes e fluxo de dados

~~~mermaid
flowchart TD
    WhatsApp["WhatsApp"] <--> Gateway["Processo Neonize"]
    Gateway <--> DB[("PostgreSQL")]
    UI["Painel React"] --> API["API FastAPI"]
    API <--> DB
    Worker["Worker de conversas e integrações"] <--> DB
    Worker <--> Models["Ollama, Whisper ou APIs"]
    Worker <--> External["Agenda, planilhas e e-mail"]
~~~

**Processo WhatsApp.** Possui a sessão e o acesso ao telefone. Identifica o remetente pelos identificadores fornecidos pelo protocolo, verifica o dono, registra a entrada e guarda mídias necessárias com referência local. No sentido de saída, consome a fila de envio e registra o ID retornado pelo canal. Uma tarefa do próprio processo materializa ocorrências de WhatsApp vencidas em transação curta. Ela usa relógio e dados já gravados. Isso permite despachar às 09h mesmo se o worker estiver processando uma foto.

**Worker.** Ordena os turnos de uma conversa, transcreve, extrai dados, resolve a intenção e chama os serviços. Tem filas de trabalho para conversa/mídia e integrações, com concorrência limitada. Começar com uma inferência local por vez e um executor separado para chamadas de I/O. A síntese do texto de uma mensagem agendada termina no momento do pedido. No horário combinado, o gateway já tem o texto exato a transmitir.

**API e painel.** A API autentica o usuário e chama os mesmos serviços usados pelo WhatsApp. Em desenvolvimento, o Vite encaminha `/api` para a API. Em operação, servir o build estático do frontend e a API sob a mesma origem. O painel pode estar fechado durante envios e lembretes.

**Infraestrutura.** PostgreSQL roda em volume persistente. Ollama roda no host e os três processos Python usam a mesma virtualenv. Mídias privadas e sessão ficam em diretório de estado fora do repositório. O gateway inicia e mantém sua conexão de saída com o WhatsApp. Para o perfil local, a implantação dispensa domínio público e webhook de entrada.

### Como crescer a partir de uma máquina

| Estágio | Sinal para avançar | Mudança e condição de aceite |
|---|---|---|
| Um host, um dono | Configuração inicial | Manter API, worker e gateway em processos distintos. Reservar tempo de CPU e conexões de banco para o despacho de mensagens mesmo durante transcrição ou visão. Medir na máquina de uso. |
| Mais trabalho simultâneo | A fila envelhece continuamente ou os envios perdem a meta sob carga | Separar consumidores de conversa, mídia e integrações com limites próprios. Aumentar consumidores da fila congestionada e repetir a mesma carga. Se a GPU estiver saturada, limitar inferências concorrentes ou mover esse trabalho para outro equipamento. |
| Outro host para API ou workers | O primeiro host já atingiu o limite medido de CPU, RAM ou I/O | Manter estado de sessão web e deduplicação no banco, colocar as mídias necessárias em armazenamento acessível pelos workers autorizados e limitar a soma das conexões ao PostgreSQL. Builds de frontend são distribuíveis separadamente. O gateway e o IPC do QR permanecem juntos; a rota de pareamento continua chegando a esse host. |
| Continuidade se o host principal falhar | Passar a exigir operação durante falha física | Hospedar banco e anexos com recuperação independente. Para a mesma sessão do WhatsApp, assumir em outro host somente após isolar o gateway anterior e validar o estado da sessão. Testar a troca e a reconciliação antes de anunciar alta disponibilidade. |

Com vários consumidores, reservar um turno ativo por conversa no banco e preservar a ordem de chegada. Renovar a reserva de trabalhos longos e exigir o token vigente para concluir ou renovar a tarefa. Ao encerrar um processo, parar de adquirir trabalho e concluir ou devolver o que estiver em andamento conforme o estado de transmissão. Um único gateway possui cada sessão de WhatsApp. Aumentar o número de workers não aumenta o limite do canal. A instalação de um só host continua sujeita a falhas de energia, disco, rede e suspensão.

## 3. Pastas e fronteiras

Listagem planejada. Diretórios agrupados na mesma linha representam irmãos.

~~~text
/home/alvaro/Documentos/jarvis/
  backend/
    pyproject.toml                    dependências, empacotamento e ferramentas de qualidade
    requirements.lock                dependências resolvidas e versões reproduzíveis
    alembic.ini
    migrations/versions/
    src/jarvis/
      entrypoints/                   api.py, worker.py, whatsapp.py, cli.py
      bootstrap/                     settings.py, container.py, logging.py
      modules/
        identity/                    proprietário, sessão web, contatos e apelidos
        conversation/                turnos, intenções, contexto e ferramentas permitidas
        media/                       transcrição, OCR, extração e catálogo de figurinhas
        calendar/                    agendas, compromissos e sincronização
        scheduling/                  lembretes, rotinas, ocorrências e alterações
        messaging/                   entrada/saída WhatsApp, estados e recibos
        memory/                      fatos, preferências, origem, revisão e exclusão
        finance/                     lançamentos, orçamento, compromissos e projeções
        spreadsheets/                destinos autorizados e escrita por registro
        reports/                     consultas por período, relatórios, anexos e envio
      adapters/
        whatsapp/                    implementação Neonize e tradução de eventos
        ai/                          ollama.py, groq.py, openai.py
        media/                        faster_whisper.py, image_reader.py, webp.py
        google/                       calendar.py, sheets.py, gmail.py, oauth.py
        files/                        xlsx.py, pdf.py, blobs.py
        persistence/                  conexão, unidade de trabalho e repositórios SQL
        jobs/                         aquisição, expiração de lease e reconciliação
      http/                          routers por recurso, autenticação e erros de API
      shared/                        tipos de ID, relógio, padrão monetário e erros comuns
    tests/unit/  tests/integration/  tests/contract/  tests/e2e/
    tests/fixtures/                  mídias, mensagens e calendários sintéticos
  frontend/
    package.json  package-lock.json
    src/app/                         rotas, sessão e composição
    src/features/                    agenda, envios, contatos, memória, finanças, configurações
    src/components/                  controles compartilhados e acessíveis
    src/api/                         cliente tipado a partir do OpenAPI
    tests/                           componentes e páginas
    e2e/                             jornadas Playwright
  infra/compose.yaml                 PostgreSQL e volume
  infra/systemd/                     unidades de API, worker e WhatsApp
  scripts/                           preparação local, diagnóstico, backup e restauração
  docs/adr/  docs/contracts/  docs/runbooks/  docs/sprints/
  .env.example  .gitignore  Makefile  README.md
~~~

Dentro de cada módulo, `domain.py` contém tipos e regras puras, `service.py` implementa os casos de uso, `ports.py` declara os contratos de dependências externas e `schemas.py` valida dados de entrada e saída quando necessário. Criar apenas os arquivos usados por cada módulo. Um módulo pode importar o serviço público de outro. Routers, React e prompts não acessam tabelas diretamente. O código de domínio não importa FastAPI, Neonize, SDKs de IA ou ORM.

| Serviço | Responsabilidade | Contrato com outras partes |
|---|---|---|
| `ContactService` | Buscar pelo nome na agenda disponível à conta conectada, por apelido conhecido ou por número de WhatsApp informado pelo dono. Resolver JID e LID verificados. | Retorna a identidade do destinatário, opções em caso de homônimos ou o dado que falta. O contato pode ser usado já no primeiro pedido, sem cadastro manual prévio no Jarvis. Se a conta dedicada não enxergar o contato do telefone pessoal, aceitar cartão de contato compartilhado ou número e verificar o destino. |
| `ConversationService` | Construir contexto, planejar uma intenção, pedir um dado faltante e formular a resposta com o resultado real. | Recebe um turno autenticado. Emite um comando tipado e consome o resultado. |
| `MediaService` | Baixar pelo gateway, validar o arquivo e entregar transcrição ou extração com campos incertos. Selecionar figurinha por ID. | O conteúdo extraído é dado. Instruções impressas em recibos ou em mensagens citadas não autorizam ações. |
| `CalendarService` | Consultar, criar, atualizar e cancelar eventos. Expor disponibilidade e intervalos completos. | A agenda externa é a autoridade sobre compromissos. Retorna ID, revisão e situação da sincronização. |
| `SchedulingService` | Converter datas relativas, criar recorrência, materializar ocorrências e remarcar/cancelar. | Entrega ordem de envio com destinatário, texto já definido, horário e validade. |
| `MessagingService` | Registrar eventos do canal, deduplicar, transmitir e classificar o resultado. | Informa se o envio foi aceito, entregue, falhou ou ficou incerto. |
| `MemoryService` | Guardar e recuperar fatos, preferências e resumos com origem e validade. | Entrega contexto pertinente ao pedido e aplica correções do proprietário. |
| `FinanceService` | Registrar e corrigir despesas e receitas, calcular totais e simular uma compra. | Entrega valores em centavos e premissas verificáveis para a resposta. |
| `SpreadsheetService` e `ReportService` | Escrever em planilha indicada, compor documento por período e enviá-lo ao e-mail configurado. | A escrita na planilha, a geração do arquivo e a transmissão do e-mail têm resultados independentes. |

## 4. Contratos e autorização

**Entrada normalizada.** `InboundMessage(v1)` leva ID do provedor, conta conectada, chat, remetente verificado, tipo, instante original, instante de recebimento, texto e referência à mídia. A chave única combina conta, conversa, remetente e ID da mensagem. Persistir o envelope necessário para retomar a tarefa após reinício. Baixar e validar anexos em etapa recuperável, com expiração explícita se a mídia já não estiver disponível.

**Saída da IA.** `Intent(v1)` discrimina tipos como `schedule_message`, `set_reminder`, `list_events`, `record_expense` e `save_memory`. Validar pelo schema de cada ação, com campos extras proibidos. Valor em fala, expressão de data e nome do destinatário entram como dados de origem. O serviço resolve a data e procura o contato. A IA não fornece SQL, comandos de sistema ou credenciais. Com JSON inválido, tentar um reparo limitado. Persistindo a dúvida, pedir informação ou explicar a falha e manter a ação pendente.

**Execução.** `ActionResult` carrega `operation_id`, estado, campos efetivados, horário resolvido e eventual motivo de falha. O mesmo comando pode gerar ação composta, por exemplo, criar evento e programar aviso. Cada etapa tem chave idempotente. A confirmação explica um eventual sucesso parcial e o que ainda está pendente. A resposta “agendei” só é liberada após commit. “Enviei” requer aceite do canal.

**Proprietário.** Primeiro perfil de operação proposto: WhatsApp dedicado ao Jarvis e comandos exclusivamente do identificador privado de Álvaro. Vincular o dono localmente no painel, com desafio de posse, antes de habilitar ações. Ignorar grupos, status, mensagens próprias, duplicatas e ordens de terceiros. O destinatário recebe somente o recado endereçado a ele. O acesso à agenda e às finanças continua exclusivo do proprietário. Conteúdo citado ou encaminhado por Álvaro pode servir como material, mas o comando vem da instrução explícita dele. Se for escolhido usar somente uma conta, homologar separadamente chat “Você”, identificação entre dispositivos e supressão de ecos inclusive após crash.

### Agendamento de mensagens a qualquer contato

Cada pedido informa três dados variáveis: **quem recebe**, **quando enviar** e **qual mensagem transmitir**. O mesmo serviço atende texto digitado e áudio transcrito. O horário pode ter data e hora explícitas (“dia 18 às 14h35”), ser relativo (“daqui a 45 minutos”) ou recorrente quando o dono pedir (“toda sexta às 16h”). O assunto é livre. Se o dono ditar palavras exatas, enviá-las literalmente. Se pedir “avise que…”, redigir um recado fiel e incluir o texto na confirmação do agendamento. Quando os três dados estiverem claros, concluir em um único comando. Em caso de ambiguidade, perguntar somente o que falta.

| Pedido de exemplo | Comportamento esperado |
|---|---|
| “Daqui a 45 minutos, avisa o Rafael que terminei o relatório.” | Calcular o instante a partir do pedido e agendar o recado para Rafael. |
| “Amanhã às 22h17, manda pra Ana exatamente assim: ‘Já cheguei.’” | Resolver a data em Manaus e preservar “Já cheguei.” como conteúdo. |
| “Toda sexta às 16h, manda pro Bruno a mensagem ‘Pode fechar os pedidos da semana’.” | Criar uma recorrência semanal para Bruno, que o dono poderá remarcar ou cancelar. |

### Exemplo de execução com áudio

Cenário sintético. Domingo, 8 de novembro de 2026, 08h10 de Manaus. Áudio privado do dono: “Avise a Gabriela às nove horas da manhã que ela precisa tomar o remédio dela”.

1. O gateway autentica o remetente e registra o ID do áudio. O worker transcreve em português.
2. O interpretador devolve `schedule_message` com “Gabriela”, “às nove horas da manhã” e o recado solicitado. O serviço resolve exatamente um contato e a próxima ocorrência válida **naquele dia**. Se já passou das 09h e não existe preferência salva, pergunta “Amanhã às 09h?”. Se o processamento atrasou e o horário pretendido venceu entretanto, aplica a política de atraso.
3. Na mesma transação, registra o comando, o agendamento da ocorrência para `2026-11-08T13:00:00Z` e a confirmação destinada a Álvaro. O conteúdo é definido uma vez: “Gabriela, o Álvaro pediu para te lembrar de tomar o seu remédio.” Sem adicionar dose ou instrução. Se o usuário ditar texto literal, preservar as palavras ditadas.
4. Resposta ao dono: “Agendado para hoje, 08/11, às 09h de Manaus. Vou mandar para Gabriela: ‘…’”. Um contato e um horário claros bastam para concluir em um áudio.
5. Às 09h o gateway transmite o texto ao ID do contato e grava o resultado. “Muda pras dez” altera a ocorrência pendente. “Cancela o aviso da Gabriela” cancela antes da transmissão. Se o envio já começou, o Jarvis informa o estado real.

Se a pessoa mandar só o conteúdo em um primeiro áudio e determinar o horário no turno seguinte, associar a continuação ao rascunho pendente da conversa. Mostrar o texto que será enviado. Tratar “envia meu áudio original” como pedido distinto de “transcreve e manda o texto”.

## 5. Persistência, tempo e entrega

| Grupo de dados | Tabelas ou entidades propostas | Invariante |
|---|---|---|
| Dono, destinatários e contas | `owners`, `contacts`, `contact_identities`, `contact_aliases`, `integration_accounts` | Todo registro pessoal é vinculado ao dono. Apelido pode ter múltiplas correspondências. Credenciais são referenciadas pelo identificador do segredo. |
| Conversas e intenções | `conversations`, `inbound_messages`, `turns`, `commands`, `pending_questions` | Um ID de origem gera um processamento lógico. Guardar ordem por conversa e chave de operação por ação. Pergunta pendente expira e pode ser retomada. |
| Memória e estilo | `memory_facts`, `memory_revisions`, `conversation_summaries` | Fato possui tipo, origem, data, vigência e estado. Preferências expressas prevalecem sobre inferências. |
| Calendário e rotinas | `calendars`, `calendar_events_cache`, `sync_cursors`, `schedules`, `occurrences` | Identidade externa por agenda/evento. Unicidade por regra e instante de ocorrência. Controle de versão em alteração ou cancelamento. |
| Entrega e auditoria | `jobs`, `outbox`, `delivery_attempts`, `audit_events` | Cada efeito possui chave de operação única, tentativas, prazo, erro classificado e ID externo quando existir. |
| Finanças e documentos | `ledger_entries`, `budgets`, `commitments`, `sheet_targets`, `sheet_writes`, `report_runs`, `attachments` | Valor inteiro em centavos e moeda. Exportações e correções referenciam o lançamento original. Mesmo recibo reenviado com outro ID gera aviso de possível duplicidade. |

**Relógio.** Injetar `Clock` nos serviços. Persistir instantes com `timestamptz`, fuso IANA e expressão original. Interpretar “hoje”, “amanhã” e “sexta” em `America/Manaus` usando o instante original da fala/mensagem quando disponível. Calendário pessoal começa a semana na segunda. Intervalos de consulta são fechados no início e abertos no fim. Evento de dia inteiro conserva a data civil. Esses critérios também governam consultas financeiras e relatórios.

**Filas.** Transação curta com `FOR UPDATE SKIP LOCKED` adquire trabalho e grava um lease com expiração. Não manter o lock SQL durante inferência ou chamada externa. Usar transação de negócio + outbox para confirmação e efeitos futuros. Verificar a versão do agendamento e sua validade imediatamente antes de transmitir. Reagendar e cancelar invalidam ocorrências anteriores ainda pendentes. O índice por estado e horário atende à busca de tarefas vencidas.

**Entrega.** Estados mínimos: agendado, pronto, transmitindo, aceito pelo canal, entregue quando houver recibo, cancelado, falha, expirado e resultado incerto. Persistir “transmitindo” antes da chamada externa. Se o processo cair depois de uma possível transmissão, não repetir cegamente. Reconciliar por ID/recibo quando disponível e expor o caso no painel. O aceite do servidor não comprova que a pessoa leu. Em falha comprovadamente anterior ao envio, reagendar com atraso progressivo dentro do prazo. Tratar cada destinatário e cada parte de texto/figurinha como efeitos distintos.

**Máquina indisponível.** Por padrão proposto, bom dia às 07h e boa noite às 21h têm tolerância de 15 minutos e depois expiram. Lembrete pessoal atrasado volta com indicação do horário original. Mensagem a terceiro vencida após 5 minutos fica pendente de decisão de Álvaro, evitando enviar tarde um recado sensível ao horário. Os limites são ajustáveis. O aviso da decisão fica também no painel caso o próprio WhatsApp esteja fora. Após restauração de backup, iniciar com saídas pausadas e reconciliar a janela restaurada antes de habilitar os envios.

### Política de erros e reprocessamento

Cada adaptador traduz exceções externas para um erro tipado com código estável, operação, ID de correlação e estado do efeito externo. O serviço decide se pode tentar novamente; API, painel e WhatsApp traduzem o mesmo resultado para o usuário. Cada operação tem um único responsável pelas tentativas para impedir que SDK, worker e fila multipliquem chamadas entre si.

| Falha | Comportamento previsto |
|---|---|
| Dado inválido, contato ambíguo ou horário incompleto | Preservar o rascunho, explicar o campo a corrigir e retomar o mesmo comando após a resposta. |
| Limite do provedor, leitura indisponível ou falha comprovadamente anterior à transmissão | Definir timeout de conexão e de operação. Reagendar tentativas com espera crescente e variação aleatória, respeitando o prazo da tarefa e o tempo indicado pelo provedor. Proposta inicial de até três tentativas totais para tarefas externas elegíveis. |
| Credencial revogada, permissão insuficiente ou serviço local sem configuração | Pausar a integração atingida e indicar a correção no painel. Retomar após reparo, verificando a validade dos pedidos pendentes. |
| Timeout em operação que pode ter produzido efeito | Registrar resultado incerto. Usar o ID externo ou a chave idempotente aceita pelo destino para reconciliar. Se o canal não permitir verificar, expor ao dono antes de qualquer novo envio. |
| Banco indisponível, disco cheio ou limite da fila atingido | Só confirmar agendamento depois da gravação durável. Interromper o despacho se o estado não puder ser gravado. Sinalizar degradação e reconciliar entradas na retomada conforme a reentrega disponível no canal. Limitar bytes e concorrência de mídia para preservar o despacho de tarefas já aceitas. |
| Falha inesperada ou tentativas esgotadas | Encaminhar a operação para revisão persistente com motivo, última tentativa e correlação. Permitir reprocessar após correção, com a mesma chave de operação e checagem de prazo, cancelamento e efeitos já realizados. |

Se uma integração acumular falhas consecutivas, suspender novas chamadas por um intervalo e sondar sua recuperação com uma requisição controlada. Uma falha no Gmail, por exemplo, deve deixar o relatório disponível para download e preservar os envios de WhatsApp. Registrar detalhes técnicos sanitizados para diagnóstico e mostrar ao usuário se o trabalho foi salvo, está pendente ou exige ação dele.

## 6. Comportamento por área

### Agenda, lembretes e datas pessoais

- Permitir ao proprietário selecionar no painel as agendas de leitura e a agenda padrão de escrita. Suportar criação, consulta por dia/semana/mês, alteração, cancelamento, recorrência e conflito de horários. Ao alterar uma série, resolver se o pedido vale para uma ocorrência ou para a série. Um conflito de agenda precisa aparecer na resposta.
- Para “toda a minha agenda de novembro”, buscar todas as páginas do período, expandir ocorrências e incluir eventos de dia inteiro e os que cruzam os limites do mês. Ordenar por data local e dividir respostas longas no WhatsApp por semana. A interface de agenda usa os mesmos resultados.
- Buscar atualização do provedor antes de afirmar que a agenda está atualizada. Usar sincronização incremental com cursor e reconciliação periódica. Se a credencial expirar ou a agenda estiver indisponível, informar quando foi a última sincronização. Edição feita no celular remarca ou cancela também o aviso vinculado ao evento após sincronização.
- Persistir intenção externa, usar identificador estável na criação do evento e checar versão nas alterações concorrentes. Se o Google aceitou uma criação antes de cair a conexão, reconciliar pelo ID. Um lembrete puramente pessoal pode existir sem compromisso no calendário.
- Bom dia às 07h e boa noite às 21h são regras locais que enviam ao próprio Álvaro. Podem incluir o primeiro compromisso ou resumo do dia. Se a IA estiver fora, enviar o texto básico. “Meu aniversário é dia X” cria um fato e uma regra anual deduplicada. Proposta inicial: incluir os parabéns no bom dia; permitir aviso dedicado. Mudança da data invalida ocorrências futuras antigas. Em 29/02, registrar a preferência para anos sem esse dia.

### Memória de meses ou anos

O Jarvis monta cada turno com o relógio, as últimas interações pertinentes, os fatos pessoais relevantes e os resultados das consultas. Recuperar fatos e lançamentos estruturados por SQL. Para conversa antiga, começar por resumos datados e busca textual em português no PostgreSQL. Adicionar busca semântica somente se uma avaliação real de recordação justificar. O histórico do WhatsApp e a janela de contexto do modelo não são o arquivo permanente do assistente.

“Gosto de respostas diretas” vira preferência explícita. Um hábito percebido em vários turnos pode influenciar o tom com menor prioridade. Guardar a origem e evitar transformar palpite em fato pessoal. “O que você sabe sobre mim?”, “muda meu apelido” e “esquece isso” são casos de uso. Ao esquecer um fato, remover também suas versões sensíveis e invalidar resumos/índices derivados, com marca de exclusão para evitar reimportação do histórico. A auditoria registra o ato de exclusão sem repetir o conteúdo. A retenção dos backups define quando cópias antigas expiram.

### Gastos, cálculo, planilhas e relatórios

Para “posso gastar R$ 250?”, consultar saldo informado com data, receitas confirmadas, vencimentos, reserva e limite da categoria dentro de um horizonte explícito. Calcular disponível como saldo-base + receitas recebidas após a data desse saldo + entradas futuras confirmadas até o fim do horizonte − despesas pagas após a data do saldo − compromissos ainda em aberto que vencem no horizonte − reserva. Associar pagamento e compromisso para evitar dupla contagem. Calcular separadamente a folga da categoria. Se faltar saldo atualizado ou uma fatura relevante, mostrar qual dado falta. Em consultas históricas, totalizar os registros do intervalo; em simulações, informar as premissas usadas. Usar centavos e `Decimal` na conversão e no arredondamento.

Uma foto de cupom pode trazer subtotal, desconto, total pago e troco. Extrair campos separados e conferir as relações aritméticas no código. Se “89,90” e “39,90” forem leituras plausíveis, perguntar apenas o valor. “Guarda isso na planilha” também funciona para dados não financeiros: selecionar destino e esquema já cadastrados, por exemplo data/item/quantidade em uma lista de compras. Quando houver duas planilhas plausíveis, perguntar qual. Tratar texto iniciado por fórmula como texto literal no Excel e usar modo de valores brutos no Sheets.

Para Excel, usar arquivo Jarvis com aba e coluna de ID estável, escrita exclusiva e substituição atômica do arquivo. Se estiver sendo editado ou bloqueado, manter a exportação pendente; não sobrescrever alterações detectadas. Para Sheets, preferir aba administrada pelo Jarvis, mapear ID de registro para linha, verificar o ID antes de atualizar e reconciliar uma escrita incerta antes de tentar de novo. Correção de despesa atualiza o mesmo registro. “Salvei o gasto” e “atualizei a planilha” são duas confirmações distintas.

“Envia pro meu e-mail o relatório de outubro” resolve período e assunto pelo contexto. Se houver mais de um tipo possível, perguntar qual relatório. Guardar parâmetros, corte dos dados, arquivo gerado e situação do envio. Gerar PDF legível e XLSX quando solicitado. Usar conta e destinatário definidos pelo dono. Persistir a identificação da tentativa antes da chamada ao Gmail. Se o resultado for incerto, reconciliar quando a permissão disponível permitir; caso contrário, mostrar estado incerto. Separar “arquivo pronto” de “e-mail aceito”.

### Modelos, privacidade e personalidade

| Capacidade | Opção inicial | Critério para habilitar |
|---|---|---|
| Conversa / interpretação | Ollama `qwen2.5:3b`. Modelos da Groq e da OpenAI selecionáveis separadamente. | JSON tipado para intenções e dados mínimos no prompt. Testar português, datas, ambiguidade, correções, tom direto e resistência a conteúdo de terceiros. |
| Áudio | faster-whisper, modelo `base` multilíngue, português, CPU int8. | Preparar pesos uma vez, limitar tamanho/duração e testar comando de voz com ruído, silêncio e horário semelhante. |
| Imagem | Ollama `qwen3-vl:2b`. OCR de apoio quando necessário. | Testar recibos reais anonimizados ou sintéticos, fotos sem legenda e dígitos ambíguos. Modelo remoto de visão só quando configurado. |
| Figurinhas | Catálogo local de IDs e arquivos WebP válidos. | A resposta escolhe um item existente. A confirmação de envio vem do adaptador. |

A configuração de cada capacidade inclui provedor, modelo e limite de tempo. No perfil local, uma falha do Ollama não autoriza encaminhar conversas, imagens ou gastos à nuvem. No perfil híbrido, enviar ao provedor habilitado apenas o conteúdo necessário ao pedido. O painel mostra o provedor ativo e o resultado do diagnóstico. Guardar prompts versionados e testar a personalidade em amostras, com atenção a respostas que alegam ações inexistentes. “Aprender” aqui significa registrar preferências e fatos recuperáveis durante a conversa. Não há necessidade de retreinar um modelo a cada interação.

## 7. Painel e API

| Tela / ação | Contrato inicial | O que o usuário consegue verificar |
|---|---|---|
| Início e conexões | `GET /api/v1/status`, `GET /api/v1/integrations` | Próximo evento, envios pendentes, sessão WhatsApp, provedor de IA e hora da última sincronização. |
| Agenda | `GET/POST /api/v1/events`, `PATCH/DELETE /api/v1/events/{id}` | Semana/mês, agenda de origem, recorrência, edição e cancelamento. |
| Lembretes, rotinas e envios | `GET/POST /api/v1/schedules`, `PATCH/DELETE /api/v1/schedules/{id}`, `GET /api/v1/deliveries` | Texto previsto, contato, hora local, estado e motivo de falha. |
| Memória e contatos | `/api/v1/memory`, `/api/v1/contacts` | Consultar, corrigir ou excluir fatos; vincular identidade e apelidos. |
| Finanças e documentos | `/api/v1/ledger`, `/api/v1/budgets`, `/api/v1/sheets`, `/api/v1/reports` | Corrigir lançamentos, cadastrar destinos, revisar gastos e baixar relatórios privados. |
| Configuração e pareamento | `/api/v1/settings`, `/api/v1/whatsapp/pairing`, `/api/v1/integrations/google` | Iniciar pareamento local, ver QR temporário autenticado, escolher provedores e conectar uma conta por OAuth. |

Especificar schemas e exemplos no OpenAPI. Paginar listagens por cursor e validar intervalos. Usar chave de idempotência em comandos mutáveis e versão em edições para detectar concorrência. Erros estáveis como `contact_ambiguous`, `schedule_in_past`, `provider_unavailable` e `version_conflict` orientam a interface. Operações demoradas retornam `202` com ID consultável. O QR é efêmero, exibido ao dono autenticado, mantido só em memória em canal IPC local com permissões restritas entre gateway e API, com invalidação a cada renovação. Registrar o estado de pareamento, nunca a imagem do QR nos logs.

## 8. Preparação da máquina e sequência de inicialização

Foram encontrados Python 3.12.3, Node 24.14.0, npm 11.9.0, Git, FFmpeg, Docker Compose e Ollama. O endereço proposto é `~/Documentos/jarvis`. A pasta do projeto antigo serve apenas como referência. O roteiro distingue o primeiro scaffold de um checkout futuro que já contenha os arquivos.

### Primeiro scaffold no Ubuntu

Executar este primeiro bloco **uma única vez**, ao iniciar a implementação em diretório novo. O uso de `mkdir` sem `-p` para a raiz interrompe a operação se já houver um projeto ali. Os comandos rodam em um subshell para não encerrar o terminal do usuário em caso de erro.

~~~bash
(
  set -eu
  umask 077
  cd "$HOME/Documentos"
  mkdir jarvis
  cd jarvis
  git init -b main
  mkdir -p backend/src/jarvis/{entrypoints,bootstrap,http,shared}
  mkdir -p backend/src/jarvis/modules/{identity,conversation,media,calendar,scheduling,messaging,memory,finance,spreadsheets,reports}
  mkdir -p backend/src/jarvis/adapters/{whatsapp,ai,media,google,files,persistence,jobs}
  mkdir -p backend/{migrations/versions,tests/{unit,integration,contract,e2e,fixtures}}
  mkdir -p infra/systemd docs/{adr,contracts,runbooks,sprints} scripts
  python3 -m venv backend/.venv
  npm create vite@latest frontend -- --template react-ts
  cd frontend
  npm install
)
~~~

Concluir na S00: criar `.gitignore` **antes do primeiro commit**, `backend/pyproject.toml`, o pacote Python sob `src/jarvis`, `.env.example`, migrações, `infra/compose.yaml`, Makefile e arquivos de lock. Fixar a versão do scaffold e do Node usadas no primeiro commit. Nos checkouts seguintes usar `npm ci`. Se o Ubuntu sinalizar falta de `ensurepip`, instalar o pacote `python3-venv` compatível com o Python e repetir apenas a criação da virtualenv.

No backend, começar com FastAPI, Uvicorn, Pydantic Settings, SQLAlchemy, psycopg, Alembic e um cliente HTTP. Adicionar por sprint Neonize, faster-whisper, Pillow, openpyxl, gerador de PDF e bibliotecas OAuth/Google. Qualidade: pytest, Ruff e verificador de tipos. Frontend: React Router, cliente de dados, Vitest, Testing Library e Playwright. Manter um lock Python compilado no Linux com `pip-tools` e o `package-lock.json` do npm. Registrar a versão do compilador de lock. A dependência `neonize==0.10.0` é o ponto de compatibilidade verificado no bot de referência.

### Configuração inicial

Exemplo do contrato de configuração a implementar. Após S00, copiar `.env.example` para `.env` com permissões `0600`, escolher senha própria do PostgreSQL e configurar os dados do proprietário com o assistente local. As chaves externas ficam vazias para Álvaro preencher quando quiser. Nenhum segredo do bot antigo precisa ser importado.

~~~dotenv
APP_ENV=local
APP_TIMEZONE=America/Manaus
API_BIND=127.0.0.1
API_PORT=8787
POSTGRES_USER=jarvis
POSTGRES_DB=jarvis
POSTGRES_PASSWORD=
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=55432
LLM_PROVIDER=ollama
VISION_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5:3b
OLLAMA_VISION_MODEL=qwen3-vl:2b
TRANSCRIPTION_PROVIDER=faster_whisper
WHISPER_MODEL=base
GROQ_API_KEY=
GROQ_MODEL=
OPENAI_API_KEY=
OPENAI_MODEL=
ALLOW_CLOUD_FALLBACK=false
~~~

`settings.py` deve montar a URL do banco a partir dos campos separados e rejeitar senha em branco ao subir a infraestrutura. Guardar sessão, anexos ativos, exports e caches sob `~/.local/share/jarvis/`; configuração privada sob `~/.config/jarvis/`. Usar permissões restritas e ignorar `.env`, `.venv`, `node_modules`, DBs de sessão, artefatos e cache no Git. Segredos Google e tokens OAuth ficam fora do frontend. Com perfil local ativo, a ausência de chaves Groq/OpenAI é válida.

### Subir um checkout após a implementação

Ao chegar aqui, o repositório já deve incluir lock, migrações e pontos de entrada. Esta é a interface operacional que a S00 vai instituir e as demais sprints vão completar. Executar na ordem:

~~~bash
cd "$HOME/Documentos/jarvis"
python3 -m venv backend/.venv
source backend/.venv/bin/activate
python -m pip install -r backend/requirements.lock
python -m pip install --no-deps -e ./backend
npm --prefix frontend ci
docker compose --env-file .env -f infra/compose.yaml up -d --wait postgres
python -m alembic -c backend/alembic.ini upgrade head
ollama list
ollama run qwen2.5:3b "Responda em português com a palavra pronto."
python -m jarvis.entrypoints.cli doctor
~~~

No Compose, definir healthcheck do PostgreSQL, publicar `127.0.0.1:55432:5432` e apontar o volume para o diretório de dados da imagem escolhida e fixada. Resolver o caminho das migrações com base na localização de `alembic.ini`. O diagnóstico deve checar escrita nos diretórios, conexão ao banco, revisão Alembic, relógio, Blob/Whisper e modelos disponíveis. A preparação inicial baixa os pesos de voz quando necessário; para os Qwen, só executar `ollama pull <tag>` se a tag não estiver presente.

Depois de preparar banco e credenciais locais, iniciar os processos em terminais separados, na raiz do repo, com a virtualenv ativa nos terminais Python:

~~~bash
python -m uvicorn jarvis.entrypoints.api:app --host 127.0.0.1 --port 8787
~~~

~~~bash
python -m jarvis.entrypoints.worker
~~~

~~~bash
python -m jarvis.entrypoints.whatsapp
~~~

~~~bash
npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173
~~~

Primeira configuração pelo painel: cadastrar o proprietário local, parear o número do Jarvis pelo QR, verificar o número de comando de Álvaro, selecionar um destinatário de teste pela agenda ou pelo número e testar texto e voz. Conectar calendário/e-mail por OAuth quando chegar à respectiva sprint. Usar conta de teste e destinatário controlado para o primeiro ensaio. Para uso contínuo, gerar o build do frontend e instalar unidades `systemd`; definir início no boot, reinício com espera progressiva e diretório de trabalho explícito. Ao optar por unidades de usuário, habilitar o gerenciamento necessário para iniciar sem login. Configurar o host para ficar acordado nos horários esperados.

## 9. Estratégia de testes

| Camada | Como executar | Casos que precisam passar |
|---|---|---|
| Unidade | pytest, relógio injetado, sem rede | Centavos e vírgula decimal; dois contatos homônimos; horários relativos e minutos arbitrários; 08h59/09h01; virada de ano/mês; 29/02; precedência de preferências; orçamento incompleto; edição após confirmação. |
| Contrato | Fixtures sintéticas versionadas para Neonize, Ollama, Groq, OpenAI e Google. Validar também o cliente TypeScript contra OpenAPI. | JID e LID; áudio silencioso; foto sem legenda; figurinha; JSON inválido ou truncado; 401/429/timeout; resposta de modelo que tenta inventar uma ação; OAuth expirado. |
| Integração | PostgreSQL descartável da mesma versão de operação; Alembic em banco vazio e em snapshot da revisão anterior | Entrada reentregue após reinício; duas reservas concorrentes de tarefa; rollback; lease órfão; cancelamento concorrente; XLSX bloqueado; mudança de compromisso no celular e sincronização paginada. |
| Ponta a ponta do backend | Canal, relógio, modelos e provedores substituídos por implementações controláveis, com DB real | Áudio→contato→agendamento→reinício→envio único no ensaio; alteração antes do disparo; queda entre transmissão e confirmação; recibo duplicado; ordem de mensagens no mesmo chat. |
| Frontend | Vitest e Testing Library; Playwright para fluxos completos | Login, mês com eventos de dia inteiro, revisão de envio incerto, troca de horário, edição de memória, estados de carregamento/erro e uso no celular. |
| Homologação local | Serviços reais habilitados individualmente, conta e números controlados; medir texto, áudio e foto na máquina | QR e reconexão; exemplo de voz sem comando escrito; prazo do envio sob carga de visão; evento criado pelo celular; relatório recebido no e-mail; restauração de backup em ambiente isolado. |

Preparar uma base pequena e versionada de exemplos em português com resultado esperado: fala espontânea, ruído, abreviações, horários relativos, pedidos compostos e ambíguos. Rodar os mesmos exemplos ao trocar o modelo ou o prompt e apresentar acerto por intenção, destinatário, data, valor e taxa de perguntas desnecessárias. **Critério de liberação de ações automáticas**: nenhum caso crítico do conjunto de aceite pode enviar ao destinatário errado, executar sem autorização, trocar o valor ou inventar horário. Ampliar o conjunto quando surgir uma falha real. Uma tag de modelo instalada por si só não satisfaz esse critério.

**Ensaios de envio.** Parametrizar por destinatário (nome, apelido, cartão ou número), canal de entrada (voz ou texto), data, horário e conteúdo. Usar contatos diferentes e temas como trabalho, viagem e lembrete pessoal. Verificar envio pontual e recorrente, resolução de contato no primeiro uso e pedido de esclarecimento para homônimos. Incluir dados novos em cada execução para detectar nomes, horas ou frases fixados no código.

**Um dos cenários de reinício.** Usar relógio simulado às 08h10, um único contato “Gabriela teste” e um áudio sintético. Verificar transcrição, horário registrado de 13h UTC, edição opcional e ausência de tráfego antes da hora. Reiniciar gateway e worker, avançar o relógio para as 09h e observar uma transmissão ao destinatário simulado. Em segundo ensaio, provocar perda de confirmação depois da chamada e verificar que nenhum novo envio sai sem reconciliação. Em homologação real, usar aviso inofensivo com o mesmo formato e conferir o telefone de destino.

### Capacidade e critérios mensuráveis de robustez

Protocolo inicial de homologação, com metas propostas para o host que ficará ligado. Registrar versão dos modelos, hardware e recursos disponíveis. Rodar primeiro com relógio e canal controlados e repetir uma amostra com o aparelho de teste.

- **Pontualidade.** Agendar 100 ocorrências distribuídas em 10 minutos no canal simulado enquanto a fila de mídia processa áudios e imagens. Medir o tempo entre o vencimento e o início da transmissão. Meta inicial de p95 até 5 segundos com banco e canal disponíveis. Medir o aceite do provedor e o recebimento no telefone separadamente.
- **Integridade.** Rodar a mesma carga com um e com dois consumidores, injetar duplicatas e encerrar um processo antes e depois da transmissão. Esperar zero lançamentos duplicados, zero inversões dentro da conversa e classificação explícita de cada envio incerto. Incluir indisponibilidade do banco, 429 e expiração de credencial.
- **Recuperação.** Restaurar o backup em ambiente isolado e medir a idade real dos dados recuperados e o tempo até o serviço estar utilizável. Metas iniciais propostas de perda máxima de 24h e restauração em até 2h, sujeitas ao teste com a cópia em segundo dispositivo.
- **Decisão de escala.** Registrar p95 por fila, idade da tarefa mais antiga, vazão, conexões SQL, memória e uso de CPU/GPU. Comparar as execuções sob a mesma carga e identificar qual recurso saturou antes de adicionar processos ou hardware. Manter os resultados como evidência de aceite.

## 10. Operação e recuperação

- **Autenticação local.** Inicializar o primeiro dono com segredo de uso único gerado pela CLI e consumido no loopback. Usar sessão do painel com cookie HttpOnly, SameSite e proteção CSRF em mutações. Segredos de sessão e tokens OAuth precisam de proteção por permissões e criptografia com chave fora do banco. Autorizar os menores escopos necessários para as agendas selecionadas, o envio de e-mail e as planilhas configuradas.
- **Saúde do serviço.** Separar processo vivo, serviço pronto e integração degradada. O dashboard mostra conexão do canal, heartbeat dos processos, atraso da fila mais antiga, tentativas incertas, último backup e última sincronização. Desconectar o modelo de IA não deve impedir a entrega de mensagens já preparadas.
- **Rastreamento.** Correlacionar `inbound_id`, `command_id`, `schedule_id` e `attempt_id`. Registrar transições e tempos; omitir QR, chaves, tokens, transcrições, conteúdo de fotos e corpos de mensagens dos logs operacionais. Manter o histórico pessoal em banco protegido com política de retenção própria.
- **Backup.** Agendar `pg_dump` e cópia consistente das mídias e configurações indispensáveis. Para a base de sessão Neonize, usar backup consistente de SQLite ou janela com gateway parado. Guardar cópia criptografada em segundo dispositivo com chave recuperável. Proposta: diário, sete diários e quatro semanais; testar restauração mensalmente. Ao restaurar, validar fatos, agenda, ID do último lançamento e pendências de envio. Reparear se a sessão foi invalidada.
- **Atualizações.** Antes da migração, criar backup e testar upgrade com dados sintéticos da versão anterior. Após a atualização, validar conexão, consulta da agenda e fila. Para voltar de uma migração incompatível, usar a versão anterior do aplicativo com o backup consistente, reconciliando efeitos externos ocorridos depois daquele backup.
- **Disponibilidade.** Para cumprir horários reais, máquina ligada e acordada, relógio sincronizado, conexão com a internet e sessão válida. Desligamento e suspensão seguem a política de atraso. O pareamento Neonize é uma integração de terceiros, sujeita a mudanças do WhatsApp e a restrições na conta. A escolha de uma sessão exclusiva também preserva o funcionamento independente do bot de referência.

## 11. Sprints e critérios de saída

As sprints são incrementos funcionais. Dependências vêm na segunda coluna. O aceite exige demonstração pelo canal usado pelo usuário, além dos testes do serviço.

| Sprint | Depende de | Entrega demonstrável | Critério de saída |
|---|---|---|---|
| S00 · Base local | — | Pastas, virtualenv, frontend, Git, locks, Compose Postgres, Alembic, OpenAPI, login inicial e diagnóstico. | Checkout limpo sobe conforme README. CI executa lint, tipagem, testes de unidade/integração e build do painel em dados sintéticos. API e painel respondem. Migração e testes rodam em banco descartável. Nenhuma chave de API é exigida no perfil local. |
| S01 · WhatsApp e conversa | S00 | QR próprio, autorização do dono, inbox e outbox persistentes, conversa natural com Ollama e envio simples. | Dono recebe resposta. Terceiro e grupo não comandam nada. Reinício preserva sessão e uma entrada reentregue não provoca nova ação. |
| S02 · Voz, contato e envio agendado | S01 | Transcrição local, aliases, agendar/remarcar/cancelar, fila de WhatsApp e tela de envios. | Demonstrar envios a diferentes contatos com assuntos livres, datas e horários variados, incluindo um contato usado pela primeira vez. Cobrir ambiguidade, horário passado, recorrência, envio incerto e disparo enquanto o modelo atende outro pedido. **Primeira versão utilizável.** |
| S03 · Agenda e rotinas | S02 | Google Agenda, vistas de semana/mês, alteração externa, lembretes avulsos, bom dia, boa noite e aniversário. | Criar por voz, remarcar pelo celular e consultar pelo painel. Sem aviso antigo após sincronização. Exatamente uma ocorrência anual do aniversário no teste. |
| S04 · Memória e provedores | S02 | Fatos e estilo com origem, resumos, consulta/correção/esquecimento, seleção de modelos Groq/OpenAI e perfis de privacidade. | Recuperar fato antigo, respeitar correção após reiniciar e após trocar o provedor. Chave ausente desabilita apenas a opção externa correspondente. |
| S05 · Visão, figurinhas, finanças e planilhas | S02, S04 | Leitura de fotos, catálogo de figurinhas, lançamentos e orçamento, Excel e adaptador Sheets. | Foto→lançamento→linha única. Corrigir valor atualiza mesma linha. Comparar gasto com reserva e orçamento. Enviar figurinha do catálogo; extras incertos viram pergunta. |
| S06 · Relatórios e e-mail | S03, S05 | Relatórios por período com agregações verificadas, PDF/XLSX, envio e rastreamento. | Pedir relatório por voz e recebê-lo no e-mail de teste. Conferir os totais com a base e reconciliar falha do provedor sem repetir envio às cegas. |
| S07 · Operação contínua | S03–S06 | systemd, documentação operacional, backups, restauração, observabilidade e homologação final. | Reiniciar a máquina, recuperar sessão e fila, validar rotinas e simular indisponibilidade. Restaurar em perfil isolado, verificar pendências antigas e registrar os resultados dos ensaios de pontualidade, integridade, capacidade e recuperação da seção 9. |

### Documentação que acompanha o código

| Artefato | Conteúdo verificável |
|---|---|
| `README.md` | Requisitos, preparação do zero, perfil sem chaves, comandos de desenvolvimento e primeiro teste seguro. |
| `docs/adr/` | Decisões de monólito modular, Neonize isolado, dono autorizado, fila PostgreSQL, memória, política de provedores e recuperação de envio incerto. Cada decisão registra motivo e quando reavaliar. |
| `docs/contracts/` | Eventos e estados, schemas de intenções, ferramentas autorizadas, OpenAPI, políticas de prazo e glossário de “agendado / aceito / entregue”. |
| `docs/runbooks/` | Parear/reparear, preparar Whisper e Qwen, conceder OAuth, recuperar fila, renovar credenciais, instalar as unidades systemd, fazer e restaurar backup. |
| `docs/sprints/` | Escopo, dependências, casos de teste, evidências reproduzíveis, critérios de saída e limitações identificadas. |

**Pronto para uso pessoal contínuo** quando as sprints tiverem evidência de aceite, o dono conseguir operar e corrigir os dados por voz ou painel, os totais financeiros conferirem com os registros e a recuperação após falha tiver sido demonstrada no equipamento que ficará ligado.

## Referências técnicas

- Projeto local examinado: `/home/alvaro/Documentos/whats-manha`. Arquivos e linhas pertinentes estão na seção 1.
- Pareamento e integração: [Neonize](https://github.com/krypton-byte/neonize) · [WhatsApp sobre dispositivos conectados](https://faq.whatsapp.com/378279804439436/?locale=pt_BR).
- Modelos e contratos: [Ollama e saídas estruturadas](https://docs.ollama.com/capabilities/structured-outputs) · [Ollama Vision](https://docs.ollama.com/capabilities/vision) · [Groq e saídas estruturadas](https://console.groq.com/docs/structured-outputs) · [Ferramentas na API OpenAI](https://developers.openai.com/api/docs/guides/function-calling) · [faster-whisper](https://github.com/SYSTRAN/faster-whisper).
- Agendamento e integrações: [PostgreSQL e bloqueios de linha](https://www.postgresql.org/docs/current/sql-select.html) · [Sincronização do Google Agenda](https://developers.google.com/workspace/calendar/api/guides/sync) · [Eventos recorrentes](https://developers.google.com/workspace/calendar/api/guides/recurringevents) · [Envio pelo Gmail](https://developers.google.com/workspace/gmail/api/guides/sending) · [Atualização de valores no Sheets](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/update).
