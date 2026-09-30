# Imagens da base de conhecimento servidas no chat do Dify — Design

- **Data:** 2026-09-30
- **Branch:** `feat/kb-assets`
- **Escopo:** fase 1 (publicação mantida pela DTI)

## Objetivo

O chatbot da DTI/CMRJ que orienta a instalação e o uso do Office 365 deve exibir as
imagens ilustrativas do manual **dentro da conversa**, como em um atendimento por
WhatsApp ou chat de site — e não como links para o usuário clicar.

Critério de sucesso: ao perguntar, por exemplo, "como configuro o Authenticator?", a
resposta no WebApp do Dify mostra o texto dos passos e as imagens correspondentes
renderizadas inline, e as imagens continuam visíveis ao reabrir a conversa depois.

## Contexto e restrições

- Manual em `kb/manual-office365-rag/manual-office365-rag.md`, com 13 imagens em
  `images/` (1,6 MB no total) referenciadas por caminho relativo
  (`![alt](images/04-login-portal-microsoft.png)`).
- Canais previstos: WebApp do Dify, widget embutido em site e, futuramente,
  WhatsApp/Teams. Canal definitivo ainda não definido.
- Manutenção: inicialmente pela equipe da DTI com pouca frequência; futuramente por
  pessoas não técnicas via interface web (fase 2).
- Ambiente DEV em Docker Swarm single-node; acesso externo por HTTP via NGPM
  (`http://dify.dev.dti`), rede overlay externa `net_nginx_pm`.

## Constatações que fundamentam o design

1. **O chat do Dify já renderiza imagens Markdown inline.** O renderizador
   (Streamdown) está configurado no bundle 1.17.1 com
   `harden: { allowedImagePrefixes: ["*"], allowDataImages: true }`; imagens abrem em
   modal ao clicar (`web/app/components/base/image-gallery`). O `.env` não define
   `CSP_WHITELIST`, então nenhuma CSP é enviada (e mesmo com CSP, `img-src *`).
2. **Arquivos do próprio Dify não servem.** O mecanismo de anexos de chunk (tema da
   issue langgenius/dify#34437) serve imagens por URLs assinadas que expiram em
   `FILES_ACCESS_TIMEOUT` (300 s) e alimenta o LLM como entrada de visão — não exibe a
   imagem ao usuário.
3. **Caminhos relativos não funcionam.** Na indexação não são baixados; no chat
   resolveriam contra a URL da página e dariam 404.
4. **O texto do chunk preserva a URL original.** Uma URL absoluta e estável no `.md`
   chega intacta ao contexto do LLM, que pode reproduzi-la na resposta.

## Arquitetura

```
Navegador ──HTTP──> NGPM (dify.dev.dti)
                     ├── /kb-assets/*  ──> kb_assets:80  (nginx:alpine, volume ro)
                     └── /*            ──> dify_nginx:80 (Dify)
```

### 1. Serviço `kb_assets` (em `docker/docker-stack.yml`)

- Imagem `nginx:alpine`, 1 réplica, `restart_policy: any`, sem portas publicadas.
- Rede: somente `net_nginx_pm` (não precisa falar com o Dify).
- Volume nomeado `dify_kb_assets` (nome real no Swarm: `dify_dify_kb_assets`)
  montado **somente leitura** em `/usr/share/nginx/html/kb-assets`.
- Configuração via `configs:` (padrão já usado na stack), arquivo
  `docker/kb_assets/default.conf`:
  - `location /kb-assets/` servindo arquivos estáticos, `autoindex off`,
    `Cache-Control` de 1 dia;
  - qualquer outro caminho responde 404.

### 2. Roteamento no NGPM

Custom location `/kb-assets/` no proxy host `dify.dev.dti`, encaminhando para
`http://kb_assets:80` (o caminho completo é repassado, por isso o nginx serve sob o
prefixo `/kb-assets/`). Feito pela interface do NGPM; passo documentado no README.

URL final: `http://dify.dev.dti/kb-assets/<slug>/<arquivo>`.

### 3. Script `kb/publish.sh`

Uso: `kb/publish.sh <pasta-do-manual> <slug>`
(ex.: `kb/publish.sh kb/manual-office365-rag office365`).

1. Copia `<pasta>/images/*` para `/<slug>/` no volume usando um container
   temporário `busybox` (o volume pertence ao Swarm; o serviço monta em ro).
   A cópia substitui arquivos de mesmo nome e remove do slug os que não existem
   mais na origem.
2. Gera `<pasta>/build/<nome>.dify.md` reescrevendo `](images/` para
   `](${KB_ASSETS_BASE_URL}/<slug>/`. Padrão:
   `KB_ASSETS_BASE_URL=http://dify.dev.dti/kb-assets`. `build/` já é ignorado pelo
   `.gitignore` do repositório.
3. Verifica com `curl` que cada imagem referenciada responde HTTP 200; lista as que
   falharem e termina com código de erro.

Pré-condições checadas pelo script: volume existente, pasta `images/` e um único
`.md` na raiz da pasta do manual (fora `README.md`).

### 4. Configuração no Dify (manual, documentada)

- **Base de conhecimento:** enviar o `build/*.dify.md`; chunking **pai-filho** com
  chunks pai separados por `\n## ` para que cada seção volte inteira (texto +
  imagens) na recuperação.
- **Chatflow:** `Início → Knowledge Retrieval → LLM → Resposta`. Instrução do LLM
  deve exigir: reproduzir as imagens do contexto exatamente no formato
  `![descrição](URL)`, logo após o passo que ilustram; nunca inventar, encurtar ou
  alterar URLs; não exibir imagens que não estejam no contexto.
- Texto sugerido do prompt incluído no README.

### 5. Versionamento

- Toda a mudança na branch `feat/kb-assets`, commits separados: spec; manual em
  `kb/`; serviço + config nginx; script; documentação.
- `docker stack deploy` e merge na `main` somente com autorização do usuário.

## Tratamento de erros

- Imagem inexistente: o chat remove silenciosamente a `<img>` quebrada
  (`onError`), então o risco é a imagem simplesmente não aparecer — mitigado pela
  verificação HTTP 200 do script.
- `kb_assets` fora do ar: mesma consequência; o texto da resposta continua íntegro.
- LLM alterando URLs: detectado nos testes; se ocorrer, adicionar (fora deste
  escopo) um nó de código que valide as URLs contra a lista publicada.

## Testes / verificação

1. `nginx -t` da configuração no container.
2. Após deploy: `curl -I http://dify.dev.dti/kb-assets/office365/04-login-portal-microsoft.png`
   → 200, `Content-Type: image/png`; `curl -I http://dify.dev.dti/kb-assets/` → 404;
   caminho inexistente → 404.
3. Script: execução real publica 13 imagens e gera o `.dify.md` sem nenhum
   `](images/` remanescente; execução com imagem faltando retorna erro.
4. Ponta a ponta: WebApp do Dify exibindo ao menos uma imagem inline em resposta
   do chatbot, e continuando visível ao reabrir a conversa.

## Fora de escopo

- Fase 2: serviço `filebrowser` sobre o mesmo volume para upload por não técnicos.
- Gateway WhatsApp/Teams (exigirá URL acessível pela internet ou envio da mídia
  como anexo).
- Validação automática de URLs na resposta do LLM.
- HTTPS (limitação conhecida do ambiente DEV: porta 443 ocupada pelo DirectAccess).
