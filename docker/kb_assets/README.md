# Base de conhecimento do chatbot da DTI

Manuais em Markdown usados como base de conhecimento no Dify, com imagens
exibidas diretamente nas respostas do chatbot (como em um atendimento por
WhatsApp ou chat de site).

## Como funciona

- Cada manual fica em uma pasta própria com um `.md` e a pasta `images/`, e usa
  caminhos relativos (`![Descrição](images/arquivo.png)`).

A pasta `kb/` (na raiz do repositório) é conteúdo interno, ignorado pelo git, e não é enviada ao repositório remoto.

- O serviço `dify_kb_assets` (stack `dify`) serve as imagens em
  `http://dify.dev.dti/kb-assets/<slug>/<arquivo>`. Essas URLs não expiram.
- O script `publish.sh` copia as imagens para o serviço e gera uma versão do
  `.md` com URLs absolutas em `build/`, que é o arquivo enviado ao Dify.
- O chat do Dify exibe `![...](URL)` como imagem, com ampliação ao clicar.

## Manuais

| Pasta | Slug | Conteúdo |
|---|---|---|
| `kb/manual-office365-rag/` | `office365` | Instalação do Office e configuração do MFA |

## Publicar ou atualizar um manual

1. Edite o `.md` e as imagens da pasta do manual. Nomes de imagem só podem ter
   letras sem acento, números, `.`, `_` e `-`.
2. Rode, na raiz do repositório:

   ```bash
   docker/kb_assets/publish.sh kb/manual-office365-rag office365
   ```

3. No Dify, substitua o documento da base de conhecimento pelo arquivo indicado
   pelo script (`build/<nome>.dify.md`).

Imagens removidas da pasta saem do ar na próxima publicação.

## Configuração única

### NGPM

No proxy host `dify.dev.dti`, aba **Custom locations**, adicione:

- Location: `/kb-assets/`
- Scheme `http`, Forward Hostname `dify_kb_assets`, Forward Port `80`

### Base de conhecimento no Dify

- Envie o `build/<nome>.dify.md`.
- Modo de segmentação **Pai-filho**; separador dos segmentos pai: `\n## `
  (cada seção volta inteira, com texto e imagens).

### Chatflow

`Início → Recuperação de conhecimento → LLM → Resposta`. Instrução sugerida para
o LLM:

```text
Você é o assistente da Diretoria de Tecnologia da Informação da Câmara Municipal
do Rio de Janeiro e ajuda os usuários a instalar e usar o Microsoft Office 365.
Responda em português, em passos curtos, usando somente as informações do
contexto abaixo.

Imagens: quando o contexto trouxer imagens no formato ![descrição](URL) ligadas
aos passos que você explicar, inclua cada uma na resposta exatamente como está
no contexto, em uma linha própria, logo após o passo correspondente. Nunca
invente, encurte, traduza ou altere URLs. Não inclua imagens que não estejam no
contexto e não mostre as URLs como texto ou link.

Contexto:
{{#context#}}
```

## Testes

```bash
docker/kb_assets/test_default_conf.sh   # configuração do nginx
docker/kb_assets/test_publish.sh                # script de publicação
```
