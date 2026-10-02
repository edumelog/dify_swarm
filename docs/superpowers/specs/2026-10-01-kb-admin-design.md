# Interface web para publicar as imagens da base de conhecimento (kb_admin)

Data: 2026-10-01

## Objetivo

Hoje as imagens dos manuais da base de conhecimento do chatbot são publicadas
pelo `docker/kb_assets/publish.sh`, na linha de comando, e o `.md` com URLs
absolutas é enviado à mão pela interface do Dify. O objetivo é uma interface web
simples, na mesma stack do Dify, que permita a quem já administra bases no Dify
enviar o pacote `.zip` de um manual, publicar as imagens, baixar o `.md`
convertido e gerenciar (listar, substituir, apagar) os manuais publicados.

## Fora do escopo

- Ingestão do `.md` no Dify: continua sendo feita pela interface do Dify, com
  as configurações de chunk, indexação e busca ajustadas por lá.
- Apagar ou atualizar documentos/bases no Dify: o app só avisa que isso deve
  ser feito no Dify.
- Conversão de PDF para `.md` (continua fora do projeto, ver
  `docker/kb_assets/docs/PDF_TO_RAG.md`).

## Arquitetura

- Novo serviço `kb_admin` (na stack: `dify_kb_admin`): app Python com FastAPI e
  páginas HTML renderizadas no servidor (Jinja2), sem framework de frontend.
  Código em `docker/kb_admin/`. A imagem é construída localmente
  (`dify-kb-admin:local`) pelo `deploy.sh` antes do `docker stack deploy`.
- Acesso pelo mesmo domínio do Dify, em `/kb-admin/`, via custom location no
  NGPM apontando para `dify_kb_admin:8000`. O app roda com `root_path=/kb-admin`.
- Redes: `net_nginx_pm` (para o NGPM) e `default` (para alcançar `api:5001`).
- Volumes:
  - `dify_kb_assets` (já existe): montado com escrita no `kb_admin` em
    `/data/kb-assets`. O `kb_assets` (nginx) continua com ele somente leitura e
    segue sendo quem serve as imagens.
  - `dify_kb_admin_data` (novo, privado): guarda o `.md` original e os
    metadados de cada manual. Não é servido pelo nginx, porque o conteúdo é
    interno.
- Os volumes são locais: `kb_admin` e `kb_assets` precisam estar no mesmo nó.
  Ambos recebem a mesma placement constraint (`node.role == manager`),
  documentada no README.
- Cada ambiente (DEV, HMG, PROD) tem a sua stack, os seus volumes e o seu
  `kb_admin`. O zip é enviado em cada ambiente.

## URL das imagens por ambiente

- O `.md` é guardado na forma original (`images/arquivo.png`). A troca para URL
  absoluta acontece só no download, então cada ambiente gera o `.md` com a sua
  própria URL.
- URL base: `KB_ASSETS_BASE_URL`, se definida no `.env`; senão
  `${APP_WEB_URL}/kb-assets` (o `APP_WEB_URL` já é obrigatório no `deploy.sh`).
  Se nenhuma das duas estiver definida, o app não sobe e registra o erro.
- Todas as telas mostram no topo: "Imagens publicadas em `<URL base>/`". A tela
  de download avisa: "Este .md só funciona no Dify de `<domínio>`".

## Autenticação

- Tela de login com e-mail e senha. O app chama
  `POST http://api:5001/console/api/login` com `{"email", "password": base64(senha)}`.
  Se der certo, usa os cookies devolvidos para chamar
  `GET /console/api/workspaces/current` e ler o papel (`role`) do usuário.
- Só entram os papéis `owner`, `admin` e `editor`. Os outros recebem "Sem
  permissão para gerenciar a base de conhecimento".
- Erros do Dify viram mensagens em português: credenciais inválidas, conta
  bloqueada, limite de tentativas (o rate limit é aplicado pelo próprio Dify),
  Dify indisponível.
- Depois do login, os tokens do Dify são descartados. O app cria a sua própria
  sessão: cookie assinado (`kb_admin_session`, `HttpOnly`, `SameSite=Strict`,
  `Path=/kb-admin`, `Secure` quando a requisição chega por https), com e-mail e
  papel, válido por 8 horas. Segredo de assinatura: `KB_ADMIN_SECRET_KEY` ou,
  na falta dele, derivado do `SECRET_KEY` do Dify.
- Os formulários que alteram dados (enviar, substituir, apagar, sair) levam um
  token CSRF ligado à sessão.

## Formato do zip

- O nome do zip, sem `.zip`, é o slug do manual: `^[a-z0-9][a-z0-9-]*$` (ex.:
  `manual-office365-rag`). As URLs ficam `<URL base>/<slug>/<arquivo>`.
- Conteúdo aceito: uma única pasta raiz com o nome do slug (como no
  `manual-office365-rag.zip` de exemplo) ou os arquivos direto na raiz do zip.
  Dentro dela:
  - exatamente um `<slug>.md` (o `.md` tem o mesmo nome do zip);
  - `README.md` opcional, ignorado;
  - pasta `images/` com as imagens;
  - qualquer outro arquivo é ignorado e listado como aviso.
