-- scripts/crear_tablas_productos_keywords_resenas.sql
-- Tablas que consumen los agentes de ingesta/competencia/precio_valor/keywords/resenas.
-- Compatible con Neon / PostgreSQL 15+.

CREATE TABLE IF NOT EXISTS productos (
    id                       SERIAL PRIMARY KEY,
    asin                     VARCHAR(20)     NOT NULL,
    titulo                   VARCHAR(500),
    marca                    VARCHAR(255),
    categoria                VARCHAR(255),
    size_tier                VARCHAR(50),
    precio                   DECIMAL(10,2),
    bsr                      INTEGER,
    reviews_count            INTEGER,
    rating                   DECIMAL(3,2),
    ventas_mensuales_asin    INTEGER,
    ventas_mensuales_parent  INTEGER,
    revenue_mensual_asin     DECIMAL(12,2),
    revenue_mensual_parent   DECIMAL(12,2),
    fees                     DECIMAL(10,2),
    active_sellers           INTEGER,
    review_velocity          INTEGER,
    fba                      BOOLEAN,
    dimensiones              VARCHAR(100),
    peso_kg                  DECIMAL(8,3),
    seller_nombre            VARCHAR(255),
    seller_age_months        INTEGER,
    buy_box                  VARCHAR(255),
    best_seller              BOOLEAN,
    pais_vendedor            VARCHAR(10),
    imagen_url               TEXT,
    fecha_creacion_listing   DATE,
    fuente                   VARCHAR(50)     NOT NULL,
    mercado                  VARCHAR(255)    NOT NULL,
    fecha_captura            DATE            NOT NULL DEFAULT CURRENT_DATE,
    UNIQUE (asin, fuente, fecha_captura)
);

CREATE INDEX IF NOT EXISTS idx_productos_mercado ON productos (mercado);
CREATE INDEX IF NOT EXISTS idx_productos_bsr      ON productos (bsr);

CREATE TABLE IF NOT EXISTS keywords (
    id                       SERIAL PRIMARY KEY,
    keyword                  VARCHAR(500)    NOT NULL,
    volumen_busqueda         INTEGER,
    tendencia_30d            DECIMAL(10,2),
    productos_competidores   INTEGER,
    cerebro_iq_score         INTEGER,
    keyword_sales            INTEGER,
    title_density            INTEGER,
    competitor_rank_avg      DECIMAL(10,2),
    sugerido_ppc_bid         DECIMAL(10,2),
    fuente                   VARCHAR(50)     NOT NULL,
    asin_origen              VARCHAR(20)     NOT NULL DEFAULT '',
    mercado                  VARCHAR(255)    NOT NULL,
    fecha_captura            DATE            NOT NULL DEFAULT CURRENT_DATE,
    UNIQUE (keyword, fuente, asin_origen, fecha_captura)
);

CREATE INDEX IF NOT EXISTS idx_keywords_mercado ON keywords (mercado);

CREATE TABLE IF NOT EXISTS resenas (
    id                SERIAL PRIMARY KEY,
    id_resena         VARCHAR(50),
    asin              VARCHAR(20),
    mercado           VARCHAR(255)    NOT NULL,
    marca             VARCHAR(255),
    rating            INTEGER         NOT NULL,
    titulo            VARCHAR(500),
    cuerpo            TEXT,
    fecha             DATE,
    verificado        BOOLEAN,
    util_votos        INTEGER,
    tipo              VARCHAR(20),
    fecha_captura     DATE            DEFAULT CURRENT_DATE
);

CREATE INDEX IF NOT EXISTS idx_resenas_mercado ON resenas (mercado);
