# Interface web para publicar as imagens da base de conhecimento (kb_admin)

Data: 2026-10-01

## Objetivo

Hoje as imagens dos manuais da base de conhecimento do chatbot são publicadas
pelo `docker/kb_assets/publish.sh`, na linha de comando, e o `.md` com URLs
absolutas é enviado à mão pela interface do Dify. O objetivo é uma interface web
simples, na mesma stack do Dify, que permita a quem já administra bases no Dify
enviar o pacote `.zip` de um manual, publicar as imagens, baixar o `.md`
convertido, ver os parâmetros recomendados para o ingest no Dify (calculados a
partir do próprio `.md`) e gerenciar (listar, substituir, apagar) os manuais
publicados.

## Fora do escopo

- Ingestão do `.md` no Dify: continua sendo feita pela interface do Dify. O
  app só calcula e mostra os parâmetros recomendados; quem os digita é o
  usuário, na tela de criação do documento no Dify.
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
  NGPM apontando para `dify_kb_admin:8000`. O NGPM repassa o caminho completo,
  então todas as rotas do app ficam sob o prefixo `/kb-admin` (inclusive
  `/kb-admin/healthz`, sem login, usado no teste do deploy). Os estilos e o
  pouco JavaScript ficam embutidos no HTML, sem arquivos estáticos, para não
  esbarrar no "Cache Assets" do NGPM.
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
  `GET /console/api/workspaces/current/summary` e ler o papel (`role`) do usuário
  (na imagem `langgenius/dify-api:1.17.1` em execução; o código-fonte em `api/` deste
  repositório é de outra versão e ainda tem o antigo `POST /workspaces/current`).
  Essa chamada exige o token de acesso e o token CSRF que o login devolve em
  cookies. Com https, os cookies saem com prefixo `__Host-` e `Secure`, então
  o app os lê do `Set-Cookie` e os reenvia manualmente (`Authorization:
  Bearer`, cabeçalho `X-CSRF-Token` e o cookie CSRF).
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

## Parâmetros recomendados para o ingest

O app analisa o `.md` convertido (com as URLs absolutas, que são o que o Dify
recebe) e mostra, na tela do manual, os valores a digitar na tela de ingest do
Dify, cada um com uma justificativa curta e um botão de copiar. Os valores
dependem do tamanho e da estrutura de cada `.md`.

### Como o Dify 1.17 processa um `.md` (base do cálculo)

Verificado no código (`api/core/rag/...`):

- Com `ETL_TYPE=dify`, o `MarkdownExtractor` divide o arquivo por títulos
  (`^#+\s`, fora de blocos de código) antes do chunking: cada seção vira um
  documento `"<título sem #>\n<corpo>"`. Os `#` são apagados e o texto entre
  `<` e `>` é removido. Nenhum chunk atravessa um título.
- Os tamanhos são medidos em caracteres (`len`), com mínimo de 50 e máximo de
  `INDEXING_MAX_SEGMENTATION_TOKENS_LENGTH` (4000 no `.env`).
- O splitter corta cada seção pelo delimitador (que é descartado) e **não
  junta** pedaços pequenos. Só o pedaço maior que o limite é recortado de novo,
  pela ordem `\n\n`, `。`, `. `, ` `, `""`. No corte por espaço as palavras
  ficam grudadas, então esse caso tem de ser evitado.
- No Pai-filho, o Top K conta chunks filhos, e os pais repetidos são unidos
  (podem voltar menos de K pais). Pai-filho exige High Quality. O tipo de chunk
  (General ou Pai-filho) fica fixo na base depois do primeiro documento.
- "Delete all URLs and email addresses" preserva `![...](http...)`, mas apaga
  URLs soltas e e-mails do texto.

Consequência: o delimitador `\n#`, recomendado hoje no
`docker/kb_assets/README.md`, nunca casa com os títulos (os `#` já foram
apagados). Ele só funciona por acaso, deixando cada seção inteira como pai.

### Simulação

