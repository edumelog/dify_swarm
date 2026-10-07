# Banco `support` e webhooks do n8n para o assistente de atendimento do Dify

Documento de passagem para a sessão do Claude CLI que vai **criar o banco e os webhooks do n8n**.
Contém o contexto, as decisões já tomadas (e por quê), o SQL completo, o comportamento de cada
webhook e os critérios de pronto. O contrato da API, com exemplos e roteiro de testes, está em
[`api.md`](api.md) — ele é a referência para o que os webhooks devem aceitar e devolver.

> Tudo que está em "Decisões" já foi discutido e aprovado com o usuário. Não reabra essas
> decisões; se encontrar um impedimento real, pare e pergunte.

## 1. Contexto e motivo

- O Dify (stack `dify` no Docker Swarm, atrás do Nginx Proxy Manager) terá um **Chatflow de
  atendimento** aberto por **link público, sem login**.
- O usuário quer que quem volta pelo **mesmo navegador** não precise informar nome, e-mail e
  ramal de novo, e quer guardar os **atendimentos encerrados** (resumo, classificação,
  sugestões).
- O Dify não tem nó de armazenamento persistente entre conversas. Por isso os dados ficam num
  banco Postgres acessado **somente pelo n8n**; o Chatflow chama webhooks do n8n com o nó HTTP.

### Como a identificação funciona

- O WebApp do Dify gera um `sys.user_id` por navegador (e por app) e o guarda no navegador. Ele
  serve **apenas** para reconhecer um navegador já usado.
- A **chave do usuário é o e-mail**. Um navegador aponta para um usuário; um usuário pode ter
  vários navegadores.
- Opcionalmente o WebApp pode receber o **login do Windows** pela URL (`windows_user`). É só uma
  pista: não é confiável (a URL pode ser editada).
- Fluxo no Chatflow (para entender o uso da API; o Chatflow **não** é tarefa desta sessão):
  1. Primeira mensagem → `identify`.
  2. Encontrou → "Você é Fulano, ramal X?". Sim → `register` com os dados existentes (só liga o
     navegador). Não → pede dados novos → `register`.
  3. Não encontrou → pede nome, e-mail e ramal → `register`.
  4. Quando o caso fecha (resolvido, despedida ou encaminhado a humano) → `list-categories` para
     montar o prompt de classificação → `save-ticket`.

## 2. Decisões (aprovadas)

| Decisão | Motivo |
|---|---|
| Banco **`support`** no **Postgres 18 que já roda na stack do n8n**, separado do banco do n8n | Quem acessa é o n8n; evita mais um contêiner/volume/backup. Banco separado isola tabelas, backup e restore. |
| **Não** usar o Postgres da stack do Dify | Atualizações e restores do Dify não podem afetar o histórico de atendimentos. |
| Usuário **`support_app`** com `SELECT`, `INSERT`, `UPDATE` (sem `DELETE`) só no banco `support` | Menor privilégio; a credencial dos webhooks não enxerga o banco do n8n. |
| Lógica de negócio **nos webhooks/SQL** (operações `identify`, `register`, `save-ticket`, `list-categories`, `save-category`), não no Chatflow | Regra num só lugar, testável sem o Dify. |
| **Regra A**: navegador novo + e-mail já existente → liga o navegador ao e-mail e grava nome/ramal digitados agora, **sem devolver** nada do que estava salvo | Não vaza dados de quem digita o e-mail de outra pessoa. |
| "Não sou eu" **troca o vínculo** do navegador; nunca sobrescreve o cadastro da pessoa anterior | Não destruir o cadastro de terceiros em computador compartilhado. |
| Atendimento gravado **só quando o caso fecha**; um registro por `conversation_id` (regravar atualiza) | Escolha do usuário; conversas abandonadas não são registradas. |
| Categorias numa **tabela** (`categories`), com FK em `tickets`; gestão por webhooks chamados por um Workflow administrativo do Dify | Categorias ainda não definidas e não podem ficar fixas no SQL. |
| Categorias **nunca são apagadas**, só desativadas; `outros` sempre existe e não pode ser desativada | Atendimentos antigos continuam válidos; `outros` é o destino quando a classificação falha. |
| Identificadores (tabelas, colunas, campos JSON, caminhos) em **inglês**; descrições (`COMMENT ON`), mensagens de erro e textos ao usuário em **português do Brasil** | Convenção do usuário. |
| Autenticação dos webhooks por cabeçalho **`X-Support-Token`** (credencial *Header Auth* do n8n) | Simples e suficiente; o token fica como variável *secret* no Chatflow. |