- As regras do `.md` são as mesmas do `publish.sh`: imagens só como
  `![descrição](images/arquivo)` ou `./images/arquivo`, nomes só com
  `[A-Za-z0-9._-]`. São recusados `<img>`, imagem fora de `images/` e imagem
  estilo referência; URLs `http(s)://` são mantidas. Todos os problemas são
  listados de uma vez.
- Formatos de imagem: qualquer extensão de imagem conhecida (png, jpg, jpeg,
  gif, webp, svg, bmp, ico, avif, tif, tiff).
- Só as imagens referenciadas no `.md` são publicadas. Imagem referenciada que
  não está no zip é erro; imagem do zip não referenciada vira aviso.

## Segurança do upload

- Limites: zip de até 50 MB, até 200 MB descompactado e até 500 arquivos. Esses
  limites são checados pelos tamanhos declarados antes de extrair e de novo
  durante a extração.
- Recusa entradas com caminho absoluto, `..`, links simbólicos ou nomes
  duplicados.
- No `kb_assets` (nginx), as respostas de `/kb-assets/` passam a enviar
  `Content-Security-Policy: sandbox`, para que scripts dentro de svg não rodem
  quando a imagem for aberta direto no navegador.

## Telas e operações

1. **Login** (`/kb-admin/login`).
2. **Lista de manuais** (`/kb-admin/`): slug, quantidade de imagens, data da
   última publicação e e-mail de quem publicou, mais o botão "Enviar manual".
   Pastas que existem no volume público sem metadados (ex.: `office365`,
   publicada pelo `publish.sh`) aparecem como "publicado fora da interface (sem
   .md)" e só podem ser apagadas.
3. **Enviar manual**: upload do zip, seguido de validação.
   - Com erros: lista dos problemas, nada é publicado.
   - Manual novo e válido: publica e abre a tela do manual.
   - Manual já existente: tela de confirmação com as imagens que entram, saem
     e mudam. Só publica depois de "Confirmar substituição". O zip validado
     fica numa área temporária do volume privado por até 1 hora, identificado
     por um token.
4. **Manual** (`/kb-admin/manuals/<slug>`): galeria das imagens publicadas, o
   botão "Baixar .md para o Dify" (arquivo `<slug>.md` com URLs absolutas), o
   aviso de ambiente e as ações "Substituir" e "Apagar".
5. **Apagar**: confirmação. Remove as imagens do volume público e o `.md` e os
   metadados do volume privado. Mensagem final: "Lembre-se de apagar o
   documento correspondente na base de conhecimento do Dify."

## Publicação atômica

- As imagens são gravadas em `/data/kb-assets/.<slug>.tmp-<id>/`. Depois o app
  renomeia a pasta atual para `.<slug>.old-<id>`, renomeia a temporária para
  `<slug>` e remove a antiga. O nginx já recusa caminhos ocultos.
- O `.md` e o `meta.json` (`slug`, `published_at`, `published_by`, `images`)
  são gravados no volume privado em `/data/manuals/<slug>/`, também com troca
  por renomeação.
- Um lock por slug (arquivo de lock no volume privado) impede duas publicações
  simultâneas do mesmo manual. Na inicialização, o app limpa as pastas
  temporárias que sobraram.

## Erros

- Cada falha de validação, autenticação ou publicação tem uma exceção própria
  no app. Todas viram mensagens em português na tela, nunca um stack trace.
- Falhas inesperadas mostram "Erro inesperado; tente novamente" e são
  registradas no log do container.

## Mudanças em arquivos existentes

- `docker/docker-stack.yml`: serviço `kb_admin`, volume `dify_kb_admin_data` e
  placement constraint no `kb_assets`.
- `docker/kb_assets/default.conf`: cabeçalho `Content-Security-Policy: sandbox`.
- `docker/deploy.sh`: construir a imagem `dify-kb-admin:local` antes do deploy.
- `docker/kb_assets/README.md` (ou novo `docker/kb_admin/README.md`): uso da
  interface e configuração da custom location `/kb-admin/` no NGPM.
- `.env.example`: `KB_ASSETS_BASE_URL` e `KB_ADMIN_SECRET_KEY` (opcionais,
  comentados).
- O `publish.sh` continua funcionando como alternativa na linha de comando.

## Testes

- `pytest` (em `docker/kb_admin/tests/`), sem depender de `kb/`. Os zips de
  teste são gerados na hora.
  - Validação do zip: pasta raiz ou arquivos na raiz, nome do `.md` diferente
    do zip, zip slip, symlink, limites de tamanho, todas as regras do `.md`.
  - Reescrita das URLs com URL base vinda de `KB_ASSETS_BASE_URL` e de
    `APP_WEB_URL`.
  - Publicação atômica, substituição (diff de imagens), exclusão e pastas
    legadas.
  - Login com o Dify simulado: sucesso, papel sem permissão, senha errada,
    rate limit, Dify fora do ar. Também sessão expirada e CSRF.
- `docker/kb_assets/test_default_conf.sh` passa a conferir o cabeçalho CSP.
- Teste manual: subir a stack de DEV, configurar `/kb-admin/` no NGPM, enviar
  o `manual-office365-rag.zip`, abrir uma imagem pela URL pública, baixar o
  `.md`, conferir as URLs e apagar o manual.
