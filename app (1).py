"""
App web (Flask) - Chatbot financiero CUN
Misma lógica ya probada (SQL + Azure OpenAI), adaptada para correr
como servicio web permanente en Render.com en vez de Azure Functions.
"""

import json
import os
import pytds
from flask import Flask, request, jsonify
from openai import AzureOpenAI

app = Flask(__name__)

# ============================================================
# 1. CONFIGURACIÓN - se lee de variables de entorno
#    (en Render, se configuran en el panel "Environment", no aquí)
# ============================================================

SQL_SERVER = "172.16.1.33"
SQL_DATABASE = "CUN_REPOSITORIO"
SQL_USER = "agente_ia_lectura"

NOMBRE_VISTA = "Financiera.Ventas_Recibos_contact"

ESQUEMA = f"""
Vista: {NOMBRE_VISTA}
(Esta vista ya combina la información financiera por estudiante/periodo,
el método de pago principal de cada estudiante, y el estado del periodo
académico. NO reconstruyas JOINs manuales: todos los datos que necesitas
ya están disponibles como columnas de esta única vista.)

Columnas reales confirmadas:
  - Documento_Estudiante_zoho (varchar): identificación del estudiante
  - PERIODO_ORIGEN (varchar)
  - PERIODO (varchar): periodo académico, ej '26V01', '25A', '24V02'
  - NUEVO (varchar): indica si el estudiante es nuevo o antiguo
  - FuerzaComercialFinal (nvarchar): fuerza comercial agrupada
  - ESTADO_PAGO (varchar)
  - EST_MATRICULADO (varchar): estado de matrícula del estudiante
  - SECCIONAL (varchar)
  - SEDE (varchar)
  - COD_UNI (varchar)
  - PROGRAMA (varchar): código del programa académico
  - NOM_PROGRAMA (varchar): nombre del programa académico (SIN normalizar)
  - NOM_PROGRAMA_NORM (varchar): nombre del programa NORMALIZADO -- USA ESTA
    COLUMNA para filtrar por nombre de programa.
  - DOCUMENTO (varchar)
  - MODALIDAD (varchar)
  - Ciudad (varchar): ciudad SIN normalizar
  - CIUDAD_GEOLOCALIZADA (varchar): ciudad geolocalizada SIN normalizar
  - CIUDAD_GEOLOCALIZADA_NORM (varchar): ciudad geolocalizada NORMALIZADA --
    USA ESTA COLUMNA para filtrar por ciudad.
  - ORDEN (varchar)
  - NIVEL (varchar)
  - NOMBRE_DE_CONVENIO (varchar)
  - CLASE_ACTUAL (varchar)
  - TIPO_PRODUCTO (varchar)
  - VALOR_ORDEN (decimal)
  - ADICIONALES (decimal)
  - VAL_BECDTOS (decimal): valor de becas/descuentos
  - VAL_OTRAS_NCR (decimal)
  - VAL_2X1 (float)
  - Valor_ingles (float): valor correspondiente a inglés
  - ORDEN_NETO (decimal)
  - CARTERA_ESTUDIANTE (decimal): cartera del estudiante
  - TIP_INSCR (varchar): tipo de inscripción
  - VALOR_RECAUDO (decimal): valor recaudado
  - NOMBRE_FRANQUICIA (varchar)
  - NOMBRE_CAJA (varchar)
  - VALOR_CUOTA_INICIAL (decimal)
  - VALOR_FINANCIACION (decimal)
  - fecha_actualizacion (datetime)
  - clasificacion_vendedor (varchar)
  - ESP_MARCA (varchar)
  - ULTIMO_PERIODO (varchar)
  - Ingreso_Neto_Ejecutado (decimal): = ORDEN_NETO. Métrica principal de
    "ingreso neto ejecutado". ÚSALA por defecto cuando pregunten por
    "ingreso neto" o "ingreso" sin más especificación.
  - Ingreso_Neto_sin_Ingles (float): = ORDEN_NETO - Valor_ingles. Úsala SOLO
    si el usuario pide explícitamente el ingreso "sin inglés".
  - METODO_PAGO (varchar): método de pago principal del estudiante
  - METODO_PAGO_ABREVIADO (varchar)
  - VALOR_PAGADO (numeric): valor pagado según el método de pago principal
  - Estado_Periodo (varchar): estado del periodo académico
"""


def get_client():
    return AzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_KEY"],
        api_version="2024-08-01-preview",
    )


# ============================================================
# 2. CONEXIÓN A SQL SERVER
# ============================================================

def conectar_sql():
    sql_password = os.environ["SQL_PASSWORD"]
    return pytds.connect(
        dsn=SQL_SERVER,
        database=SQL_DATABASE,
        user=SQL_USER,
        password=sql_password,
    )