O módulo `ingest_params` reproduz o pipeline acima, em Python puro e sem
importar o Dify: extração por títulos, limpeza com "Replace consecutive
spaces" ligado e as medidas de cada seção, parágrafo (`\n\n`) e linha (`\n`).
O resultado é uma recomendação determinística. As constantes copiadas do Dify
(regex do extrator, lista de separadores, limites) ficam num único arquivo,
com o caminho de origem no Dify 1.17.1 anotado, para conferir quando o Dify for
atualizado.

### Regras

Modo e pré-processamento (fixos, com justificativa):

| Parâmetro | Valor | Motivo |
|---|---|---|
| Chunk mode | Parent-child, parent em **Paragraph** | o pai (seção inteira, com as imagens) vai para o LLM, o filho (pequeno) é usado na busca. Full-doc mandaria o manual inteiro e pularia a limpeza. |
| Replace consecutive spaces, newlines and tabs | marcado | necessário para a regra do delimitador do pai |
| Delete all URLs and email addresses | desmarcado | manuais citam e-mails e endereços de portais |
| Summary Auto-Gen | desligado | o resumo não carrega as imagens |
| Index method | High Quality | exigido pelo Pai-filho |
| Embedding model | `text-embedding-3-small` (o da base) | o app só exibe; a escolha é feita na base |

Chunk pai (calculado):

- **Delimiter:** uma sequência que não aparece em nenhuma seção depois da
  limpeza, para que cada seção vire um único pai e o passo fique junto das
  suas imagens: `\n\n\n`, que nunca sobra porque a limpeza "Replace
  consecutive spaces" troca 3 ou mais quebras por 2 em todo o texto, inclusive
  em blocos de código.
- **Maximum chunk length:** o tamanho da maior seção, arredondado para cima ao
  múltiplo de 100, com mínimo de 500 e teto em
  `INDEXING_MAX_SEGMENTATION_TOKENS_LENGTH`.
- Seção com mais de 3.000 caracteres: aviso "acima do recomendado pelo
  PDF_TO_RAG", com o título e o tamanho. Seção maior que o teto (4000): aviso
  de erro, sugerindo dividi-la com subtítulos, porque o Dify recorta a seção e
  pode separar um passo da sua imagem.

Chunk filho (calculado):

- **Delimiter:** `\n\n` (parágrafo) se todos os parágrafos cabem no teto do
  filho (`KB_CHILD_MAX_LENGTH`, padrão 1000 caracteres). O modelo de embedding
  previsto, `text-embedding-3-small`, aceita 8.191 tokens, então o teto não vem
  do modelo, e sim da precisão da busca: filhos menores casam melhor com a
  pergunta. Senão, `\n` (linha), que divide listas e tabelas
  linha a linha.
- **Maximum chunk length:** o tamanho do maior parágrafo (ou da maior linha),
  arredondado para cima ao múltiplo de 50, com mínimo de 100 e teto em
  `KB_CHILD_MAX_LENGTH`.
- Linha maior que o teto: aviso com o trecho. O Dify cortaria por espaço,
  grudando as palavras.

### Alinhamento com o prompt de conversão

As regras que o `.md` precisa seguir estão em
`docker/kb_assets/docs/PDF_TO_RAG.md`, seção "Regras exigidas pela ingestão no
Dify": títulos com `#` e espaço, seções de até 3.000 caracteres, linhas de até
1.000, tabelas com linhas autocontidas, nada entre `<` e `>` e nome do pacote
igual ao do `.md` e do `.zip`. Os avisos do app usam os mesmos números e citam
a regra correspondente do prompt. Se um limite mudar, ele muda nos dois
lugares.

Busca (configuração da base, igual para todos os manuais dela):

- **Hybrid Search** com **Rerank Model** ligado, usando o modelo previsto
  `jina-reranker-v3` (o oferecido hoje pelo plugin da Jina no Dify; o v3.5 é substituto direto, bastando trocar o modelo quando o plugin o oferecer). Se a base não tiver modelo de rerank, a alternativa é
  Weighted Score 0.7 semântico / 0.3 palavra-chave (o padrão do Dify). Os
  nomes dos modelos ficam em variáveis (`KB_EMBEDDING_MODEL_LABEL` e
  `KB_RERANK_MODEL_LABEL`) e servem só para o texto exibido.
