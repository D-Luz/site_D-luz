CREATE TABLE IF NOT EXISTS usuarios (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    nome           TEXT NOT NULL,
    email          TEXT NOT NULL UNIQUE COLLATE NOCASE,
    senha_hash     TEXT NOT NULL,
    perfil         TEXT NOT NULL
                   CHECK (perfil IN ('solicitante', 'gestor', 'fiscal', 'admin')),
    ativo          INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
    trocar_senha   INTEGER NOT NULL DEFAULT 1 CHECK (trocar_senha IN (0, 1)),
    falhas_login   INTEGER NOT NULL DEFAULT 0,
    bloqueado_ate  TEXT,
    criado_em      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pedidos (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente           TEXT NOT NULL,
    valor_centavos    INTEGER NOT NULL CHECK (valor_centavos >= 0),
    descricao         TEXT NOT NULL,
    pdf_arquivo       TEXT NOT NULL,
    pdf_nome_original TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pendente'
                      CHECK (status IN ('pendente', 'aprovado', 'reprovado', 'enviado',
                                        'faturado', 'recebido', 'arquivado')),
    solicitante_id    INTEGER NOT NULL REFERENCES usuarios (id),
    reenvio_de        INTEGER REFERENCES pedidos (id),
    data_pedido       TEXT,
    numero_nf         TEXT,
    data_chegada      TEXT,
    criado_em         TEXT NOT NULL,
    atualizado_em     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pedidos_status ON pedidos (status);
CREATE INDEX IF NOT EXISTS idx_pedidos_solicitante ON pedidos (solicitante_id);

-- Só recebe inserções, nunca alterações.
CREATE TABLE IF NOT EXISTS historico (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    pedido_id        INTEGER NOT NULL REFERENCES pedidos (id),
    usuario_id       INTEGER NOT NULL REFERENCES usuarios (id),
    acao             TEXT NOT NULL,
    status_anterior  TEXT
                     CHECK (status_anterior IS NULL OR status_anterior IN
                            ('pendente', 'aprovado', 'reprovado', 'enviado',
                             'faturado', 'recebido', 'arquivado')),
    status_novo      TEXT NOT NULL
                     CHECK (status_novo IN ('pendente', 'aprovado', 'reprovado', 'enviado',
                                            'faturado', 'recebido', 'arquivado')),
    comentario       TEXT,
    criado_em        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_historico_pedido ON historico (pedido_id);

CREATE TABLE IF NOT EXISTS avisos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id    INTEGER NOT NULL REFERENCES usuarios (id),
    pedido_id     INTEGER REFERENCES pedidos (id),
    assunto       TEXT NOT NULL,
    corpo         TEXT NOT NULL,
    lido          INTEGER NOT NULL DEFAULT 0 CHECK (lido IN (0, 1)),
    email_status  TEXT NOT NULL DEFAULT 'pendente'
                  CHECK (email_status IN ('pendente', 'enviado', 'erro', 'desligado')),
    criado_em     TEXT NOT NULL,
    -- Tentativas de envio do e-mail e a hora (ISO UTC) da última; ver
    -- notificacoes.enviar_pendentes. Colunas acrescentadas a bancos antigos
    -- por db.init_db.
    email_tentativas        INTEGER NOT NULL DEFAULT 0,
    email_ultima_tentativa  TEXT
);

CREATE INDEX IF NOT EXISTS idx_avisos_usuario ON avisos (usuario_id);
