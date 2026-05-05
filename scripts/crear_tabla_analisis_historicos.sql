-- scripts/crear_tabla_analisis_historicos.sql
-- Memoria institucional del pipeline multiagente.
-- Una fila por run exitoso del pipeline.
-- Compatible con Neon / PostgreSQL 15+.

CREATE TABLE IF NOT EXISTS analisis_historicos (
    -- Identificación del run
    id                      SERIAL PRIMARY KEY,
    mercado                 VARCHAR(255)    NOT NULL,
    modo                    VARCHAR(50)     NOT NULL DEFAULT 'marca_propia',  -- 'arbitraje' | 'marca_propia'
    fecha_analisis          DATE            NOT NULL DEFAULT CURRENT_DATE,

    -- Métricas clave del mercado
    score_mercado           INTEGER,                          -- 0-100
    precio_mediana_mx       DECIMAL(10,2),
    intensidad_competencia  VARCHAR(20),                      -- 'baja' | 'media' | 'alta'
    categoria_producto      VARCHAR(50),                      -- 'alimentos' | 'electronica' | etc.

    -- Resultado del análisis (modo arbitraje)
    veredicto               VARCHAR(100),                     -- 'INVERTIR' | 'RIESGO MEDIO' | 'DESCARTAR'

    -- Resultado del análisis (modo marca_propia)
    concepto_nombre         VARCHAR(255),
    precio_objetivo_mx      DECIMAL(10,2),

    -- Hallazgos estructurados (JSONB para búsquedas eficientes)
    pain_points_top         JSONB,            -- [{tema, frecuencia_pct}, ...]
    gaps_top                JSONB,            -- [{area, impacto}, ...]
    keywords_top            JSONB,            -- [{keyword, score}, ...]

    -- Snapshot de métricas del mercado en el momento del análisis
    metricas_mercado        JSONB,            -- {num_productos, revenue_total, precio_min, precio_max, bsr_mediana}

    -- Snapshot completo de memoria_pipeline.json (para auditoría y re-entrenamiento)
    hallazgos_pipeline      JSONB,

    -- Calibración post-análisis: el usuario actualiza estos campos después
    resultado_usuario       VARCHAR(50),      -- 'invertido' | 'descartado' | 'pendiente' | 'en_seguimiento'
    roi_real_pct            DECIMAL(8,2),     -- NULL hasta que el usuario liquide la inversión
    notas_usuario           TEXT,
    fecha_resultado         DATE,

    created_at              TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Índices para las tres estrategias de búsqueda en obtener_contexto_historico()

-- 1. Mismo mercado exacto (historial directo) — búsqueda por igualdad + fecha descendente
CREATE INDEX IF NOT EXISTS idx_ah_mercado_fecha
    ON analisis_historicos (mercado, fecha_analisis DESC);

-- 2. Misma categoría (patrones cross-mercado) — filtra por categoría + fecha
CREATE INDEX IF NOT EXISTS idx_ah_categoria_fecha
    ON analisis_historicos (categoria_producto, fecha_analisis DESC)
    WHERE categoria_producto IS NOT NULL;

-- 3. Rango de precio similar — índice numérico para consultas de rango
CREATE INDEX IF NOT EXISTS idx_ah_precio_mediana
    ON analisis_historicos (precio_mediana_mx)
    WHERE precio_mediana_mx IS NOT NULL;

-- 4. Resultado del usuario — útil para filtrar solo análisis con feedback real
CREATE INDEX IF NOT EXISTS idx_ah_resultado_usuario
    ON analisis_historicos (resultado_usuario)
    WHERE resultado_usuario IS NOT NULL;

-- Comentarios de columnas para documentación en Neon console
COMMENT ON TABLE  analisis_historicos                    IS 'Memoria institucional: un registro por run exitoso del pipeline de análisis de mercado';
COMMENT ON COLUMN analisis_historicos.hallazgos_pipeline IS 'Snapshot completo de outputs/memoria_pipeline.json al final del run';
COMMENT ON COLUMN analisis_historicos.metricas_mercado   IS 'Resumen numérico del mercado: {num_productos, revenue_total, precio_min, precio_max, bsr_mediana}';
COMMENT ON COLUMN analisis_historicos.pain_points_top    IS 'Top pain points del análisis de reseñas: [{tema, frecuencia_pct}]';
COMMENT ON COLUMN analisis_historicos.gaps_top           IS 'Top gaps del GAP analysis: [{area, impacto}]';
COMMENT ON COLUMN analisis_historicos.keywords_top       IS 'Top keywords del análisis SEO: [{keyword, score}]';
COMMENT ON COLUMN analisis_historicos.resultado_usuario  IS 'Retroalimentación del usuario post-análisis: invertido | descartado | pendiente | en_seguimiento';