- **Top K:** o número de filhos por seção (mediana) vezes 3 (cerca de três
  seções distintas por pergunta, já que o Top K conta filhos), limitado entre
  3 e 10. Como vale para a base inteira, a tela avisa: "Se a base tiver
  outros manuais, use o maior Top K recomendado entre eles".
- **Score Threshold:** desligado. O valor depende do modelo de embedding e
  deve ser ajustado testando no Dify ("Retrieval Testing").

Avisos extras da análise:

- texto entre `<` e `>` no corpo das seções, inclusive em blocos de código (o
  Dify apaga esse texto);
- `#` dentro de títulos (ex.: "C#"), que o Dify também apaga;
- se a base já tiver documentos em modo General, o Pai-filho não poderá ser
  usado nela (o tipo de chunk é fixo por base).

Exemplo com o `manual-office365-rag` (convertido):

- 25 seções, maior com 2.442 caracteres → pai: delimitador `\n\n\n`, tamanho
  2500;
- maior parágrafo com 2.341 caracteres (a tabela de "Problemas Comuns") passa
  do teto de 1000 → filho: delimitador `\n`, tamanho 300 (maior linha: 296).
- mediana de 8 filhos por seção → Top K = 8 × 3 = 24, limitado a 10.

### Onde aparece

- Na tela do manual: o painel "Parâmetros para o ingest no Dify" (a tabela
  acima com os valores calculados), os avisos e um resumo (seções, maior
  seção, quantidade estimada de chunks pai e filho).
- Na tela de confirmação de substituição: os parâmetros antigos e os novos
  lado a lado, destacando o que mudou (por exemplo, o tamanho máximo do pai).
  Se mudou, o documento precisa ser reprocessado no Dify com os novos valores.
- Os parâmetros são recalculados a cada exibição a partir do `.md` guardado,
  e não ficam gravados no `meta.json`.

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
- `docker/deploy.sh`: construir a imagem `dify-kb-admin:local` antes do deploy
  e marcá-la também com uma tag derivada do ID da imagem
  (`dify-kb-admin:<12 caracteres do ID>`), exportada em `KB_ADMIN_IMAGE` para a
  stack. Sem a tag nova, o Swarm não atualizaria o serviço quando o código
  mudasse, porque a especificação do serviço continuaria igual. O
  `docker-compose.yaml` não ganha o serviço (o kb_admin é só do Swarm).
- `docker/kb_assets/README.md` (ou novo `docker/kb_admin/README.md`): uso da
  interface e configuração da custom location `/kb-admin/` no NGPM. A seção
  "Base de conhecimento no Dify" deixa de recomendar o delimitador `\n#` e
  passa a apontar para os parâmetros calculados pelo app.
- `.env.example`: `KB_ASSETS_BASE_URL`, `KB_ADMIN_SECRET_KEY`,
  `KB_CHILD_MAX_LENGTH`, `KB_EMBEDDING_MODEL_LABEL` e `KB_RERANK_MODEL_LABEL`
  (opcionais, comentados). O `kb_admin` lê
  `INDEXING_MAX_SEGMENTATION_TOKENS_LENGTH` do mesmo `.env` do Dify.
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
  - Parâmetros de ingest: a simulação do extrator (títulos, `#` apagados,
    blocos de código, `<...>` removido), as regras de delimitador e tamanho
    do pai e do filho (seção no teto, acima do teto, parágrafo grande que
    leva a `\n`, linha acima do teto), o Top K e os avisos. Inclui um teste
    com `.md` sintético da mesma forma do manual do Office 365 e o resultado
    esperado (pai 2500 com `\n\n\n`, filho `\n`).
  - Login com o Dify simulado: sucesso, papel sem permissão, senha errada,
    rate limit, Dify fora do ar. Também sessão expirada e CSRF.
- `docker/kb_assets/test_default_conf.sh` passa a conferir o cabeçalho CSP.
- Teste manual: subir a stack de DEV, configurar `/kb-admin/` no NGPM, enviar
  o `manual-office365-rag.zip`, abrir uma imagem pela URL pública, baixar o
  `.md`, conferir as URLs, fazer o ingest no Dify com os parâmetros
  recomendados, conferir no "Preview Chunk" que cada pai é uma seção inteira
  com as suas imagens e testar uma pergunta no chat. Depois, apagar o manual.
