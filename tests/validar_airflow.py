"""Valida a estrutura, a execução real e o bloqueio por falha da DAG existente."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

DAG_ID = "rota_livre_ingestao_diaria"
DAG_FILE = f"/opt/airflow/dags/{DAG_ID}.py"
EXTRACTIONS = {"extrair_obstaculos", "extrair_dados_urbanos"}
VERIFY = "verificar_arquivos"


def load_dag():
    from airflow.models import DagBag

    bag = DagBag(dag_folder=DAG_FILE, include_examples=False)
    if bag.import_errors or DAG_ID not in bag.dags:
        raise AssertionError("A DAG esperada não foi importada")
    return bag.dags[DAG_ID]


def inspect_run(run_id):
    from airflow.configuration import conf
    from airflow.models import DagRun, Pool
    from airflow.settings import Session
    from ingestao import FILES, verify

    dag = load_dag()
    assert set(dag.task_ids) == EXTRACTIONS | {VERIFY}
    for name in EXTRACTIONS:
        task = dag.get_task(name)
        assert not task.upstream_task_ids
        assert task.downstream_task_ids == {VERIFY}
    check = dag.get_task(VERIFY)
    assert check.upstream_task_ids == EXTRACTIONS
    assert check.trigger_rule == "all_success"
    executor = conf.get("core", "executor")
    parallelism = conf.getint("core", "parallelism")
    assert executor == "LocalExecutor" and parallelism >= 2 and dag.max_active_tasks >= 2

    with Session() as session:
        pool = session.query(Pool).filter_by(pool="default_pool").one()
        extraction_tasks = [dag.get_task(name) for name in sorted(EXTRACTIONS)]
        assert all(task.pool == pool.pool for task in extraction_tasks)
        assert pool.slots >= sum(task.pool_slots for task in extraction_tasks)
        assert all(task.max_active_tis_per_dag is None for task in extraction_tasks)
        assert all(task.max_active_tis_per_dagrun is None for task in extraction_tasks)

        run = session.query(DagRun).filter_by(dag_id=DAG_ID, run_id=run_id).one()
        assert run.state == "success"
        instances = {ti.task_id: ti for ti in run.get_task_instances(session=session)}
        assert set(instances) == set(dag.task_ids)
        assert all(ti.state == "success" for ti in instances.values())
        extract = [instances[name] for name in sorted(EXTRACTIONS)]
        verification = instances[VERIFY]
        latest_start = max(ti.start_date for ti in extract)
        earliest_end = min(ti.end_date for ti in extract)
        overlap = (earliest_end - latest_start).total_seconds()
        assert overlap > 0, "As extrações não se sobrepuseram"
        assert verification.start_date >= max(ti.end_date for ti in extract)

        day = run.logical_date.astimezone(ZoneInfo("America/Sao_Paulo")).date().isoformat()
        counts = {}
        for source in FILES:
            metadata = verify(source, day)
            counts[source] = {key: metadata[key] for key in ("registros", "sha256", "status")}
        assert verification.xcom_pull(task_ids=VERIFY, session=session) == counts
        print(json.dumps({
            "dag_id": dag.dag_id, "run_id": run_id, "data_logica": day,
            "executor": executor, "parallelism": parallelism,
            "max_active_runs": dag.max_active_runs,
            "max_active_tasks": dag.max_active_tasks, "pool_slots": pool.slots,
            "trigger_rule": str(check.trigger_rule),
            "sobreposicao_segundos": overlap,
            "tarefas": [
                {"id": ti.task_id, "estado": ti.state,
                 "inicio_utc": ti.start_date.isoformat(), "fim_utc": ti.end_date.isoformat(),
                 "dependencias": sorted(dag.get_task(ti.task_id).upstream_task_ids)}
                for ti in [*extract, verification]
            ],
            "resultado_verificacao": counts,
        }, indent=2))


def expect_failure(operation, error):
    try:
        operation()
    except error:
        return
    raise AssertionError(f"Esperava falha {error.__name__}")


def test_failure(logical_date):
    real_root = Path(os.environ["INGEST_ROOT"])
    with TemporaryDirectory(prefix="rota-livre-etapa02-") as temp:
        home = Path(temp)
        landing = home / "chegada"
        os.environ.update({
            "AIRFLOW_HOME": str(home),
            "AIRFLOW__CORE__EXECUTOR": "SequentialExecutor",
            "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN": f"sqlite:///{home / 'airflow.db'}",
            "AIRFLOW__LOGGING__BASE_LOG_FOLDER": str(home / "logs"),
            "INGEST_ROOT": str(landing),
            "ROTA_LIVRE_API_URL": "http://api:8000/obstaculos",
        })
        subprocess.run([sys.executable, "-m", "airflow", "db", "migrate"],
                       check=True, stdout=subprocess.DEVNULL)

        import pendulum
        from ingestao import FILES, verify

        date = pendulum.parse(logical_date)
        day = date.in_timezone("America/Sao_Paulo").to_date_string()
        partition = f"data={day}"
        source = "geosampa_calcadas"
        copy = landing / source / partition
        shutil.copytree(real_root / source / partition, copy)

        dag = load_dag()
        dag.get_task("extrair_obstaculos").retries = 0
        run = dag.test(execution_date=date)
        states = {ti.task_id: ti.state for ti in run.get_task_instances()}
        assert run.state == "failed"
        assert states == {"extrair_obstaculos": "failed", "extrair_dados_urbanos": "success",
                          VERIFY: "upstream_failed"}
        assert not (landing / "obstaculos" / partition).exists()

        expect_failure(lambda: verify("obstaculos", day), FileNotFoundError)
        payload = copy / FILES[source]
        payload.write_bytes(payload.read_bytes() + b" ")
        expect_failure(lambda: verify(source, day), ValueError)
        print(json.dumps({"dag": run.state, "tarefas": states,
                          "lote_invalido_publicado": False,
                          "arquivo_ausente_rejeitado": True,
                          "checksum_divergente_rejeitado": True}, indent=2))


if __name__ == "__main__":
    mode, target = sys.argv[1:]
    {"run": inspect_run, "failure": test_failure}[mode](target)

