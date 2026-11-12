from datetime import timedelta

import pendulum
from airflow.decorators import dag, task
from airflow.operators.python import get_current_context

from ingestao import FILES, ingest, verify

TIMEZONE = "America/Sao_Paulo"


def logical_day():
    return get_current_context()["logical_date"].in_timezone(TIMEZONE).to_date_string()


@dag(
    dag_id="rota_livre_ingestao_diaria",
    schedule="0 3 * * *",
    start_date=pendulum.datetime(2026, 1, 1, tz=TIMEZONE),
    catchup=False,
    max_active_runs=1,
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=2),
        "execution_timeout": timedelta(minutes=3),
    },
    tags=["rota-livre", "big-data"],
)
def rota_livre_ingestao_diaria():
    @task(do_xcom_push=False)
    def extrair_obstaculos():
        ingest("obstaculos", logical_day(), get_current_context()["run_id"])

    @task(do_xcom_push=False)
    def extrair_dados_urbanos():
        ingest("geosampa_calcadas", logical_day(), get_current_context()["run_id"])

    @task
    def verificar_arquivos():
        return {
            source: {key: metadata[key] for key in ("registros", "sha256", "status")}
            for source in FILES
            for metadata in [verify(source, logical_day())]
        }

    [extrair_obstaculos(), extrair_dados_urbanos()] >> verificar_arquivos()


rota_livre_ingestao_diaria()