## 3. Escopo desta sessão

**Fazer:**
1. Descobrir a stack do n8n e o acesso ao Postgres (seção 4).
2. Criar o usuário `support_app` e o banco `support` (seção 5).
3. Aplicar o esquema (seção 6) e as permissões (seção 7).
4. Criar no n8n as credenciais e os **cinco workflows** de webhook (seções 8 e 9), ativá-los.
5. Rodar o roteiro de testes de [`api.md`](api.md#roteiro-de-testes) e limpar os dados de teste.
6. Incluir o banco `support` na rotina de backup existente (seção 10).
7. Entregar ao usuário o que está na seção 11.

**Não fazer:** nada na stack do Dify, no Chatflow ou no Workflow administrativo do Dify (seção 12
lista o que fica para depois, só para contexto). Não versionar senhas nem o token.

## 4. Descoberta (antes de alterar qualquer coisa)

Confirme e anote — não presuma:

- Nome da stack e do serviço Postgres do n8n (`docker stack ls`, `docker stack services <stack>`),
  o nó onde a task roda (`docker service ps <serviço>`), e o arquivo da stack (procure o
  repositório/diretório da stack do n8n; pergunte ao usuário se não encontrar).
- Usuário administrador do Postgres (normalmente `POSTGRES_USER` do serviço) e como obter a senha
  (variável, secret ou `.env` da stack). Não imprima a senha no terminal.
- Versão: `SELECT version();` (esperado: 18).
- Se já existe banco ou role com os nomes `support`/`support_app` (`\l`, `\du`). Se existir, **pare
  e pergunte**.
- URL pública do n8n (proxy host no NGPM) e se os webhooks de produção respondem em
  `https://<host-do-n8n>/webhook/...`.
- Como os workflows do n8n são mantidos (criados pela UI, importados por `n8n import:workflow`,
  ou via API pública do n8n com API key). Siga o padrão existente; se não houver, combine com o
  usuário.
- Se há rotina de backup do Postgres do n8n e onde ela está.

Para rodar `psql` no Swarm: no nó da task,
`docker exec -it $(docker ps -q -f name=<stack>_<serviço-postgres>) psql -U <admin>`.

## 5. Usuário e banco

Gere a senha com `openssl rand -base64 32` e guarde-a apenas na credencial do n8n (seção 8) e onde
o usuário indicar. Como administrador:

```sql
CREATE ROLE support_app LOGIN PASSWORD '<senha-gerada>';
CREATE DATABASE support ENCODING 'UTF8' TEMPLATE template0;
REVOKE ALL ON DATABASE support FROM PUBLIC;
GRANT CONNECT ON DATABASE support TO support_app;
```

## 6. Esquema

Conectado ao banco `support` como administrador (`\c support`). Salve este script como migração
versionada junto da stack do n8n (por exemplo `sql/support/001_init.sql`, seguindo a organização
que existir) — o script é a fonte da verdade do esquema.

```sql
BEGIN;

CREATE TABLE users (
  email          text PRIMARY KEY CHECK (email = lower(email)),
  full_name      text NOT NULL,
  phone_ext      text,
  windows_user   text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX users_windows_user_idx ON users (lower(windows_user));

CREATE TABLE browsers (
  user_id        text PRIMARY KEY,
  email          text NOT NULL REFERENCES users(email) ON UPDATE CASCADE,
  first_seen_at  timestamptz NOT NULL DEFAULT now(),
  last_seen_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX browsers_email_idx ON browsers (email);

CREATE TABLE categories (
  code         text PRIMARY KEY CHECK (code ~ '^[a-z0-9_]+$'),
  label        text NOT NULL,
  description  text,
  active       boolean NOT NULL DEFAULT true,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT categories_outros_always_active CHECK (code <> 'outros' OR active)
);

CREATE TABLE tickets (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  conversation_id  text NOT NULL UNIQUE,
  email            text NOT NULL REFERENCES users(email) ON UPDATE CASCADE,
  user_id          text,
  summary          text NOT NULL,
  category         text NOT NULL REFERENCES categories(code) ON UPDATE CASCADE,
  suggestions      text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX tickets_email_created_at_idx ON tickets (email, created_at DESC);
CREATE INDEX tickets_category_created_at_idx ON tickets (category, created_at DESC);

INSERT INTO categories (code, label, description)
VALUES ('outros', 'Outros', 'Quando nenhuma outra categoria se aplica');

COMMENT ON TABLE  users                   IS 'Pessoas atendidas pelo assistente do Dify; a chave é o e-mail';
COMMENT ON COLUMN users.email             IS 'E-mail do usuário, sempre em minúsculas';
COMMENT ON COLUMN users.full_name         IS 'Nome informado pelo usuário';
COMMENT ON COLUMN users.phone_ext         IS 'Ramal informado pelo usuário';
COMMENT ON COLUMN users.windows_user      IS 'Login do Windows, quando recebido pela URL do WebApp (opcional, não confiável)';
COMMENT ON COLUMN users.created_at        IS 'Data do primeiro cadastro';
COMMENT ON COLUMN users.updated_at        IS 'Data da última alteração de nome, ramal ou login do Windows';

COMMENT ON TABLE  browsers                IS 'Navegadores conhecidos: cada sys.user_id do WebApp do Dify aponta para um usuário';
COMMENT ON COLUMN browsers.user_id        IS 'sys.user_id gerado pelo WebApp do Dify (um por navegador e por app)';
COMMENT ON COLUMN browsers.email          IS 'Usuário que confirmou o uso deste navegador por último';
COMMENT ON COLUMN browsers.first_seen_at  IS 'Primeira vez que o navegador foi ligado a um usuário';
COMMENT ON COLUMN browsers.last_seen_at   IS 'Último reconhecimento ou troca de usuário do navegador';

COMMENT ON TABLE  categories              IS 'Categorias de classificação dos atendimentos; nunca apagadas, só desativadas';
COMMENT ON COLUMN categories.code         IS 'Identificador da categoria (minúsculas, dígitos e _)';
COMMENT ON COLUMN categories.label        IS 'Nome exibido da categoria';
COMMENT ON COLUMN categories.description  IS 'Orientação para o LLM sobre o que pertence a esta categoria';
COMMENT ON COLUMN categories.active       IS 'Categorias inativas não são oferecidas ao LLM; outros é sempre ativa';

COMMENT ON TABLE  tickets                 IS 'Atendimentos encerrados; um registro por conversa do Dify';
COMMENT ON COLUMN tickets.conversation_id IS 'sys.conversation_id do Dify';
COMMENT ON COLUMN tickets.email           IS 'Usuário atendido';
COMMENT ON COLUMN tickets.user_id         IS 'Navegador usado na conversa';
COMMENT ON COLUMN tickets.summary         IS 'Resumo do problema relatado';
COMMENT ON COLUMN tickets.category        IS 'Classificação do problema';
COMMENT ON COLUMN tickets.suggestions     IS 'Sugestões dadas pelo usuário';
COMMENT ON COLUMN tickets.created_at      IS 'Data da primeira gravação do atendimento';
COMMENT ON COLUMN tickets.updated_at      IS 'Data da última regravação do atendimento';

COMMIT;
```

## 7. Permissões

Ainda no banco `support`, como administrador (o administrador continua dono dos objetos; o
`support_app` só manipula dados):

```sql
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO support_app;
GRANT SELECT, INSERT, UPDATE ON users, browsers, categories, tickets TO support_app;
GRANT USAGE ON SEQUENCE tickets_id_seq TO support_app;
```

Verifique conectando como `support_app`: `SELECT` funciona; `DELETE FROM categories` e
`CREATE TABLE x()` falham com *permission denied*.

## 8. Credenciais no n8n

- **Postgres** — nome `support (Postgres)`: host = nome do serviço Postgres na rede da stack
  (o mesmo que o n8n usa para o próprio banco), porta 5432, banco `support`, usuário
  `support_app`.
- **Header Auth** — nome `support webhook token`: name `X-Support-Token`, value = token gerado
  com `openssl rand -hex 32`.

## 9. Workflows do n8n

Cinco workflows, um por operação, nomeados `support - <operação>` (ex.: `support - identify`).
Contrato completo (campos, respostas, mensagens de erro) em [`api.md`](api.md).

### Estrutura comum

1. **Webhook**: método `POST`, path `support/<operação>`, autenticação *Header Auth* com a
   credencial `support webhook token`, *Respond* = "Using 'Respond to Webhook' Node".
2. **Code (validação)**: lê `body`, aplica `trim`, troca strings vazias por `null`, normaliza
   (`email` em minúsculas; `code` em minúsculas com espaços → `_`), valida obrigatórios e
   formatos. Em caso de erro, segue para um **Respond to Webhook** com código `400` e
   `{"ok": false, "error": "<mensagem em português>"}`.
3. **Postgres**: operação *Execute Query* com a credencial `support (Postgres)` e **Query
   Parameters** (`$1`, `$2`, …). Nunca interpole valores na string SQL. Ative *Always Output
   Data* onde zero linhas é um resultado válido. Configure *On Error* = "Continue (using error
   output)" e ligue a saída de erro a um **Respond to Webhook** `500`
   `{"ok": false, "error": "falha ao acessar o banco de dados"}` — exceto onde abaixo se pede
   tradução do erro para `400`.