# ============================================================
# 3. LÓGICA (idéntica a la ya probada)
# ============================================================

def generar_sql(client, pregunta_usuario, filtros=None):
    filtros_texto = ""
    if filtros:
        filtros_texto = (
            f"\nFiltros activos en el reporte de Power BI (úsalos SOLO si la "
            f"pregunta no especifica su propio filtro para esa misma dimensión): "
            f"{json.dumps(filtros, ensure_ascii=False)}"
        )

    prompt = f"""Eres un asistente que traduce preguntas en español a consultas SQL de SQL Server.

Esquema de la base de datos:
{ESQUEMA}
{filtros_texto}

Reglas:
- Genera SOLO la consulta SQL, sin explicaciones, sin comentarios, sin marcado markdown.
- SOLO puedes generar consultas SELECT. Nunca generes INSERT, UPDATE, DELETE, DROP, ALTER.
- SIEMPRE consulta ÚNICAMENTE la vista {NOMBRE_VISTA}. NUNCA reconstruyas JOINs manuales.
- Para filtrar por programa, usa NOM_PROGRAMA_NORM (no NOM_PROGRAMA).
- Para filtrar por ciudad, usa CIUDAD_GEOLOCALIZADA_NORM (no Ciudad ni CIUDAD_GEOLOCALIZADA).
- Para "ingreso neto" o "ingreso", usa Ingreso_Neto_Ejecutado por defecto,
  a menos que el usuario pida explícitamente excluir inglés.
- Si la pregunta menciona un filtro específico, ese tiene prioridad sobre el filtro
  activo del reporte.
- Si no hay filtro ni en la pregunta ni en los filtros activos, trae el total general.

Pregunta del usuario: {pregunta_usuario}

Consulta SQL:"""

    respuesta = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=800,
        temperature=0,
    )
    sql = respuesta.choices[0].message.content.strip()
    return sql.replace("```sql", "").replace("```", "").strip()


def es_consulta_segura(sql):
    sql_upper = sql.strip().upper()
    palabras_prohibidas = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "EXEC", "MERGE"]
    if not sql_upper.startswith("SELECT"):
        return False
    return not any(p in sql_upper for p in palabras_prohibidas)


def ejecutar_consulta(sql):
    conn = conectar_sql()
    cursor = conn.cursor()
    cursor.execute(sql)
    columnas = [c[0] for c in cursor.description]
    filas = cursor.fetchall()
    conn.close()
    return columnas, filas


def redactar_respuesta(client, pregunta_usuario, columnas, filas, sql_usado):
    resultado_texto = f"Columnas: {columnas}\nFilas: {filas[:20]}"
    prompt = f"""El usuario preguntó: "{pregunta_usuario}"

Se ejecutó esta consulta SQL contra la base de datos real:
{sql_usado}

Resultado exacto de la base de datos:
{resultado_texto}

Redacta una respuesta clara y breve en español, con el dato exacto tal cual viene
de la base de datos (no lo redondees ni lo cambies). Si el resultado está vacío,
dilo claramente."""

    respuesta = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=300,
        temperature=0,
    )
    return respuesta.choices[0].message.content.strip()


def responder_pregunta(pregunta_usuario, filtros=None):
    client = get_client()
    sql = generar_sql(client, pregunta_usuario, filtros)

    if not es_consulta_segura(sql):
        return {"respuesta": "La consulta generada no es segura. No se ejecutó.", "sql": sql, "datos_fuente": []}

    columnas, filas = ejecutar_consulta(sql)

    if not filas:
        return {
            "respuesta": "No tengo datos suficientes para responder con los filtros indicados.",
            "sql": sql,
            "datos_fuente": [],
        }

    respuesta_texto = redactar_respuesta(client, pregunta_usuario, columnas, filas, sql)
    datos_fuente = [dict(zip(columnas, [str(v) for v in fila])) for fila in filas[:20]]

    return {"respuesta": respuesta_texto, "sql": sql, "datos_fuente": datos_fuente}


# ============================================================
# 4. RUTA HTTP
# ============================================================

@app.route("/api/chat", methods=["POST"])
def chat():
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Body inválido. Se espera JSON con 'pregunta'."}), 400

    pregunta_usuario = body.get("pregunta", "").strip()
    filtros = body.get("filtros", {})

    if not pregunta_usuario:
        return jsonify({"error": "Falta el campo 'pregunta'."}), 400

    try:
        resultado = responder_pregunta(pregunta_usuario, filtros)
    except Exception as e:
        return jsonify({"error": f"Error al procesar la pregunta: {str(e)}"}), 500

    return jsonify(resultado)


@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "mensaje": "Chatbot financiero CUN activo"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
