# Base de conhecimento do chatbot da DTI

Manuais em Markdown usados como base de conhecimento no Dify, com imagens
exibidas diretamente nas respostas do chatbot (como em um atendimento por
WhatsApp ou chat de site).

## Como funciona

- Cada manual fica em uma pasta própria com um `.md` e a pasta `images/`, e usa
  caminhos relativos (`![Descrição](images/arquivo.png)`).
- A pasta `kb/` (na raiz do repositório) é conteúdo interno, ignorado pelo git,
  e não é enviada ao repositório remoto.
- O serviço `dify_kb_assets` (stack `dify`) serve as imagens em
  `<protocolo>://<domínio-do-dify>/kb-assets/<slug>/<arquivo>` (por exemplo,
  `http://dify.dev.dti/kb-assets/office365/tela.png`). Essas URLs não expiram.
- O script `publish.sh` copia as imagens para o serviço e gera uma versão do
  `.md` com URLs absolutas, gravada em `<pasta-do-manual>/build/<nome>.dify.md`
  (por exemplo, `kb/manual-office365-rag/build/manual-office365-rag.dify.md`),
  que é o arquivo enviado ao Dify. Só as imagens referenciadas no `.md` são
  publicadas.
- O chat do Dify exibe `![...](URL)` como imagem, com ampliação ao clicar.

## Manuais

| Pasta | Slug | Conteúdo |
|---|---|---|
| `kb/manual-office365-rag/` | `office365` | Instalação do Office e configuração do MFA |

## Formato de entrada

Cada manual é uma pasta `kb/<manual>/` com:

- um único arquivo `.md` (um `README.md` opcional é ignorado);
- a pasta `images/` com as imagens.

A conversão de PDF para `.md` é feita fora deste projeto: o `.md` e as imagens
já chegam prontos. Para gerar esse pacote a partir de um PDF com um LLM, use o
prompt em [`docs/PDF_TO_RAG.md`](docs/PDF_TO_RAG.md), que já produz o formato
aceito pelo `publish.sh`.

As imagens só podem ser escritas como `![descrição](images/arquivo.png)` (ou
`./images/arquivo.png`). Os nomes de arquivo só podem ter letras sem acento,
números, `.`, `_` e `-`. Imagens absolutas `http://` ou `https://` são mantidas
como estão.

O script recusa (e lista todos os problemas antes de publicar qualquer coisa):

- imagem em HTML (`<img ...>`);
- imagem fora de `images/` (por exemplo, `imgs/a.png`, `../a.png`, `/a.png` ou
  `a.png`);
- imagem estilo referência (`![descrição][id]`);
- nome de imagem inválido ou arquivo referenciado que não existe em `images/`.

## Publicar ou atualizar um manual

1. Edite o `.md` e as imagens da pasta do manual.
2. Rode, na raiz do repositório:

   ```bash
   docker/kb_assets/publish.sh kb/manual-office365-rag office365
   ```

   O script pergunta o domínio e o protocolo e pede confirmação:

   ```text
   Domínio do Dify (ex.: dify.dev.dti, chat.hmg.dti): dify.dev.dti
   Protocolo [http/https] (padrão: http):
   As imagens serão publicadas em http://dify.dev.dti/kb-assets/office365/
   Confirma? [S/n]:
   ```

   Para uso não interativo, informe a URL base pela variável
   `KB_ASSETS_BASE_URL` (nesse caso nada é perguntado):

   ```bash
   KB_ASSETS_BASE_URL=http://dify.dev.dti/kb-assets docker/kb_assets/publish.sh kb/manual-office365-rag office365
   ```

   A máquina que roda o script precisa alcançar o domínio, pois o script
   verifica se cada imagem publicada responde HTTP 200.
3. No Dify, envie o arquivo gerado pelo script,
   `<pasta-do-manual>/build/<nome>.dify.md` (por exemplo,
   `kb/manual-office365-rag/build/manual-office365-rag.dify.md`).

**Atenção:** envie sempre o `build/<nome>.dify.md`, nunca o `.md` original. No
original as imagens aparecem como `images/...` e não carregam no chat.

Só as imagens referenciadas no `.md` são publicadas. Imagens que deixarem de
ser referenciadas no `.md` (ou que forem removidas da pasta) saem do ar na
próxima publicação.

## Configuração única

### NGPM

Faça o deploy da stack (que já inclui o serviço `dify_kb_assets`) ANTES de criar
a custom location. Se o NGPM não conseguir resolver o host `dify_kb_assets`, ele
desativa o proxy host inteiro e o Dify sai do ar (reabrir o proxy host e salvar
de novo resolve).

No proxy host do Dify (por exemplo, `dify.dev.dti`), aba **Custom locations**,
adicione:

- Location: `/kb-assets/`
- Scheme `http`, Forward Hostname `dify_kb_assets`, Forward Port `80`

Mantenha **Cache Assets** desligado nesse proxy host. Quando ligado, o NGPM
cria uma regra por extensão de arquivo (`location ~* ^.*\.(css|js|jpe?g|gif|png|...)$`)
que tem prioridade sobre a custom location `/kb-assets/` e desvia as imagens
para o Dify, que responde 404.

Alternativa, se for preciso manter o Cache Assets ligado: em vez da custom
location, use a aba **Advanced** do proxy host com:

```nginx
location ^~ /kb-assets/ {
  proxy_pass http://dify_kb_assets:80;
}
```

### Base de conhecimento no Dify

- Envie o `<pasta>/build/<nome>.dify.md` (nunca o `.md` original).
- Modo de segmentação **Pai-filho**:
  - Parent-chunk: **Paragraph**, Delimiter `\n#`, Maximum chunk length `3000`;
  - Child-chunk: Delimiter `\n\n`, Maximum chunk length `512`;
  - "Replace consecutive spaces, newlines and tabs": marcado;
  - "Delete all URLs and email addresses": desmarcado;
  - Summary Auto-Gen: desligado.

O limite é em caracteres (o teto de 4000 vem do `.env`). O delimitador `\n#`
separa por qualquer título, mantendo cada passo junto das suas imagens.

### Chatflow

`Início → Recuperação de conhecimento → LLM → Resposta`. Instrução sugerida para
o LLM:

```text
Você é o assistente da Diretoria de Tecnologia da Informação da Câmara Municipal
do Rio de Janeiro e ajuda os usuários a instalar e usar o Microsoft Office 365.
Responda em português, em passos curtos e numerados, usando somente as
informações do contexto abaixo. Se a resposta não estiver no contexto, diga
apenas: "Eu não sei".

Imagens: o contexto contém imagens no formato ![descrição](URL). Sempre que um
passo da sua resposta corresponder a uma imagem do contexto, copie o markdown
dessa imagem exatamente como está, em uma linha própria, logo após o passo.
Inclua todas as imagens relacionadas aos passos que você explicar. Nunca
invente, encurte, traduza ou altere URLs, não inclua imagens que não estejam
no contexto e não mostre as URLs como texto ou link.

Contexto:
{{#context#}}
```

## Testes

```bash
docker/kb_assets/test_default_conf.sh   # configuração do nginx
docker/kb_assets/test_publish.sh        # script de publicação
```
