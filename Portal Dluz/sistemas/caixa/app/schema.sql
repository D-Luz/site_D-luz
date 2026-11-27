-- Valores em centavos (INTEGER). Datas/horas em texto ISO, horário local do servidor.

CREATE TABLE IF NOT EXISTS usuario (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    login         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    nome          TEXT NOT NULL,
    senha_hash    TEXT NOT NULL,
    papel         TEXT NOT NULL CHECK (papel IN ('caixa', 'financeiro', 'admin')),
    empresa       TEXT,                       -- NULL = todas as empresas
    ativo         INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
    trocar_senha  INTEGER NOT NULL DEFAULT 1 CHECK (trocar_senha IN (0, 1)),
    criado_em     TEXT NOT NULL
);

-- Um caixa por empresa e dia.
CREATE TABLE IF NOT EXISTS caixa (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa         TEXT NOT NULL,
    data            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'aberto'
                    CHECK (status IN ('aberto', 'fechado', 'conferido')),
    saldo_inicial   INTEGER NOT NULL DEFAULT 0,
    saldo_contado   INTEGER,
    justificativa   TEXT,
    -- Leitura do ATS e resultado no momento do fechamento (JSON): o histórico mostra
    -- o que foi conferido, mesmo que o PDV mude depois no ATS.
    snapshot        TEXT,
    divergencias    INTEGER,
    aberto_por      INTEGER NOT NULL REFERENCES usuario (id),
    aberto_em       TEXT NOT NULL,
    fechado_por     INTEGER REFERENCES usuario (id),
    fechado_em      TEXT,
    conferido_por   INTEGER REFERENCES usuario (id),
    conferido_em    TEXT,
    obs_financeiro  TEXT,
    UNIQUE (empresa, data)
);

CREATE TABLE IF NOT EXISTS lancamento (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    caixa_id       INTEGER NOT NULL REFERENCES caixa (id),
    forma          TEXT NOT NULL,
    valor          INTEGER NOT NULL CHECK (valor > 0),
    obs            TEXT NOT NULL DEFAULT '',
    pdv_vinculado  TEXT,                      -- PDV escolhido à mão no encerramento
    criado_por     INTEGER NOT NULL REFERENCES usuario (id),
    criado_em      TEXT NOT NULL,
    alterado_por   INTEGER REFERENCES usuario (id),
    alterado_em    TEXT,
    excluido       INTEGER NOT NULL DEFAULT 0 CHECK (excluido IN (0, 1)),
    aprovado_por   INTEGER REFERENCES usuario (id),
    aprovado_em    TEXT,
    aprovacao_obs  TEXT
);

CREATE INDEX IF NOT EXISTS idx_lancamento_caixa ON lancamento (caixa_id);

-- Registro de tudo o que acontece (tira-teima). Só recebe inserções.
CREATE TABLE IF NOT EXISTS historico (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    caixa_id       INTEGER NOT NULL REFERENCES caixa (id),
    lancamento_id  INTEGER REFERENCES lancamento (id),
    usuario_id     INTEGER NOT NULL REFERENCES usuario (id),
    acao           TEXT NOT NULL,
    detalhe        TEXT NOT NULL DEFAULT '',
    em             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_historico_caixa ON historico (caixa_id);

CREATE TRIGGER IF NOT EXISTS historico_sem_update BEFORE UPDATE ON historico
BEGIN SELECT RAISE(ABORT, 'O histórico não pode ser alterado.'); END;
CREATE TRIGGER IF NOT EXISTS historico_sem_delete BEFORE DELETE ON historico
BEGIN SELECT RAISE(ABORT, 'O histórico não pode ser apagado.'); END;