4. **Respond to Webhook** `200` com o JSON do contrato.

Regex do e-mail: `^[^@\s]+@[^@\s]+\.[^@\s]+$`. Regex do `code` (após normalizar):
`^[a-z0-9_]+$`.

### `identify`

Entrada: `user_id` (obrigatório), `windows_user` (opcional). Parâmetros: `$1 = user_id`,
`$2 = windows_user`.

```sql
WITH win AS (
  SELECT email, full_name, phone_ext
  FROM users
  WHERE $2::text IS NOT NULL AND lower(windows_user) = lower($2::text)
),
win_one AS (
  SELECT * FROM win WHERE (SELECT count(*) FROM win) = 1
),
br AS (
  UPDATE browsers SET last_seen_at = now()
  WHERE user_id = $1::text AND NOT EXISTS (SELECT 1 FROM win_one)
  RETURNING email
)
SELECT 'windows' AS source, email, full_name, phone_ext FROM win_one
UNION ALL
SELECT 'browser', u.email, u.full_name, u.phone_ext
FROM br JOIN users u USING (email);
```

Uma linha → `{"found": true, "source", "email", "full_name", "phone_ext"}`; zero linhas →
`{"found": false}`. Não grava vínculo em `browsers`.

### `register`

Entrada: `user_id`, `email`, `full_name` (obrigatórios); `phone_ext`, `windows_user`
(opcionais). Parâmetros: `$1 = user_id`, `$2 = email`, `$3 = full_name`, `$4 = phone_ext`,
`$5 = windows_user`.

