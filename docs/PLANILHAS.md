# Planilhas do Jarvis

Em **Finanças e planilhas**, o painel mostra a situação do Excel e do Google Sheets para o vínculo ativo do WhatsApp. O Excel local funciona sem credenciais. Para obter uma cópia atualizada, aguarde a sincronização e clique em **Baixar Excel**. O arquivo contém as despesas, as receitas e os registros gerais. As metas de orçamento e de reserva ficam no Jarvis.

## Conectar uma planilha do Google

1. Crie um arquivo Google Sheets dedicado a este vínculo. Copie o identificador entre `/spreadsheets/d/` e `/edit` na URL. O Jarvis criará uma aba chamada **Jarvis**. Se já existir uma aba manual com esse nome, renomeie-a primeiro.
2. No projeto correspondente do Google Cloud, ative a API **Google Sheets**. Em **IAM e administrador → Contas de serviço**, crie uma conta de serviço para essa integração. Baixe uma chave no formato JSON e guarde o arquivo fora da pasta do projeto. [Google Sheets API](https://console.cloud.google.com/apis/library/sheets.googleapis.com) · [Contas de serviço](https://console.cloud.google.com/iam-admin/serviceaccounts)
3. No botão **Compartilhar** da planilha, acrescente o e-mail `client_email` dessa conta como **Editor**. A conta pessoal continua proprietária do arquivo. O vínculo com o Google Agenda é independente.
4. No Linux, deixe a chave legível apenas pelo usuário que executa o Jarvis. Por exemplo, execute `chmod 600 ~/.config/jarvis/sheets.json` se esse for o local do JSON. Preencha o `.env` do projeto com o ID real e o caminho absoluto do arquivo.

   ```dotenv
   JARVIS_GOOGLE_SHEETS_ID=IDENTIFICADOR_DA_PLANILHA
   JARVIS_GOOGLE_SHEETS_CREDENTIALS=/home/alvaro/.config/jarvis/sheets.json
   ```

5. Reinicie o Jarvis no mesmo perfil e clique em **Ativar Google Sheets**. A primeira atualização exporta os registros do vínculo ativo. O painel indica quando o serviço confirmou a cópia. **Desativar Google Sheets** interrompe as próximas exportações. As linhas já publicadas continuam na sua conta.

## Atualizações e conflitos

Cada lançamento conserva um código como `F12`. Registros gerais usam códigos como `P14`. Uma correção de valor atualiza a linha daquele código. Ao remover um registro, a próxima sincronização limpa seus dados na aba e mantém o código com o estado **Removido**.

As colunas de A até H dos registros identificados pertencem ao Jarvis. Para notas manuais, use a coluna I em diante, outra aba ou uma linha sem código gerenciado. A coluna H, inicialmente oculta, permite detectar alterações nas células administradas. Se houver conflito, confira o valor original no painel e restaure a linha no editor antes de clicar em **Atualizar Excel** ou **Atualizar Sheets**. Para remover um dado de ambas as cópias, use o painel ou o comando de exclusão do Jarvis.

O arquivo Excel do perfil fica em `planilhas/<identificador-do-vínculo>/Jarvis.xlsx` sob o diretório de dados do Jarvis. Feche esse arquivo no editor para a atualização terminar. O download feito pelo painel é uma cópia independente. Na planilha online, a aba registra o vínculo autorizado e cada linha tem um identificador interno que acompanha seu deslocamento. Quando a conexão cai após uma escrita, o Jarvis confere os identificadores e o conteúdo antes de repetir. Para outra instalação ou outro vínculo, use outro arquivo Google Sheets. [Metadados de linha](https://developers.google.com/workspace/sheets/api/guides/metadata) · [Escrita por filtro de dados](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchUpdateByDataFilter)

A autenticação usa a conta de serviço e o escopo `spreadsheets`, com compartilhamento limitado ao arquivo escolhido. Textos são enviados à API no modo `RAW`. No Excel, textos que começam com `=` são armazenados como texto literal. [Autenticação de serviço](https://developers.google.com/identity/protocols/oauth2/service-account) · [Valores RAW](https://developers.google.com/workspace/sheets/api/reference/rest/v4/ValueInputOption)
