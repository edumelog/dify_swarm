# Dify no Docker Swarm (single-node)

Este guia adapta o deploy oficial por `docker compose` para `docker stack` no Swarm, focado em **1 manager**, **volumes locais** e exposição via **Nginx Proxy Manager** (rede externa `net_nginx_pm`).

Referência oficial de deploy base (Compose): [Deploy Dify with Docker Compose](https://docs.dify.ai/en/self-host/quick-start/docker-compose).

## 1) Pré-requisitos

- Docker Engine com Swarm ativo (`docker swarm init`, se ainda não estiver)
- Docker CLI com suporte a `docker stack`
- Host Linux com os recursos mínimos recomendados pelo Dify
- Rede overlay externa `net_nginx_pm` (compartilhada com o Nginx Proxy Manager). Se não existir:

```bash
docker network create -d overlay --attachable net_nginx_pm
```

Confira o estado do Swarm e da rede:

```bash
docker info --format '{{.Swarm.LocalNodeState}}'   # deve retornar "active"
docker network ls --filter name=net_nginx_pm
```

## 2) Preparar o `.env`

Todos os comandos abaixo são executados no diretório `docker` do projeto:

```bash
cd docker
cp .env.example .env
```

Preencha as variáveis abaixo. Os valores de senhas e chaves do `.env.example` são públicos e **não podem** ir para um ambiente real. As demais variáveis do `.env.example` são os padrões do Dify e só precisam mudar se você quiser alterar o comportamento correspondente.

Gere cada segredo com `openssl rand -base64 42`.

| Variável | O que preencher | Exemplo | Obrigatória |
|---|---|---|---|
| `SECRET_KEY` | Chave que assina os cookies de sessão e os tokens de login. Trocá-la depois desloga todos os usuários. | `openssl rand -base64 42` | Sim |
| `DB_PASSWORD` | Senha do usuário do Postgres. Só vale na **criação** do volume `dify_postgres_data`; para trocar depois, altere também dentro do banco. | segredo gerado | Sim |
| `REDIS_PASSWORD` | Senha do Redis (cache e broker do Celery). | segredo gerado | Sim |
| `PLUGIN_DAEMON_KEY` | Chave que a API usa para chamar o `plugin_daemon`. | segredo gerado | Sim |
| `PLUGIN_DIFY_INNER_API_KEY` | Chave que o `plugin_daemon` usa para chamar a API. | segredo gerado | Sim |
| `SANDBOX_API_KEY` | Chave de acesso ao serviço `sandbox` (execução de código). | segredo gerado | Sim |
| `WEAVIATE_API_KEY` e `WEAVIATE_AUTHENTICATION_APIKEY_ALLOWED_KEYS` | Chave do Weaviate (banco vetorial). As duas variáveis precisam ter **o mesmo valor**. | segredo gerado | Sim |
| `CONSOLE_API_URL` | URL pública da API do console. Também é chamada pelo SSR do `web` (ver seção 8). | `https://dify.hmg.dti` | Sim |
| `CONSOLE_WEB_URL` | URL pública do console web. | `https://dify.hmg.dti` | Sim |
| `APP_API_URL` | URL pública da API dos apps publicados. | `https://dify.hmg.dti` | Sim |
| `APP_WEB_URL` | URL pública dos apps publicados (WebApp). | `https://dify.hmg.dti` | Sim |
| `FILES_URL` | URL pública usada nos links de arquivos e imagens enviados. | `https://dify.hmg.dti` | Sim |
| `SERVICE_API_URL` | URL base da Service API exibida no console. Vazio = usa a URL atual do navegador. | `https://dify.hmg.dti` | Não |
| `DIFY_PUBLIC_HOST` | Domínio público, injetado no `extra_hosts` do `web` (seção 8). Sem padrão: o deploy no Swarm falha se estiver vazia. | `dify.hmg.dti` | Sim, no Swarm |
| `DIFY_PUBLIC_HOST_IP` | IP pelo qual o container `web` alcança o Nginx Proxy Manager na 443 (seção 8). Sem padrão. | `10.100.2.25` | Sim, no Swarm |
| `DIFY_EXTRA_CA_FILE` | Certificado **público** da CA que assina o domínio (nunca a `.key`), em `docker/certs/` (seção 8). O `localCA.pem` de dev/hmg já vem no repositório; vazio = CA pública. O `deploy.sh` pergunta o arquivo. | `./certs/localCA.pem` | Sim, no Swarm (ou vazio) |
| `DIFY_PROXY_NETWORK` | Rede overlay externa compartilhada com o Nginx Proxy Manager. | `net_nginx_pm` (padrão) | Não |

Use a mesma origem (protocolo + domínio) em todas as URLs públicas.

## 3) Exportar o `.env` no shell

O `docker stack deploy` **não carrega o `.env` automaticamente** para interpolar as variáveis `${VAR:-default}` do `docker-stack.yml`. O `env_file:` só injeta variáveis dentro dos containers. Sem exportar, o YAML usa os defaults e, por exemplo, a senha do Postgres/Redis pode divergir da que a API recebe.

```bash
while IFS= read -r line; do [[ $line =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] && export "$line"; done < .env
```

O laço exporta cada linha `CHAVE=valor` literalmente, sem executá-la. **Não use `source .env`**: o arquivo tem valores com espaços e caracteres especiais sem aspas (ex.: `LOG_DATEFORMAT=%Y-%m-%d %H:%M:%S`, `NGINX_SSL_PROTOCOLS=TLSv1.2 TLSv1.3`), que o bash tenta interpretar como comandos.

Repita este passo em todo novo shell antes de um deploy.

A stack também exige duas variáveis que não ficam no `.env`, porque dependem do código
deste repositório: `KB_ADMIN_IMAGE` (imagem local do `kb_admin`, com uma tag derivada do
ID da imagem) e `KB_ASSETS_CONF_HASH` (hash do `kb_assets/default.conf`, usado no nome do
config). O `deploy.sh` faz isso sozinho; no deploy manual, rode antes:

```bash
docker build -q -t dify-kb-admin:local kb_admin
export KB_ADMIN_IMAGE="dify-kb-admin:$(docker image inspect -f '{{.Id}}' dify-kb-admin:local | cut -c8-19)"
docker tag dify-kb-admin:local "${KB_ADMIN_IMAGE}"
export KB_ASSETS_CONF_HASH="$(sha256sum kb_assets/default.conf | cut -c1-12)"
```

Sem elas, o `docker stack deploy` para com `defina KB_ADMIN_IMAGE rodando o docker/deploy.sh`.

## 4) Validar o manifesto

```bash
docker stack config -c docker-stack.yml > /dev/null
```

## 5) Deploy da stack

```bash
docker stack deploy -c docker-stack.yml dify
```

Atalho para as seções 2 a 5: o `deploy.sh` confere o `.env` e completa o que faltar:

- **segredos** vazios ou iguais aos do `.env.example`: pergunta se você quer **gerar** valores aleatórios (alfanuméricos, 42 caracteres) ou **digitá-los** (Enter gera aquele valor). As duas chaves do Weaviate recebem o mesmo valor;
- **URLs públicas** vazias: pede cada uma no terminal; Enter repete a anterior;
- antes de gravar, copia o original para `.env.bak.<data-hora>` (ignorado pelo git, assim como o `.env`) e altera só as linhas dessas chaves.

Atenção: se o banco já existir, uma `DB_PASSWORD` nova no `.env` não muda a senha guardada no Postgres; o script avisa nesse caso.

Em seguida, pergunta se a subida é com Docker Swarm e então:

- **Swarm:** pede `DIFY_PUBLIC_HOST` e `DIFY_PUBLIC_HOST_IP` se estiverem vazios, sugerindo o host de `CONSOLE_API_URL` e o gateway da rede `bridge` (seção 8). Pergunta o arquivo da CA em `docker/certs/` (padrão `localCA.pem`) e não prossegue sem um certificado válido. Depois confere se o nó é manager e se a rede `DIFY_PROXY_NETWORK` (padrão `net_nginx_pm`) existe, exporta o `.env`, valida o manifesto e roda `docker stack deploy` após confirmação. Em seguida **espera o Dify ficar pronto**: todos os serviços no ar, o banco migrado (tabela `alembic_version`) e nenhuma migração em andamento (trava `db_upgrade_lock` no Redis). Só então mostra `Dify pronto`. O prazo é `DEPLOY_WAIT_TIMEOUT` (padrão: 1200s), e Ctrl+C interrompe só a espera;
- **sem Swarm:** valida e sobe o `docker-compose.yaml` com `docker compose up -d` após confirmação.

```bash
./deploy.sh              # pergunta o modo
./deploy.sh --swarm      # ou --compose, sem perguntar
STACK_NAME=dify-hmg ./deploy.sh --swarm   # outro nome de stack (padrão: dify)
```

Os testes do script (com `docker` simulado, sem subir nada) ficam em `./test_deploy.sh`.

Para evitar problemas de bind mount no Swarm (comum no Docker Desktop/WSL), este stack usa **volumes nomeados locais** para dados e **configs do Swarm** para templates/scripts (incluindo `nginx/conf.d/default.conf.template`).

## 6) Verificação

```bash
docker stack services dify
docker service ps dify_init_permissions   # job único; deve terminar em "Complete"
docker service logs -f dify_api           # no primeiro deploy, as migrations rodam aqui
```

O `depends_on` não existe no Swarm: é normal `api`, `worker` e `plugin_daemon` reiniciarem algumas vezes até Postgres e Redis ficarem prontos.

## 7) Expor via Nginx Proxy Manager

O serviço `nginx` do Dify **não publica portas** no host — ele está apenas na rede `net_nginx_pm`. No Nginx Proxy Manager, crie um Proxy Host:

- Domínio: `dify.dev.dti` (ou o domínio configurado no `.env`)
- Scheme: `http`
- Forward Hostname: `dify_nginx`
- Forward Port: `80`

Use `dify_nginx` (nome completo do serviço na stack), e não `dify_web`: o `web` não está na rede `net_nginx_pm` e, mesmo que estivesse, as rotas `/console/api`, `/api` e `/files` não chegariam à API.

Depois acesse:

- `http://dify.dev.dti/install` (criação do admin)
- `http://dify.dev.dti` (console após instalação)

### Qual IP usar para chamar o NGPM (dev no WSL)

O NGPM publica as portas 80, 443 e 81 (painel) pelo Swarm, que atende só em **IPv4**. No WSL em modo NAT:

- Use o IP da interface `eth0` do WSL, que é o primeiro de `hostname -I` (ex.: `172.19.56.58`). Painel: `http://<ip-do-wsl>:81`.
- No `hosts` do Windows (`C:\Windows\System32\drivers\etc\hosts`), aponte os domínios para esse IP, ex.: `172.19.56.58  dify.dev.dti`.
- `localhost` pode não responder: ele resolve primeiro para o IPv6 `::1`. Dentro do WSL, use `127.0.0.1`.
- O IP do WSL **muda** quando o WSL reinicia (`wsl --shutdown` ou reboot do Windows). Se os domínios `*.dev.dti` pararem de abrir, rode `hostname -I` no WSL e atualize o IP no `hosts` do Windows.
- O domínio no `hosts` precisa ser idêntico ao do Proxy Host no NGPM.

### Um host quebrado derruba o NGPM inteiro

O nginx do NGPM resolve na inicialização os nomes fixos de `proxy_pass`. Se uma **Custom Location** apontar para um serviço que não existe (ex.: `backend:3000` com o projeto fora do ar), o nginx entra em loop com `[emerg] host not found in upstream` e **todos** os domínios caem, inclusive o painel na porta 81. O destino principal do Proxy Host não tem esse problema, porque o NGPM já o gera em variável (`set $server ...`).

Para recuperar sem o painel, renomeie o arquivo do host problemático dentro do container e depois desative o host no painel:

```bash
docker logs --tail 20 $(docker ps -q -f name=ngpm_ngpm) | grep emerg   # mostra o N.conf culpado
docker exec $(docker ps -q -f name=ngpm_ngpm) mv /data/nginx/proxy_host/N.conf /data/nginx/proxy_host/N.conf.err
```

Para evitar, escreva as locations na aba **Advanced** do Proxy Host com o destino em variável, que só é resolvida a cada requisição:

```nginx
location /api/public {
    set $upstream http://<stack>_backend:3000;
    proxy_pass $upstream;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Real-IP $remote_addr;
}
```

## 8) SSR do `web`: domínio público e CA interna

Os domínios `*.dti` não estão no DNS — são resolvidos pelo arquivo hosts de quem navega. Mas o SSR do Next.js, **dentro do container `web`**, também chama `CONSOLE_API_URL` (ex.: `https://dify.hmg.dti`). Sem ajuste, o log do `web` mostra `getaddrinfo ENOTFOUND` e o browser exibe "Ocorreu um erro inesperado ao renderizar este componente".

Configure no `.env` de **cada ambiente**. A stack não tem valores padrão para o domínio e o IP: se faltarem, o `docker stack deploy` falha com `required variable DIFY_PUBLIC_HOST is missing a value`. O `deploy.sh` pergunta os dois quando estão vazios.

```env
# Entrada de hosts injetada no container web (extra_hosts)
DIFY_PUBLIC_HOST=dify.hmg.dti
DIFY_PUBLIC_HOST_IP=10.100.2.25

# Certificado PÚBLICO da CA interna que assina o domínio (nunca a chave .key); vazio = nenhuma CA extra
DIFY_EXTRA_CA_FILE=./certs/localCA.pem
```

O certificado público da CA interna de dev e hmg, `docker/certs/localCA.pem`, **já vem no repositório** (veja "Para que serve o certificado da CA" abaixo). Para outra CA, copie o certificado público dela para `docker/certs/` (esse arquivo não é versionado):

```bash
cp /caminho/para/outra-ca.pem docker/certs/outra-ca.pem
```

Como descobrir os valores em qualquer servidor:

- `DIFY_PUBLIC_HOST`: o host de `CONSOLE_API_URL`, que é o domínio do Proxy Host no NGPM. O `deploy.sh` sugere esse valor.
- `DIFY_PUBLIC_HOST_IP`: um IP que, **de dentro de um container**, chegue ao NGPM na 443. O gateway da rede `bridge` costuma funcionar e não muda com reinícios. Descubra-o com `docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}'`; o `deploy.sh` sugere esse valor. Também serve o IP fixo do servidor na rede. Não use um VIP do Swarm, que muda a cada deploy, nem o IP do WSL (`hostname -I`), que muda a cada reinício.
- `DIFY_EXTRA_CA_FILE`: o certificado da CA que emitiu o certificado do Proxy Host. O `deploy.sh` pergunta o nome do arquivo (veja abaixo).

Sintomas de valor errado no log do `web`: `connect EHOSTUNREACH <ip>:443` ou `getaddrinfo ENOTFOUND` (IP ou domínio errados) e `UNABLE_TO_VERIFY_LEAF_SIGNATURE` (CA ausente ou errada).

### Para que serve o certificado da CA

Ao abrir `https://<domínio>`, há duas conexões HTTPS diferentes:

```
1) navegador ──HTTPS──> NGPM (certificado do domínio) ──> dify_nginx ──> web/api

2) container web (Node, SSR) ──HTTPS──> https://<domínio> (NGPM) ──> dify_nginx ──> api
```

1. **Navegador → NGPM:** o navegador confia no certificado porque a CA está instalada no Windows (ou porque é uma CA pública). O arquivo da stack não participa dessa conexão.
2. **SSR → NGPM:** ao renderizar a página no servidor, o Next.js chama `CONSOLE_API_URL` passando pelo NGPM. O Node.js **não usa** o repositório de certificados do Windows nem o do sistema: ele traz a própria lista de CAs públicas. Se a CA do domínio for interna, a chamada falha com `UNABLE_TO_VERIFY_LEAF_SIGNATURE` e o navegador mostra "Ocorreu um erro inesperado ao renderizar este componente".

Como o arquivo é usado:

1. `DIFY_EXTRA_CA_FILE` no `.env` aponta para o arquivo (ex.: `./certs/localCA.pem`).
2. O `docker-stack.yml` transforma esse arquivo no config `dify_web_extra_ca`.
3. O config é montado **só no container `web`**, em `/etc/ssl/dify/extra-ca.pem`.
4. `NODE_EXTRA_CA_CERTS=/etc/ssl/dify/extra-ca.pem` faz o Node **somar** essa CA às públicas que ele já conhece. Chamadas a sites públicos continuam funcionando.

Os outros serviços (`api`, `worker`, `nginx`) não usam esse arquivo.

| Ambiente | Quem assina o certificado do NGPM | `DIFY_EXTRA_CA_FILE` |
|---|---|---|
| dev | `localCA` | `./certs/localCA.pem` (versionado) |
| hmg | `localCA` | `./certs/localCA.pem` (versionado) |
| produção, com CA pública (Let's Encrypt, comercial) | uma CA que o Node já conhece | vazio: responda `nenhum` no `deploy.sh` |
| produção, com CA corporativa | essa CA | o `.pem` público **dessa** CA, copiado para `docker/certs/` |

Para descobrir quem assina o certificado servido pelo NGPM, e conferir se a CA certa foi escolhida:

```bash
openssl s_client -connect <domínio>:443 -servername <domínio> </dev/null 2>/dev/null | openssl x509 -noout -issuer
openssl verify -CAfile docker/certs/<ca>.pem <fullchain.pem>
```

**Versionamento:** a pasta `docker/certs/` é ignorada pelo git, com exceção do `no-extra-ca.pem` e do `localCA.pem`. O `localCA.pem` é só o certificado **público** (o mesmo que qualquer navegador recebe), então pode ir para o repositório. A chave `localCA.key` **nunca** deve ser copiada para cá: se vazar, qualquer pessoa pode emitir certificados válidos para os domínios `*.dti`. Qualquer outro arquivo em `docker/certs/`, inclusive chaves e CAs de produção, continua fora do git.

**Pergunta do `deploy.sh`:** no modo Swarm, o script pergunta o arquivo da CA dentro de `docker/certs/`:

```
Arquivo da CA [localCA.pem]:
```

- Enter aceita o sugerido: o valor atual de `DIFY_EXTRA_CA_FILE` ou, se estiver vazio, `localCA.pem`. Também é possível digitar outro nome.
- Se o arquivo não existir, o script mostra `ALERTA: ... não encontrado` e repete a pergunta até o arquivo ser colocado na pasta (pressione Enter depois de copiá-lo) ou até ser informado outro nome. Ele **não prossegue** sem um arquivo válido.
- Também é recusado um arquivo que contenha chave privada (`PRIVATE KEY`) ou que não tenha `BEGIN CERTIFICATE`.
- `nenhum` grava `DIFY_EXTRA_CA_FILE=` vazio, para CA pública.
- O valor escolhido é gravado no `.env` como `./certs/<arquivo>`.

**Troca de certificado:** a CA vira um config do Swarm, e configs do Swarm são imutáveis. Se o certificado escolhido for diferente do que está na stack no ar, o `deploy.sh` avisa. Nesse caso, remova a stack (`docker stack rm <stack>`), espere a remoção terminar e faça o deploy de novo. Os volumes e os dados são preservados.

Para testar de dentro do container:

```bash
W=$(docker ps -q -f name=<stack>_web | head -1)
docker exec $W node -e 'fetch(process.env.CONSOLE_API_URL + "/console/api/system-features").then(r => console.log(r.status)).catch(e => console.log(e.cause?.code || e.message))'
```

## 9) Operações do dia a dia

- Aplicar mudanças no stack, no `.env` ou no código do `kb_admin` (recomendado: `./deploy.sh --swarm`). Na forma manual, exporte também `KB_ADMIN_IMAGE` e `KB_ASSETS_CONF_HASH` (seção 3):

```bash
while IFS= read -r line; do [[ $line =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] && export "$line"; done < .env
# + os comandos da seção 3 para KB_ADMIN_IMAGE e KB_ASSETS_CONF_HASH
docker stack deploy -c docker-stack.yml dify
```

- Reiniciar um serviço:

```bash
docker service update --force dify_api
```

- Remover a stack (os volumes `dify_*` e os dados são preservados):

```bash
docker stack rm dify
```

- Remover a stack com o script, que também espera a remoção terminar e oferece apagar os volumes:

```bash
./remove.sh                     # stack "dify"
STACK_NAME=difytest ./remove.sh # outra stack
```

  O script lista os serviços e os volumes da stack, que ele identifica pelo label `com.docker.stack.namespace`, e pergunta `Apagar também os volumes (dados)? [s/n]`. Essa pergunta só aceita s/S/n/N.
  - Com `n`, remove a stack, espera a remoção terminar e **mantém** os volumes. Um novo deploy com o mesmo `STACK_NAME` reaproveita os dados e as senhas gravadas no banco, então use o mesmo `.env`.
  - Com `s`, exige que você digite o nome da stack para confirmar. Depois remove a stack e apaga **todos** os volumes dela: banco, arquivos, bases vetoriais e volumes antigos. Isso não pode ser desfeito. Se a confirmação não conferir, nada é removido.
  - Depois de apagar os volumes, **limpe os dados do site no navegador** (só do domínio do Dify) ou use uma janela anônima. A sessão da instalação anterior fica guardada em cookies e no armazenamento local; com o banco novo a API responde 401 e a página mostra "Ocorreu um erro inesperado ao renderizar este componente". Para limpar só esse domínio: com o site aberto, pressione F12, vá em **Application**, depois **Storage** e clique em **Clear site data**. Também dá pelo cadeado da barra de endereço, em **Cookies e dados do site**.

  O script não mexe no `.env`, nos certificados nem na rede `net_nginx_pm`. Os testes ficam em `./test_remove.sh`, com `docker` simulado.

- Alterações em arquivos de `nginx/` ou `ssrf_proxy/`: os `configs` do Swarm são imutáveis, então um redeploy com conteúdo alterado falha. Remova a stack, aguarde a remoção terminar e faça o deploy de novo com `./deploy.sh --swarm`. A exceção é o `kb_assets/default.conf`: o nome do config dele leva o hash do arquivo, então basta fazer o deploy de novo.

```bash
docker stack rm dify
# aguarde até `docker stack ls` não listar mais "dify"
./deploy.sh --swarm
```

  Depois de recriar a stack, os serviços ganham IPs novos e o NGPM pode responder 502 em `/kb-assets/` e `/kb-admin/`; recarregue o nginx dele com `docker exec $(docker ps -q -f name=ngpm_ngpm) nginx -s reload`.

## 10) Imagens da base de conhecimento (kb_assets)

O serviço `dify_kb_assets` (nginx) serve as imagens dos manuais da base de conhecimento em `<protocolo>://<domínio-do-dify>/kb-assets/<slug>/<arquivo>`, para que o chatbot as exiba nas respostas. As imagens ficam no volume `dify_kb_assets`.

Ordem de uso:

1. Gere o pacote do manual (`<nome>.zip` com a pasta `<nome>/`, o `<nome>.md` e `images/`) a partir do PDF com o prompt `PDF_TO_RAG.md`, que se baixa na aba **Documentos de apoio** do `kb_admin` (arquivo inicial em [`kb_admin/seed/PDF_TO_RAG.md`](kb_admin/seed/PDF_TO_RAG.md)).
2. Faça o deploy da stack (seção 5), que já inclui os serviços `dify_kb_assets` e `dify_kb_admin`.
3. No NGPM, crie no proxy host do Dify as custom locations `/kb-assets/` → `dify_kb_assets:80` e `/kb-admin/` → `dify_kb_admin:8000`.
4. Abra `<protocolo>://<domínio-do-dify>/kb-admin/`, entre com o usuário do Dify (owner, admin ou editor) e envie o zip.
5. Na tela do manual, baixe o `.md` para o Dify e use os parâmetros de ingest mostrados ao criar o documento no Dify.

A alternativa pela linha de comando continua disponível: `docker/kb_assets/publish.sh kb/<manual> <slug>`.

Interface web: [`kb_admin/README.md`](kb_admin/README.md). Detalhes (formato do manual, segmentação, prompt do LLM): [`kb_assets/README.md`](kb_assets/README.md).

## Colisão de nomes na rede compartilhada (502 Bad Gateway)

O serviço `nginx` fica em duas redes: `default` (da stack) e `net_nginx_pm` (compartilhada com outras stacks). No Swarm, o nome curto de um serviço (ex.: `api`) vira alias DNS em toda rede à qual ele pertence. Se outra stack tiver um serviço chamado `api` em `net_nginx_pm`, o nginx pode resolver `api` para o serviço errado ao iniciar e passar a responder **502** (`connect() failed (111: Connection refused)` no log do nginx), com o front exibindo "Ocorreu um erro inesperado ao renderizar este componente".

Por isso este stack usa um template de nginx próprio para Swarm, `nginx/swarm/default.conf.template`, que aponta para os **nomes completos** dos serviços (`${DIFY_STACK_NAME}_api`, `${DIFY_STACK_NAME}_web`, `${DIFY_STACK_NAME}_plugin_daemon`). A variável `DIFY_STACK_NAME` é preenchida automaticamente pelo Swarm com o nome da stack usado no `docker stack deploy` (ex.: `dify_hmg`). O `nginx/conf.d/default.conf.template` original continua sendo usado pelo `docker-compose.yaml`.

Não use `aliases` de rede para isso: no Docker 27, aliases adicionados a serviços com `update_config.order: start-first` não são registrados no DNS do Swarm, e o nginx entra em loop com `host not found in upstream`.

Para diagnosticar, compare o IP do upstream no log do nginx com os VIPs dos serviços:

```bash
docker service logs --tail 50 <stack>_nginx | grep upstream
docker service inspect <stack>_api --format '{{json .Endpoint.VirtualIPs}}'
docker network inspect net_nginx_pm --verbose --format '{{range $k,$v := .Services}}{{$k}} VIP={{$v.VIP}}{{println}}{{end}}'
```

## Observações importantes para Swarm

- `depends_on` do Compose não controla ordem no Swarm; os serviços sobem de forma independente.
- **Primeira subida demora:** a migração do banco (`flask upgrade-db`) roda no `api`, no `worker` e no `worker_beat`; quem pegar a trava no Redis migra e os outros pulam. Com o banco vazio ela leva vários minutos (~6 min no WSL). Enquanto isso o `api` fica em `Starting` e o `nginx` reinicia com `host not found in upstream "dify_api"`, pois o Swarm só registra no DNS tarefas saudáveis. Isso é esperado: o `start_period: 15m` do healthcheck do `api` evita que o Swarm o mate no meio da migração. Acompanhe com `docker service logs -f dify_worker | grep -i migration`.
- **Todos os serviços `1/1` não significa banco pronto:** o `api` pode ficar saudável com o banco ainda vazio, porque pula a migração quando outro serviço segura a trava. O `deploy.sh` espera a migração terminar antes de anunciar `Dify pronto`.
- **Postgres na primeira subida:** com o volume vazio, o `initdb` leva quase 2 minutos no WSL. O `start_period: 5m` do healthcheck do `db_postgres` evita que o Swarm o mate no meio do `initdb`, o que deixaria o volume do banco pela metade.
- Este setup usa **volumes nomeados locais**, adequado para **single-node**.
- Para produção multi-nó, migre para storage compartilhado (NFS/driver distribuído) e políticas de placement.