```sql
WITH u AS (
  INSERT INTO users (email, full_name, phone_ext, windows_user)
  VALUES ($2::text, $3::text, $4::text, $5::text)
  ON CONFLICT (email) DO UPDATE SET
    full_name    = EXCLUDED.full_name,
    phone_ext    = EXCLUDED.phone_ext,
    windows_user = COALESCE(EXCLUDED.windows_user, users.windows_user),
    updated_at   = now()
  RETURNING email
)
INSERT INTO browsers (user_id, email)
SELECT $1::text, email FROM u
ON CONFLICT (user_id) DO UPDATE SET
  email        = EXCLUDED.email,
  last_seen_at = now()
RETURNING email;
```

É uma única instrução, portanto atômica. Resposta: `{"ok": true, "email": "<email>"}`.

### `save-ticket`

Entrada: `conversation_id`, `email`, `summary`, `category` (obrigatórios); `user_id`,
`suggestions` (opcionais). Parâmetros: `$1 = conversation_id`, `$2 = email`, `$3 = user_id`,
`$4 = summary`, `$5 = category`, `$6 = suggestions`.

```sql
WITH cat AS (
  SELECT COALESCE(
    (SELECT code FROM categories WHERE code = $5::text AND active),
    'outros'
  ) AS code
)
INSERT INTO tickets (conversation_id, email, user_id, summary, category, suggestions)
SELECT $1::text, $2::text, $3::text, $4::text, cat.code, $6::text
FROM cat
WHERE EXISTS (SELECT 1 FROM users WHERE email = $2::text)
ON CONFLICT (conversation_id) DO UPDATE SET
  email       = EXCLUDED.email,
  user_id     = EXCLUDED.user_id,
  summary     = EXCLUDED.summary,
  category    = EXCLUDED.category,
  suggestions = EXCLUDED.suggestions,
  updated_at  = now()
RETURNING id AS ticket_id, category, (category <> $5::text) AS category_fallback;
```

