# Dify Swarm — chatbot da DTI/CMRJ

Este projeto sobe o [Dify](https://github.com/langgenius/dify) 1.17.1 em **Docker Swarm** (um nó),
atrás do **Nginx Proxy Manager (NGPM)**, e acrescenta o necessário para o chatbot da Diretoria
de Tecnologia da Informação da Câmara Municipal do Rio de Janeiro (DTI/CMRJ): uma base de
conhecimento feita de manuais em Markdown, com **imagens exibidas nas respostas**, e uma
interface web para publicar e manter esses manuais.

O código do Dify continua neste repositório (pastas `api/`, `web/` etc.). Este README documenta só
o que é próprio do projeto; a documentação original do Dify está no
[repositório oficial](https://github.com/langgenius/dify#readme).

## Sumário

1. [Funcionalidades](#1-funcionalidades)
2. [Arquitetura](#2-arquitetura)
3. [Pré-requisitos](#3-pré-requisitos)
4. [Configurar o `.env`](#4-configurar-o-env)
5. [Deploy e remoção](#5-deploy-e-remoção)
6. [Nginx Proxy Manager (NGPM)](#6-nginx-proxy-manager-ngpm)
7. [SSR do `web`: domínio público e CA interna](#7-ssr-do-web-domínio-público-e-ca-interna)
8. [Base de conhecimento do chatbot](#8-base-de-conhecimento-do-chatbot)
9. [Operação do dia a dia](#9-operação-do-dia-a-dia)
10. [Solução de problemas](#10-solução-de-problemas)
11. [Testes](#11-testes)
12. [Estrutura do repositório](#12-estrutura-do-repositório)
13. [Sobre o Dify](#13-sobre-o-dify)

## 1. Funcionalidades

- **Dify em Docker Swarm**: adaptação do deploy oficial por Compose para `docker stack`, com
  volumes locais, configs do Swarm e nomes completos de serviço (sem colisão com outras stacks
  na rede compartilhada com o NGPM).
- **`deploy.sh`**: confere e completa o `.env` (gera segredos, pede URLs, domínio, IP e CA),
  constrói a imagem do `kb_admin`, faz o deploy e espera o Dify ficar pronto (serviços no ar e
  banco migrado).
- **`remove.sh`**: remove a stack e, se você confirmar, apaga os volumes.
- **Também sem Swarm**: o `deploy.sh --compose` e o `remove.sh --compose` usam o Docker Compose
  do Dify somado ao `docker-compose.kb.yaml`, com o mesmo `kb_assets` e `kb_admin`.
- **Imagens nas respostas do chatbot**: o serviço `kb_assets` (nginx) serve as imagens dos
  manuais em `<URL do Dify>/kb-assets/<manual>/<arquivo>`, com URLs que não expiram.
- **Interface `kb_admin`** (`<URL do Dify>/kb-admin/`), com login pelas credenciais do Dify:
  - envio do `.zip` de um manual, validação, publicação das imagens e download do `.md` com as
    URLs absolutas do ambiente (DEV, HMG ou PROD);
  - parâmetros recomendados para o ingest no Dify, calculados a partir do próprio `.md`;
  - substituição com confirmação (imagens e parâmetros que mudam) e exclusão de manuais;
  - aba **Documentos de apoio**: biblioteca de arquivos com descrição (prompt de conversão
    PDF → Markdown, manuais de uso etc.);
  - perfis: owner e admin alteram; editor só consulta;
  - modo claro e escuro.
- **Prompt de conversão `PDF_TO_RAG.md`**: instruções para uma LLM externa transformar um PDF
  no pacote `.zip` aceito pelo `kb_admin`.

## 2. Arquitetura

```text
navegador ──HTTP(S)──> NGPM (stack ngpm, rede net_nginx_pm)
                        │  proxy host do domínio do Dify
                        ├── /            → dify_nginx:80 ──> dify_web / dify_api / dify_plugin_daemon
                        ├── /kb-assets/  → dify_kb_assets:80   (imagens dos manuais, somente leitura)
                        └── /kb-admin/   → dify_kb_admin:8000  (interface de publicação)

dify_kb_admin ──> dify_api:5001 (login com as credenciais do Dify, rede interna da stack)
dify_kb_admin ──escreve──> volume dify_kb_assets  <──lê── dify_kb_assets
dify_kb_admin ──escreve──> volume dify_kb_admin_data (.md originais, documentos de apoio)
```

Serviços da stack `dify`: `api`, `worker`, `worker_beat`, `web`, `nginx`, `db_postgres`, `redis`,
`weaviate`, `sandbox`, `ssrf_proxy`, `plugin_daemon`, `init_permissions` (tarefa única),
`kb_assets` e `kb_admin`. Só o `nginx`, o `kb_assets` e o `kb_admin` ficam na rede do NGPM;
nenhum serviço publica porta no host.

## 3. Pré-requisitos

- Docker Engine com Swarm ativo (`docker swarm init`, se ainda não estiver) e um nó **manager**.
- Host Linux com os recursos mínimos recomendados pelo Dify.
- NGPM rodando no mesmo Swarm e a rede overlay externa `net_nginx_pm` compartilhada com ele:

```bash
docker network create -d overlay --attachable net_nginx_pm   # se ainda não existir
docker info --format '{{.Swarm.LocalNodeState}}'             # deve retornar "active"
docker network ls --filter name=net_nginx_pm
```

## 4. Configurar o `.env`

Todos os comandos desta seção e da seguinte rodam na pasta `docker/`:

```bash
cd docker
cp .env.example .env
```

Os segredos do `.env.example` são públicos e **não podem** ir para um ambiente real. O `deploy.sh`
(seção 5) detecta isso e oferece gerá-los; para gerar à mão, use `openssl rand -base64 42`. As demais
variáveis do `.env.example` são os padrões do Dify.

| Variável | O que preencher | Exemplo | Obrigatória |
|---|---|---|---|
| `SECRET_KEY` | Assina cookies de sessão e tokens de login. Trocá-la desloga todos os usuários. | segredo gerado | Sim |
| `DB_PASSWORD` | Senha do Postgres. Só vale na **criação** do volume `dify_postgres_data`; para trocar depois, altere também dentro do banco. | segredo gerado | Sim |
| `REDIS_PASSWORD` | Senha do Redis (cache e broker do Celery). | segredo gerado | Sim |
| `PLUGIN_DAEMON_KEY` | Chave que a API usa para chamar o `plugin_daemon`. | segredo gerado | Sim |
| `PLUGIN_DIFY_INNER_API_KEY` | Chave que o `plugin_daemon` usa para chamar a API. | segredo gerado | Sim |
| `SANDBOX_API_KEY` | Chave do serviço `sandbox` (execução de código). | segredo gerado | Sim |
| `WEAVIATE_API_KEY` e `WEAVIATE_AUTHENTICATION_APIKEY_ALLOWED_KEYS` | Chave do Weaviate. As duas precisam ter **o mesmo valor**. | segredo gerado | Sim |
| `CONSOLE_API_URL` | URL pública da API do console. Também é chamada pelo SSR do `web` (seção 7). | `https://dify.hmg.dti` | Sim |
| `CONSOLE_WEB_URL` | URL pública do console. | `https://dify.hmg.dti` | Sim |
| `APP_API_URL` | URL pública da API dos apps publicados. | `https://dify.hmg.dti` | Sim |
| `APP_WEB_URL` | URL pública dos apps publicados. Também define a URL das imagens dos manuais. | `https://dify.hmg.dti` | Sim |
| `FILES_URL` | URL pública dos arquivos enviados. | `https://dify.hmg.dti` | Sim |
| `SERVICE_API_URL` | URL da Service API exibida no console; vazio usa a do navegador. | `https://dify.hmg.dti` | Não |
| `DIFY_PUBLIC_HOST` | Domínio público, injetado no `extra_hosts` do `web` (seção 7). | `dify.hmg.dti` | Sim |
| `DIFY_PUBLIC_HOST_IP` | IP pelo qual o container `web` alcança o NGPM na 443 (seção 7). | `10.100.2.25` | Sim |
| `DIFY_EXTRA_CA_FILE` | Certificado **público** da CA que assina o domínio, em `docker/certs/` (seção 7). Vazio = CA pública. | `./certs/localCA.pem` | Sim (ou vazio) |
| `DIFY_PROXY_NETWORK` | Rede overlay compartilhada com o NGPM. | `net_nginx_pm` (padrão) | Não |
| `INDEXING_MAX_SEGMENTATION_TOKENS_LENGTH` | Tamanho máximo de chunk no Dify (caracteres); também é o teto usado pelo `kb_admin`. | `4000` | Não |

Use a mesma origem (protocolo + domínio) em todas as URLs públicas.

Variáveis opcionais do `kb_admin`:

| Variável | Padrão | Uso |
|---|---|---|
| `KB_ASSETS_BASE_URL` | `${APP_WEB_URL}/kb-assets` | URL base gravada no `.md` baixado |
| `KB_ADMIN_SECRET_KEY` | derivado do `SECRET_KEY` | assinatura do cookie de sessão |
| `KB_CHILD_MAX_LENGTH` | `1000` | teto do chunk filho recomendado |
| `KB_EMBEDDING_MODEL_LABEL` | `text-embedding-3-small` | nome exibido na tabela de parâmetros |
| `KB_RERANK_MODEL_LABEL` | `jina-reranker-v3` | nome exibido na tabela de parâmetros |

## 5. Deploy e remoção

### Com o `deploy.sh` (recomendado)

```bash
./deploy.sh              # pergunta o modo (Swarm ou Compose)
./deploy.sh --swarm      # Swarm, sem perguntar
STACK_NAME=dify-hmg ./deploy.sh --swarm   # outro nome de stack (padrão: dify)
```

O script:

1. Confere o `.env`. Segredos vazios ou iguais aos do `.env.example` podem ser **gerados**
   (alfanuméricos, 42 caracteres) ou **digitados**; as duas chaves do Weaviate recebem o mesmo
   valor. URLs públicas vazias são pedidas no terminal (Enter repete a anterior). Antes de gravar,
   copia o original para `.env.bak.<data-hora>` (ignorado pelo git) e altera só essas linhas. Se o
   banco já existir, avisa que uma `DB_PASSWORD` nova não muda a senha guardada no Postgres.
2. No Swarm, pede `DIFY_PUBLIC_HOST` e `DIFY_PUBLIC_HOST_IP` se estiverem vazios (sugere o host de
   `CONSOLE_API_URL` e o gateway da rede `bridge`) e pergunta o arquivo da CA (seção 7).
3. Confere se o nó é manager e se a rede `DIFY_PROXY_NETWORK` existe.
4. Constrói a imagem do `kb_admin` e exporta `KB_ADMIN_IMAGE` (tag derivada do ID da imagem, para
   o Swarm atualizar o serviço quando o código muda) e `KB_ASSETS_CONF_HASH` (hash do
   `kb_assets/default.conf`, usado no nome do config).
5. Exporta o `.env`, valida o manifesto, pede confirmação e roda o `docker stack deploy`.
6. **Espera o Dify ficar pronto**: serviços no ar, banco migrado (tabela `alembic_version`) e
   nenhuma migração em andamento (trava `db_upgrade_lock` no Redis). O prazo é
   `DEPLOY_WAIT_TIMEOUT` (padrão: 1200 s); Ctrl+C interrompe só a espera.

Sem Swarm (`--compose`), veja "Deploy sem Swarm (Docker Compose)" abaixo.

### Deploy manual

O `docker stack deploy` **não lê o `.env`** para interpolar o `docker-stack.yml` (o `env_file:` só
injeta variáveis dentro dos containers). Exporte-o e calcule as duas variáveis do `kb_admin`:

```bash
while IFS= read -r line; do [[ $line =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] && export "$line"; done < .env
docker build -q -t dify-kb-admin:local kb_admin
export KB_ADMIN_IMAGE="dify-kb-admin:$(docker image inspect -f '{{.Id}}' dify-kb-admin:local | cut -c8-19)"
docker tag dify-kb-admin:local "${KB_ADMIN_IMAGE}"
export KB_ASSETS_CONF_HASH="$(sha256sum kb_assets/default.conf | cut -c1-12)"
docker stack config -c docker-stack.yml > /dev/null   # valida o manifesto
docker stack deploy -c docker-stack.yml dify
```

**Não use `source .env`**: há valores com espaços sem aspas (ex.: `LOG_DATEFORMAT=%Y-%m-%d %H:%M:%S`)
que o bash tentaria executar. Repita a exportação em todo shell novo.

### Deploy sem Swarm (Docker Compose)

O `kb_assets` e o `kb_admin` também rodam sem Swarm. O `docker-compose.yaml` é o original do
Dify (gerado a partir de `docker-compose-template.yaml`) e não é editado; os serviços do projeto
ficam em `docker/docker-compose.kb.yaml`, usado junto com ele:

```bash
./deploy.sh --compose                      # valida e sobe os dois arquivos, construindo o kb_admin
# ou, à mão:
docker compose -f docker-compose.yaml -f docker-compose.kb.yaml up -d --build
```

Diferenças em relação ao Swarm:

- O nginx do Dify publica as portas (`EXPOSE_NGINX_PORT`, padrão 80, e `EXPOSE_NGINX_SSL_PORT`,
  padrão 443) e já encaminha `/kb-assets/` e `/kb-admin/`: o `docker-compose.kb.yaml` troca o
  template dele por `docker/nginx/compose/default.conf.template`, que é o do Dify mais essas duas
  rotas. Não é preciso NGPM; se houver um proxy na frente, basta apontar o domínio para esse nginx.
- O `kb_admin` fala com a API pelo nome curto `api` e lê as variáveis do mesmo `.env`.
- As imagens dos manuais e os documentos ficam nos volumes nomeados `<projeto>_kb_assets_data` e
  `<projeto>_kb_admin_data` (o projeto padrão é `docker`, o nome da pasta). Os dados do Dify ficam
  em `docker/volumes/`, como no Compose original.
- Não se aplicam as variáveis `DIFY_PUBLIC_HOST`, `DIFY_PUBLIC_HOST_IP`, `DIFY_EXTRA_CA_FILE`,
  `DIFY_PROXY_NETWORK`, `KB_ADMIN_IMAGE` e `KB_ASSETS_CONF_HASH`, que são do Swarm.
- Ao atualizar o Dify, se o `nginx/conf.d/default.conf.template` dele mudar, atualize também o
  `nginx/compose/default.conf.template`; o `test_compose_kb.sh` acusa a diferença.

### Verificação

```bash
docker stack services dify
docker service ps dify_init_permissions   # tarefa única; deve terminar em "Complete"
docker service logs -f dify_api           # na primeira subida, as migrações rodam aqui
```

Depois de configurar o NGPM (seção 6), acesse `http(s)://<domínio>/install` para criar o
administrador e `http(s)://<domínio>` para o console.

### Remoção

```bash
./remove.sh                     # stack "dify"
STACK_NAME=difytest ./remove.sh # outra stack
```

O script lista os serviços e volumes da stack (pelo label `com.docker.stack.namespace`) e pergunta
`Apagar também os volumes (dados)? [s/n]`:

- **`n`**: remove a stack, espera terminar e **mantém** os volumes. Um novo deploy com o mesmo
  `STACK_NAME` reaproveita dados e senhas gravadas; use o mesmo `.env`.
- **`s`**: exige digitar o nome da stack e apaga **todos** os volumes dela (banco, arquivos, bases
  vetoriais, imagens dos manuais, documentos de apoio). Não pode ser desfeito. Depois, **limpe os
  dados do site no navegador** (F12 → Application → Storage → Clear site data) ou use uma janela
  anônima: a sessão antiga faz a página mostrar "Ocorreu um erro inesperado ao renderizar este
  componente".

O script não mexe no `.env`, nos certificados nem na rede `net_nginx_pm`.

No modo Compose:

```bash
./remove.sh --compose
```

Ele lista os containers e os volumes nomeados do projeto, faz a mesma pergunta e roda
`docker compose ... down` (com `s` e o nome do projeto confirmado, `down -v`, que apaga os
volumes nomeados, inclusive os do `kb_admin`). A pasta `docker/volumes/`, com os dados do Dify
no Compose, **não é apagada** pelo script: ela também guarda arquivos de configuração
versionados do Dify.

## 6. Nginx Proxy Manager (NGPM)

Faça o deploy da stack **antes** de configurar o NGPM. A configuração abaixo é a recomendada: ela
**não trava o NGPM** se a stack estiver fora do ar e **não precisa de reload** depois de recriar a
stack.

### Passo a passo

1. **Proxy Hosts → Add Proxy Host**, aba **Details**:
   - Domain Names: o domínio de `CONSOLE_WEB_URL` (ex.: `dify.dev.dti`)
   - Scheme: `http` · Forward Hostname: `dify_nginx` · Forward Port: `80`
   - **Cache Assets: desligado**
2. Aba **SSL**: o certificado do domínio, como nos outros hosts do NGPM.
3. Aba **Custom locations**: **não crie** `/kb-assets/` nem `/kb-admin/` aqui. Se já existirem,
   apague as duas (ícone de lixeira).
4. Aba **Advanced** (engrenagem): cole o trecho abaixo **exatamente como está** e salve.

```nginx
# Imagens dos manuais (kb_assets) e interface de publicação (kb_admin).
# O destino fica numa variável: o NGPM resolve o nome a cada requisição. Se a stack estiver fora
# do ar, só estas rotas respondem 502; o proxy host e os outros domínios continuam funcionando.
location ^~ /kb-assets/ {
    set $kb_assets_upstream http://dify_kb_assets:80;
    proxy_pass $kb_assets_upstream;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Scheme $scheme;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Real-IP $remote_addr;
}

location ^~ /kb-admin {
    set $kb_admin_upstream http://dify_kb_admin:8000;
    proxy_pass $kb_admin_upstream;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Scheme $scheme;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Real-IP $remote_addr;
}
```

5. Teste: `http(s)://<domínio>/kb-admin/healthz` responde `ok`, e `http(s)://<domínio>/kb-admin/`
   abre a tela de login.

Os nomes `dify_kb_assets` e `dify_kb_admin` valem para a stack `dify`; com outro `STACK_NAME`, troque
o prefixo (ex.: `dify-hmg_kb_admin`).

### Por que esta configuração e não as Custom locations

- O nginx do NGPM resolve na **inicialização** os nomes usados em Custom locations. Se um deles não
  existir (stack removida ou ainda subindo), ou o NGPM **desativa o proxy host inteiro** (o Dify sai
  do ar), ou o nginx entra em loop com `[emerg] host not found in upstream` e **todos** os domínios
  caem, inclusive o painel na porta 81.
- Com o destino em variável, o nome é resolvido **a cada requisição** pelo DNS do Docker (o NGPM já
  traz `resolver 127.0.0.11 valid=10s`). Stack fora do ar vira só um 502 nessas rotas, e os IPs novos
  de uma stack recriada são usados em até 10 segundos, sem `nginx -s reload`.
- O `^~` dá prioridade a estas rotas sobre as regras de cache por extensão do NGPM. Mesmo assim,
  mantenha **Cache Assets desligado**: ligado, ele desvia para o Dify (404) qualquer imagem ou
  arquivo `.css`/`.js` que não esteja numa rota com `^~`.
- O destino principal do proxy host (`dify_nginx`) já é gerado pelo NGPM em variável e não tem o
  problema.

### Se o NGPM já travou

Para recuperar sem o painel, desative o arquivo do host problemático dentro do container e depois
corrija o host no painel (passo a passo acima):

```bash
docker logs --tail 20 $(docker ps -q -f name=ngpm_ngpm) | grep emerg   # mostra o N.conf culpado
docker exec $(docker ps -q -f name=ngpm_ngpm) mv /data/nginx/proxy_host/N.conf /data/nginx/proxy_host/N.conf.err
```

Se ainda houver Custom locations antigas e a stack tiver sido recriada (502 em `/kb-assets/` e
`/kb-admin/`), um reload resolve até a migração para a aba Advanced:

```bash
docker exec $(docker ps -q -f name=ngpm_ngpm) nginx -s reload
```

### Acesso ao NGPM no WSL (dev)

O NGPM publica as portas 80, 443 e 81 (painel) pelo Swarm, só em **IPv4**. No WSL em modo NAT:

- use o IP da `eth0` do WSL, o primeiro de `hostname -I` (painel em `http://<ip-do-wsl>:81`);
- no `hosts` do Windows, aponte os domínios para esse IP (ex.: `172.19.56.58  dify.dev.dti`), com
  o domínio idêntico ao do proxy host;
- `localhost` pode não responder (resolve primeiro para `::1`); dentro do WSL, use `127.0.0.1`;
- o IP do WSL **muda** a cada reinício: se os domínios `*.dev.dti` pararem de abrir, atualize o
  `hosts` do Windows.

## 7. SSR do `web`: domínio público e CA interna

Os domínios `*.dti` não estão no DNS; quem navega os resolve pelo arquivo hosts. Mas o SSR do
Next.js, **dentro do container `web`**, também chama `CONSOLE_API_URL`. Sem ajuste, o log do `web`
mostra `getaddrinfo ENOTFOUND` e o navegador exibe "Ocorreu um erro inesperado ao renderizar este
componente". Por isso a stack exige, em cada ambiente:

```env
DIFY_PUBLIC_HOST=dify.hmg.dti            # entrada de hosts injetada no container web
DIFY_PUBLIC_HOST_IP=10.100.2.25          # IP pelo qual um container chega ao NGPM na 443
DIFY_EXTRA_CA_FILE=./certs/localCA.pem   # certificado PÚBLICO da CA do domínio; vazio = CA pública
```

- `DIFY_PUBLIC_HOST`: o host de `CONSOLE_API_URL`.
- `DIFY_PUBLIC_HOST_IP`: o gateway da rede `bridge` costuma servir e não muda com reinícios
  (`docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}'`); o IP fixo do
  servidor também serve. Não use um VIP do Swarm (muda a cada deploy) nem o IP do WSL.
- Sintomas de valor errado no log do `web`: `EHOSTUNREACH <ip>:443` ou `ENOTFOUND` (IP ou domínio)
  e `UNABLE_TO_VERIFY_LEAF_SIGNATURE` (CA ausente ou errada).

### Para que serve o certificado da CA

```text
1) navegador ──HTTPS──> NGPM (certificado do domínio) ──> dify_nginx ──> web/api
2) container web (Node, SSR) ──HTTPS──> https://<domínio> (NGPM) ──> dify_nginx ──> api
```

Na conexão 1, o navegador confia no certificado porque a CA está instalada no Windows (ou é
pública). Na conexão 2, o Node.js **não usa** o repositório de certificados do sistema: ele traz a
própria lista de CAs públicas. Com CA interna, a stack transforma `DIFY_EXTRA_CA_FILE` no config
`dify_web_extra_ca`, montado só no `web` em `/etc/ssl/dify/extra-ca.pem`, e
`NODE_EXTRA_CA_CERTS` faz o Node **somar** essa CA às públicas.

| Ambiente | Quem assina o certificado do NGPM | `DIFY_EXTRA_CA_FILE` |
|---|---|---|
| dev e hmg | `localCA` | `./certs/localCA.pem` (versionado) |
| produção com CA pública | Let's Encrypt ou comercial | vazio (responda `nenhum` no `deploy.sh`) |
| produção com CA corporativa | essa CA | o `.pem` público dela, copiado para `docker/certs/` |

Para conferir quem assina o certificado servido e se a CA certa foi escolhida:

```bash
openssl s_client -connect <domínio>:443 -servername <domínio> </dev/null 2>/dev/null | openssl x509 -noout -issuer
openssl verify -CAfile docker/certs/<ca>.pem <fullchain.pem>
```

**Versionamento:** `docker/certs/` é ignorada pelo git, exceto `no-extra-ca.pem` e `localCA.pem`
(certificado **público**, o mesmo que qualquer navegador recebe). A chave `localCA.key` **nunca**
deve ir para lá: se vazar, qualquer pessoa emite certificados válidos para `*.dti`.

**Pergunta do `deploy.sh`** (`Arquivo da CA [localCA.pem]:`): Enter aceita o valor atual ou
`localCA.pem`; outro nome pode ser digitado; `nenhum` grava vazio. O script repete a pergunta
enquanto o arquivo não existir em `docker/certs/` e recusa arquivos com `PRIVATE KEY` ou sem
`BEGIN CERTIFICATE`.

**Troca de certificado:** configs do Swarm são imutáveis. Se a CA mudar, o `deploy.sh` avisa;
remova a stack, espere a remoção e faça o deploy de novo (os dados são preservados).

Teste de dentro do container:

```bash
W=$(docker ps -q -f name=<stack>_web | head -1)
docker exec $W node -e 'fetch(process.env.CONSOLE_API_URL + "/console/api/system-features").then(r => console.log(r.status)).catch(e => console.log(e.cause?.code || e.message))'
```

## 8. Base de conhecimento do chatbot

Os manuais são Markdown com imagens. O chat do Dify exibe `![descrição](URL)` como imagem (com
ampliação ao clicar), então as imagens precisam estar publicadas numa URL que o navegador alcance:
é o papel do `kb_assets` e do `kb_admin`.

### Fluxo

1. **PDF → pacote:** na aba **Documentos de apoio** do `kb_admin`, baixe o prompt `PDF_TO_RAG.md` e
   use-o numa LLM externa com o PDF. Ela gera o pacote:

   ```text
   manual-office365-rag.zip
   └── manual-office365-rag/
       ├── manual-office365-rag.md
       ├── README.md            (opcional, ignorado)
       └── images/
           ├── 01-pasta-office365.png
           └── ...
   ```

2. **Publicar:** em `<URL do Dify>/kb-admin/`, entre com o usuário do Dify e envie o `.zip`.
3. **Ingest:** na tela do manual, baixe o `.md` (já com as URLs absolutas do ambiente) e crie o
   documento no Dify com os parâmetros mostrados ao lado. Envie sempre esse `.md`, nunca o original
   do zip: no original as imagens aparecem como `images/...` e não carregam no chat.

### Regras do pacote

- O nome do zip, da pasta e do `.md` é o mesmo e só tem letras minúsculas sem acento, números e
  hífen (ex.: `manual-office365-rag`). Esse nome também é o caminho das imagens:
  `<URL do Dify>/kb-assets/manual-office365-rag/<arquivo>`.
- Imagens só como `![descrição](images/arquivo.png)` (ou `./images/...`), com nomes de arquivo só
  com letras sem acento, números, `.`, `_` e `-`. Formatos: png, jpg, jpeg, gif, webp, svg, bmp,
  ico, avif, tif. Imagens com URL `http(s)://` ficam como estão.
- São recusados `<img>`, imagem fora de `images/`, imagem no estilo referência
  (`![descrição][id]`), imagem citada que não está no zip, zip com senha, caminhos inseguros e
  links simbólicos. Limites: 50 MB enviados, 200 MB descompactados, 500 arquivos. Todos os
  problemas aparecem de uma vez, e nada é publicado.
- Só as imagens citadas no `.md` são publicadas; as outras viram aviso. Ao substituir um manual,
  imagens que deixaram de ser citadas saem do ar.
- O prompt `PDF_TO_RAG.md` tem também as regras exigidas pelo Dify: títulos com `#` e espaço,
  seções de até 3.000 caracteres, linhas de até 1.000, tabelas com linhas autocontidas e nada entre
  `<` e `>` (o Dify apaga esse texto). O `kb_admin` avisa quando o `.md` não segue essas regras.

### Interface `kb_admin`

- **Login:** mesmo e-mail e senha do Dify. Entram owner, admin e editor; a sessão dura 8 horas.
- **Perfis:** owner e admin enviam, substituem e apagam manuais e documentos. O **editor só
  consulta**: vê listas, galeria e parâmetros e baixa o `.md` e os documentos.
- **Aba Manuais:** lista dos manuais (imagens, data e autor da publicação), envio de `.zip`, tela
  do manual com galeria, download do `.md`, parâmetros de ingest e avisos; substituição com
  confirmação (imagens que entram, saem e mudam e parâmetros que mudaram) e exclusão. Pastas
  publicadas pelo `publish.sh` aparecem como "publicado fora da interface (sem .md)" e só podem
  ser apagadas.
- **Aba Documentos de apoio:** tabela com arquivo, descrição, tamanho, data e autor; envio
  (qualquer tipo, até 50 MB, descrição obrigatória, nome único), download, substituição, edição da
  descrição e exclusão. Na primeira subida, o app inclui o `PDF_TO_RAG.md` a partir de
  `docker/kb_admin/seed/`; se for apagado, ele só volta com um volume `dify_kb_admin_data` novo.
- **Exclusão de manual:** apaga as imagens e o `.md` guardado; o documento correspondente na base
  do Dify precisa ser apagado no Dify.
- **Modo escuro:** botão no cabeçalho; a escolha fica salva no navegador.

### Parâmetros de ingest no Dify

O `kb_admin` simula como o Dify processa o `.md` (o extrator divide o arquivo pelos títulos e apaga
os `#` antes do chunking; tamanhos são contados em caracteres) e recomenda:

| Parâmetro | Valor |
|---|---|
| Chunk mode | **Parent-child**, pai em **Paragraph** |
| Delimitador do pai | `\n\n\n` (não aparece no texto limpo: cada seção vira um pai inteiro, com as suas imagens) |
| Tamanho do pai | a maior seção, arredondada para cima (mín. 500, máx. `INDEXING_MAX_SEGMENTATION_TOKENS_LENGTH`) |
| Delimitador do filho | `\n\n` se todos os parágrafos cabem em `KB_CHILD_MAX_LENGTH`; senão `\n` |
| Tamanho do filho | o maior parágrafo ou linha, arredondado (mín. 100, máx. `KB_CHILD_MAX_LENGTH`) |
| Replace consecutive spaces, newlines and tabs | marcado |
| Delete all URLs and email addresses | desmarcado |
| Summary Auto-Gen | desligado |
| Index method | High Quality, com o modelo de embedding da base (`text-embedding-3-small`) |
| Retrieval | Hybrid Search com Rerank (`jina-reranker-v3`); sem rerank, Weighted Score 0,7 / 0,3 |
| Top K | filhos por seção (mediana) × 3, entre 3 e 10; numa base com vários manuais, use o maior |
| Score Threshold | desligado (ajuste testando em "Retrieval Testing") |

Não use `\n#` como delimitador: os `#` já foram apagados quando o splitter roda. O tipo de chunk
fica fixo na base depois do primeiro documento, então uma base com documentos em modo General não
aceita Parent-child.

### Chatflow sugerido

`Início → Recuperação de conhecimento → LLM → Resposta`, com esta instrução para o LLM:

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

### Publicação pela linha de comando (alternativa)

O `docker/kb_assets/publish.sh` faz a mesma publicação de imagens a partir de uma pasta local
(`kb/<manual>/` com o `.md` e `images/`) e gera `kb/<manual>/build/<nome>.dify.md`:

```bash
docker/kb_assets/publish.sh kb/manual-office365-rag manual-office365-rag
KB_ASSETS_BASE_URL=http://dify.dev.dti/kb-assets docker/kb_assets/publish.sh kb/<manual> <slug>   # sem perguntas
```

Sem `KB_ASSETS_BASE_URL`, o script pergunta domínio e protocolo. A máquina precisa alcançar o
domínio, porque o script confere se cada imagem responde HTTP 200. Manuais publicados assim
aparecem no `kb_admin` sem `.md` (só podem ser apagados por lá).

A pasta `kb/` na raiz guarda manuais internos da CMRJ e **nunca** é versionada (está no
`.gitignore`).

### Segurança das imagens

O `kb_assets` só serve arquivos em `/kb-assets/`, responde 404 para caminhos ocultos e de
diretório, envia `Cache-Control` de 1 dia, `X-Content-Type-Options: nosniff` e
`Content-Security-Policy: sandbox` (scripts dentro de SVG não rodam nem quando a imagem é aberta
direto no navegador).

## 9. Operação do dia a dia

- **Aplicar mudanças** no `.env`, no `docker-stack.yml` ou no código do `kb_admin`:
  `./deploy.sh --swarm` (ou `./deploy.sh --compose` no modo Compose).
- **Reiniciar um serviço:** `docker service update --force dify_api`.
- **Mudanças em `nginx/` ou `ssrf_proxy/`:** configs do Swarm são imutáveis, então o redeploy com
  conteúdo alterado falha. Remova a stack (`./remove.sh`, respondendo `n`), espere e faça o deploy
  de novo. O `kb_assets/default.conf` é a exceção: o nome do config leva o hash do arquivo, e basta
  o deploy.
- **Logs do `kb_admin`:** `docker service logs -f dify_kb_admin` (login, chamadas ao Dify e erros).
- **Primeira subida:** a migração do banco (`flask upgrade-db`) roda no `api`, no `worker` ou no
  `worker_beat` (quem pegar a trava no Redis) e leva vários minutos com o banco vazio (~6 min no
  WSL). Enquanto isso o `api` fica em `Starting` e o `nginx` reinicia com
  `host not found in upstream "dify_api"`; é esperado. O `start_period: 15m` do `api` e o
  `start_period: 5m` do `db_postgres` (o `initdb` leva ~2 min no WSL) evitam que o Swarm os mate.
  Todos os serviços em `1/1` não significam banco pronto: o `deploy.sh` espera a migração.
- `depends_on` não existe no Swarm: é normal `api`, `worker` e `plugin_daemon` reiniciarem algumas
  vezes até Postgres e Redis ficarem prontos.

## 10. Solução de problemas

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| "Ocorreu um erro inesperado ao renderizar este componente" | SSR do `web` não alcança o domínio, CA errada ou sessão de uma instalação anterior | Seção 7; limpar os dados do site no navegador |
| 502 em `/kb-assets/` ou `/kb-admin/` | stack fora do ar ou ainda subindo; ou Custom locations antigas com IPs de antes de recriar a stack | `docker stack services dify`; migrar para a aba Advanced (seção 6) |
| Todos os domínios do NGPM fora do ar, inclusive o painel | Custom location apontando para serviço inexistente | Seção 6, "Se o NGPM já travou" |
| Imagens dos manuais com 404 pelo domínio, mas 200 dentro da rede | Cache Assets ligado, ou `/kb-assets/` como Custom location | Seção 6, passo a passo |
| 502 no Dify com `connect() failed (111)` no log do `nginx` | colisão de nomes curtos com outra stack em `net_nginx_pm` | A stack já usa nomes completos (`<stack>_api` etc.); confira se o template do Swarm está em uso |
| Login no `kb_admin`: "Não foi possível falar com o Dify" | API fora do ar ou incompatível | `docker service logs dify_kb_admin` mostra a chamada que falhou |
| `kb_admin`: "Seu papel no Dify (editor) permite só consulta." | perfil editor | Peça a um owner ou admin |
| Deploy falha com `only updates to Labels are allowed` | config do Swarm alterado | Remover a stack e fazer o deploy de novo (seção 9) |

Sobre a colisão de nomes: no Swarm, o nome curto de um serviço (ex.: `api`) vira alias DNS em
toda rede a que ele pertence. Por isso a stack usa `nginx/swarm/default.conf.template`, com
`${DIFY_STACK_NAME}_api`, `_web` e `_plugin_daemon` (o Swarm preenche `DIFY_STACK_NAME`), e o
`kb_admin` chama `<stack>_api`. Não use `aliases` de rede: no Docker 27 eles não são registrados
no DNS para serviços com `update_config.order: start-first`. Para diagnosticar:

```bash
docker service logs --tail 50 <stack>_nginx | grep upstream
docker service inspect <stack>_api --format '{{json .Endpoint.VirtualIPs}}'
docker network inspect net_nginx_pm --verbose --format '{{range $k,$v := .Services}}{{$k}} VIP={{$v.VIP}}{{println}}{{end}}'
```

## 11. Testes

```bash
docker/test_deploy.sh                  # deploy.sh, com docker simulado
docker/test_remove.sh                  # remove.sh (Swarm e Compose), com docker simulado
docker/test_compose_kb.sh              # docker-compose.kb.yaml: compose válido, template do nginx e nginx -t
docker/kb_assets/test_default_conf.sh  # nginx do kb_assets (container nginx:alpine temporário)
docker/kb_assets/test_publish.sh       # publish.sh
docker/kb_admin/run_tests.sh           # kb_admin (pytest em container Python 3.12)
```

Os testes não dependem de nada em `kb/`.

## 12. Estrutura do repositório

| Caminho | Conteúdo |
|---|---|
| `docker/docker-stack.yml` | Stack do Swarm |
| `docker/docker-compose.kb.yaml` | `kb_assets` e `kb_admin` para o modo Docker Compose |
| `docker/nginx/compose/` | Template do nginx do Dify com as rotas do kb (modo Compose) |
| `docker/deploy.sh`, `docker/remove.sh` | Deploy e remoção (e os testes `test_*.sh`) |
| `docker/.env.example` | Modelo do `.env` |
| `docker/nginx/swarm/` | Template do nginx do Dify com nomes completos de serviço |
| `docker/certs/` | Certificados públicos de CA (só `localCA.pem` e `no-extra-ca.pem` versionados) |
| `docker/kb_assets/` | nginx das imagens (`default.conf`) e `publish.sh` |
| `docker/kb_admin/` | Interface web (FastAPI); `seed/PDF_TO_RAG.md` é o prompt inicial |
| `docs/superpowers/specs/`, `docs/superpowers/plans/` | Histórico de design de cada funcionalidade |
| `kb/` | Manuais internos (fora do git) |
| `api/`, `web/`, demais pastas | Código do Dify |

**Atenção ao atualizar o Dify:** o código em `api/` deste repositório não é necessariamente o da
imagem `langgenius/dify-api` em execução. Antes de depender de uma rota ou comportamento do Dify
(como faz o `kb_admin` no login e na simulação do chunking), confira o arquivo dentro do container:
`docker exec $(docker ps -q -f name=dify_api) cat /app/api/<caminho>`. As constantes copiadas do
Dify ficam em `docker/kb_admin/kb_admin/ingest_params.py`.

## 13. Sobre o Dify

O Dify é uma plataforma open source para criar aplicações com LLMs, mantida pela LangGenius. A
documentação, a apresentação e o guia de deploy oficial (Docker Compose) estão no
[repositório oficial](https://github.com/langgenius/dify) e em
[docs.dify.ai](https://docs.dify.ai). O uso segue a licença do Dify, em [`LICENSE`](LICENSE).
