# kb_admin — publicação das imagens da base de conhecimento

Interface web, na mesma stack do Dify, para publicar as imagens dos manuais usados
como base de conhecimento do chatbot e preparar o `.md` para o ingest no Dify.

## Funcionalidades

- Login com o mesmo e-mail e senha do Dify (somente papéis owner, admin e editor).
- Envio do `.zip` de um manual (`<nome>.zip` com a pasta `<nome>/`, o `<nome>.md` e
  `images/`), validado com as mesmas regras do `publish.sh` e do prompt
  `docker/kb_assets/docs/PDF_TO_RAG.md`.
- Publicação das imagens em `<URL do Dify>/kb-assets/<nome>/`, servidas pelo
  `dify_kb_assets`.
- Download do `<nome>.md` com as URLs absolutas do ambiente (DEV, HMG ou PROD), pronto
  para enviar ao Dify.
- Parâmetros recomendados para o ingest (Parent-child, delimitadores, tamanhos, Top K,
  rerank), calculados a partir do próprio `.md`, com avisos do que precisa ser
  corrigido.
- Substituição com confirmação (imagens que entram, saem e mudam e parâmetros que
  mudaram) e exclusão de manuais.

## Configuração única no NGPM

No proxy host do Dify, aba **Custom locations**, adicione:

- Location: `/kb-admin/`
- Scheme `http`, Forward Hostname `dify_kb_admin`, Forward Port `8000`

Faça o deploy da stack antes de criar a custom location (o NGPM desativa o proxy host
se não resolver o nome). Mantenha **Cache Assets** desligado, como para o
`/kb-assets/`. Acesse em `http(s)://<domínio do Dify>/kb-admin/`.

## Variáveis (opcionais, no `docker/.env`)

| Variável | Padrão | Uso |
|---|---|---|
| `KB_ASSETS_BASE_URL` | `${APP_WEB_URL}/kb-assets` | URL base gravada no `.md` baixado |
| `KB_ADMIN_SECRET_KEY` | derivado do `SECRET_KEY` | assinatura do cookie de sessão |
| `KB_CHILD_MAX_LENGTH` | `1000` | teto do chunk filho recomendado |
| `KB_EMBEDDING_MODEL_LABEL` | `text-embedding-3-small` | nome exibido na tabela |
| `KB_RERANK_MODEL_LABEL` | `jina-reranker-v3` | nome exibido na tabela |

O limite máximo de chunk vem de `INDEXING_MAX_SEGMENTATION_TOKENS_LENGTH` (o mesmo do
Dify).

## Deploy

O `docker/deploy.sh` constrói a imagem (`dify-kb-admin`) e a passa para a stack. Os
volumes `dify_kb_assets` e `dify_kb_admin_data` são locais: o `kb_admin` e o
`kb_assets` rodam no nó manager.

## Testes

```bash
docker/kb_admin/run_tests.sh        # pytest em container Python 3.12
```
