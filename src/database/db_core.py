from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL


def create_mssql_engine():
    """
    SQL Server connection using SQL login credentials.
    """
    server = "localhost"
    port = 1433
    database = "SotexHackathon"
    username = "sa"
    password = "SotexSolutions123!"

    connection_url = URL.create(
        "mssql+pyodbc",
        username=username,
        password=password,
        host=server,
        port=port,
        database=database,
        query={"driver": "ODBC Driver 17 for SQL Server"},
    )

    engine = create_engine(connection_url, fast_executemany=True)
    return engine


class DatabaseCore:
    def __init__(self, engine=None) -> None:
        self.engine = engine or create_mssql_engine()

    def fetch_all(self, sql: str, params: dict | None = None):
        params = params or {}
        with self.engine.connect() as conn:
            result = conn.execute(text(sql), params)
            rows = [dict(row) for row in result.mappings().all()]
        return rows