Zero linhas (com *Always Output Data*) → `400` `{"ok": false, "error": "usuário não cadastrado"}`.
Uma linha → `{"ok": true, "ticket_id", "category", "category_fallback"}`.

### `list-categories`

Entrada: `include_inactive` (opcional, padrão `false`). Parâmetro: `$1 = include_inactive`.

```sql
SELECT code, label, description, active
FROM categories
WHERE active OR $1::boolean
ORDER BY (code = 'outros'), label;
```

Junte as linhas com um nó **Aggregate** (todos os itens num campo `categories`) e responda
`{"ok": true, "categories": [...]}`.

### `save-category`

Entrada: `code` (obrigatório); `label`, `description`, `active` (opcionais). Parâmetros:
`$1 = code`, `$2 = label`, `$3 = description`, `$4 = active`.

```sql
INSERT INTO categories (code, label, description, active)
SELECT $1::text,
       COALESCE($2::text, c.label),
       COALESCE($3::text, c.description),
       COALESCE($4::boolean, c.active, true)
FROM (SELECT 1) AS one
LEFT JOIN categories c ON c.code = $1::text
ON CONFLICT (code) DO UPDATE SET
  label       = EXCLUDED.label,
  description = EXCLUDED.description,
  active      = EXCLUDED.active,
  updated_at  = now()
RETURNING code, label, description, active;
```

Os valores atuais são completados **antes** do `INSERT` (pelo `LEFT JOIN`) porque o Postgres
verifica `NOT NULL` e `CHECK` na linha proposta antes de decidir pelo `ON CONFLICT`; com
`VALUES (..., NULL, ...)` uma alteração sem `label` falharia mesmo para categoria existente.

Traduza os erros do Postgres na saída de erro do nó (pela mensagem/código SQLSTATE) em `400`:
- `23502` (*not null*, coluna `label`) → `label é obrigatório para criar uma categoria`;
- `23514` com `categories_outros_always_active` → `a categoria outros não pode ser desativada`;
- `23514` com o `CHECK` de `code` → `code inválido` (a validação no nó Code já deve barrar antes).
Outros erros → `500`. Sucesso → `{"ok": true, "category": {...}}`.

### Ativação

Ative os cinco workflows (URLs de produção `/webhook/support/<operação>`) e exporte cada um como
JSON para o repositório da stack do n8n, se esse for o padrão existente (sem credenciais — o n8n
exporta só a referência).

## 10. Backup

Inclua o banco `support` na rotina de backup do Postgres do n8n (ex.: `pg_dump -Fc support`). Se
não houver rotina, informe o usuário em vez de criar uma por conta própria.

## 11. Critérios de pronto e entrega

- [ ] Roteiro de testes de [`api.md`](api.md#roteiro-de-testes) executado **pela URL pública do
      n8n** (passando pelo NGPM), todos os passos com o resultado esperado; dados de teste
      removidos.
- [ ] `support_app` sem `DELETE` e sem acesso ao banco do n8n (testado).
- [ ] Script do esquema versionado; workflows exportados (se for o padrão).
- [ ] Backup incluído (ou pendência informada).
- [ ] Entregue ao usuário: URL base dos webhooks, onde está o token (nunca no git nem no chat
      sem pedido explícito), nomes das credenciais no n8n e qualquer desvio deste documento.

## 12. Fora do escopo (próximas etapas, lado do Dify)

Registrado só para contexto:

- O nó HTTP do Dify sai pelo `ssrf_proxy` (squid) e chamará a **URL pública** do n8n via NGPM. Em
  dev/hmg o certificado é da localCA: configurar `HTTP_REQUEST_NODE_SSL_CERT_DATA` (CA em base64)
  no `.env` do Dify ou desligar a verificação TLS no nó.
- Chatflow de atendimento: entrada oculta `windows_user`; variáveis de conversa `stage`, `email`,
  `full_name`, `phone_ext`, `pending_query`; URL base e token do n8n em variáveis de ambiente
  (token como *secret*); falhas do n8n não podem travar o atendimento.
- Workflow administrativo "Categorias de atendimento" no Dify (listar/salvar/desativar), usado
  **só pelo "Executar" do Studio**, com o WebApp do app desligado (o link do WebApp é público).
